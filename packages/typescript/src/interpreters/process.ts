/**
 * Process interpreter — self-host inbound.
 *
 * Platform POST → verify secret → ACK 200 → parse → step → executeTurn.
 * Same kernel as Memory. Adapter Layer does platform I/O.
 */
import * as Effect from "effect/Effect"
import * as Layer from "effect/Layer"
import { executeTurn } from "../adapters/turn.ts"
import type { App } from "../core/app.ts"
import type { Connection } from "../core/connection.ts"
import type { Event } from "../core/events.ts"
import { AdapterPort, type HostPort } from "../core/ports.ts"
import {
  makeMemoryInterpreter,
  type MemoryInterpreter,
} from "./memory.ts"

export type ProcessOptions = {
  readonly channelName?: string
  readonly secretToken?: string
  readonly secretHeader?: string
  readonly connection: Connection
  readonly adapter: Layer.Layer<AdapterPort>
  readonly host?: Layer.Layer<HostPort>
}

export type ProcessInbound = {
  readonly secretHeader?: string
}

export type ProcessInterpreter = {
  readonly handle: (
    request: Request,
    inbound?: ProcessInbound,
  ) => Effect.Effect<Response>
}

const jsonOf = (request: Request): Effect.Effect<unknown> =>
  Effect.tryPromise({
    try: () => request.json() as Promise<unknown>,
    catch: () => new Error("invalid json"),
  }).pipe(Effect.orElseSucceed((): unknown => ({})))

export const makeProcessInterpreter = (
  app: App,
  options: ProcessOptions,
): Effect.Effect<ProcessInterpreter> =>
  Effect.gen(function* () {
    const memory: MemoryInterpreter = yield* makeMemoryInterpreter(app, {
      channelName: options.channelName ?? "",
      ...(options.host === undefined ? {} : { host: options.host }),
    })

    const runEvent = (event: Event) =>
      Effect.gen(function* () {
        const adapter = yield* AdapterPort
        yield* memory.run(event, adapter.overlapKey(event))
        const delta = yield* memory.produced
        yield* executeTurn(event, delta, options.connection)
      }).pipe(Effect.provide(options.adapter))

    const handle = (
      request: Request,
      inbound?: ProcessInbound,
    ): Effect.Effect<Response> =>
      Effect.gen(function* () {
        if (options.secretToken !== undefined) {
          const secretHeader = inbound?.secretHeader ?? options.secretHeader
          if (secretHeader === undefined) {
            return new Response(null, { status: 401 })
          }
          const got = request.headers.get(secretHeader)
          if (got !== options.secretToken) {
            return new Response(null, { status: 401 })
          }
        }
        const raw = yield* jsonOf(request)
        const adapter = yield* AdapterPort
        const events = yield* adapter.parse(raw).pipe(
          Effect.catchAll(() => Effect.succeed<ReadonlyArray<Event>>([])),
        )
        yield* Effect.forEach(events, (event) => runEvent(event), {
          discard: true,
        }).pipe(Effect.catchAll(() => Effect.void))
        return new Response(null, { status: 200 })
      }).pipe(Effect.provide(options.adapter))

    return { handle }
  })
