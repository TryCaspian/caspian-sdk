/**
 * Hosted outbound — kernel Commands into gateway requests.
 *
 * Mirrors the Python `caspian.hosted.outbound`. Every path here was checked
 * against the live openapi; commands the gateway has no endpoint for fail
 * loudly rather than POSTing into a 404.
 */
import * as Effect from "effect/Effect"
import type { Command } from "../core/commands.ts"
import { AdapterError } from "../core/errors.ts"
import type { ThreadId } from "../core/ids.ts"
import type { JsonObject } from "../core/json.ts"
import type { GatewayRequest } from "./client.ts"

/** Hosted thread ids are "channel:conversation". */
export const conversationOf = (threadId: ThreadId): string => {
  const raw = String(threadId)
  const at = raw.indexOf(":")
  return at < 0 ? raw : raw.slice(at + 1)
}

/** The gateway has no endpoint for these. Say so instead of 404ing. */
const unsupported = (tag: string): AdapterError =>
  new AdapterError({
    reason:
      `${tag} is not available in hosted mode: the gateway exposes no ` +
      `endpoint for it. Use self-host for this.`,
    commandTag: tag,
  })

export type OutboundOptions = {
  /**
   * Id of the message that triggered this turn, when there is one.
   *
   * A handler answering an inbound message means "reply to this". Only the
   * reply path sets In-Reply-To/References and the Re: subject, which on email
   * is the difference between a threaded conversation and a stray new message.
   */
  readonly replyTo?: string
}

export const toRequest = (
  command: Command,
  options: OutboundOptions = {},
): Effect.Effect<GatewayRequest, AdapterError> => {
  const cid = "thread_id" in command
    ? conversationOf(command.thread_id as ThreadId)
    : ""

  switch (command.tag) {
    case "Post": {
      const body = {
        text: command.text,
        ...(command.actions.length > 0
          ? { actions: command.actions as unknown }
          : {}),
      } as unknown as JsonObject
      // Thread the answer onto its trigger unless explicitly standalone.
      const replyTo = command.standalone === true ? "" : (options.replyTo ?? "")
      if (replyTo !== "") {
        return Effect.succeed({
          method: "POST",
          path: `/v1/messages/${replyTo}/reply`,
          body,
        })
      }
      return Effect.succeed({
        method: "POST",
        path: `/v1/conversations/${cid}/messages`,
        body,
      })
    }
    case "Reply":
      return Effect.succeed({
        method: "POST",
        path: `/v1/messages/${command.reply_to}/reply`,
        body: { text: command.text },
      })
    case "Edit":
      return Effect.succeed({
        method: "POST",
        path: `/v1/messages/${command.message_id}/edit`,
        body: { text: command.text },
      })
    case "React":
      return Effect.succeed({
        method: "POST",
        path: `/v1/messages/${command.message_id}/react`,
        body: { emoji: command.emoji },
      })
    case "Typing": {
      // The gateway hangs a typing hint off a MESSAGE, not a conversation.
      // With nothing to hang it on this is a no-op: a missing indicator must
      // never fail the reply that follows it (adapter handles this via orElseSucceed).
      const target = options.replyTo ?? ""
      if (target === "") return Effect.fail(unsupported("Typing"))
      return Effect.succeed({
        method: "POST",
        path: `/v1/messages/${target}/typing`,
        body: {},
      })
    }
    case "SendMedia":
      return Effect.succeed({
        method: "POST",
        path: `/v1/conversations/${cid}/messages`,
        body: { media: [command.attachment] } as unknown as JsonObject,
      })
    case "SendBlocks":
      return Effect.succeed({
        method: "POST",
        path: `/v1/conversations/${cid}/messages`,
        body: { blocks: command.blocks } as unknown as JsonObject,
      })
    case "ListHistory":
      return Effect.succeed({
        method: "POST",
        path: `/v1/conversations/${cid}/backfill`,
        body: { limit: command.limit },
      })
    case "Delete":
      return Effect.fail(unsupported("Delete"))
    case "Pin":
      return Effect.fail(unsupported("Pin"))
    case "Unpin":
      return Effect.fail(unsupported("Unpin"))
    case "Forward":
      return Effect.fail(unsupported("Forward"))
    case "MarkRead":
      return Effect.fail(unsupported("MarkRead"))
    case "OpenModal":
    case "UpdateModal":
      return Effect.fail(unsupported(command.tag))
    default:
      return Effect.fail(unsupported(command.tag))
  }
}
