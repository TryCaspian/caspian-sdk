/**
 * Slack Socket Mode runner — self-host Slack with no public URL.
 *
 * The TypeScript twin of `caspian.interpreters.slack_socket`. The webhook path
 * needs a public HTTPS route and a signing secret; Socket Mode needs neither.
 * POST apps.connections.open with an app-level token (xapp-) for a short-lived
 * wss URL, hold it, and Slack pushes events down it.
 *
 * Two rules the protocol enforces:
 *
 *   - Ack every envelope IMMEDIATELY, before running the handler. Slack
 *     redelivers anything unacked after about 3 seconds, and handlers here call
 *     an LLM, so acking afterwards means the same message is answered twice.
 *   - A "disconnect" frame is routine, not an error: Slack cycles sockets to
 *     rebalance, so it is treated as a normal reconnect.
 *
 * A bad app token is fatal and stops the loop; retrying cannot fix invalid_auth.
 */
import * as Effect from "effect/Effect"
import { type SocketOpener, delay, openWebSocket } from "./socket.ts"

export type SocketSink = (
  body: unknown,
  headers?: { readonly [key: string]: string },
) => Effect.Effect<ReadonlyArray<unknown>>

const SLACK_API = "https://slack.com/api"

/** Fatal: the app-level token is bad. Stop rather than reconnect-spin. */
export class SlackAuthError extends Error {
  readonly _tag = "SlackAuthError"
}

export type SlackSocketOptions = {
  readonly apiBase?: string
  /** Injected in tests so the protocol runs without a network. */
  readonly open?: SocketOpener
  readonly openUrl?: (appToken: string) => Promise<string>
  readonly log?: (message: string) => void
}

const asRecord = (value: unknown): { readonly [key: string]: unknown } =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as { readonly [key: string]: unknown })
    : {}

export class SlackSocketRunner {
  #eventsSeen = 0
  #stop = false

  constructor(
    readonly appToken: string,
    readonly sink: SocketSink,
    readonly options: SlackSocketOptions = {},
  ) {}

  stop(): void {
    this.#stop = true
  }

  #note(message: string): void {
    this.options.log?.(message)
  }

  async #wssUrl(): Promise<string> {
    if (this.options.openUrl !== undefined) {
      return await this.options.openUrl(this.appToken)
    }
    const base = this.options.apiBase ?? SLACK_API
    const response = await fetch(`${base}/apps.connections.open`, {
      method: "POST",
      headers: { Authorization: `Bearer ${this.appToken}` },
    })
    const body = asRecord(await response.json())
    if (body.ok !== true) {
      // invalid_auth / not_allowed_token_type: the token is wrong, and
      // retrying cannot fix it.
      throw new SlackAuthError(String(body.error ?? "apps.connections.open failed"))
    }
    return String(body.url)
  }

  async #runOnce(maxEvents: number | undefined, collected: unknown[]): Promise<void> {
    const url = await this.#wssUrl()
    const open = this.options.open ?? openWebSocket
    const socket = await open(url)
    try {
      while (!this.#stop) {
        let frame: { readonly [key: string]: unknown }
        try {
          frame = asRecord(JSON.parse(await socket.receive()))
        } catch (error) {
          if (error instanceof SyntaxError) {
            continue // a malformed frame must not kill the socket
          }
          throw error
        }
        const kind = frame.type
        if (kind === "hello") {
          this.#note("socket mode connected")
          continue
        }
        if (kind === "disconnect") {
          // Routine: Slack cycles sockets. Reconnect rather than fail.
          throw new Error("slack asked us to reconnect")
        }
        // Ack BEFORE the handler. Slack redelivers unacked envelopes after ~3s.
        const envelopeId = frame.envelope_id
        if (typeof envelopeId === "string" && envelopeId !== "") {
          await socket.send(JSON.stringify({ envelope_id: envelopeId }))
        }
        if (kind !== "events_api") {
          continue
        }
        this.#eventsSeen += 1
        const payload = asRecord(frame.payload)
        collected.push(...(await Effect.runPromise(this.sink(payload, {}))))
        if (maxEvents !== undefined && this.#eventsSeen >= maxEvents) {
          this.#stop = true
          return
        }
      }
    } finally {
      socket.close()
    }
  }

  /** Hold the socket open, reconnecting forever. Returns send results. */
  run(
    options: { readonly maxEvents?: number } = {},
  ): Effect.Effect<ReadonlyArray<unknown>> {
    return Effect.promise(async () => {
      this.#stop = false
      const collected: unknown[] = []
      let backoff = 1000
      while (!this.#stop) {
        try {
          await this.#runOnce(options.maxEvents, collected)
          backoff = 1000
        } catch (error) {
          if (error instanceof SlackAuthError) {
            this.#note(`fatal auth error, not retrying: ${error.message}`)
            return collected
          }
          if (this.#stop) {
            break
          }
          this.#note(
            `socket dropped (${String(error)}); reconnecting in ${backoff / 1000}s`,
          )
          await delay(backoff)
          backoff = Math.min(backoff * 2, 60000)
        }
      }
      return collected
    })
  }
}
