/**
 * Slack Socket Mode protocol — envelope ack, disconnect-as-reconnect. No I/O.
 */
import { sentFromCall } from "../port.ts"
import { isRecord } from "../util.ts"
import type { SocketDecision, SocketDriver, SocketUrl } from "../socket.ts"
import type { Sent } from "../../core/ports.ts"

const API_BASE = "https://slack.com/api"

export class SlackSocket implements SocketDriver {
  constructor(
    readonly appToken: string,
    readonly options: { readonly apiBase?: string } = {},
  ) {}

  openPlan(): Sent {
    const base = this.options.apiBase ?? API_BASE
    return sentFromCall({
      transport: "http_json",
      method: "POST",
      url: `${base}/apps.connections.open`,
      headers: { Authorization: `Bearer ${this.appToken}` },
      native: "apps.connections.open",
    })
  }

  urlOf(sent: Sent): SocketUrl {
    const data = isRecord(sent.raw.response) ? sent.raw.response : {}
    if (data.ok !== true) {
      return { fatal: String(data.error ?? "apps.connections.open failed") }
    }
    return { url: typeof data.url === "string" ? data.url : "" }
  }

  onFrame(frame: { readonly [key: string]: unknown }): SocketDecision {
    const kind = frame.type
    if (kind === "hello") {
      return {}
    }
    if (kind === "disconnect") {
      return { reconnect: true }
    }
    const envelopeId = frame.envelope_id
    const send =
      typeof envelopeId === "string" ? [JSON.stringify({ envelope_id: envelopeId })] : []
    if (kind !== "events_api") {
      return { send }
    }
    return { send, sink: frame.payload ?? {} }
  }

  heartbeatPayload(): string | undefined {
    return undefined
  }
}
