/**
 * Socket inbound — adapters unwrap frames; the interpreter holds the connection.
 */
import type { Connection } from "../core/connection.ts"
import { ProvisionError } from "../core/errors.ts"
import type { Sent } from "../core/ports.ts"
import { CHANNELS, socketChannels, type ChannelName } from "../catalog.ts"
import { configString } from "./util.ts"
import { DiscordSocket } from "./discord/socket.ts"
import { SlackSocket } from "./slack/socket.ts"

export type SocketDecision = {
  readonly sink?: unknown
  readonly send?: ReadonlyArray<string>
  readonly reconnect?: boolean
  readonly fatal?: string
  readonly heartbeatInterval?: number
}

export type SocketUrl = {
  readonly url?: string
  readonly fatal?: string
}

export type SocketDriver = {
  readonly openPlan: () => Sent
  readonly urlOf: (sent: Sent) => SocketUrl
  readonly onFrame: (frame: { readonly [key: string]: unknown }) => SocketDecision
  readonly heartbeatPayload: () => string | undefined
}

export const socketDriver = (
  conn: Connection,
): SocketDriver | ProvisionError => {
  const row = CHANNELS[conn.channel as ChannelName]
  const kind = row?.socket
  if (kind === "discord") {
    const token = configString(conn.config as { readonly [key: string]: unknown }, "botToken")
    if (token === "") {
      return new ProvisionError({ reason: "discord self-host needs a bot_token" })
    }
    return new DiscordSocket(token)
  }
  if (kind === "slack") {
    const appToken = configString(conn.config as { readonly [key: string]: unknown }, "appToken")
    if (appToken === "") {
      return new ProvisionError({
        reason:
          "slack socket mode needs an app_token (xapp-, scope connections:write) " +
          "alongside the bot_token; without a public URL there is no webhook to fall back to",
      })
    }
    return new SlackSocket(appToken)
  }
  const names = socketChannels().join(", ")
  return new ProvisionError({
    reason: `listen() supports ${names}, not ${JSON.stringify(conn.channel)}; use run() for hosted or handle() for webhook self-host`,
  })
}
