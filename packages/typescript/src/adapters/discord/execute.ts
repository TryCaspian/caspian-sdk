import * as Effect from "effect/Effect"
import type { Command, PostAction } from "../../core/commands.ts"
import type { Connection } from "../../core/connection.ts"
import { AdapterError } from "../../core/errors.ts"
import type { Event } from "../../core/events.ts"
import type { HttpJsonCall } from "../plan.ts"
import { skippedCommand } from "../recording.ts"
import { buttonData, buttonText, configString, isRecord } from "../util.ts"
import { decodeThreadId } from "./ids.ts"

const API_BASE = "https://discord.com/api/v10"
const CALLBACK_DEFERRED_UPDATE = 6
const BUTTON_STYLE: { readonly [key: string]: number } = {
  default: 2,
  primary: 1,
  danger: 4,
}

const tokenOf = (conn: Connection): string =>
  configString(conn.config, "botToken")

const messagesUrl = (threadId: string): string =>
  `${API_BASE}/channels/${decodeThreadId(threadId).channelId}/messages`

const componentsOf = (
  actions: ReadonlyArray<PostAction>,
): ReadonlyArray<unknown> => {
  const buttons = actions.map((action) => {
    const url = "url" in action && typeof action.url === "string" ? action.url : undefined
    if (url !== undefined) {
      return { type: 2, label: buttonText(action), style: 5, url }
    }
    const styleKey =
      "style" in action && typeof action.style === "string" ? action.style : "default"
    return {
      type: 2,
      label: buttonText(action),
      style: BUTTON_STYLE[styleKey] ?? 2,
      custom_id: buttonData(action),
    }
  })
  return [{ type: 1, components: buttons }]
}

const req = (
  token: string,
  method: string,
  url: string,
  body: { readonly [key: string]: unknown } | undefined,
  native: string,
): HttpJsonCall => {
  const call: HttpJsonCall = {
    transport: "http_json",
    method,
    url,
    headers: {
      Authorization: `Bot ${token}`,
      "Content-Type": "application/json",
    },
    native,
  }
  return body === undefined ? call : { ...call, json: body }
}

export const planAck = (event: Event): HttpJsonCall | undefined => {
  if (event.kind !== "action") {
    return undefined
  }
  const id = event.raw["id"]
  const token = event.raw["token"]
  if (typeof id !== "string" || typeof token !== "string" || id.length === 0) {
    return undefined
  }
  return {
    transport: "http_json",
    method: "POST",
    url: `${API_BASE}/interactions/${id}/${token}/callback`,
    json: { type: CALLBACK_DEFERRED_UPDATE },
    headers: { "Content-Type": "application/json" },
    native: "interactionCallback",
  }
}

export const planCommand = (
  command: Command,
  conn: Connection,
): Effect.Effect<HttpJsonCall | undefined, AdapterError> => {
  if (skippedCommand(command)) {
    return Effect.succeed(undefined)
  }
  const token = tokenOf(conn)
  if (token.length === 0) {
    return Effect.fail(
      new AdapterError({
        reason: "No botToken in connection config",
        commandTag: command.tag,
      }),
    )
  }
  switch (command.tag) {
    case "Post": {
      const body: { [key: string]: unknown } = { content: command.text }
      if (command.actions.length > 0) {
        body.components = componentsOf(command.actions)
      }
      return Effect.succeed(
        req(token, "POST", messagesUrl(command.thread_id), body, "post"),
      )
    }
    case "Edit": {
      const body: { [key: string]: unknown } = { content: command.text }
      return Effect.succeed(
        req(
          token,
          "PATCH",
          `${messagesUrl(command.thread_id)}/${command.message_id}`,
          body,
          "edit",
        ),
      )
    }
    case "React":
      return Effect.succeed(
        req(
          token,
          "PUT",
          `${messagesUrl(command.thread_id)}/${command.message_id}/reactions/${command.emoji}/@me`,
          undefined,
          "react",
        ),
      )
    case "Typing":
      return Effect.succeed(
        req(
          token,
          "POST",
          `${API_BASE}/channels/${decodeThreadId(command.thread_id).channelId}/typing`,
          {},
          "typing",
        ),
      )
    case "Call": {
      const args = command.args
      const method = typeof args.method === "string" ? args.method : "POST"
      const url = typeof args.url === "string" ? args.url : ""
      if (url.length === 0) {
        return Effect.fail(
          new AdapterError({
            reason: "Call requires args.url",
            commandTag: "Call",
          }),
        )
      }
      const json = isRecord(args.json) ? args.json : undefined
      return Effect.succeed(req(token, method, url, json, command.method))
    }
    default:
      return Effect.fail(
        new AdapterError({
          reason: `Unsupported command: ${command.tag}`,
          commandTag: command.tag,
        }),
      )
  }
}
