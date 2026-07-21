/**
 * Pure bridge between the Caspian gateway and an OpenClaw channel account.
 * Imports no OpenClaw SDK modules, so it is fully testable offline.
 */
import { CommClient, type Message } from "caspian-sdk";

export interface CaspianChannelConfig {
  apiKey?: string;
  baseUrl?: string;
  displayName?: string;
  /** Allowlist of Caspian sub-channels (slack, whatsapp, ...). Empty = all. */
  channels?: string[];
  /** Allowlist of sender addresses permitted to reach the agent. Empty = all. */
  allowFrom?: string[];
}

export interface InboundEnvelope {
  /** caspian:<sub-channel>:<conversation id> — one Caspian conversation = one session. */
  sessionKey: string;
  subChannel: string;
  conversationId: string;
  messageId: string;
  senderAddress: string;
  senderName?: string;
  text: string;
}

export function resolveConfig(cfg: CaspianChannelConfig): CaspianChannelConfig {
  return {
    ...cfg,
    apiKey: cfg.apiKey ?? process.env.COMM_API_KEY,
    baseUrl: cfg.baseUrl ?? process.env.COMM_BASE_URL,
  };
}

export function toEnvelope(m: {
  id: string;
  conversationId: string;
  channel?: string;
  text?: string | null;
  sender?: { address?: string; name?: string };
}): InboundEnvelope {
  const sub = m.channel ?? "unknown";
  return {
    sessionKey: `caspian:${sub}:${m.conversationId}`,
    subChannel: sub,
    conversationId: m.conversationId,
    messageId: m.id,
    senderAddress: m.sender?.address ?? "",
    senderName: m.sender?.name,
    text: m.text ?? "",
  };
}

export function admits(cfg: CaspianChannelConfig, envelope: InboundEnvelope): boolean {
  if (cfg.channels?.length && !cfg.channels.includes(envelope.subChannel)) return false;
  if (cfg.allowFrom?.length && !cfg.allowFrom.includes(envelope.senderAddress)) return false;
  return true;
}

export class CaspianBridge {
  private client: CommClient;
  private cfg: CaspianChannelConfig;
  private controller = new AbortController();

  constructor(cfg: CaspianChannelConfig, client?: CommClient) {
    this.cfg = resolveConfig(cfg);
    this.client =
      client ??
      new CommClient({ apiKey: this.cfg.apiKey, baseUrl: this.cfg.baseUrl });
  }

  /** Long-running receive loop; resolves when `signal` aborts. */
  async start(
    onInbound: (envelope: InboundEnvelope) => void | Promise<void>,
    signal?: AbortSignal,
  ): Promise<void> {
    this.client.onMessage(async (message: Message) => {
      const envelope = toEnvelope(message as never);
      if (!admits(this.cfg, envelope)) return;
      await onInbound(envelope);
    });
    signal?.addEventListener("abort", () => this.controller.abort(), { once: true });
    await this.client.listen({ signal: this.controller.signal });
  }

  /** Threaded reply to an inbound Caspian message id. */
  sendReply(messageId: string, text: string): Promise<Record<string, unknown>> {
    return this.client.reply(messageId, text);
  }

  /** Proactive send into an existing conversation (Capability.SEND channels). */
  sendToConversation(conversationId: string, text: string): Promise<Record<string, unknown>> {
    return this.client.sendMessage(conversationId, text);
  }

  stop(): void {
    this.controller.abort();
  }
}
