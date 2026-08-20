/**
 * Discord Gateway runner — the WebSocket inbound path for self-host Discord.
 *
 * The TypeScript twin of `caspian.interpreters.discord_gateway`. Discord
 * delivers ordinary channel and DM messages only over a persistent socket; its
 * webhook events cover app lifecycle and Social SDK lobbies. So this is the one
 * inbound transport that holds a connection open.
 *
 * Speaks the minimum of the protocol: IDENTIFY with the message intents,
 * heartbeat on the server's interval, RESUME after a drop, reconnect with
 * backoff. Each MESSAGE_CREATE goes to a sink, which handleRaw fits, so
 * parsing, rule matching and sending are identical to every other channel.
 *
 * Only the frame's inner `d` object is forwarded, because that is the shape the
 * Discord adapter's parse() expects.
 */
import * as Effect from "effect/Effect"
import { type GatewaySocket, type SocketOpener, delay, openWebSocket } from "./socket.ts"

export type GatewaySink = (
  body: unknown,
  headers?: { readonly [key: string]: string },
) => Effect.Effect<ReadonlyArray<unknown>>

/**
 * GUILD_MESSAGES (1<<9) | GUILD_MESSAGE_REACTIONS (1<<10) | DIRECT_MESSAGES
 * (1<<12) | DIRECT_MESSAGE_REACTIONS (1<<13) | MESSAGE_CONTENT (1<<15).
 * MESSAGE_CONTENT is privileged: toggle it on in the dev portal, which is
 * self-serve below 10,000 users. DMs carry content without it.
 */
export const INTENTS =
  (1 << 9) | (1 << 10) | (1 << 12) | (1 << 13) | (1 << 15)

const OP_DISPATCH = 0
const OP_HEARTBEAT = 1
const OP_IDENTIFY = 2
const OP_RESUME = 6
const OP_RECONNECT = 7
const OP_INVALID_SESSION = 9

const FORWARDED = new Set([
  "MESSAGE_CREATE",
  "MESSAGE_REACTION_ADD",
  "MESSAGE_REACTION_REMOVE",
])

const API_BASE = "https://discord.com/api/v10"

export type DiscordGatewayOptions = {
  readonly apiBase?: string
  readonly intents?: number
  /** Injected in tests so the protocol runs without a network. */
  readonly open?: SocketOpener
  readonly gatewayUrl?: (token: string) => Promise<string>
  readonly log?: (message: string) => void
}

const asRecord = (value: unknown): { readonly [key: string]: unknown } =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as { readonly [key: string]: unknown })
    : {}

export class DiscordGatewayRunner {
  #sequence: number | null = null
  #sessionId: string | null = null
  #resumeUrl: string | null = null
  #eventsSeen = 0
  #stop = false

  constructor(
    readonly botToken: string,
    readonly sink: GatewaySink,
    readonly options: DiscordGatewayOptions = {},
  ) {}

  stop(): void {
    this.#stop = true
  }

  get sessionId(): string | null {
    return this.#sessionId
  }

  get resumeUrl(): string | null {
    return this.#resumeUrl
  }

  #note(message: string): void {
    this.options.log?.(message)
  }

  async #url(): Promise<string> {
    if (this.#resumeUrl !== null) {
      return this.#resumeUrl
    }
    if (this.options.gatewayUrl !== undefined) {
      return await this.options.gatewayUrl(this.botToken)
    }
    const base = this.options.apiBase ?? API_BASE
    const response = await fetch(`${base}/gateway/bot`, {
      headers: { Authorization: `Bot ${this.botToken}` },
    })
    if (!response.ok) {
      throw new Error(`gateway/bot returned ${response.status}`)
    }
    const body = asRecord(await response.json())
    return String(body.url)
  }

  #identify(): string {
    return JSON.stringify({
      op: OP_IDENTIFY,
      d: {
        token: this.botToken,
        intents: this.options.intents ?? INTENTS,
        properties: { os: "linux", browser: "caspian", device: "caspian" },
      },
    })
  }

  #resume(): string {
    return JSON.stringify({
      op: OP_RESUME,
      d: {
        token: this.botToken,
        session_id: this.#sessionId,
        seq: this.#sequence,
      },
    })
  }

  /** Fire-and-forget heartbeat; stops when the socket dies. */
  #startHeartbeat(socket: GatewaySocket, interval: number): () => void {
    let live = true
    const beat = async (): Promise<void> => {
      // Jittered first beat, per Discord's guidance.
      await delay(interval * Math.random())
      while (live && !this.#stop) {
        try {
          await socket.send(JSON.stringify({ op: OP_HEARTBEAT, d: this.#sequence }))
        } catch {
          return // socket gone; the read loop surfaces the error
        }
        await delay(interval)
      }
    }
    void beat()
    return () => {
      live = false
    }
  }

  async #dispatch(frame: { readonly [key: string]: unknown }): Promise<unknown[]> {
    const name = frame.t
    const data = asRecord(frame.d)
    if (name === "READY") {
      this.#sessionId = typeof data.session_id === "string" ? data.session_id : null
      this.#resumeUrl =
        typeof data.resume_gateway_url === "string" ? data.resume_gateway_url : null
      const user = asRecord(data.user)
      const guilds = Array.isArray(data.guilds) ? data.guilds.length : 0
      this.#note(
        `connected as ${String(user.username ?? "?")} (${String(user.id ?? "?")}), ${guilds} guild(s)`,
      )
      return []
    }
    if (typeof name !== "string" || !FORWARDED.has(name)) {
      return []
    }
    this.#eventsSeen += 1
    // The adapter parses the inner payload, not the gateway envelope.
    return [...(await Effect.runPromise(this.sink(data, {})))]
  }

  async #runOnce(maxEvents: number | undefined, collected: unknown[]): Promise<void> {
    let url = await this.#url()
    if (!url.includes("?")) {
      url = `${url}?v=10&encoding=json`
    }
    const open = this.options.open ?? openWebSocket
    const socket = await open(url)
    try {
      const hello = asRecord(JSON.parse(await socket.receive()))
      const interval = Number(asRecord(hello.d).heartbeat_interval ?? 45000)
      const cancelHeartbeat = this.#startHeartbeat(socket, interval)
      try {
        await socket.send(this.#sessionId !== null ? this.#resume() : this.#identify())
        while (!this.#stop) {
          const frame = asRecord(JSON.parse(await socket.receive()))
          if (frame.s !== null && typeof frame.s === "number") {
            this.#sequence = frame.s
          }
          const op = frame.op
          if (op === OP_DISPATCH) {
            collected.push(...(await this.#dispatch(frame)))
            if (maxEvents !== undefined && this.#eventsSeen >= maxEvents) {
              this.#stop = true
              return
            }
          } else if (op === OP_RECONNECT) {
            return // reconnect and RESUME
          } else if (op === OP_INVALID_SESSION) {
            this.#sessionId = null
            this.#resumeUrl = null
            return
          }
        }
      } finally {
        cancelHeartbeat()
      }
    } finally {
      socket.close()
    }
  }

  /** Hold the connection open, reconnecting forever. Returns send results. */
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
          if (this.#stop) {
            break
          }
          this.#note(
            `gateway dropped (${String(error)}); reconnecting in ${backoff / 1000}s`,
          )
          await delay(backoff)
          backoff = Math.min(backoff * 2, 60000)
        }
      }
      return collected
    })
  }
}
