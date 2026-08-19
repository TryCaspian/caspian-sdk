/**
 * Interpret Plan. The only module that talks to GatewayClient.
 *
 * planIntent is the denotation (pure). This is one interpreter. Chaos and
 * recording clients are the others — same Plan, different GatewayClient.
 */
import type { GatewayClient, GatewayRequest, GatewayResponse } from "caspian"
import * as Effect from "effect/Effect"
import { hostedNeeded, UsageError } from "./errors.ts"
import type { Intent } from "./intent.ts"
import {
  planIntent,
  type Json,
  type Plan,
} from "./plan.ts"

const payload = (response: GatewayResponse): Json => {
  if (response.rows.length > 0) return [...response.rows]
  if (Object.keys(response.json).length > 0) return response.json
  return []
}

const send = (
  client: GatewayClient,
  request: GatewayRequest,
): Effect.Effect<Json, UsageError> =>
  client.send(request).pipe(
    Effect.map(payload),
    Effect.mapError((error) => new UsageError({ reason: error.reason })),
  )

const asObject = (value: Json): { readonly [key: string]: Json } | undefined =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as { readonly [key: string]: Json })
    : undefined

const filterRows = (rows: Json, channel: string): Json => {
  if (channel === "") return rows
  const list = Array.isArray(rows) ? rows : []
  return list.filter((row) => {
    const record = asObject(row)
    if (record === undefined) return false
    return (
      String(record["channel"] ?? "") === channel ||
      String(record["id"] ?? "").startsWith(`${channel}:`)
    )
  })
}

export const runPlan = (
  plan: Plan,
  client?: GatewayClient,
): Effect.Effect<Json, UsageError> => {
  switch (plan._tag) {
    case "Gateway":
      if (client === undefined) {
        return Effect.fail(hostedNeeded())
      }
      return Effect.map(send(client, plan.request), (rows) =>
        filterRows(rows, plan.filterChannel),
      )
    case "Local":
      return Effect.succeed(plan.value)
    case "Init":
      return Effect.succeed({
        gateway: plan.gateway,
        name: plan.name,
        force: plan.force,
      })
  }
}

export const runIntent = (
  intent: Intent,
  client: GatewayClient,
): Effect.Effect<Json, UsageError> =>
  Effect.flatMap(planIntent(intent), (plan) => runPlan(plan, client))
