/**
 * Hosted interpreter — Caspian event delivery.
 *
 * Gateway POST → verify HMAC → ACK 200 → parse Event envelope → step → outbox.
 * Same kernel as Memory/Process. Adapter Layer talks to the outbox, not Telegram.
 */
import * as Effect from "effect/Effect"
import * as Layer from "effect/Layer"
import * as Schema from "effect/Schema"
import { executeTurn } from "../adapters/turn.ts"
import type { App } from "../core/app.ts"
import { Command } from "../core/commands.ts"
import type { Connection } from "../core/connection.ts"
import { AdapterError } from "../core/errors.ts"
import { Event } from "../core/events.ts"
import { decodeStrict } from "../core/parse.ts"
import { AdapterPort, type HostPort, type ThreadStore } from "../core/ports.ts"
import {
  makeMemoryInterpreter,
  type MemoryInterpreter,
} from "./memory.ts"

export type HostedCall =
  | { readonly op: "ack"; readonly event: Event }
  | { readonly op: "execute"; readonly command: Command }

export type HostedFetch = (
  input: string | URL | Request,
  init?: RequestInit,
) => Promise<Response>

export type HostedOptions = {
  readonly channelName?: string
  readonly webhookSecret: string
  readonly connection: Connection
  readonly adapter: Layer.Layer<AdapterPort>
  readonly host?: Layer.Layer<HostPort, never, ThreadStore>
}

export type HostedInbound = {
  readonly signatureHeader?: string
}

export type HostedInterpreter = {
  readonly handle: (
    request: Request,
    inbound?: HostedInbound,
  ) => Effect.Effect<Response>
}

const HostedDelivery = Schema.Struct({
  event: Event,
})
const decodeDelivery = decodeStrict(HostedDelivery)

const DEFAULT_OUTBOX = "https://api.trycaspianai.com/v1/outbox"

const hex = (bytes: ArrayBuffer): string =>
  [...new Uint8Array(bytes)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("")

const hmacSha256 = (secret: string, body: string): Effect.Effect<string> =>
  Effect.tryPromise({
    try: async () => {
      const key = await crypto.subtle.importKey(
        "raw",
        new TextEncoder().encode(secret),
        { name: "HMAC", hash: "SHA-256" },
        false,
        ["sign"],
      )
      const mac = await crypto.subtle.sign(
        "HMAC",
        key,
        new TextEncoder().encode(body),
      )
      return `sha256=${hex(mac)}`
    },
    catch: () => new Error("hmac failed"),
  }).pipe(Effect.orElseSucceed(() => "sha256="))

const timingSafeEqual = (left: string, right: string): boolean => {
  if (left.length !== right.length) {
    return false
  }
  let mismatch = 0
  for (let i = 0; i < left.length; i += 1) {
    mismatch |= left.charCodeAt(i) ^ right.charCodeAt(i)
  }
  return mismatch === 0
}

const jsonOf = (text: string): unknown => {
  try {
    return JSON.parse(text) as unknown
  } catch {
    return {}
  }
}

const apiKeyOf = (conn: Connection): string => {
  const key = conn.config.apiKey
  return typeof key === "string" ? key : ""
}

const skipExecute = (command: Command): boolean => command.tag === "Host"

export const hostedLayer = (
  sink: HostedCall[],
): Layer.Layer<AdapterPort> =>
  Layer.succeed(AdapterPort, {
    name: "hosted",
    parse: (raw) =>
      decodeDelivery(raw).pipe(
        Effect.map((delivery) => [delivery.event] as const),
      ),
    overlapKey: (event) => String(event.thread_id),
    ack: (event) =>
      Effect.sync(() => {
        if (event.kind === "action") {
          sink.push({ op: "ack", event })
        }
        return { ok: true as const }
      }),
    execute: (command) =>
      Effect.sync(() => {
        if (!skipExecute(command)) {
          sink.push({ op: "execute", command })
        }
        return { ok: true as const }
      }),
  })

const postOutbox = (
  fetchImpl: HostedFetch,
  conn: Connection,
  outboxUrl: string,
  body: unknown,
  commandTag: string,
): Effect.Effect<{ readonly ok: true }, AdapterError> => {
  const apiKey = apiKeyOf(conn)
  if (apiKey.length === 0) {
    return Effect.fail(
      new AdapterError({
        reason: "missing apiKey on connection",
        commandTag,
      }),
    )
  }
  return Effect.tryPromise({
    try: async () => {
      const response = await fetchImpl(outboxUrl, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${apiKey}`,
        },
        body: JSON.stringify(body),
      })
      if (!response.ok) {
        throw new Error(`outbox HTTP ${response.status}`)
      }
      return { ok: true as const }
    },
    catch: (cause) =>
      new AdapterError({
        reason: cause instanceof Error ? cause.message : String(cause),
        commandTag,
      }),
  })
}

export const hostedHttpLayer = (
  fetchImpl: HostedFetch = fetch,
  outboxUrl = DEFAULT_OUTBOX,
): Layer.Layer<AdapterPort> =>
  Layer.succeed(AdapterPort, {
    name: "hosted",
    parse: (raw) =>
      decodeDelivery(raw).pipe(
        Effect.map((delivery) => [delivery.event] as const),
      ),
    overlapKey: (event) => String(event.thread_id),
    ack: (event, conn) => {
      if (event.kind !== "action") {
        return Effect.succeed({ ok: true as const })
      }
      return postOutbox(
        fetchImpl,
        conn,
        outboxUrl,
        { op: "ack", event: Schema.encodeSync(Event)(event) },
        "Ack",
      )
    },
    execute: (command, conn) => {
      if (skipExecute(command)) {
        return Effect.succeed({ ok: true as const })
      }
      return postOutbox(
        fetchImpl,
        conn,
        outboxUrl,
        { op: "execute", command: Schema.encodeSync(Command)(command) },
        command.tag,
      )
    },
  })

export const makeHostedInterpreter = (
  app: App,
  options: HostedOptions,
): Effect.Effect<HostedInterpreter> =>
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
      inbound?: HostedInbound,
    ): Effect.Effect<Response> =>
      Effect.gen(function* () {
        const bodyText = yield* Effect.tryPromise({
          try: () => request.text(),
          catch: () => "",
        }).pipe(Effect.orElseSucceed(() => ""))
        const signatureHeader = inbound?.signatureHeader
        if (signatureHeader === undefined) {
          return new Response(null, { status: 401 })
        }
        const got = request.headers.get(signatureHeader) ?? ""
        const expected = yield* hmacSha256(options.webhookSecret, bodyText)
        if (!timingSafeEqual(got, expected)) {
          return new Response(null, { status: 401 })
        }
        const adapter = yield* AdapterPort
        const events = yield* adapter.parse(jsonOf(bodyText)).pipe(
          Effect.catchAll(() => Effect.succeed<ReadonlyArray<Event>>([])),
        )
        yield* Effect.forEach(events, (event) => runEvent(event), {
          discard: true,
        }).pipe(Effect.catchAll(() => Effect.void))
        return new Response(null, { status: 200 })
      }).pipe(Effect.provide(options.adapter))

    return { handle }
  })
