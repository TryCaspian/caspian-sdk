/**
 * Cross-channel session sync marker.
 *
 * Stamped on outbound email / replies so Gmail ↔ TUI ↔ other channels can
 * rejoin the same OpenCode session by id — even when the Caspian conversation
 * is new (TUI-first → email → reply).
 *
 * Format (stable, parseable):
 *   ---\ncaspian-opencode:session=<sessionId>\n
 */

export const SESSION_FOOTER_MARKER = "caspian-opencode:session=";

/** Matches footer even when Gmail quotes/prefixes lines. */
export const SESSION_FOOTER_RE =
  /caspian-opencode:session=([A-Za-z0-9_-]+)/;

export function extractSessionId(text: string | null | undefined): string | null {
  if (!text) return null;
  const m = text.match(SESSION_FOOTER_RE);
  return m?.[1] ?? null;
}

/** Remove our footer block(s) so the agent prompt stays clean. */
export function stripSessionFooter(text: string): string {
  return text
    .replace(/\n*-{3,}\s*\n?\s*caspian-opencode:session=[A-Za-z0-9_-]+\s*/gi, "\n")
    .replace(/\n*caspian-opencode:session=[A-Za-z0-9_-]+\s*/gi, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trimEnd();
}

/** Append (or refresh) the session footer. Idempotent. */
export function appendSessionFooter(body: string, sessionId: string): string {
  const id = sessionId.trim();
  if (!id) return body;
  const core = stripSessionFooter(body).trimEnd();
  return `${core}\n\n---\n${SESSION_FOOTER_MARKER}${id}\n`;
}

/** Pull conversation id from Caspian initiate/send payloads when present. */
export function extractConversationId(raw: unknown): string | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  for (const key of [
    "conversation_id",
    "conversationId",
    "provider_thread_id",
    "thread_id",
  ]) {
    const v = r[key];
    if (typeof v === "string" && v.length > 0) return v;
  }
  const data = r.data;
  if (data && typeof data === "object") {
    return extractConversationId(data);
  }
  return null;
}
