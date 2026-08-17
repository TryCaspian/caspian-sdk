/**
 * Shared adapter helpers. Not core — adapters may use these.
 */
import { createHmac, timingSafeEqual } from "node:crypto"
import * as Schema from "effect/Schema"
import type { PostAction } from "../core/commands.ts"
import { DecodeError } from "../core/errors.ts"
import { ThreadId } from "../core/ids.ts"
import type { Attachment, Block } from "../core/events.ts"
import type { JsonObject } from "../core/json.ts"

export const messageDefaults = {
  message_id: "",
  attachments: [] as Attachment[],
  blocks: [] as Block[],
  reply_to: "",
  topic_id: "",
  metadata: {} as JsonObject,
}

export const actionDefaults = {
  message_id: "",
  interaction_id: "",
  metadata: {} as JsonObject,
}

export const reactionDefaults = {
  message_id: "",
  removed: false,
}

export const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

export const asJsonObject = (value: unknown): JsonObject => {
  try {
    const parsed: unknown = JSON.parse(JSON.stringify(value))
    if (isRecord(parsed)) {
      return parsed as JsonObject
    }
  } catch {
    return {}
  }
  return {}
}

export const configString = (
  config: { readonly [key: string]: unknown },
  key: string,
): string => {
  const value = config[key]
  return typeof value === "string" ? value : ""
}

export const encodePrefixed = (prefix: string, rest: string): ThreadId =>
  Schema.decodeUnknownSync(ThreadId)(`${prefix}${rest}`)

export const suffixAfter = (threadId: string, prefix: string): string => {
  const value = String(threadId)
  return value.startsWith(prefix) ? value.slice(prefix.length) : value
}

export const buttonText = (action: PostAction): string =>
  action.text ?? action.label ?? "ok"

export const buttonData = (action: PostAction): string =>
  action.data ?? action.value ?? action.text ?? ""

/** Object stays; JSON string is parsed; other values are `undefined`. */
export const jsonObjectOf = (
  raw: unknown,
): { readonly ok: true; readonly value: Record<string, unknown> | undefined } | {
  readonly ok: false
  readonly error: DecodeError
} => {
  if (isRecord(raw)) {
    return { ok: true, value: raw }
  }
  if (typeof raw === "string") {
    try {
      const parsed: unknown = JSON.parse(raw)
      if (isRecord(parsed)) {
        return { ok: true, value: parsed }
      }
      return { ok: true, value: undefined }
    } catch (cause) {
      return {
        ok: false,
        error: new DecodeError({
          reason: `Invalid JSON: ${cause instanceof Error ? cause.message : String(cause)}`,
        }),
      }
    }
  }
  return { ok: true, value: undefined }
}

export const hmacSha256Hex = (secret: string, body: string | Uint8Array): string =>
  createHmac("sha256", secret).update(body).digest("hex")

export const hmacSha256Base64 = (secret: string, body: string | Uint8Array): string =>
  createHmac("sha256", secret).update(body).digest("base64")

export const hmacSha1Base64 = (secret: string, body: string | Uint8Array): string =>
  createHmac("sha1", secret).update(body).digest("base64")

export const timingSafeEqualUtf8 = (left: string, right: string): boolean => {
  const a = Buffer.from(left)
  const b = Buffer.from(right)
  if (a.length !== b.length) {
    return false
  }
  return timingSafeEqual(a, b)
}

export const formFieldsOf = (
  raw: unknown,
): { readonly ok: true; readonly value: Record<string, string> } | {
  readonly ok: false
  readonly error: DecodeError
} => {
  if (typeof raw === "string") {
    try {
      const params = new URLSearchParams(raw)
      const value: Record<string, string> = {}
      for (const [key, item] of params.entries()) {
        if (value[key] === undefined) {
          value[key] = item
        }
      }
      return { ok: true, value }
    } catch (cause) {
      return {
        ok: false,
        error: new DecodeError({
          reason: `Invalid form body: ${cause instanceof Error ? cause.message : String(cause)}`,
        }),
      }
    }
  }
  const decoded = jsonObjectOf(raw)
  if (!decoded.ok) {
    return decoded
  }
  if (decoded.value === undefined) {
    return { ok: true, value: {} }
  }
  const value: Record<string, string> = {}
  for (const [key, item] of Object.entries(decoded.value)) {
    value[key] = typeof item === "string" ? item : String(item)
  }
  return { ok: true, value }
}

export const firstHeader = (
  headers: { readonly [key: string]: string } | undefined,
  name: string,
): string => {
  if (headers === undefined) {
    return ""
  }
  const direct = headers[name]
  if (typeof direct === "string") {
    return direct
  }
  const lower = name.toLowerCase()
  for (const [key, value] of Object.entries(headers)) {
    if (key.toLowerCase() === lower) {
      return value
    }
  }
  return ""
}
