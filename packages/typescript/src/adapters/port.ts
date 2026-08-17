import * as Effect from "effect/Effect"
import type { Command } from "../core/commands.ts"
import type { Connection } from "../core/connection.ts"
import { AdapterError } from "../core/errors.ts"
import type { Event } from "../core/events.ts"
import type { JsonObject } from "../core/json.ts"
import { emptySent, type Sent } from "../core/ports.ts"
import type { PlannedCall } from "./plan.ts"

export const plannedRaw = (call: PlannedCall): JsonObject =>
  JSON.parse(JSON.stringify(call)) as JsonObject

export const sentFromCall = (call: PlannedCall | undefined): Sent => {
  if (call === undefined) {
    return emptySent()
  }
  return { ok: true, message_id: "", raw: plannedRaw(call) }
}

export const inboundHeaders = (
  raw: unknown,
): { readonly [key: string]: string } => {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    return {}
  }
  const headers = (raw as { readonly headers?: unknown }).headers
  if (typeof headers !== "object" || headers === null || Array.isArray(headers)) {
    return {}
  }
  const out: { [key: string]: string } = {}
  for (const [key, value] of Object.entries(headers)) {
    if (typeof value === "string") {
      out[key] = value
    }
  }
  return out
}

export const inboundBody = (raw: unknown): unknown => {
  if (typeof raw === "object" && raw !== null && !Array.isArray(raw) && "body" in raw) {
    return (raw as { readonly body: unknown }).body
  }
  return raw
}

export type AdapterPlan = {
  readonly name: string
  readonly parse: (
    raw: unknown,
  ) => Effect.Effect<ReadonlyArray<Event>, import("../core/errors.ts").DecodeError>
  readonly overlapKey: (event: Event) => string
  readonly planAck: (event: Event, conn: Connection) => PlannedCall | undefined
  readonly planCommand: (
    command: Command,
    conn: Connection,
  ) => Effect.Effect<PlannedCall | undefined, AdapterError>
  readonly verify?: (raw: unknown, conn: Connection) => boolean
  readonly capabilities?: () => ReadonlyArray<string>
  readonly format?: (text: string) => string
  readonly poll?: (
    offset: number,
    conn: Connection,
  ) => Effect.Effect<Sent, AdapterError>
}

export const portFields = (spec: AdapterPlan) => ({
  name: spec.name,
  parse: spec.parse,
  overlapKey: spec.overlapKey,
  verify: spec.verify ?? (() => true),
  capabilities: spec.capabilities ?? ((): ReadonlyArray<string> => ["receive", "send"]),
  format: spec.format ?? ((text: string): string => text),
  ...(spec.poll === undefined ? {} : { poll: spec.poll }),
})
