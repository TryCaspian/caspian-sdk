/**
 * Critical path: inbound → capacity → session → prompt → reply.
 * Keep this file simple. Heavy logic belongs outside CP.
 */

import type { PluginConfig } from "./config.js";
import { claimMessage } from "./dedupe.js";
import {
  admits,
  formatInboundPrompt,
  type InboundEnvelope,
} from "./email.js";
import { CircuitBreaker } from "./reliability/circuit.js";
import { CapacityLimiter } from "./reliability/limits.js";
import { Metrics } from "./reliability/metrics.js";
import { nonCritical } from "./reliability/switches.js";
import type { OpenCodePort } from "./ports.js";
import {
  appendSessionFooter,
  extractSessionId,
} from "./session-footer.js";
import type { SessionStore } from "./session-map.js";
import { sessionMapKey, sessionTitle } from "./threading.js";
import {
  extractAssistantText as defaultExtractAssistantText,
  thinkingEnabledForChannel,
} from "./thinking.js";

export interface PipelineDeps {
  config: PluginConfig;
  opencode: OpenCodePort;
  sessions: SessionStore;
  limiter: CapacityLimiter;
  caspianCircuit: CircuitBreaker;
  opencodeCircuit: CircuitBreaker;
  metrics: Metrics;
  /** Outbound reply via Caspian (message id scoped). */
  reply: (messageId: string, text: string) => Promise<unknown>;
  /** Optional non-CP typing. */
  typing?: (messageId: string) => Promise<unknown>;
  /**
   * Non-CP: bind thread + toast/OS notify once the OpenCode session is known.
   * Called BEFORE session.prompt so tools/toasts work during the agent turn.
   */
  onDelivered?: (info: {
    messageId: string;
    sessionId: string;
    conversationId: string;
    channel: string;
    from?: string;
    subject?: string | null;
    text?: string;
    inboxAddress?: string;
  }) => void;
  /** Override exactly-once claim (tests). */
  claimMessage?: (messageId: string) => { duplicate: boolean };
  extractAssistantText?: (
    result: unknown,
    opts?: { includeThinking?: boolean },
  ) => string;
  sleep?: (ms: number) => Promise<void>;
}

async function defaultSleep(ms: number): Promise<void> {
  await new Promise((r) => setTimeout(r, ms));
}

async function resolveSessionId(
  deps: PipelineDeps,
  envelope: InboundEnvelope,
): Promise<string> {
  const key = sessionMapKey(deps.config.threading, envelope);

  // Cross-channel sync: footer from a prior TUI/email send wins.
  if (deps.config.threading.sessionFooter) {
    const fromFooter = extractSessionId(envelope.text);
    if (fromFooter) {
      deps.sessions.set(key, fromFooter);
      // Bind conversation so later mails without a quoted footer still sync.
      if (deps.config.threading.enabled && envelope.conversationId) {
        deps.sessions.set(envelope.conversationId, fromFooter);
      }
      return fromFooter;
    }
  }

  const existing = deps.sessions.get(key);
  if (existing) return existing;

  // Fallback: conversation was bound earlier (outbound send / prior footer).
  if (deps.config.threading.enabled && envelope.conversationId) {
    const bound = deps.sessions.get(envelope.conversationId);
    if (bound) {
      deps.sessions.set(key, bound);
      return bound;
    }
  }

  const title = sessionTitle(deps.config.threading, envelope);
  const created = await deps.opencodeCircuit.exec(() =>
    deps.opencode.session.create({
      body: { title },
    }),
  );

  const id =
    (created as { data?: { id?: string }; id?: string }).data?.id ??
    (created as { id?: string }).id;
  if (!id) throw new Error("opencode session.create returned no id");
  deps.sessions.set(key, id);
  if (deps.config.threading.enabled && envelope.conversationId) {
    deps.sessions.set(envelope.conversationId, id);
  }
  return id;
}

