/**
 * Proactive email send (OpenCode → human) via Caspian initiate.
 */

import type { EmailConnection, EmailIdentityConfig } from "./identity.js";
import { resolveSendConnection } from "./identity.js";
import { CircuitBreaker } from "./reliability/circuit.js";
import { Metrics } from "./reliability/metrics.js";
import {
  appendSessionFooter,
  extractConversationId,
} from "./session-footer.js";
import { normalizeOutboundSubject } from "./subject.js";

export interface SendEmailInput {
  to: string;
  body: string;
  subject?: string;
  /** Override config identity for this send. */
  connectionId?: string;
  /**
   * OpenCode session id to stamp in the footer and bind for inbound replies.
   * Enables TUI → email → reply → same TUI session.
   */
  openCodeSessionId?: string;
  /** When true (default if session id set), append session footer. */
  sessionFooter?: boolean;
}

export interface SendEmailResult {
  ok: true;
  connectionId: string;
  fromAddress: string;
  to: string;
  openCodeSessionId?: string;
  caspianConversationId?: string;
  raw: unknown;
}

export interface OutboundDeps {
  identity: EmailIdentityConfig;
  listConnections: () => Promise<EmailConnection[]>;
  initiate: (
    connectionId: string,
    recipient: string,
    text: string,
  ) => Promise<unknown>;
  circuit: CircuitBreaker;
  metrics: Metrics;
  /** Bind Caspian conversation ↔ OpenCode session when known. */
  bindSession?: (conversationId: string, openCodeSessionId: string) => void;
}

function normalizeEmail(addr: string): string {
  return addr.trim();
}

function looksLikeEmail(addr: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(addr);
}

export interface FormatOutboundOptions {
  /** Prior thread topic when subject is blank / bare "Re:". */
  threadBase?: string | null;
}

/** Compose initiate body; subject prefixed for email channels. */
export function formatOutboundText(
  input: SendEmailInput,
  opts: FormatOutboundOptions = {},
): string {
  const body = input.body.trim();
  const subject = normalizeOutboundSubject(input.subject, {
    threadBase: opts.threadBase,
    asReply: false,
  });
  const withSubject = subject ? `Subject: ${subject}\n\n${body}` : body;
  const stampFooter =
    input.sessionFooter !== false && Boolean(input.openCodeSessionId);
  if (stampFooter && input.openCodeSessionId) {
    return appendSessionFooter(withSubject, input.openCodeSessionId);
  }
  return withSubject;
}

export async function sendEmail(
  deps: OutboundDeps,
  input: SendEmailInput,
): Promise<SendEmailResult> {
  const to = normalizeEmail(input.to);
  if (!looksLikeEmail(to)) {
    throw new Error(`Invalid recipient email: ${input.to}`);
  }
  const body = input.body.trim();
  if (!body) throw new Error("Email body must not be empty");

  const connections = await deps.listConnections();
  const identity: EmailIdentityConfig = input.connectionId
    ? {
        ...deps.identity,
        connectionId: input.connectionId,
      }
    : deps.identity;

  const conn = resolveSendConnection(identity, connections);
  if (!conn) {
    throw new Error(
      "No active email connection to send from. Run connectEmail or set email.connectionId.",
    );
  }

  const subject = normalizeOutboundSubject(input.subject);
  if (!subject) {
    throw new Error(
      "Email subject is required for a new email. " +
        "Do not use bare 'Re:' or leave it empty. " +
        "If you meant to answer an existing thread, use caspian_reply_email instead.",
    );
  }

  const text = formatOutboundText({ ...input, subject });
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
      fromAddress: conn.address ?? "",
      to,
      openCodeSessionId: input.openCodeSessionId,
      caspianConversationId,
      raw,
    };
  } catch (err) {
    deps.metrics.incr("outbound.send_fail");
    throw err;
  }
}
