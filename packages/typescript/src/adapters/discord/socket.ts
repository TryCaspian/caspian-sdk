/**
 * Discord Gateway protocol — IDENTIFY, heartbeat, RESUME. No I/O.
 */
import { sentFromCall } from "../port.ts"
import { isRecord } from "../util.ts"
import type { SocketDecision, SocketDriver, SocketUrl } from "../socket.ts"
import type { Sent } from "../../core/ports.ts"

const asRecord = (value: unknown): Record<string, unknown> =>
  isRecord(value) ? value : {}

/**
 * GUILD_MESSAGES | GUILD_MESSAGE_REACTIONS | DIRECT_MESSAGES |
 * DIRECT_MESSAGE_REACTIONS | MESSAGE_CONTENT.
 */
export const INTENTS =
  (1 << 9) | (1 << 10) | (1 << 12) | (1 << 13) | (1 << 15)

const OP_DISPATCH = 0
const OP_IDENTIFY = 2
const OP_RESUME = 6
const OP_RECONNECT = 7
const OP_INVALID_SESSION = 9
const OP_HELLO = 10

const FORWARDED = new Set([
  "MESSAGE_CREATE",
  "MESSAGE_REACTION_ADD",
  "MESSAGE_REACTION_REMOVE",
])

const API_BASE = "https://discord.com/api/v10"

export class DiscordSocket implements SocketDriver {
  #sequence: number | null = null
  #sessionId: string | null = null
  #resumeUrl: string | null = null

  constructor(
    readonly botToken: string,
    readonly options: { readonly apiBase?: string; readonly intents?: number } = {},
  ) {}

  openPlan(): Sent {
    if (this.#resumeUrl !== null) {
      return {
        ok: true,
        message_id: "",
        raw: { transport: "noop", native: "resume", url: this.#resumeUrl },
      }
    }
    const base = this.options.apiBase ?? API_BASE
    return sentFromCall({
      transport: "http_json",
      method: "GET",
      url: `${base}/gateway/bot`,
      headers: { Authorization: `Bot ${this.botToken}` },
      native: "gateway",
    })
  }

  urlOf(sent: Sent): SocketUrl {
    const url =
      this.#resumeUrl ??
      (typeof asRecord(sent.raw.response).url === "string"
        ? String(asRecord(sent.raw.response).url)
        : "")
    if (url === "") {
      return {}
    }
    return { url: url.includes("?") ? url : `${url}?v=10&encoding=json` }
  }

  onFrame(frame: { readonly [key: string]: unknown }): SocketDecision {
    if (typeof frame.s === "number") {
      this.#sequence = frame.s
    }
    const op = frame.op
    if (op === OP_HELLO) {
      const interval = Number(asRecord(frame.d).heartbeat_interval ?? 45000) / 1000
      return { heartbeatInterval: interval, send: [this.#greeting()] }
    }
    if (op === OP_RECONNECT) {
      return { reconnect: true }
    }
    if (op === OP_INVALID_SESSION) {
      this.#sessionId = null
      this.#resumeUrl = null
      return { reconnect: true }
    }
    if (op !== OP_DISPATCH) {
      return {}
    }
    const name = frame.t
    const data = asRecord(frame.d)
    if (name === "READY") {
      this.#sessionId = typeof data.session_id === "string" ? data.session_id : null
      this.#resumeUrl =
        typeof data.resume_gateway_url === "string" ? data.resume_gateway_url : null
      return {}
    }
    if (typeof name !== "string" || !FORWARDED.has(name)) {
      return {}
    }
    return { sink: data }
  }

  heartbeatPayload(): string | undefined {
    return JSON.stringify({ op: 1, d: this.#sequence })
  }

  #greeting(): string {
    if (this.#sessionId !== null) {
      return JSON.stringify({
        op: OP_RESUME,
        d: {
          token: this.botToken,
          session_id: this.#sessionId,
          seq: this.#sequence,
        },
      })
    }
    return JSON.stringify({
      op: OP_IDENTIFY,
      d: {
        token: this.botToken,
        intents: this.options.intents ?? INTENTS,
        properties: { os: "linux", browser: "caspian", device: "caspian" },
      },
    })
  }
}
