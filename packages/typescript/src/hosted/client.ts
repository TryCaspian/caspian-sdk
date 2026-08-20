/**
 * Gateway client — the one place hosted mode performs HTTP.
 *
 * Mirrors the Python `caspian.hosted.client`. The important detail, learned the
 * hard way against the live API: several endpoints (/v1/events, /v1/channels,
 * /v1/connections) answer with a JSON **array**, so a client that only keeps
 * objects silently reports success with an empty body.
 */
import * as Effect from "effect/Effect"
import { AdapterError } from "../core/errors.ts"
import type { Json, JsonObject } from "../core/json.ts"

export const DEFAULT_BASE_URL = "https://api.trycaspianai.com"

export type GatewayRequest = {
  readonly method: "GET" | "POST" | "PATCH" | "DELETE"
  readonly path: string
  readonly params?: Readonly<Record<string, string>>
  readonly body?: JsonObject
}

export type GatewayResponse = {
  readonly status: number
  /** Object bodies. Empty when the endpoint answered with an array. */
  readonly json: JsonObject
  /** Array bodies. Empty when the endpoint answered with an object. */
  readonly rows: ReadonlyArray<Json>
}

export type GatewayClient = {
  readonly send: (
    request: GatewayRequest,
  ) => Effect.Effect<GatewayResponse, AdapterError>
}

export type GatewayFetch = (
  input: string,
  init?: {
    method?: string
    headers?: Record<string, string>
    body?: string
  },
) => Promise<{
  readonly ok: boolean
  readonly status: number
  readonly text: () => Promise<string>
}>

const urlOf = (base: string, request: GatewayRequest): string => {
  const query = new URLSearchParams(request.params ?? {}).toString()
  return `${base}${request.path}${query.length > 0 ? `?${query}` : ""}`
}

/** Split a decoded body into the object and array halves. Never throws. */
const partition = (text: string): { json: JsonObject; rows: ReadonlyArray<Json> } => {
  try {
    const parsed: unknown = JSON.parse(text)
    if (Array.isArray(parsed)) {
      return { json: {}, rows: parsed as ReadonlyArray<Json> }
    }
    if (parsed !== null && typeof parsed === "object") {
      return { json: parsed as JsonObject, rows: [] }
    }
  } catch {
    // fall through: a non-JSON body is not an error the kernel can act on
  }
  return { json: {}, rows: [] }
}

export const httpGatewayClient = (
  apiKey: string,
  baseUrl: string = DEFAULT_BASE_URL,
  fetchImpl: GatewayFetch = fetch as unknown as GatewayFetch,
): GatewayClient => ({
  send: (request) =>
    Effect.tryPromise({
      try: async () => {
        const init: {
          method: string
          headers: Record<string, string>
          body?: string
        } = {
          method: request.method,
          headers: {
            "content-type": "application/json",
            authorization: `Bearer ${apiKey}`,
          },
        }
        if (request.body !== undefined) init.body = JSON.stringify(request.body)
        const response = await fetchImpl(urlOf(baseUrl.replace(/\/$/, ""), request), init)
        const text = await response.text()
        if (!response.ok) {
          throw new Error(`${response.status}: ${text.slice(0, 200)}`)
        }
        const { json, rows } = partition(text)
        return { status: response.status, json, rows }
      },
      catch: (cause) =>
        new AdapterError({
          reason: cause instanceof Error ? cause.message : String(cause),
          commandTag: "gateway",
        }),
    }),
})

/** Test double: records requests, returns queued responses. No network. */
export const fakeGatewayClient = (): GatewayClient & {
  readonly requests: GatewayRequest[]
  readonly queue: (response: Partial<GatewayResponse>) => void
} => {
  const requests: GatewayRequest[] = []
  const queued: GatewayResponse[] = []
  return {
    requests,
    queue: (response) =>
      void queued.push({ status: 200, json: {}, rows: [], ...response }),
    send: (request) =>
      Effect.sync(() => {
        requests.push(request)
        return queued.shift() ?? { status: 200, json: {}, rows: [] }
      }),
  }
}
