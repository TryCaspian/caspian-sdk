/**
 * Hosted mode as an AdapterPort, so it reuses the ONE inbound pipeline.
 *
 * Mirrors the Python `caspian.hosted.adapter`. In hosted mode the "platform" is
 * Caspian's gateway: this composes the event parser, the outbound mapping and
 * the gateway client behind the same shape a channel adapter has, so the
 * interpreter runs unchanged (verify -> parse -> step -> handlers -> execute).
 */
import * as Effect from "effect/Effect"
import * as Layer from "effect/Layer"
import type { Command } from "../core/commands.ts"
import { AdapterError } from "../core/errors.ts"
import type { Event } from "../core/events.ts"
import type { Json } from "../core/json.ts"
import { AdapterPort, emptySent } from "../core/ports.ts"
import type { Sent } from "../core/ports.ts"
import type { GatewayClient } from "./client.ts"
import { parseBatch } from "./inbound.ts"
import { toRequest } from "./outbound.ts"

/**
 * Remembers the newest inbound message id per thread.
 *
 * Typing and threaded replies are both keyed off a message on the gateway, but
 * the Typing and Post commands only carry a thread id, so the link has to be
 * kept here rather than in the pure kernel.
 */
const lastInbound = (): {
  readonly remember: (events: ReadonlyArray<Event>) => void
  readonly get: (threadId: string) => string
} => {
  const seen = new Map<string, string>()
  return {
    remember: (events) => {
      for (const event of events) {
        const id = (event as { message_id?: string }).message_id ?? ""
        if (id !== "") seen.set(String(event.thread_id), id)
      }
    },
    get: (threadId) => seen.get(threadId) ?? "",
  }
}

export const gatewayAdapterLayer = (
  client: GatewayClient,
): Layer.Layer<AdapterPort> => {
  const trigger = lastInbound()

  const execute = (command: Command): Effect.Effect<Sent, AdapterError> =>
    Effect.gen(function* () {
      const replyTo = "thread_id" in command
        ? trigger.get(String(command.thread_id))
        : ""
      const request = yield* toRequest(command, { replyTo })
      const response = yield* client.send(request)
      const id = response.json["id"]
      return {
        ok: true as const,
        message_id: typeof id === "string" ? id : "",
        raw: response.json,
      }
    })

  return Layer.succeed(AdapterPort, {
    name: "gateway",
    parse: (raw) =>
      Effect.sync(() => {
        const events = parseBatch(raw as Json)
        trigger.remember(events)
        return events
      }),
    overlapKey: (event) => String(event.thread_id),
    verify: () => true,
    acknowledge: () => Effect.succeed(emptySent()),
    execute: (command) =>
      // A missing typing indicator must never fail the reply that follows it.
      command.tag === "Typing"
        ? Effect.orElseSucceed(execute(command), () => emptySent())
        : execute(command),
    capabilities: () => [
      "receive",
      "reply",
      "send",
      "media",
      "blocks",
      "buttons",
      "edit",
      "react",
      "typing",
      "history",
      "threading",
    ],
    format: (text) => text,
  })
}
