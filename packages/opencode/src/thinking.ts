/**
 * Whether model reasoning ("thinking") is included in Caspian channel replies.
 * OpenCode may still show thinking in the TUI; this only controls outbound text.
 */

export interface ThinkingConfig {
  /** Global default. Default false — strip reasoning from channel replies. */
  enabled: boolean;
  /**
   * Per-channel override. When set, wins over `enabled`.
   * Example: `{ "telegram": false, "email": true }`
   */
  channels: Record<string, boolean>;
}

export const DEFAULT_THINKING: ThinkingConfig = {
  enabled: false,
  channels: {},
};

/** Resolve effective thinking-in-reply for a Caspian channel. */
export function thinkingEnabledForChannel(
  cfg: ThinkingConfig,
  channel: string,
): boolean {
  const key = (channel || "").toLowerCase();
  if (key && Object.prototype.hasOwnProperty.call(cfg.channels, key)) {
    return Boolean(cfg.channels[key]);
  }
  return Boolean(cfg.enabled);
}

export type AssistantPart = { type?: string; text?: string };

/** Collect parts from common OpenCode prompt result shapes. */
export function collectAssistantParts(result: unknown): AssistantPart[] {
  const r = result as {
    data?: { parts?: AssistantPart[]; info?: { parts?: AssistantPart[] } };
    parts?: AssistantPart[];
    info?: { parts?: AssistantPart[] };
  };
  const parts =
    r?.parts ??
    r?.data?.parts ??
    r?.info?.parts ??
    r?.data?.info?.parts ??
    [];
  return Array.isArray(parts) ? parts : [];
}

/**
 * Extract the text to send back on a Caspian channel.
 * By default only `type: "text"` parts — never `reasoning` / thinking.
 */
export function extractAssistantText(
  result: unknown,
  opts?: { includeThinking?: boolean },
): string {
  const includeThinking = opts?.includeThinking === true;
  const parts = collectAssistantParts(result);

  const textParts = parts
    .filter((p) => p && (p.type === "text" || (!p.type && p.text)))
    .map((p) => (p.text ?? "").trim())
    .filter(Boolean);

  if (!includeThinking) {
    return textParts.join("\n").trim();
  }

  const reasoning = parts
    .filter(
      (p) =>
        p &&
        (p.type === "reasoning" ||
          p.type === "thinking" ||
          p.type === "reasoning_content"),
    )
    .map((p) => (p.text ?? "").trim())
    .filter(Boolean);

  if (!reasoning.length) return textParts.join("\n").trim();

  return ["(thinking)", ...reasoning, "", ...textParts].join("\n").trim();
}
