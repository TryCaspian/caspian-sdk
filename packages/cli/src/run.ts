/**
 * Interpret Intent. Hosted I/O goes through an injected GatewayClient.
 *
 * call desugars to the same hosted outbound mapping as the TypeScript SDK
 * (`toRequest`). Catalog lookup is by id; dispatch is by command_tag.
 */
import {
  toRequest,
  type Command,
  type GatewayClient,
  type GatewayRequest,
  type GatewayResponse,
} from "caspian"

type Json =
  | string
  | number
  | boolean
  | null
  | ReadonlyArray<Json>
  | { readonly [key: string]: Json }
import * as Effect from "effect/Effect"
import { getCatalog, loadCatalog, searchCatalog } from "./catalog.ts"
import { UsageError } from "./desugar.ts"
import type { Call, ChannelsAdd, Intent } from "./intent.ts"

const HOSTED_INSTALL = new Set(["slack", "discord", "x", "github"])

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

const channelsAdd = (
  intent: ChannelsAdd,
  client: GatewayClient,
): Effect.Effect<Json, UsageError> => {
  if (intent.via === "self-host") {
    if (intent.bot_token === "") {
      return Effect.fail(
        new UsageError({
          reason:
            `Self-host ${JSON.stringify(intent.channel)} requires --bot-token. ` +
            "Omit --via for hosted (Caspian owns the identity).",
        }),
      )
    }
    return Effect.succeed({
      channel: intent.channel,
      via: "self-host",
      webhook_url: intent.webhook_url,
      inbound: intent.inbound,
    })
  }
  const path = HOSTED_INSTALL.has(intent.channel)
    ? `/v1/connections/${intent.channel}/install`
    : `/v1/connections/${intent.channel}`
  const body: { readonly [key: string]: Json } = intent.display_name
    ? { wait: true, display_name: intent.display_name }
    : { wait: true }
  return send(client, { method: "POST", path, body })
}

const fileName = (path: string): string => {
  const at = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"))
  return at < 0 ? path : path.slice(at + 1)
}

const asCommand = (intent: Call): Effect.Effect<Command, UsageError> => {
  let entry
  try {
    entry = getCatalog(intent.id)
  } catch (error) {
    return Effect.fail(
      new UsageError({
        reason: error instanceof Error ? error.message : String(error),
      }),
    )
  }
  const threadId = intent.args["thread_id"] ?? ""
  const text = intent.args["text"] ?? ""
  if (entry.command_tag === "Post") {
    return Effect.succeed({
      tag: "Post",
      thread_id: threadId,
      text,
      actions: [],
      standalone: true,
    } as unknown as Command)
  }
  if (entry.command_tag === "SendMedia") {
    const file = intent.args["file"] ?? ""
    const kind = entry.method === "sendPhoto" ? "photo" : "file"
    return Effect.succeed({
      tag: "SendMedia",
      thread_id: threadId,
      attachment: {
        type: kind,
        url: file,
        file_id: "",
        filename: fileName(file),
        mime_type: "",
        size_bytes: 0,
        caption: text,
      },
      caption: text,
    } as unknown as Command)
  }
  return Effect.fail(
    new UsageError({
      reason: `${entry.command_tag} is not available in hosted mode`,
    }),
  )
}

const call = (
  intent: Call,
  client: GatewayClient,
): Effect.Effect<Json, UsageError> =>
  Effect.gen(function* () {
    const command = yield* asCommand(intent)
    const request = yield* toRequest(command).pipe(
      Effect.mapError((error) => new UsageError({ reason: error.reason })),
    )
    return yield* send(client, request)
  })

const asObject = (value: Json): { readonly [key: string]: Json } | undefined =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as { readonly [key: string]: Json })
    : undefined

const threadsLs = (
  channel: string,
  client: GatewayClient,
): Effect.Effect<Json, UsageError> =>
  Effect.gen(function* () {
    const rows = yield* send(client, { method: "GET", path: "/v1/conversations" })
    const list = Array.isArray(rows) ? rows : []
    if (channel === "") return list
    return list.filter((row) => {
      const record = asObject(row)
      if (record === undefined) return false
      return (
        String(record["channel"] ?? "") === channel ||
        String(record["id"] ?? "").startsWith(`${channel}:`)
      )
    })
  })

export const runIntent = (
  intent: Intent,
  client: GatewayClient,
): Effect.Effect<Json, UsageError> => {
  switch (intent._tag) {
    case "ChannelsAdd":
      return channelsAdd(intent, client)
    case "ChannelsLs":
      return send(client, { method: "GET", path: "/v1/connections" })
    case "CatalogList":
      return Effect.succeed(loadCatalog() as unknown as Json)
    case "CatalogSearch":
      return Effect.succeed(searchCatalog(intent.query) as unknown as Json)
    case "CatalogGet":
      try {
        return Effect.succeed(getCatalog(intent.id) as unknown as Json)
      } catch (error) {
        return Effect.fail(
          new UsageError({
            reason: error instanceof Error ? error.message : String(error),
          }),
        )
      }
    case "Call":
      return call(intent, client)
    case "ThreadsLs":
      return threadsLs(intent.channel, client)
    case "ThreadsTail":
      return send(client, { method: "GET", path: "/v1/events" })
    case "Login":
      return send(client, {
        method: "POST",
        path: "/v1/auth/device/start",
        body: {},
      })
    case "Init":
      return Effect.fail(
        new UsageError({
          reason: "init writes .env; use the caspian binary",
        }),
      )
  }
}
