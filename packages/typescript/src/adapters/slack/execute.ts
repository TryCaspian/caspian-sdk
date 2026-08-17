import * as Effect from "effect/Effect"
import type { Command, PostAction } from "../../core/commands.ts"
import type { Connection } from "../../core/connection.ts"
import { AdapterError } from "../../core/errors.ts"
import type { HttpJsonCall } from "../plan.ts"
import { skippedCommand } from "../recording.ts"
import { buttonData, buttonText, configString, isRecord } from "../util.ts"
import { decodeThreadId } from "./ids.ts"

const API_BASE = "https://slack.com/api"

const tokenOf = (conn: Connection): string =>
  configString(conn.config, "botToken")

const req = (
  token: string,
  method: string,
  body: { readonly [key: string]: unknown },
): HttpJsonCall => ({
  transport: "http_json",
  method: "POST",
  url: `${API_BASE}/${method}`,
  json: body,
  headers: {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  },
  native: method,
})

const actionsBlock = (
  actions: ReadonlyArray<PostAction>,
): { readonly [key: string]: unknown } => ({
  type: "actions",
  elements: actions.map((action) => {
    const el: { [key: string]: unknown } = {
      type: "button",
      text: { type: "plain_text", text: buttonText(action) },
      action_id: buttonData(action) || buttonText(action),
    }
    if (buttonData(action).length > 0) {
      el.value = buttonData(action)
    }
    return el
  }),
})

const actionsBlocks = (
  text: string,
  actions: ReadonlyArray<PostAction>,
): ReadonlyArray<unknown> => {
  const blocks: unknown[] = []
  if (text.length > 0) {
    blocks.push({ type: "section", text: { type: "mrkdwn", text } })
  }
  blocks.push(actionsBlock(actions))
  return blocks
}

export const planAck = (): undefined => undefined

export const planCommand = (
  command: Command,
  conn: Connection,
): Effect.Effect<HttpJsonCall | undefined, AdapterError> => {
  if (skippedCommand(command)) {
    return Effect.succeed(undefined)
  }
  if (command.tag === "Typing") {
    return Effect.fail(
      new AdapterError({
        reason: "Slack Web API cannot send typing indicators for bots",
        commandTag: "Typing",
      }),
    )
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
      const { channel, threadTs } = decodeThreadId(command.thread_id)
      const body: { [key: string]: unknown } = { channel, text: command.text }
      if (threadTs.length > 0) {
        body.thread_ts = threadTs
      }
      if (command.actions.length > 0) {
        body.blocks = actionsBlocks(command.text, command.actions)
      }
      return Effect.succeed(req(token, "chat.postMessage", body))
    }
    case "Edit": {
      const { channel } = decodeThreadId(command.thread_id)
      const body: { [key: string]: unknown } = {
        channel,
        ts: command.message_id,
        text: command.text,
      }
      return Effect.succeed(req(token, "chat.update", body))
    }
    case "React": {
      const { channel } = decodeThreadId(command.thread_id)
      return Effect.succeed(
        req(token, "reactions.add", {
          channel,
          timestamp: command.message_id,
          name: command.emoji.replaceAll(":", ""),
        }),
      )
    }
    case "Call": {
      const args = isRecord(command.args) ? command.args : {}
      return Effect.succeed(req(token, command.method, args))
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
