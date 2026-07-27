/**
 * Outbound Discord send (OpenCode → channel) via Caspian.
 *
 * Reliability: non-CP relative to inbound listen. Uses circuit breaker +
 * existing-conversation preference (blast-radius: don't invent new threads
 * when a prior conversation exists). Recipient is a Discord channel snowflake
 * (adapter initiate = post to that channel id).
 */

import type { EmailConnection } from "./identity.js";
import { CircuitBreaker } from "./reliability/circuit.js";
import { Metrics } from "./reliability/metrics.js";
import {
  appendSessionFooter,
  extractConversationId,
} from "./session-footer.js";

export const DISCORD_CHANNEL_NOTE =
  "Note (Discord): `to` must be a Discord channel snowflake id the bot can " +
  "post in (right-click channel → Copy Channel ID; enable Developer Mode). " +
  "Invite the bot to the server first. For DMs, use the DM channel id after " +
  "the user has opened a DM with the bot.";

export interface DiscordIdentityConfig {
  connectionId?: string;
}

export interface SendDiscordInput {
  /** Discord channel snowflake id. */
  to: string;
  body: string;
  connectionId?: string;
  openCodeSessionId?: string;
  sessionFooter?: boolean;
}

export interface SendDiscordResult {
  ok: true;
  connectionId: string;
  botAddress?: string;
  to: string;
  mode: "conversation" | "initiate";
  conversationId?: string;
  openCodeSessionId?: string;
  raw: unknown;
  note: string;
}

export interface DiscordSendDeps {
  identity: DiscordIdentityConfig;
  listConnections: () => Promise<EmailConnection[]>;
  listConversations: (connectionId?: string) => Promise<Record<string, unknown>[]>;
  listMessages: (conversationId: string) => Promise<Record<string, unknown>[]>;
  sendMessage: (conversationId: string, text: string) => Promise<unknown>;
  initiate: (
    connectionId: string,
    recipient: string,
    text: string,
  ) => Promise<unknown>;
  circuit: CircuitBreaker;
  metrics: Metrics;
  bindSession?: (conversationId: string, openCodeSessionId: string) => void;
}

/** Discord channel / snowflake ids are numeric strings. */
export function normalizeDiscordRecipient(raw: string): string {
  const s = raw.trim().replace(/^<#/, "").replace(/>$/, "");
  if (!s) throw new Error("Discord channel id must not be empty");
  if (!/^\d{5,32}$/.test(s)) {
    throw new Error(
      `Invalid Discord channel id "${raw}". Use a numeric snowflake (Copy Channel ID).`,
    );
  }
  return s;
}

function resolveDiscordConnection(
  identity: DiscordIdentityConfig,
  connections: EmailConnection[],
  overrideId?: string,
): EmailConnection | null {
  const discord = connections.filter(
    (c) =>
      c.channel === "discord" &&
      (c.status === "active" || c.status === "pending_oauth"),
  );
  const active = discord.filter((c) => c.status === "active");
  const pool = active.length ? active : discord;
  if (!pool.length) return null;
  const want = overrideId || identity.connectionId;
  if (want) {
    const hit = pool.find((c) => c.id === want);
    if (hit) return hit;
  }
  return pool[0] ?? null;
}

async function findConversationForChannel(
  deps: DiscordSendDeps,
  connectionId: string,
  channelId: string,
): Promise<string | null> {
  const conversations = await deps.listConversations(connectionId);
  for (const conv of conversations) {
    const id = typeof conv.id === "string" ? conv.id : null;
    if (!id) continue;
    for (const key of [
      "peer",
      "peer_address",
      "external_id",
      "title",
      "provider_thread_id",
    ]) {
      const v = conv[key];
      if (typeof v === "string" && v.trim() === channelId) return id;
    }
    let messages: Record<string, unknown>[] = [];
    try {
      messages = await deps.listMessages(id);
    } catch {
      continue;
    }
    for (const m of messages) {
      // Inbound Discord often keys thread by channel; match recipients / chat ids.
      const recipients = m.recipients;
      if (Array.isArray(recipients)) {
        for (const r of recipients) {
          const a =
            typeof r === "string"
              ? r
              : typeof r === "object" && r && "address" in r
                ? String((r as { address?: unknown }).address ?? "")
                : "";
          if (a && a.trim() === channelId) return id;
        }
      }
      const chatType = m.chat_type ?? m.chatType;
      void chatType;
    }
  }
  return null;
}

export async function sendDiscord(
  deps: DiscordSendDeps,
  input: SendDiscordInput,
): Promise<SendDiscordResult> {
  const to = normalizeDiscordRecipient(input.to);
  const body = input.body.trim();
  if (!body) throw new Error("Discord message body must not be empty");

  const connections = await deps.listConnections();
  const conn = resolveDiscordConnection(
    deps.identity,
    connections,
    input.connectionId,
  );
  if (!conn) {
    throw new Error(
      "No Discord connection. Run /caspian:connect-discord first.",
    );
  }
  if (conn.status !== "active") {
    throw new Error(
      `Discord connection ${conn.id} is ${conn.status}. Finish OAuth (authorize_url) and wait until status is active.`,
    );
  }

  const stampFooter =
    input.sessionFooter !== false && Boolean(input.openCodeSessionId);
  const text =
    stampFooter && input.openCodeSessionId
      ? appendSessionFooter(body, input.openCodeSessionId)
      : body;

  const existing = await findConversationForChannel(deps, conn.id, to);
  if (existing) {
    try {
      const raw = await deps.circuit.exec(() =>
        deps.sendMessage(existing, text),
      );
      deps.metrics.incr("outbound.send_ok");
      if (input.openCodeSessionId && deps.bindSession) {
        deps.bindSession(existing, input.openCodeSessionId);
      }
      return {
        ok: true,
        connectionId: conn.id,
        botAddress: conn.address,
        to,
        mode: "conversation",
        conversationId: existing,
        openCodeSessionId: input.openCodeSessionId,
        raw,
        note: DISCORD_CHANNEL_NOTE,
      };
    } catch (err) {
      deps.metrics.incr("outbound.send_fail");
      throw err;
    }
  }

  try {
    const raw = await deps.circuit.exec(() =>
      deps.initiate(conn.id, to, text),
    );
    deps.metrics.incr("outbound.send_ok");
    const caspianConversationId = extractConversationId(raw) ?? undefined;
    if (
      caspianConversationId &&
      input.openCodeSessionId &&
      deps.bindSession
    ) {
      deps.bindSession(caspianConversationId, input.openCodeSessionId);
    }
    return {
      ok: true,
      connectionId: conn.id,
      botAddress: conn.address,
      to,
      mode: "initiate",
      conversationId: caspianConversationId,
      openCodeSessionId: input.openCodeSessionId,
      raw,
      note: DISCORD_CHANNEL_NOTE,
    };
  } catch (err) {
    deps.metrics.incr("outbound.send_fail");
    throw new Error(
      [
        `Could not post to Discord channel ${to}.`,
        DISCORD_CHANNEL_NOTE,
        `Detail: ${String(err)}`,
      ].join("\n"),
    );
  }
}
