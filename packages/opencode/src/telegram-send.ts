/**
 * Outbound Telegram send (OpenCode → human) via Caspian.
 *
 * Bot API cannot cold-DM users who have never started the bot (Telegram policy).
 * We prefer an existing conversation / SEND; fall back to initiate (user accounts).
 */

import type { EmailConnection } from "./identity.js";
import { CircuitBreaker } from "./reliability/circuit.js";
import { Metrics } from "./reliability/metrics.js";
import {
  appendSessionFooter,
  extractConversationId,
} from "./session-footer.js";

/** Side note shown on every send attempt / skill. */
export const TELEGRAM_BOT_DM_NOTE =
  "Note (Telegram): bots cannot start a private chat. The recipient must " +
  "message your bot first (open the bot and tap Start / send any message) " +
  "before DMs will work. User-account Telegram connections can cold-start; " +
  "Bot API connections cannot.";

export interface TelegramIdentityConfig {
  connectionId?: string;
}

export interface SendTelegramInput {
  /** @username, username, or numeric chat id. */
  to: string;
  body: string;
  connectionId?: string;
  openCodeSessionId?: string;
  sessionFooter?: boolean;
}

export interface SendTelegramResult {
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

export interface TelegramSendDeps {
  identity: TelegramIdentityConfig;
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

/** Normalize to @username or numeric chat id string. */
export function normalizeTelegramRecipient(raw: string): string {
  const s = raw.trim();
  if (!s) throw new Error("Telegram recipient must not be empty");
  if (/^-?\d+$/.test(s)) return s;
  const user = s.replace(/^@+/, "").replace(/\s+/g, "");
  // Telegram usernames are typically 5–32; allow a bit wider for tests / aliases.
  if (!/^[A-Za-z0-9_]{3,64}$/.test(user)) {
    throw new Error(
      `Invalid Telegram recipient "${raw}". Use @username or a numeric chat id.`,
    );
  }
  return `@${user}`;
}

export function telegramRecipientsMatch(a: string, b: string): boolean {
  const norm = (x: string) => x.trim().replace(/^@+/, "").toLowerCase();
  return norm(a) === norm(b);
}

function resolveTelegramConnection(
  identity: TelegramIdentityConfig,
  connections: EmailConnection[],
  overrideId?: string,
): EmailConnection | null {
  const tg = connections.filter(
    (c) => c.channel === "telegram" && c.status === "active",
  );
  if (!tg.length) return null;
  const want = overrideId || identity.connectionId;
  if (want) {
    const hit = tg.find((c) => c.id === want);
    if (hit) return hit;
  }
  return tg[0] ?? null;
}

async function findConversationForRecipient(
  deps: TelegramSendDeps,
  connectionId: string,
  recipient: string,
): Promise<string | null> {
  const conversations = await deps.listConversations(connectionId);
  for (const conv of conversations) {
    const id = typeof conv.id === "string" ? conv.id : null;
    if (!id) continue;

    // Some APIs may surface peer on the conversation itself.
    for (const key of ["peer", "peer_address", "external_id", "title"]) {
      const v = conv[key];
      if (typeof v === "string" && telegramRecipientsMatch(v, recipient)) {
        return id;
      }
    }

    let messages: Record<string, unknown>[] = [];
    try {
      messages = await deps.listMessages(id);
    } catch {
      continue;
    }
    for (const m of messages) {
      const sender = m.sender as { address?: string } | undefined;
      const addr = sender?.address;
      if (typeof addr === "string" && telegramRecipientsMatch(addr, recipient)) {
        return id;
      }
      const recipients = m.recipients;
      if (Array.isArray(recipients)) {
        for (const r of recipients) {
          const a =
            typeof r === "string"
              ? r
              : typeof r === "object" && r && "address" in r
                ? String((r as { address?: unknown }).address ?? "")
                : "";
          if (a && telegramRecipientsMatch(a, recipient)) return id;
        }
      }
    }
  }
  return null;
}

export async function sendTelegram(
  deps: TelegramSendDeps,
  input: SendTelegramInput,
): Promise<SendTelegramResult> {
  const to = normalizeTelegramRecipient(input.to);
  const body = input.body.trim();
  if (!body) throw new Error("Telegram message body must not be empty");

  const connections = await deps.listConnections();
  const conn = resolveTelegramConnection(
    deps.identity,
    connections,
    input.connectionId,
  );
  if (!conn) {
    throw new Error(
      "No active Telegram connection. Run /caspian:connect-telegram first.",
    );
  }

  const stampFooter =
    input.sessionFooter !== false && Boolean(input.openCodeSessionId);
  const text =
    stampFooter && input.openCodeSessionId
      ? appendSessionFooter(body, input.openCodeSessionId)
      : body;

  const existing = await findConversationForRecipient(deps, conn.id, to);
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
        note: TELEGRAM_BOT_DM_NOTE,
      };
    } catch (err) {
      deps.metrics.incr("outbound.send_fail");
      throw err;
    }
  }

  // No prior chat — try initiate (works for telegram_user; bots return 422).
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
      note: TELEGRAM_BOT_DM_NOTE,
    };
  } catch (err) {
    deps.metrics.incr("outbound.send_fail");
    const msg = String(err);
    throw new Error(
      [
        `Could not message ${to}.`,
        "No existing Telegram conversation with that user was found, and cold-start failed.",
        TELEGRAM_BOT_DM_NOTE,
        `Detail: ${msg}`,
      ].join("\n"),
    );
  }
}
