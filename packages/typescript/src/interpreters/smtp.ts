/**
 * SMTP transport — dispatches the email adapter's "smtp" request-descriptions.
 *
 * Tests inject a recording sender so no network I/O occurs.
 */
import * as Effect from "effect/Effect"
import { AdapterError } from "../core/errors.ts"
import type { JsonObject } from "../core/json.ts"
import type { Sent } from "../core/ports.ts"
import type { Transport } from "./transport.ts"

export type SmtpMessage = {
  readonly from: string
  readonly to: string
  readonly subject: string
  readonly body: string
  readonly inReplyTo: string
  readonly references: string
}

export type SmtpSender = (message: SmtpMessage) => void | Promise<void>

const asRecord = (value: unknown): { readonly [key: string]: unknown } =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as { readonly [key: string]: unknown })
    : {}

export class SmtpTransport implements Transport {
  readonly #sender: SmtpSender | undefined

  constructor(options: { readonly sender?: SmtpSender } = {}) {
    this.#sender = options.sender
  }

  dispatch(sent: Sent): Effect.Effect<Sent, AdapterError> {
    if (sent.raw.transport !== "smtp") {
      return Effect.succeed(sent)
    }
    const email = asRecord(sent.raw.email)
    const message: SmtpMessage = {
      from: typeof email.from === "string" ? email.from : "",
      to: typeof email.to === "string" ? email.to : "",
      subject: typeof email.subject === "string" ? email.subject : "",
      body: typeof email.body === "string" ? email.body : "",
      inReplyTo: typeof email.in_reply_to === "string" ? email.in_reply_to : "",
      references: typeof email.references === "string" ? email.references : "",
    }
    if (this.#sender === undefined) {
      return Effect.succeed({
        ok: true as const,
        message_id: "",
        raw: { native: "sendmail" } as JsonObject,
      })
    }
    return Effect.tryPromise({
      try: async () => {
        await this.#sender?.(message)
        return {
          ok: true as const,
          message_id: "",
          raw: { native: "sendmail" } as JsonObject,
        }
      },
      catch: (cause) =>
        new AdapterError({
          reason: `SMTP send failed: ${cause instanceof Error ? cause.message : String(cause)}`,
          commandTag: "sendmail",
        }),
    })
  }
}
