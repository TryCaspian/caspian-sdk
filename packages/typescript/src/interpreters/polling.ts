/**
 * Polling runner — self-host long-poll loop for channels without webhooks.
 *
 * Adapters stay pure: poll() builds a request-description Sent (Telegram
 * getUpdates). fetchUpdates dispatches it via an injected Transport and parses
 * the response into (updates, nextOffset).
 */
import * as Effect from "effect/Effect"
import { AdapterError } from "../core/errors.ts"
import type { Connection } from "../core/connection.ts"
import { AdapterPort, type Sent } from "../core/ports.ts"
import type { Transport } from "./transport.ts"

const asRecord = (value: unknown): { readonly [key: string]: unknown } =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as { readonly [key: string]: unknown })
    : {}

export const extractUpdates = (
  sent: Sent,
): ReadonlyArray<{ readonly [key: string]: unknown }> => {
  const raw = sent.raw
  let parsed: unknown = raw.body ?? raw.response ?? raw.result ?? raw
  if (typeof parsed === "string") {
    try {
      parsed = JSON.parse(parsed)
    } catch {
      return []
    }
  }
  if (Array.isArray(parsed)) {
    return parsed.filter(
      (item): item is { readonly [key: string]: unknown } =>
        typeof item === "object" && item !== null && !Array.isArray(item),
    )
  }
  const record = asRecord(parsed)
  const result = record.result
  if (!Array.isArray(result)) {
    return []
  }
  return result.filter(
    (item): item is { readonly [key: string]: unknown } =>
      typeof item === "object" && item !== null && !Array.isArray(item),
  )
}

export const fetchUpdates = (
  conn: Connection,
  offset: number,
  transport: Transport,
): Effect.Effect<
  {
    readonly updates: ReadonlyArray<{ readonly [key: string]: unknown }>
    readonly nextOffset: number
  },
  AdapterError,
  AdapterPort
> =>
  Effect.gen(function* () {
    const adapter = yield* AdapterPort
    if (adapter.poll === undefined) {
      return yield* Effect.fail(
        new AdapterError({
          reason: `Adapter ${JSON.stringify(adapter.name)} cannot poll`,
        }),
      )
    }
    const request = yield* adapter.poll(offset, conn)
    const dispatched = yield* transport.dispatch(request)
    const updates = extractUpdates(dispatched)
    let nextOffset = offset
    for (const update of updates) {
      const updateId = update.update_id
      if (typeof updateId === "number" && updateId >= nextOffset) {
        nextOffset = updateId + 1
      }
    }
    return { updates, nextOffset }
  })

export type PollSink = (
  body: unknown,
  headers: { readonly [key: string]: string },
) => Effect.Effect<ReadonlyArray<unknown>>

export class PollingRunner {
  #offset: number
  #stop = false

  constructor(
    readonly adapter: import("effect/Layer").Layer<AdapterPort>,
    readonly connection: Connection,
    readonly sink: PollSink,
    readonly transport: Transport,
    offset = 0,
  ) {
    this.#offset = offset
  }

  pollOnce(): Effect.Effect<ReadonlyArray<unknown>> {
    const connection = this.connection
    const transport = this.transport
    const sink = this.sink
    const offset = this.#offset
    return Effect.gen(function* () {
      const adapter = yield* AdapterPort
      if (adapter.poll === undefined) {
        return {
          collected: [
            {
              ok: false as const,
              error: new AdapterError({
                reason: `Adapter ${JSON.stringify(adapter.name)} cannot poll`,
              }),
            },
          ],
          nextOffset: offset,
        }
      }
      const fetched = yield* fetchUpdates(connection, offset, transport).pipe(
        Effect.either,
      )
      if (fetched._tag === "Left") {
        return {
          collected: [{ ok: false as const, error: fetched.left }],
          nextOffset: offset,
        }
      }
      const collected: unknown[] = []
      for (const update of fetched.right.updates) {
        const batch = yield* sink(update, {})
        collected.push(...batch)
      }
      return { collected, nextOffset: fetched.right.nextOffset }
    }).pipe(
      Effect.tap((result) =>
        Effect.sync(() => {
          this.#offset = result.nextOffset
        }),
      ),
      Effect.map((result) => result.collected),
      Effect.provide(this.adapter),
    )
  }

  stop(): void {
    this.#stop = true
  }

  runForever(
    options: { readonly maxIterations?: number } = {},
  ): Effect.Effect<ReadonlyArray<unknown>> {
    const pollOnce = () => this.pollOnce()
    const shouldStop = () => this.#stop
    const markRunning = () => {
      this.#stop = false
    }
    return Effect.gen(function* () {
      markRunning()
      const collected: unknown[] = []
      let iterations = 0
      while (!shouldStop()) {
        collected.push(...(yield* pollOnce()))
        iterations += 1
        if (
          options.maxIterations !== undefined &&
          iterations >= options.maxIterations
        ) {
          break
        }
      }
      return collected
    })
  }
}

