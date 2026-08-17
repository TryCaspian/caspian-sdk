/**
 * Memory interpreter — Effect runtime for an App. No HTTP.
 *
 * Kernel overlap is counters. This interpreter owns the waiting room:
 * `Queue.dropping(bound)` for queue, `Queue.sliding(1)` for debounce.
 */
import * as Chunk from "effect/Chunk"
import * as Effect from "effect/Effect"
import * as HashMap from "effect/HashMap"
import * as Layer from "effect/Layer"
import * as Option from "effect/Option"
import * as Queue from "effect/Queue"
import * as Ref from "effect/Ref"
import type { App, OverlapPolicy, Rule } from "../core/app.ts"
import type { Command } from "../core/commands.ts"
import { HostError } from "../core/errors.ts"
import type { Event } from "../core/events.ts"
import {
  drainTransition,
  idleOverlapState,
  type OverlapState,
} from "../core/overlap.ts"
import {
  HostPort,
  type StreamSink,
  ThreadStore,
} from "../core/ports.ts"
import type { Json } from "../core/json.ts"
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
  handlers: Ref.Ref<HashMap.HashMap<string, HostFn>>,
): Layer.Layer<HostPort> =>
  Layer.succeed(HostPort, {
    run: (handlerId, event, ctx) =>
      Effect.gen(function* () {
        const fn = HashMap.get(yield* Ref.get(handlers), handlerId)
        if (Option.isNone(fn)) {
          return yield* Effect.fail(
            new HostError({
              reason: `no handler registered for ${handlerId}`,
              handlerId,
            }),
          )
        }
        return fn.value(event, ctx)
      }),
  })

export const chaosHostLayer: Layer.Layer<HostPort> = Layer.succeed(HostPort, {
  run: (handlerId) =>
    Effect.fail(
      new HostError({ reason: "chaos: host failed", handlerId }),
    ),
})

export const DEFAULT_INTERPRETER_LOG_BOUND = 1024

export type MemoryInterpreterOptions = {
  readonly channelName?: string
  readonly host?: Layer.Layer<HostPort, never, ThreadStore>
  readonly logBound?: number
  readonly streamSink?: StreamSink
}

