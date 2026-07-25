/**
 * Thin adapter: CommClient + listConnections (SDK has no list helper yet).
 */

import { CommClient } from "caspian-sdk";
import type { EmailConnection } from "./identity.js";
import type { CaspianPort, CaspianInbound } from "./ports.js";

export function createCaspianPort(opts: {
  apiKey?: string;
  baseUrl?: string;
}): CaspianPort {
  const client = new CommClient({
    apiKey: opts.apiKey,
    baseUrl: opts.baseUrl,
  });
  const baseUrl = (
    opts.baseUrl ||
    process.env.CASPIAN_BASE_URL ||
    process.env.COMM_BASE_URL ||
    "https://api.trycaspianai.com"
  ).replace(/\/+$/, "");
  const apiKey =
    opts.apiKey || process.env.CASPIAN_API_KEY || process.env.COMM_API_KEY || "";

  return {
    onMessage(handler) {
      client.onMessage(async (message) => {
        const inbound: CaspianInbound = {
          id: message.id,
          conversationId: message.conversationId,
          connectionId: message.connectionId,
          channel: message.channel,
          text: message.text,
          subject: message.subject,
          sender: message.sender as CaspianInbound["sender"],
          reply: (text) => message.reply(text),
          typing: () => message.typing(),
        };
        await handler(inbound);
      });
    },
    listen(opts) {
      return client.listen(opts);
    },
    reply(messageId, text) {
      return client.reply(messageId, text);
    },
    async connectEmail(connectOpts) {
      const conn = await client.connectEmail({
        displayName: connectOpts.displayName,
      });
      return { address: conn.address ?? "", id: conn.id };
    },
    async connectTelegram(connectOpts) {
      const conn = await client.connectTelegram({
        botToken: connectOpts.botToken,
        displayName: connectOpts.displayName,
      });
      return {
        id: conn.id,
        address: conn.address,
        status: conn.status,
      };
    },
    async connectDiscord(connectOpts) {
      const conn = await client.connectDiscord({
        botToken: connectOpts.botToken,
        webhookUrl: connectOpts.webhookUrl,
        username: connectOpts.username,
        avatarUrl: connectOpts.avatarUrl,
        displayName: connectOpts.displayName,
      });
      return {
        id: conn.id,
        address: conn.address,
        status: conn.status,
        authorize_url: conn.authorize_url,
      };
    },
    async installDiscord(connectOpts) {
      const conn = await client.installDiscord({
        displayName: connectOpts.displayName,
      });
      return {
        id: conn.id,
        address: conn.address,
        status: conn.status,
        authorize_url: conn.authorize_url,
      };
    },
    async listConnections() {
      const res = await fetch(`${baseUrl}/v1/connections`, {
        headers: { Authorization: `Bearer ${apiKey}` },
        signal: AbortSignal.timeout(30_000),
      });
      if (!res.ok) {
        throw new Error(`list connections failed: ${res.status}`);
      }
      const data = (await res.json()) as EmailConnection[];
      if (!Array.isArray(data)) return [];
      return data.map((c) => {
        const extra = c as EmailConnection & { authorizeUrl?: unknown };
        const camel =
          typeof extra.authorizeUrl === "string" ? extra.authorizeUrl : undefined;
        return {
          ...c,
          authorize_url: c.authorize_url ?? camel,
        };
      });
    },
    async listConversations(connectionId) {
      const rows = await client.listConversations(connectionId);
      return (Array.isArray(rows) ? rows : []) as Record<string, unknown>[];
    },
    async listMessages(conversationId) {
      const rows = await client.listMessages(conversationId);
      return (Array.isArray(rows) ? rows : []) as Record<string, unknown>[];
    },
    sendMessage(conversationId, text) {
      return client.sendMessage(conversationId, text);
    },
    initiate(connectionId, recipient, text) {
      return client.initiate(connectionId, recipient, text);
    },
  };
}
