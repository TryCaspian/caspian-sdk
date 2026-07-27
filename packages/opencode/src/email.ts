import type { PluginConfig } from "./config.js";
import { admitsIdentity } from "./identity.js";
import { extractSessionId, stripSessionFooter } from "./session-footer.js";
import { resolveInboundSubject } from "./subject.js";

/** Normalized inbound for the CP pipeline (channel-agnostic shape). */
export interface InboundEnvelope {
  /** Stable OpenCode session key: caspian:<channel>:<conversationId> */
  sessionKey: string;
  channel: string;
  conversationId: string;
  messageId: string;
  connectionId?: string;
  /** Agent inbox address that received this mail (when known). */
  inboxAddress?: string;
  senderAddress: string;
  senderName?: string;
  subject: string | null;
  text: string;
}

export interface RawInbound {
  id: string;
  conversationId: string;
  connectionId?: string;
  channel?: string;
  text?: string | null;
  subject?: string | null;
  /** Agent-side inbox address, if the gateway/SDK exposes it. */
  inboxAddress?: string | null;
  sender?: { address?: string; name?: string } | null;
}

export function toEnvelope(m: RawInbound): InboundEnvelope {
  const channel = m.channel ?? "unknown";
  return {
    sessionKey: `caspian:${channel}:${m.conversationId}`,
    channel,
    conversationId: m.conversationId,
    messageId: m.id,
    connectionId: m.connectionId,
    inboxAddress: m.inboxAddress ?? undefined,
    senderAddress: m.sender?.address ?? "",
    senderName: m.sender?.name,
    subject: m.subject ?? null,
    text: m.text ?? "",
  };
}

/**
 * Admit boundary — channels allowlist + allowFrom + email-only identity filters.
 * Rejecting here is intentional blast-radius reduction, not an error.
 */
export function admits(cfg: PluginConfig, envelope: InboundEnvelope): boolean {
  if (cfg.channels.length && !cfg.channels.includes(envelope.channel)) {
    return false;
  }
  if (
    cfg.allowFrom.length &&
    !cfg.allowFrom.includes(envelope.senderAddress)
  ) {
    return false;
  }
  // Email identity filters apply to email only — never to telegram/slack/etc.
  if (envelope.channel === "email" && !admitsIdentity(cfg.email, envelope)) {
    return false;
  }
  return true;
}

/**
 * Build inbound prompt text for OpenCode.
 * Channel-aware: Telegram must not be framed as email (that triggers inbox /
 * caspian_reply_email / "send a new email?" confusion).
 *
 * For the listen→prompt→reply critical path, the plugin sends the assistant's
 * text back via Caspian automatically — tools are for later TUI actions only.
 */
export function formatInboundPrompt(envelope: InboundEnvelope): string {
  const channel = envelope.channel || "unknown";
  const from = envelope.senderAddress || "unknown";
  const body =
    stripSessionFooter(envelope.text).trim() || "(empty body)";
  const synced = extractSessionId(envelope.text);
  const autoReply =
    "IMPORTANT: Answer in your assistant text only. The plugin will deliver " +
    `your reply on this ${channel} conversation automatically. ` +
    "Do NOT call caspian_inbox, caspian_send_email, caspian_reply_email, " +
    "or caspian_connect_* for this message.";

  if (channel === "email") {
    const subject = resolveInboundSubject(envelope.subject, body);
    return [
      `[caspian:email] From: ${from}`,
      envelope.senderName ? `Name: ${envelope.senderName}` : null,
      envelope.inboxAddress ? `Inbox: ${envelope.inboxAddress}` : null,
      synced ? `Synced OpenCode session: ${synced}` : null,
      `Subject: ${subject}`,
      "",
      autoReply,
      "Later (in this session, after this turn): caspian_reply_email for another " +
        "on-thread reply; caspian_send_email only for a separate new email (ask first).",
      "",
      body,
    ]
      .filter((line): line is string => line !== null)
      .join("\n");
  }

  return [
    `[caspian:${channel}] From: ${from}`,
    envelope.senderName ? `Name: ${envelope.senderName}` : null,
    synced ? `Synced OpenCode session: ${synced}` : null,
    channel === "telegram"
      ? "Channel: Telegram (not email)"
      : channel === "discord"
        ? "Channel: Discord (not email)"
        : `Channel: ${channel}`,
    "",
    autoReply,
    "",
    body,
  ]
    .filter((line): line is string => line !== null)
    .join("\n");
}

/** @deprecated Prefer formatInboundPrompt — kept for existing imports/tests. */
export function formatEmailPrompt(envelope: InboundEnvelope): string {
  return formatInboundPrompt(envelope);
}
