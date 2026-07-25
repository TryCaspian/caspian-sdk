/**
 * Thread ↔ OpenCode session mapping.
 *
 * Default (enabled): each Caspian conversation is one message thread and maps
 * 1:1 to one OpenCode session. Follow-ups stay in that session.
 *
 * Disabled via config: all inbound traffic shares a single OpenCode session.
 */

import type { InboundEnvelope } from "./email.js";

export interface ThreadingConfig {
  /**
   * When true (default): one Caspian conversation/thread → one OpenCode session.
   * Set false only if you explicitly want a single shared session for all mail.
   */
  enabled: boolean;
  /**
   * Map key + title seed used when `enabled` is false.
   * Default: `caspian:shared`.
   */
  sharedSessionKey: string;
  /**
   * Stamp `caspian-opencode:session=<id>` footer on outbound mail/replies and
   * route inbound that contain it back to that OpenCode session (TUI ↔ Gmail sync).
   * Default true. Set false to disable.
   */
  sessionFooter: boolean;
}

export const DEFAULT_THREADING: ThreadingConfig = {
  enabled: true,
  sharedSessionKey: "caspian:shared",
  sessionFooter: true,
};

/**
 * Stable key used in the session map.
 * - threaded: Caspian conversationId (one email thread → one session)
 * - shared:   fixed sharedSessionKey
 */
export function sessionMapKey(
  threading: ThreadingConfig,
  envelope: InboundEnvelope,
): string {
  if (!threading.enabled) return threading.sharedSessionKey;
  return envelope.conversationId;
}

/** Human-readable OpenCode session title for the thread. */
export function sessionTitle(
  threading: ThreadingConfig,
  envelope: InboundEnvelope,
): string {
  if (!threading.enabled) {
    return threading.sharedSessionKey;
  }
  const from = envelope.senderAddress || "unknown";
  // Lazy import avoided — resolve subject without body inference here is ok;
  // pipeline titles can stay short. Prefer non-blank subjects only.
  const subject = envelope.subject?.trim();
  if (subject && !/^(?:\(no subject\)|re\s*:?\s*|fwd?\s*:?\s*)$/i.test(subject)) {
    const short =
      subject.length > 48 ? `${subject.slice(0, 45)}…` : subject;
    return `${envelope.channel}: ${short} · ${from}`;
  }
  return `${envelope.channel}: ${from}`;
}
