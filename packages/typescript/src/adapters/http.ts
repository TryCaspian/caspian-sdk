import * as Effect from "effect/Effect"
import * as Layer from "effect/Layer"
import { AdapterError } from "../core/errors.ts"
import { AdapterPort, emptySent, type Sent } from "../core/ports.ts"
import type { HttpFormCall, HttpJsonCall, HttpMultipartCall, PlannedCall } from "./plan.ts"
import { portFields, sentFromCall, type AdapterPlan } from "./port.ts"

export type AdapterFetch = (
  input: string | URL | Request,
  init?: RequestInit,
) => Promise<Response>

const extractMessageId = (payload: unknown): string => {
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    return ""
  }
  const data = payload as { readonly [key: string]: unknown }
  const result = typeof data.result === "object" && data.result !== null ? data.result : data
  if (typeof result !== "object" || result === null || Array.isArray(result)) {
    return ""
  }
  const record = result as { readonly [key: string]: unknown }
  for (const key of ["message_id", "ts", "id"]) {
    if (record[key] !== undefined && record[key] !== null) {
      return String(record[key])
    }
  }
  return ""
}

const postHttp = (
  fetchImpl: AdapterFetch,
  call: HttpJsonCall | HttpFormCall | HttpMultipartCall,
): Effect.Effect<Sent, AdapterError> =>
  Effect.tryPromise({
    try: async () => {
      const init: RequestInit = {
        method: call.method,
        headers: { ...call.headers },
      }
      if (call.transport === "http_json") {
        init.headers = {
          "content-type": "application/json",
          ...call.headers,
        }
        if (call.json !== undefined) {
          init.body = JSON.stringify(call.json)
        }
      } else if (call.transport === "http_form") {
        init.headers = {
          "content-type": "application/x-www-form-urlencoded",
          ...call.headers,
        }
        init.body = new URLSearchParams(call.form).toString()
      } else {
        const form = new FormData()
        for (const [key, value] of Object.entries(call.form ?? {})) {
          form.set(key, value)
        }
        init.body = form
      }
      const response = await fetchImpl(call.url, init)
      if (!response.ok) {
        throw new Error(`${call.native} HTTP ${response.status}`)
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
        raw: sentFromCall(call).raw,
      }
    },
    catch: (cause) =>
      new AdapterError({
        reason: cause instanceof Error ? cause.message : String(cause),
        commandTag: call.native,
      }),
  })

export const dispatchCall = (
  fetchImpl: AdapterFetch,
  call: PlannedCall,
): Effect.Effect<Sent, AdapterError> => {
  if (
    call.transport === "http_json" ||
    call.transport === "http_form" ||
    call.transport === "http_multipart"
  ) {
    return postHttp(fetchImpl, call)
  }
  return Effect.succeed(sentFromCall(call))
}

export const httpLayer = (
  spec: AdapterPlan,
  fetchImpl: AdapterFetch = fetch,
): Layer.Layer<AdapterPort> =>
  Layer.succeed(AdapterPort, {
    ...portFields(spec),
    acknowledge: (event, conn) => {
      const call = spec.planAck(event, conn)
      if (call === undefined) {
        return Effect.succeed(emptySent())
      }
      return dispatchCall(fetchImpl, call)
    },
    execute: (command, conn) =>
      spec.planCommand(command, conn).pipe(
        Effect.flatMap((call) => {
          if (call === undefined) {
            return Effect.succeed(emptySent())
          }
          return dispatchCall(fetchImpl, call)
        }),
      ),
  })
