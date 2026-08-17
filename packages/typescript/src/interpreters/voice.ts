/**
 * Voice responder — surfaces the TwiML a voice webhook must return.
 *
 * Does not send anything; lifts the TwiML string out of the adapter's
 * request-description so the webhook layer can return it.
 */
import * as Effect from "effect/Effect"
import type { JsonObject } from "../core/json.ts"
import type { Sent } from "../core/ports.ts"
import type { Transport } from "./transport.ts"

export class VoiceResponder implements Transport {
  dispatch(sent: Sent): Effect.Effect<Sent, never> {
    if (sent.raw.transport !== "twiml") {
      return Effect.succeed(sent)
    }
    const twiml = typeof sent.raw.twiml === "string" ? sent.raw.twiml : ""
    return Effect.succeed({
      ok: true as const,
      message_id: "",
      raw: { native: "twiml", twiml } as JsonObject,
    })
  }
}
