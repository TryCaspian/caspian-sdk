/**
 * List Caspian conversations/messages across configured channels.
 */

import type { PluginConfig } from "./config.js";
import type { EmailConnection } from "./identity.js";
import { listenFilters } from "./identity.js";

export interface InboxListOptions {
  /** Max conversations per connection. Default 10. */
  conversationLimit?: number;
  /** Max messages per conversation. Default 5. */
  messageLimit?: number;
  /** Optional channel filter override (e.g. ["email"]). */
  channels?: string[];
  /** Include message bodies. Default true. */
  includeMessages?: boolean;
}

export interface InboxMessageRow {
  id?: string;
  direction?: string;
  from?: string;
  subject?: string;
  text?: string;
  createdAt?: string;
}

export interface InboxConversationRow {
  id: string;
  channel: string;
  connectionId: string;
  connectionAddress?: string;
  peer?: string;
  subject?: string;
  updatedAt?: string;
  messages: InboxMessageRow[];
}

export interface InboxSnapshot {
  connections: Array<{
    id: string;
    channel: string;
    status: string;
    address?: string;
  }>;
  conversations: InboxConversationRow[];
  truncated: boolean;
}

export interface InboxListPort {
  listConnections(): Promise<EmailConnection[]>;
  listConversations(connectionId?: string): Promise<Record<string, unknown>[]>;
  listMessages(conversationId: string): Promise<Record<string, unknown>[]>;
}

function asString(v: unknown): string | undefined {
  return typeof v === "string" && v.length > 0 ? v : undefined;
}

function pickString(obj: Record<string, unknown>, keys: string[]): string | undefined {
  for (const k of keys) {
    const v = asString(obj[k]);
    if (v) return v;
  }
  return undefined;
}

function connectionMatchesConfig(
  conn: EmailConnection,
  config: PluginConfig,
  channelFilter: string[],
): boolean {
  const channel = (conn.channel || "").toLowerCase();
  if (channelFilter.length && !channelFilter.includes(channel)) return false;

  if (channel === "email") {
    const { connectionIds, addresses } = listenFilters(config.email);
    if (connectionIds.length && !connectionIds.includes(conn.id)) return false;
    if (addresses.length) {
      const addr = (conn.address ?? "").toLowerCase();
      if (!addr || !addresses.includes(addr)) return false;
    }
  }
  return true;
}

export function summarizeMessage(raw: Record<string, unknown>): InboxMessageRow {
  const sender =
    (raw.sender as Record<string, unknown> | null | undefined) ?? undefined;
  const from =
    pickString(raw, ["from", "from_address", "sender_address"]) ??
    (sender ? pickString(sender, ["address", "name", "email"]) : undefined);

  const text =
    pickString(raw, ["text", "body", "content"]) ??
    (typeof raw.html === "string" ? raw.html.replace(/<[^>]+>/g, " ").trim() : undefined);

  return {
    id: pickString(raw, ["id", "message_id"]),
    direction: pickString(raw, ["direction", "role"]),
    from,
    subject: pickString(raw, ["subject"]),
    text: text ? text.slice(0, 500) : undefined,
    createdAt: pickString(raw, [
      "created_at",
      "createdAt",
      "timestamp",
      "sent_at",
    ]),
  };
}

export function summarizeConversation(
  raw: Record<string, unknown>,
  conn: EmailConnection,
  messages: InboxMessageRow[],
): InboxConversationRow {
  const peer =
    pickString(raw, [
      "peer",
      "peer_address",
      "counterparty",
      "external_id",
      "title",
    ]) ??
    (() => {
      const customer = raw.customer as Record<string, unknown> | undefined;
      return customer
        ? pickString(customer, ["name", "email", "address"])
        : undefined;
    })();

  return {
    id: pickString(raw, ["id"]) ?? "unknown",
    channel: (conn.channel || pickString(raw, ["channel"]) || "unknown").toLowerCase(),
    connectionId: conn.id,
    connectionAddress: conn.address,
    peer,
    subject: pickString(raw, ["subject", "title"]),
    updatedAt: pickString(raw, [
      "updated_at",
      "updatedAt",
      "last_message_at",
      "created_at",
    ]),
    messages,
  };
}

