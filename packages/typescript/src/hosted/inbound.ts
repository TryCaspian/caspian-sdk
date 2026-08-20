/**
 * Hosted inbound — gateway events into kernel Events.
 *
 * Mirrors the Python `caspian.hosted.inbound`. Three details were wrong in the
 * first cut and are pinned by tests here, because every one of them failed
 * silently against the live gateway:
 *
 *   1. /v1/events answers with a bare JSON array, not {"events": [...]}.
 *   2. It pages by after_seq + limit (limit capped at 500), not a cursor token.
 *   3. An event is {id, seq, type: "message.received", data: {message: {...}}},
 *      not a flat {channel, conversation_id, type: "message"}.
 */
import * as Effect from "effect/Effect"
import type { Event } from "../core/events.ts"
import type { ThreadId } from "../core/ids.ts"
import type { Json, JsonObject } from "../core/json.ts"
import type { AdapterError } from "../core/errors.ts"
import type { GatewayClient } from "./client.ts"

/** Gateway event type -> the kernel kind this parser produces. */
const KIND_OF: Readonly<Record<string, string>> = {
  "message.received": "message",
  "message.backfilled": "message",
  "interaction.received": "action",
  "reaction.received": "reaction",
}

const asObject = (value: Json | undefined): JsonObject =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : {}

const str = (value: unknown): string => (typeof value === "string" ? value : "")

/**
 * One gateway EventOut -> kernel Events.
 *
 * Returns nothing for our own outbound copy: reacting to `message.sent` is how
 * a bot ends up answering itself.
 */
export const parseEvent = (row: Json): ReadonlyArray<Event> => {
  const event = asObject(row)
  const kind = KIND_OF[str(event["type"])] ?? ""
  if (kind === "") return []

  const data = asObject(event["data"])
  const inner = asObject(
    (data["message"] ?? data["interaction"] ?? data["reaction"]) as Json,
  )
  if (str(inner["direction"]) === "outbound") return []

  const channel = str(inner["channel"])
  const conversation = str(inner["conversation_id"])
  const threadId = `${channel}:${conversation}` as ThreadId
  const sender = asObject(inner["sender"])
  const chatType = str(inner["chat_type"])

  if (kind === "message") {
    return [
      {
        kind: "message",
        thread_id: threadId,
        text: str(inner["text"]),
        chat_kind: chatType === "dm" || chatType === "group" ? chatType : "channel",
        sender: str(sender["address"]),
        message_id: str(inner["id"]),
        attachments: [],
        blocks: [],
        reply_to: "",
        topic_id: "",
        metadata: {},
        raw: inner,
      } as unknown as Event,
    ]
  }
  if (kind === "action") {
    return [
      {
        kind: "action",
        thread_id: threadId,
        data: str(inner["data"]),
        sender: str(sender["address"]),
        message_id: str(inner["id"]),
        raw: inner,
      } as unknown as Event,
    ]
  }
  return [
    {
      kind: "reaction",
      thread_id: threadId,
      emoji: str(inner["emoji"]),
      sender: str(sender["address"]),
      message_id: str(inner["id"]),
      raw: inner,
    } as unknown as Event,
  ]
}

/** A batch body ({"events": [...]}) or a single event -> kernel Events. */
export const parseBatch = (payload: Json): ReadonlyArray<Event> => {
  const body = asObject(payload)
  const rows = body["events"]
  if (Array.isArray(rows)) {
    return rows.flatMap((row) => parseEvent(row as Json))
  }
  return parseEvent(payload)
}

/** The endpoint declares limit <= 500; asking for more is a 422. */
const MAX_LIMIT = 500

export type Poller = {
  /** Highest seq consumed so far. */
  readonly cursor: () => number
  /** GET /v1/events and return the batch as a body the parser understands. */
  readonly fetchRaw: () => Effect.Effect<JsonObject, AdapterError>
  readonly poll: () => Effect.Effect<ReadonlyArray<Event>, AdapterError>
}

export type PollerOptions = {
  /** Start from seq 0 and re-read history. Off by default: a restart must not
   *  re-answer every message the project has ever received. */
  readonly replay?: boolean
  readonly cursor?: number
}

export const gatewayPoller = (
  client: GatewayClient,
  options: PollerOptions = {},
): Poller => {
  let cursor = options.cursor ?? 0
  let needSeek = options.cursor === undefined && options.replay !== true

  const advance = (rows: ReadonlyArray<Json>): void => {
    for (const row of rows) {
      const seq = asObject(row)["seq"]
      if (typeof seq === "number" && seq > cursor) cursor = seq
    }
  }

  /** Move past existing history without processing it, paging to the end. */
  const seek = (): Effect.Effect<void, AdapterError> =>
    Effect.gen(function* () {
      needSeek = false
      for (;;) {
        const response = yield* client.send({
          method: "GET",
          path: "/v1/events",
          params: { after_seq: String(cursor), limit: String(MAX_LIMIT) },
        })
        advance(response.rows)
        if (response.rows.length < MAX_LIMIT) return
      }
    })

  const fetchRaw = (): Effect.Effect<JsonObject, AdapterError> =>
    Effect.gen(function* () {
      if (needSeek) yield* seek()
      const response = yield* client.send({
        method: "GET",
        path: "/v1/events",
        params: { after_seq: String(cursor), limit: "100" },
      })
      // Tolerate an enveloped body too, so a test double or a future gateway
      // change that returns {"events": [...]} keeps working.
      const enveloped = response.json["events"]
      const rows = response.rows.length > 0
        ? response.rows
        : Array.isArray(enveloped)
          ? (enveloped as ReadonlyArray<Json>)
          : []
      advance(rows)
      return { events: rows } as unknown as JsonObject
    })

  return {
    cursor: () => cursor,
    fetchRaw,
    poll: () => Effect.map(fetchRaw(), (body) => parseBatch(body as unknown as Json)),
  }
}
