/**
 * HTTP transport — the one place real network I/O happens for outbound commands.
 *
 * Adapters build request descriptions (pure data). The transport dispatches them.
 */
import * as Effect from "effect/Effect"
import { AdapterError } from "../core/errors.ts"
import type { JsonObject } from "../core/json.ts"
import type { Sent } from "../core/ports.ts"

export type AdapterFetch = (
  input: string | URL | Request,
  init?: RequestInit,
) => Promise<Response>

export type Transport = {
  readonly dispatch: (sent: Sent) => Effect.Effect<Sent, AdapterError>
}

const asRecord = (value: unknown): { readonly [key: string]: unknown } =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as { readonly [key: string]: unknown })
    : {}

const extractMessageId = (payload: unknown): string => {
  const data = asRecord(payload)
  const nested = asRecord(data.result ?? data)
  for (const key of ["message_id", "ts", "id"]) {
    if (nested[key] !== undefined && nested[key] !== null) {
      return String(nested[key])
    }
  }
  return ""
}

export class HttpTransport implements Transport {
  readonly #fetch: AdapterFetch
  readonly #timeoutMs: number

  constructor(
    fetchImpl: AdapterFetch = fetch,
    options: { readonly timeoutMs?: number } = {},
  ) {
    this.#fetch = fetchImpl
    this.#timeoutMs = options.timeoutMs ?? 10_000
  }

  dispatch(sent: Sent): Effect.Effect<Sent, AdapterError> {
    const req = sent.raw
    const transport = typeof req.transport === "string" ? req.transport : ""
    if (transport === "noop" || transport.length === 0) {
      return Effect.succeed({
        ok: true as const,
        message_id: sent.message_id,
        raw: { native: req.native ?? "" } as JsonObject,
      })
    }
    if (
      transport !== "http_json" &&
      transport !== "http_form" &&
      transport !== "http_multipart"
    ) {
      return Effect.fail(
        new AdapterError({ reason: `Unsupported transport: ${JSON.stringify(transport)}` }),
      )
    }
    const method = typeof req.method === "string" ? req.method : "POST"
    const url = typeof req.url === "string" ? req.url : ""
    const native = typeof req.native === "string" ? req.native : ""
    return Effect.tryPromise({
      try: async () => {
        const init: RequestInit = {
          method,
          headers: { ...asStringMap(req.headers) },
          signal: AbortSignal.timeout(this.#timeoutMs),
        }
        if (transport === "http_json") {
          init.headers = {
            "content-type": "application/json",
            ...asStringMap(req.headers),
          }
          if (req.json !== undefined) {
            init.body = JSON.stringify(req.json)
          }
        } else if (transport === "http_form") {
          init.headers = {
            "content-type": "application/x-www-form-urlencoded",
            ...asStringMap(req.headers),
          }
          init.body = new URLSearchParams(asStringMap(req.form)).toString()
        } else {
          const form = new FormData()
          for (const [key, value] of Object.entries(asStringMap(req.form))) {
            form.set(key, value)
          }
          init.body = form
        }
        const response = await this.#fetch(url, init)
        if (!response.ok) {
          const text = await response.text()
          throw new Error(`${response.status}: ${text.slice(0, 200)}`)
        }
        let payload: unknown = {}
        try {
          payload = await response.json()
        } catch {
          // not JSON
        }
        return {
          ok: true as const,
          message_id: extractMessageId(payload),
          raw: {
            status: response.status,
            response: (typeof payload === "object" && payload !== null
              ? payload
              : {}) as JsonObject,
          },
        }
      },
      catch: (cause) =>
        new AdapterError({
          reason: cause instanceof Error ? cause.message : String(cause),
          commandTag: native,
        }),
    })
  }
}

const asStringMap = (value: unknown): { readonly [key: string]: string } => {
  const record = asRecord(value)
  const out: { [key: string]: string } = {}
  for (const [key, item] of Object.entries(record)) {
    if (typeof item === "string") {
      out[key] = item
    }
  }
  return out
}

export class RecordingTransport implements Transport {
  readonly dispatched: Sent[] = []

  dispatch(sent: Sent): Effect.Effect<Sent, AdapterError> {
    this.dispatched.push(sent)
    return Effect.succeed({
      ok: true as const,
      message_id: "rec_1",
      raw: sent.raw,
    })
  }
}

export class ChaosTransport implements Transport {
  readonly reason: string

  constructor(reason = "chaos") {
    this.reason = reason
  }

  dispatch(sent: Sent): Effect.Effect<Sent, AdapterError> {
    const native = typeof sent.raw.native === "string" ? sent.raw.native : ""
    return Effect.fail(new AdapterError({ reason: this.reason, commandTag: native }))
  }
}

export class MultiplexTransport implements Transport {
  readonly #routes: { readonly [key: string]: Transport }
  readonly #default: Transport | undefined

  constructor(
    routes: { readonly [key: string]: Transport },
    fallback?: Transport,
  ) {
    this.#routes = routes
    this.#default = fallback
  }

  dispatch(sent: Sent): Effect.Effect<Sent, AdapterError> {
    const name = typeof sent.raw.transport === "string" ? sent.raw.transport : ""
    const impl = this.#routes[name] ?? this.#default
    if (impl === undefined) {
      return Effect.fail(new AdapterError({ reason: `No transport for ${JSON.stringify(name)}` }))
    }
    return impl.dispatch(sent)
  }
}

export const maybeDispatch = (
  transport: Transport | undefined,
  sent: Sent,
): Effect.Effect<Sent, AdapterError> => {
  if (transport === undefined) {
    return Effect.succeed(sent)
  }
  const name = typeof sent.raw.transport === "string" ? sent.raw.transport : ""
  if (name.length === 0) {
    return Effect.succeed(sent)
  }
  return transport.dispatch(sent)
}

export const defaultMultiplex = (
  fetchImpl: AdapterFetch = fetch,
  extras: { readonly [key: string]: Transport } = {},
): MultiplexTransport => {
  const http = new HttpTransport(fetchImpl)
  return new MultiplexTransport({
    http_json: http,
    http_form: http,
    http_multipart: http,
    noop: http,
    ...extras,
  })
}

