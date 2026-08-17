import * as Effect from "effect/Effect"
import * as Layer from "effect/Layer"
import { AdapterError } from "../core/errors.ts"
import { AdapterPort } from "../core/ports.ts"
import type { HttpFormCall, HttpJsonCall, PlannedCall } from "./plan.ts"
import type { AdapterPlan } from "./recording.ts"

export type AdapterFetch = (
  input: string | URL | Request,
  init?: RequestInit,
) => Promise<Response>

const postHttp = (
  fetchImpl: AdapterFetch,
  call: HttpJsonCall | HttpFormCall,
): Effect.Effect<{ readonly ok: true }, AdapterError> =>
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
      } else {
        init.headers = {
          "content-type": "application/x-www-form-urlencoded",
          ...call.headers,
        }
        init.body = new URLSearchParams(call.form).toString()
      }
      const response = await fetchImpl(call.url, init)
      if (!response.ok) {
        throw new Error(`${call.native} HTTP ${response.status}`)
      }
      return { ok: true as const }
    },
    catch: (cause) =>
      new AdapterError({
        reason: cause instanceof Error ? cause.message : String(cause),
        commandTag: call.native,
      }),
  })

const dispatch = (
  fetchImpl: AdapterFetch,
  call: PlannedCall,
): Effect.Effect<{ readonly ok: true }, AdapterError> => {
  if (call.transport === "http_json" || call.transport === "http_form") {
    return postHttp(fetchImpl, call)
  }
  return Effect.succeed({ ok: true as const })
}

export const httpLayer = (
  spec: AdapterPlan,
  fetchImpl: AdapterFetch = fetch,
): Layer.Layer<AdapterPort> =>
  Layer.succeed(AdapterPort, {
    name: spec.name,
    parse: spec.parse,
    overlapKey: spec.overlapKey,
    ack: (event, conn) => {
      const call = spec.planAck(event, conn)
      if (call === undefined) {
        return Effect.succeed({ ok: true as const })
      }
      return dispatch(fetchImpl, call)
    },
    execute: (command, conn) =>
      spec.planCommand(command, conn).pipe(
        Effect.flatMap((call) => {
          if (call === undefined) {
            return Effect.succeed({ ok: true as const })
          }
          return dispatch(fetchImpl, call)
        }),
      ),
  })