/** Fetch inbox snapshot for configured channels/connections. */
export async function listInbox(
  port: InboxListPort,
  config: PluginConfig,
  opts: InboxListOptions = {},
): Promise<InboxSnapshot> {
  const conversationLimit = Math.max(1, Math.min(opts.conversationLimit ?? 10, 50));
  const messageLimit = Math.max(0, Math.min(opts.messageLimit ?? 5, 50));
  const includeMessages = opts.includeMessages !== false;
  const channelFilter = (opts.channels?.length
    ? opts.channels
    : config.channels
  ).map((c) => c.toLowerCase());

  const allConnections = await port.listConnections();
  const connections = allConnections.filter((c) =>
    connectionMatchesConfig(c, config, channelFilter),
  );

  const conversations: InboxConversationRow[] = [];
  let truncated = false;

  for (const conn of connections) {
    let convs: Record<string, unknown>[] = [];
    try {
      convs = await port.listConversations(conn.id);
    } catch {
      truncated = true;
      continue;
    }
    if (!Array.isArray(convs)) continue;
    if (convs.length > conversationLimit) truncated = true;

    for (const raw of convs.slice(0, conversationLimit)) {
      const id = pickString(raw, ["id"]);
      if (!id) continue;

      let messages: InboxMessageRow[] = [];
      if (includeMessages && messageLimit > 0) {
        try {
          const msgs = await port.listMessages(id);
          const list = Array.isArray(msgs) ? msgs : [];
          if (list.length > messageLimit) truncated = true;
          messages = list.slice(-messageLimit).map(summarizeMessage);
        } catch {
          truncated = true;
        }
      }

      conversations.push(summarizeConversation(raw, conn, messages));
    }
  }

  // Newest-ish first when timestamps exist.
  conversations.sort((a, b) => {
    const ta = a.updatedAt ?? "";
    const tb = b.updatedAt ?? "";
    return tb.localeCompare(ta);
  });

  return {
    connections: connections.map((c) => ({
      id: c.id,
      channel: c.channel,
      status: c.status,
      address: c.address,
    })),
    conversations,
    truncated,
  };
}

/** Human-readable inbox listing for tool / skill output. */
export function formatInboxSnapshot(snap: InboxSnapshot): string {
  const lines: string[] = [];
  lines.push("## Caspian inbox");
  lines.push("");

  if (snap.connections.length === 0) {
    lines.push("No configured channel connections.");
    return lines.join("\n");
  }

  lines.push("### Connections");
  for (const c of snap.connections) {
    lines.push(
      `- **${c.channel}** ${c.address ?? "(no address)"} · \`${c.id}\` · ${c.status}`,
    );
  }
  lines.push("");

  if (snap.conversations.length === 0) {
    lines.push("### Conversations");
    lines.push("(none yet)");
    return lines.join("\n");
  }

  lines.push(`### Conversations (${snap.conversations.length})`);
  for (const conv of snap.conversations) {
    const head = [
      `**${conv.channel}**`,
      conv.peer ?? conv.subject ?? conv.id,
      conv.connectionAddress ? `via ${conv.connectionAddress}` : null,
    ]
      .filter(Boolean)
      .join(" · ");
    lines.push(`- ${head}`);
    lines.push(`  - conversation: \`${conv.id}\``);
    if (conv.subject) lines.push(`  - subject: ${conv.subject}`);
    if (conv.updatedAt) lines.push(`  - updated: ${conv.updatedAt}`);
    if (conv.messages.length === 0) {
      lines.push("  - messages: (none loaded)");
      continue;
    }
    lines.push("  - recent messages:");
    for (const m of conv.messages) {
      const who = m.from ?? m.direction ?? "?";
      const sub = m.subject ? ` [${m.subject}]` : "";
      const body = (m.text ?? "").replace(/\s+/g, " ").trim();
      const preview = body.length > 160 ? `${body.slice(0, 157)}…` : body;
      lines.push(`    - ${who}${sub}: ${preview || "(empty)"}`);
    }
  }

  if (snap.truncated) {
    lines.push("");
    lines.push("_Listing truncated by limits or partial API errors._");
  }

  return lines.join("\n");
}
