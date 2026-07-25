/**
 * Ports for CP dependencies — inject fakes in tests (fault / capacity / chaos).
 */

import type { EmailConnection } from "./identity.js";
import type { InboundEnvelope } from "./email.js";

/** Minimal Caspian surface the bridge needs. */
export interface CaspianPort {
  onMessage(handler: (message: CaspianInbound) => void | Promise<void>): void;
  listen(opts?: { signal?: AbortSignal }): Promise<void>;
  reply(messageId: string, text: string): Promise<unknown>;
  connectEmail?(opts: { displayName: string }): Promise<{ address: string; id?: string }>;
  connectTelegram?(opts: {
    botToken: string;
    displayName?: string;
  }): Promise<{ id: string; address?: string; status?: string }>;
  connectDiscord?(opts: {
    botToken?: string;
    webhookUrl?: string;
    username?: string;
    avatarUrl?: string;
    displayName?: string;
  }): Promise<{
    id: string;
    address?: string;
    status?: string;
    authorize_url?: string;
  }>;
  installDiscord?(opts: {
    displayName?: string;
  }): Promise<{
    id: string;
    address?: string;
    status?: string;
    authorize_url?: string;
  }>;
  listConnections(): Promise<EmailConnection[]>;
  listConversations(connectionId?: string): Promise<Record<string, unknown>[]>;
  listMessages(conversationId: string): Promise<Record<string, unknown>[]>;
  sendMessage(conversationId: string, text: string): Promise<unknown>;
  initiate(
    connectionId: string,
    recipient: string,
    text: string,
  ): Promise<unknown>;
}

export interface CaspianInbound {
  id: string;
  conversationId: string;
  connectionId: string;
  channel: string;
  text: string | null;
  subject: string | null;
  sender: { address?: string; name?: string } | null;
  reply(text: string): Promise<unknown>;
  typing?(): Promise<unknown>;
}

/** Minimal OpenCode client surface used by the pipeline. */
export interface OpenCodePort {
  session: {
    create(args: {
      body: { title?: string };
    }): Promise<{ data?: { id: string }; id?: string } | { id: string }>;
    prompt(args: {
      path: { id: string };
      body: {
        parts: Array<{ type: "text"; text: string }>;
      };
    }): Promise<OpenCodePromptResult>;
  };
}

export interface OpenCodePromptResult {
  data?: {
    parts?: Array<{ type?: string; text?: string }>;
    info?: { parts?: Array<{ type?: string; text?: string }> };
  };
  parts?: Array<{ type?: string; text?: string }>;
}

export type InboundHandler = (envelope: InboundEnvelope) => void | Promise<void>;