export type MemoryInterpreter = {
  readonly register: (
    handlerId: string,
    fn: HostFn,
  ) => Effect.Effect<void>
  readonly run: (
    event: Event,
    overlapKey?: string,
  ) => Effect.Effect<StepResult>
  readonly runSequence: (
    events: ReadonlyArray<Event>,
    overlapKey?: string,
  ) => Effect.Effect<ReadonlyArray<StepResult>>
  readonly commands: Effect.Effect<ReadonlyArray<Command>>
  readonly produced: Effect.Effect<ReadonlyArray<Command>>
  readonly errors: Effect.Effect<ReadonlyArray<HostError>>
  readonly posts: Effect.Effect<ReadonlyArray<Command>>
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

const makeBuffer = (
  policy: OverlapPolicy,
  bound: number,
): Effect.Effect<Queue.Queue<Event>> => {
  switch (policy) {
    case "debounce":
      return Queue.sliding<Event>(1)
    case "queue":
      return Queue.dropping<Event>(bound)
    case "drop":
    case "parallel":
      return Queue.dropping<Event>(1)
  }
}

export const makeMemoryInterpreter = (
  app: App,
  options: MemoryInterpreterOptions = {},
): Effect.Effect<MemoryInterpreter> =>
  Effect.gen(function* () {
    const handlers = yield* Ref.make(HashMap.empty<string, HostFn>())
    const stepState = yield* Ref.make<StepState>(emptyStepState)
    const logBound = options.logBound ?? DEFAULT_INTERPRETER_LOG_BOUND
    const recorded = yield* Ref.make<ReadonlyArray<Command>>([])
    const produced = yield* Ref.make<ReadonlyArray<Command>>([])
    const hostErrors = yield* Ref.make<ReadonlyArray<HostError>>([])
    const nextSeq = yield* Ref.make(0)
    const history = yield* Ref.make(
      HashMap.empty<string, ReadonlyArray<{ seq: number; event: Event }>>(),
    )
    const kv = yield* Ref.make(HashMap.empty<string, Json>())
    const buffers = yield* Ref.make(
      HashMap.empty<string, Queue.Queue<Event>>(),
    )
    const policyByKey = yield* Ref.make(
      HashMap.empty<string, OverlapPolicy>(),
    )
    const handlerByKey = yield* Ref.make(HashMap.empty<string, string>())

    const slide = <A>(
      current: ReadonlyArray<A>,
      extra: ReadonlyArray<A>,
    ): ReadonlyArray<A> => {
      const next = extra.length === 0 ? current : [...current, ...extra]
      return next.length <= logBound ? next : next.slice(next.length - logBound)
    }

    const kvKey = (threadId: string, key: string) => `${threadId}\0${key}`

    const threadStoreLayer = Layer.succeed(ThreadStore, {
      recent: (threadId, limit, current) =>
        Effect.gen(function* () {
          const entries = Option.getOrElse(
            HashMap.get(yield* Ref.get(history), String(threadId)),
            (): ReadonlyArray<{ seq: number; event: Event }> => [],
          )
          const currentSeq =
            entries.find((item) => item.event === current)?.seq ??
            Number.POSITIVE_INFINITY
          return entries
            .filter((item) => item.seq < currentSeq)
            .map((item) => item.event)
            .slice(-limit)
        }),
      getState: (threadId, key) =>
        Effect.map(
          Ref.get(kv),
          (map) =>
            Option.getOrElse(
              HashMap.get(map, kvKey(String(threadId), key)),
              (): Json | undefined => undefined,
            ),
        ),
      setState: (threadId, key, value) =>
        Ref.update(
          kv,
          HashMap.set(kvKey(String(threadId), key), value),
        ).pipe(Effect.asVoid),
    })

    const hostLayer = (
      options.host ??
      (memoryHostLayer(handlers) as Layer.Layer<HostPort, never, ThreadStore>)
    ).pipe(Layer.provide(threadStoreLayer))

    const appendCommands = (commands: ReadonlyArray<Command>) =>
      Effect.gen(function* () {
        yield* Ref.update(recorded, (current) => slide(current, commands))
        yield* Ref.update(produced, (current) => [...current, ...commands])
      })

    const bufferFor = (
      key: string,
      policy: OverlapPolicy,
      bound: number,
    ): Effect.Effect<Queue.Queue<Event>> =>
      Effect.gen(function* () {
        const existing = HashMap.get(yield* Ref.get(buffers), key)
        if (Option.isSome(existing)) {
          return existing.value
        }
        const queue = yield* makeBuffer(policy, bound)
        yield* Ref.update(buffers, HashMap.set(key, queue))
        return queue
      })

    const interpretHost = (
      rule: Rule,
      event: Event,
      skipped: ReadonlyArray<Event>,
    ): Effect.Effect<void> =>
      Effect.gen(function* () {
        const host = yield* HostPort
        const result = yield* host
          .run(rule.handler_id, event, {
            skipped,
            ...(options.streamSink === undefined
              ? {}
              : { sink: options.streamSink }),
          })
          .pipe(Effect.either)
        if (result._tag === "Left") {
          const error =
            result.left._tag === "HostError"
              ? result.left
              : new HostError({
                  reason: result.left._tag,
                  handlerId: rule.handler_id,
                })
          yield* Ref.update(hostErrors, (current) => slide(current, [error]))
          return
        }
        yield* appendCommands(result.right)
      }).pipe(Effect.provide(hostLayer))

    const ingest = (
      event: Event,
      key: string,
    ): Effect.Effect<StepResult> =>
      Effect.gen(function* () {
        const state = yield* Ref.get(stepState)
        const result = step(state, event, app, {
          channelName: options.channelName ?? "",
          overlapKey: key,
        })
        yield* Ref.set(stepState, result.state)
        yield* appendCommands(result.commands)
        const seq = (yield* Ref.get(nextSeq)) + 1
        yield* Ref.set(nextSeq, seq)
        yield* Ref.update(history, (map) => {
          const key = String(event.thread_id)
          const current = Option.getOrElse(
            HashMap.get(map, key),
            (): ReadonlyArray<{ seq: number; event: Event }> => [],
          )
          return HashMap.set(map, key, slide(current, [{ seq, event }]))
        })

        if (result.matched_rule) {
          yield* Ref.update(
            policyByKey,
            HashMap.set(key, result.matched_rule.overlap.policy),
          )
          yield* Ref.update(
            handlerByKey,
            HashMap.set(key, result.matched_rule.handler_id),
          )
        }

        if (result.decision === "enqueue" && result.matched_rule) {
          const queue = yield* bufferFor(
            key,
            result.matched_rule.overlap.policy,
            result.matched_rule.overlap.bound,
          )
          yield* Queue.offer(queue, event)
        }

        return result
      })

    const drainKey = (key: string): Effect.Effect<void> =>
      Effect.gen(function* () {
        const policy: OverlapPolicy = Option.getOrElse(
          HashMap.get(yield* Ref.get(policyByKey), key),
          (): OverlapPolicy => "queue",
        )
        for (;;) {
          const state = yield* Ref.get(stepState)
          const transition = drainTransition(overlapOf(state, key), policy)
          yield* Ref.set(stepState, withOverlap(state, key, transition.new_state))
          if (transition.decision !== "execute") {
            return
          }
          const queueOption = HashMap.get(yield* Ref.get(buffers), key)
          if (Option.isNone(queueOption)) {
            return
          }
          const waiting = Chunk.toReadonlyArray(
            yield* Queue.takeAll(queueOption.value),
          )
          const latest = waiting[waiting.length - 1]
          if (latest === undefined) {
            return
          }
          const skipped = waiting.slice(0, -1)
          const handlerId = HashMap.get(yield* Ref.get(handlerByKey), key)
          if (Option.isNone(handlerId)) {
            return
          }
          const rule = app.rules.find((item) => item.handler_id === handlerId.value)
          if (rule === undefined) {
            return
          }
          yield* appendCommands([
            { tag: "Typing", thread_id: latest.thread_id },
            { tag: "Host", handler_id: rule.handler_id },
          ])
          yield* interpretHost(rule, latest, skipped)
        }
      })

    const drainAll = (): Effect.Effect<void> =>
      Effect.gen(function* () {
        const state = yield* Ref.get(stepState)
        yield* Effect.forEach(Object.keys(state.overlap), drainKey, {
          discard: true,
        })
      })

    const runSequence = (
      events: ReadonlyArray<Event>,
      overlapKey?: string,
    ): Effect.Effect<ReadonlyArray<StepResult>> =>
      Effect.gen(function* () {
        yield* Ref.set(produced, [])
        const ingested: Array<{ event: Event; result: StepResult }> = []
        for (const event of events) {
          const key = overlapKey ?? event.thread_id
          const result = yield* ingest(event, key)
          ingested.push({ event, result })
        }
        for (const item of ingested) {
          if (item.result.decision === "execute" && item.result.matched_rule) {
            yield* interpretHost(item.result.matched_rule, item.event, [])
          }
        }
        yield* drainAll()
        return ingested.map((item) => item.result)
      })

    const unmatched = (): Effect.Effect<StepResult> =>
      Effect.gen(function* () {
        const state = yield* Ref.get(stepState)
        return {
          decision: "unmatched" as const,
          commands: [],
          matched_rule: undefined,
          skipped_count: 0,
          dropped: false,
          state,
        }
      })

    return {
      register: (handlerId, fn) =>
        Ref.update(handlers, HashMap.set(handlerId, fn)).pipe(Effect.asVoid),
      run: (event, overlapKey) =>
        Effect.gen(function* () {
          const results = yield* runSequence([event], overlapKey)
          return results[0] ?? (yield* unmatched())
        }),
      runSequence,
      commands: Ref.get(recorded),
      produced: Ref.get(produced),
      errors: Ref.get(hostErrors),
      posts: Ref.get(recorded).pipe(
        Effect.map((commands) =>
          commands.filter((command) => command.tag === "Post"),
        ),
      ),
    }
  })
