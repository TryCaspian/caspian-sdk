/**
 * Process interpreter — self-host inbound.
 *
 * verify → parse → step → executeTurn → transport.
 * Webhook, poll, and cx.handle all call handleRaw.
 */
import * as Effect from "effect/Effect"
import type * as Layer from "effect/Layer"
import type { App } from "../core/app.ts"
import type { Connection } from "../core/connection.ts"
import { DecodeError, type CaspianError } from "../core/errors.ts"
import type { Event } from "../core/events.ts"
import {
  AdapterPort,
  type HostPort,
  type Sent,
  type StreamSink,
  type ThreadStore,
} from "../core/ports.ts"
import {
  makeMemoryInterpreter,
  type MemoryInterpreter,
} from "./memory.ts"
import { maybeDispatch, type Transport } from "./transport.ts"

export type HandleResult =
  | { readonly ok: true; readonly value: Sent }
  | { readonly ok: false; readonly error: CaspianError }

export type ProcessOptions = {
  readonly channelName?: string
  readonly secretToken?: string
  readonly secretHeader?: string
  readonly connection: Connection
  readonly adapter: Layer.Layer<AdapterPort>
  readonly host?: Layer.Layer<HostPort, never, ThreadStore>
  readonly transport?: Transport
}

export type ProcessInbound = {
  readonly secretHeader?: string
}

export type ProcessInterpreter = {
  readonly handle: (
    request: Request,
    inbound?: ProcessInbound,
  ) => Effect.Effect<Response>
  readonly handleRaw: (
    body: unknown,
    headers?: { readonly [key: string]: string },
  ) => Effect.Effect<ReadonlyArray<HandleResult>>
}

const jsonOf = (request: Request): Effect.Effect<unknown> =>
  Effect.tryPromise({
    try: () => request.json() as Promise<unknown>,
    catch: () => new Error("invalid json"),
  }).pipe(Effect.orElseSucceed((): unknown => ({})))

const headersOf = (request: Request): { readonly [key: string]: string } => {
  const headers: { [key: string]: string } = {}
  request.headers.forEach((value, key) => {
    headers[key] = value
  })
  return headers
}

const asHandleResult = (
  effect: Effect.Effect<Sent, import("../core/errors.ts").AdapterError>,
): Effect.Effect<HandleResult> =>
  effect.pipe(
    Effect.map((value): HandleResult => ({ ok: true, value })),
    Effect.catchAll((error): Effect.Effect<HandleResult> =>
      Effect.succeed({ ok: false, error }),
    ),
  )

export const makeProcessInterpreter = (
  app: App,
  options: ProcessOptions,
): Effect.Effect<ProcessInterpreter> =>
  Effect.gen(function* () {
    const streamSink: StreamSink | undefined =
      options.transport === undefined
        ? undefined
        : {
            can_stream: true,
            emit: (command) =>
              Effect.gen(function* () {
                const adapter = yield* AdapterPort
                const sent = yield* adapter.execute(command, options.connection)
                const dispatched = yield* maybeDispatch(options.transport, sent)
                return dispatched.message_id
              }).pipe(Effect.provide(options.adapter)),
          }

    const memory: MemoryInterpreter = yield* makeMemoryInterpreter(app, {
      channelName: options.channelName ?? "",
      ...(options.host === undefined ? {} : { host: options.host }),
      ...(streamSink === undefined ? {} : { streamSink }),
    })

    const runEvent = (event: Event) =>
      Effect.gen(function* () {
        const adapter = yield* AdapterPort
        yield* memory.run(event, adapter.overlapKey(event))
      }).pipe(Effect.provide(options.adapter))

    const handleRaw = (
      body: unknown,
      headers: { readonly [key: string]: string } = {},
    ): Effect.Effect<ReadonlyArray<HandleResult>> =>
      Effect.gen(function* () {
        const adapter = yield* AdapterPort
        const envelope = { body, headers }
        if (!adapter.verify(envelope, options.connection)) {
          return [
            {
              ok: false as const,
              error: new DecodeError({
                reason: "Webhook signature verification failed",
              }),
            },
          ]
        }
        const parsed = yield* adapter.parse(body).pipe(Effect.either)
        if (parsed._tag === "Left") {
          return [{ ok: false as const, error: parsed.left }]
        }
        const results: HandleResult[] = []
        for (const event of parsed.right) {
          const ack = yield* asHandleResult(
            adapter.acknowledge(event, options.connection).pipe(
              Effect.flatMap((sent) => maybeDispatch(options.transport, sent)),
            ),
          )
          results.push(ack)
          const producedBefore = yield* memory.produced
          yield* runEvent(event).pipe(Effect.catchAll(() => Effect.void))
          const producedAfter = yield* memory.produced
          const delta = producedAfter.slice(producedBefore.length)
          for (const command of delta) {
            const executed = yield* asHandleResult(
              adapter.execute(command, options.connection).pipe(
                Effect.flatMap((sent) =>
                  maybeDispatch(options.transport, sent),
                ),
              ),
            )
            results.push(executed)
          }
        }
        return results
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
        const results = yield* handleRaw(raw, headersOf(request))
        const unauthorized = results.some(
          (result) =>
            !result.ok &&
            result.error._tag === "DecodeError" &&
            result.error.reason === "Webhook signature verification failed",
        )
        if (unauthorized) {
          return new Response(null, { status: 401 })
        }
        const twiml = results
          .filter((result) => result.ok)
          .map((result) =>
            result.ok && typeof result.value.raw.twiml === "string"
              ? result.value.raw.twiml
              : "",
          )
          .find((item) => item.length > 0)
        if (twiml !== undefined) {
          return new Response(twiml, {
            status: 200,
            headers: { "content-type": "text/xml" },
          })
        }
        return new Response(null, { status: 200 })
      })

    return { handle, handleRaw }
  })