async function replyWithRetry(
  deps: PipelineDeps,
  messageId: string,
  text: string,
): Promise<void> {
  const sleep = deps.sleep ?? defaultSleep;
  let lastErr: unknown;
  for (let attempt = 0; attempt <= deps.config.replyRetries; attempt++) {
    try {
      await deps.caspianCircuit.exec(() => deps.reply(messageId, text));
      deps.metrics.incr("outbound.reply_ok");
      return;
    } catch (err) {
      lastErr = err;
      if (attempt < deps.config.replyRetries) {
        await sleep(100 * 2 ** attempt);
      }
    }
  }
  deps.metrics.incr("outbound.reply_fail");
  throw lastErr;
}

/**
 * CP entry — one inbound message.
 * Fail-fast on capacity; degrade with a short reply when configured.
 */
export async function handleInbound(
  deps: PipelineDeps,
  envelope: InboundEnvelope,
): Promise<void> {
  const { config, metrics } = deps;
  metrics.incr("inbound.received");

  if (!config.switches.enabled) return;

  if (!admits(config, envelope)) {
    metrics.incr("inbound.rejected_admit");
    return;
  }

  // Exactly-once after admit: skip if another plugin/OpenCode process already claimed this.
  const claim = deps.claimMessage ?? claimMessage;
  const { duplicate } = claim(envelope.messageId);
  if (duplicate) {
    metrics.incr("inbound.duplicate");
    return;
  }
  metrics.incr("inbound.admitted");

  const slot = deps.limiter.tryAcquire(envelope.conversationId);
  if (!slot.ok) {
    metrics.incr("inbound.rejected_capacity");
    if (config.switches.degradedCapacityReply) {
      await nonCritical("degraded_reply", async () => {
        await deps.reply(envelope.messageId, config.degradedReplyText);
        metrics.incr("outbound.degraded_reply");
      });
    }
    return;
  }

  const started = Date.now();
  try {
    if (config.switches.typing && deps.typing) {
      const typing = deps.typing;
      await nonCritical(
        "typing",
        async () => {
          await typing(envelope.messageId);
        },
        () => metrics.incr("noncp.error"),
      );
    }

    let answer: string;
    let sessionId: string;
    try {
      sessionId = await resolveSessionId(deps, envelope);

      // Bind thread + notify before prompt (tools need thread mid-turn).
      // Non-CP: must never fail the prompt / reply switch.
      await nonCritical("on_delivered", () => {
        deps.onDelivered?.({
          messageId: envelope.messageId,
          sessionId,
          conversationId: envelope.conversationId,
          channel: envelope.channel,
          from: envelope.senderAddress,
          subject: envelope.subject,
          text: envelope.text,
          inboxAddress: envelope.inboxAddress,
        });
      });

      const promptText = formatInboundPrompt(envelope);

      const result = await deps.opencodeCircuit.exec(() =>
        deps.opencode.session.prompt({
          path: { id: sessionId },
          body: { parts: [{ type: "text", text: promptText }] },
        }),
      );
      metrics.incr("inbound.prompt_ok");

      const includeThinking = thinkingEnabledForChannel(
        deps.config.thinking,
        envelope.channel,
      );
      const extract = deps.extractAssistantText ?? defaultExtractAssistantText;
      answer =
        extract(result, { includeThinking }) ||
        "(No response generated. Please try again.)";
    } catch (err) {
      metrics.incr("inbound.prompt_fail");
      // Best-effort user-visible failure (degraded), then rethrow for logs.
      await nonCritical("error_reply", async () => {
        await deps.reply(
          envelope.messageId,
          "The agent hit an error handling your message. Please try again shortly.",
        );
        metrics.incr("outbound.degraded_reply");
      });
      throw err;
    }

    // Keep session footer on replies so continued Gmail ↔ TUI sync works.
    const outbound =
      config.threading.sessionFooter
        ? appendSessionFooter(answer, sessionId)
        : answer;
    await replyWithRetry(deps, envelope.messageId, outbound);
  } finally {
    slot.release();
    metrics.observeMs(Date.now() - started);
  }
}
