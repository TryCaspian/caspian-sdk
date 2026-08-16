/**
 * Memory interpreter — runs an App in-process. No HTTP.
 *
 * Owns the overlap buffer the kernel only counts: enqueue stores events,
 * drain runs the latest and passes skipped.
 */
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import * as Layer from "effect/Layer"
import type { App, OverlapPolicy, Rule } from "../core/app.ts"
import type { Command } from "../core/commands.ts"
import { HostError } from "../core/errors.ts"
import type { Event } from "../core/events.ts"
import {
  drainTransition,
  idleOverlapState,
  type OverlapState,
} from "../core/overlap.ts"
import { HostPort } from "../core/ports.ts"
import {
  emptyStepState,
  step,
  type StepResult,
  type StepState,
} from "../core/step.ts"

export type HostFn = (
  event: Event,
  ctx: { readonly skipped: ReadonlyArray<Event> },
) => ReadonlyArray<Command>

export const memoryHostLayer = (
  handlers: Map<string, HostFn>,
): Layer.Layer<HostPort> =>
  Layer.succeed(HostPort, {
    run: (handlerId, event, ctx) => {
      const fn = handlers.get(handlerId)
      if (fn === undefined) {
        return Effect.fail(
          new HostError({
            reason: `no handler registered for ${handlerId}`,
            handlerId,
          }),
        )
      }
      return Effect.sync(() => fn(event, ctx))
    },
  })

export const chaosHostLayer: Layer.Layer<HostPort> = Layer.succeed(HostPort, {
  run: (handlerId) =>
    Effect.fail(
      new HostError({ reason: "chaos: host failed", handlerId }),
    ),
})

export type MemoryInterpreterOptions = {
  readonly channelName?: string
  readonly host?: Layer.Layer<HostPort>
}

const overlapOf = (state: StepState, key: string): OverlapState =>
  state.overlap[key] ?? idleOverlapState

const withOverlap = (
  state: StepState,
  key: string,
  next: OverlapState,
): StepState => ({
  overlap: { ...state.overlap, [key]: next },
})

export class MemoryInterpreter {
  private state: StepState = emptyStepState
  private readonly buffers = new Map<string, Array<Event>>()
  private readonly policyByKey = new Map<string, OverlapPolicy>()
  private readonly handlerByKey = new Map<string, string>()
  private readonly handlers = new Map<string, HostFn>()
  private readonly recorded: Command[] = []
  private readonly hostErrors: HostError[] = []
  private readonly hostLayer: Layer.Layer<HostPort>

  constructor(
    private readonly app: App,
    private readonly options: MemoryInterpreterOptions = {},
  ) {
    this.hostLayer = options.host ?? memoryHostLayer(this.handlers)
  }

  register(handlerId: string, fn: HostFn): void {
    this.handlers.set(handlerId, fn)
  }

  get commands(): ReadonlyArray<Command> {
    return this.recorded
  }

  get errors(): ReadonlyArray<HostError> {
    return this.hostErrors
  }

  posts(): ReadonlyArray<Command> {
    return this.recorded.filter((command) => command.tag === "Post")
  }

  run(event: Event, overlapKey?: string): StepResult {
    return this.runSequence([event], overlapKey)[0] ?? this.emptyResult()
  }

  runSequence(
    events: ReadonlyArray<Event>,
    overlapKey?: string,
  ): ReadonlyArray<StepResult> {
    const ingested: Array<{ event: Event; result: StepResult; key: string }> =
      []
    for (const event of events) {
      const key = overlapKey ?? event.thread_id
      const result = this.ingest(event, key)
      ingested.push({ event, result, key })
    }

    for (const item of ingested) {
      if (item.result.decision === "execute" && item.result.matched_rule) {
        this.interpretHost(item.result.matched_rule, item.event, [])
      }
    }

    this.drainAll()
    return ingested.map((item) => item.result)
  }

  reset(): void {
    this.state = emptyStepState
    this.buffers.clear()
    this.policyByKey.clear()
    this.handlerByKey.clear()
    this.recorded.length = 0
    this.hostErrors.length = 0
  }

  private ingest(event: Event, key: string): StepResult {
    const result = step(this.state, event, this.app, {
      channelName: this.options.channelName ?? "",
      overlapKey: key,
    })
    this.state = result.state
    this.recorded.push(...result.commands)

    if (result.matched_rule) {
      this.policyByKey.set(key, result.matched_rule.overlap.policy)
      this.handlerByKey.set(key, result.matched_rule.handler_id)
    }

    if (result.decision === "enqueue") {
      const current = this.buffers.get(key) ?? []
      if (result.matched_rule?.overlap.policy === "debounce") {
        this.buffers.set(key, [event])
      } else {
        this.buffers.set(key, [...current, event])
      }
    }

    return result
  }

  private drainAll(): void {
    for (const key of Object.keys(this.state.overlap)) {
      this.drainKey(key)
    }
  }

  private drainKey(key: string): void {
    const policy = this.policyByKey.get(key) ?? "queue"
    for (;;) {
      const transition = drainTransition(overlapOf(this.state, key), policy)
      this.state = withOverlap(this.state, key, transition.new_state)
      if (transition.decision !== "execute") {
        return
      }
      const buffer = this.buffers.get(key) ?? []
      const latest = buffer[buffer.length - 1]
      const skipped = buffer.slice(0, -1)
      this.buffers.set(key, [])
      if (latest === undefined) {
        return
      }
      const handlerId = this.handlerByKey.get(key)
      const rule = this.app.rules.find((item) => item.handler_id === handlerId)
      if (rule === undefined) {
        return
      }
      this.recorded.push(
        { tag: "Typing", thread_id: latest.thread_id },
        { tag: "Host", handler_id: rule.handler_id },
      )
      this.interpretHost(rule, latest, skipped)
    }
  }

  private interpretHost(
    rule: Rule,
    event: Event,
    skipped: ReadonlyArray<Event>,
  ): void {
    const program = Effect.gen(function* () {
      const host = yield* HostPort
      return yield* host.run(rule.handler_id, event, { skipped })
    }).pipe(Effect.provide(this.hostLayer), Effect.either)

    const result = Effect.runSync(program)
    if (Either.isLeft(result)) {
      if (result.left._tag === "HostError") {
        this.hostErrors.push(result.left)
      } else {
        this.hostErrors.push(
          new HostError({
            reason: result.left._tag,
            handlerId: rule.handler_id,
          }),
        )
      }
      return
    }
    this.recorded.push(...result.right)
  }

  private emptyResult(): StepResult {
    return {
      decision: "unmatched",
      commands: [],
      matched_rule: undefined,
      skipped_count: 0,
      dropped: false,
      state: this.state,
    }
  }
}
