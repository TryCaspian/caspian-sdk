/**
 * Caspian ↔ pipeline wiring. Owns listen loop + heartbeat (CP) + outbound send.
 */

import type { PluginConfig } from "./config.js";
import { createCaspianPort } from "./caspian-client.js";
import { toEnvelope } from "./email.js";
import type { EmailConnection } from "./identity.js";
import {
  formatInboxSnapshot,
  listInbox,
  type InboxListOptions,
  type InboxSnapshot,
} from "./inbox.js";
import { sendEmail, type SendEmailInput, type SendEmailResult } from "./outbound.js";
import { handleInbound, type PipelineDeps } from "./pipeline.js";
import type { CaspianPort, OpenCodePort } from "./ports.js";
import { CircuitBreaker } from "./reliability/circuit.js";
import { ListenHealth } from "./reliability/health.js";
import { CapacityLimiter } from "./reliability/limits.js";
import { Metrics } from "./reliability/metrics.js";
import { MemorySessionMap } from "./session-map.js";
import { nonCritical } from "./reliability/switches.js";
import { appendSessionFooter } from "./session-footer.js";
import { resolveInboundSubject } from "./subject.js";
import {
  ThreadContextStore,
  type EmailThreadContext,
} from "./thread-context.js";
import {
  configuredChannels,
  enableChannel,
} from "./plugin-config.js";
import {
  resolveDiscordBotToken,
  resolveTelegramBotToken,
  telegramTokenSetupMessage,
} from "./secrets.js";
import {
  sendDiscord,
  type SendDiscordInput,
  type SendDiscordResult,
} from "./discord-send.js";
import {
  sendTelegram,
  type SendTelegramInput,
  type SendTelegramResult,
} from "./telegram-send.js";

export interface BridgeOptions {
  config: PluginConfig;
  opencode: OpenCodePort;
  caspian?: CaspianPort;
  metrics?: Metrics;
  /** Non-CP alert once inbound is bound to an OpenCode session (before prompt). */
  onInboundNotify?: (info: {
    channel?: string;
    from?: string;
    subject?: string | null;
    text?: string;
    sessionId: string;
  }) => void | Promise<void>;
}

export interface ReplyEmailInput {
  body: string;
  /** OpenCode session that owns the inbound thread. */
  openCodeSessionId: string;
  /** Optional override of Caspian message id to reply to. */
  messageId?: string;
}

export interface ReplyEmailResult {
  ok: true;
  messageId: string;
  conversationId: string;
  to?: string;
  subject?: string;
  openCodeSessionId: string;
}

export class CaspianOpenCodeBridge {
  readonly metrics: Metrics;
  readonly health: ListenHealth;
  readonly threads = new ThreadContextStore();
  private readonly config: PluginConfig;
  private readonly caspian: CaspianPort;
  private readonly pipelineDeps: PipelineDeps;
  /**
   * CP circuit: inbound auto-reply + tool reply on thread.
   * Must NOT share failure budget with proactive send (NonCP↛CP).
   */
  private readonly caspianCpCircuit: CircuitBreaker;
  /** Non-CP circuit: proactive email/telegram/discord send / initiate. */
  private readonly caspianOutboundCircuit: CircuitBreaker;
  private readonly controller = new AbortController();
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  /** Last known inbox address (for logging / tool context). */
  inboxAddress: string | null = null;
  inboxConnectionId: string | null = null;

  constructor(opts: BridgeOptions) {
    this.config = opts.config;
    this.metrics = opts.metrics ?? new Metrics();
    this.health = new ListenHealth(opts.config.heartbeatStaleMs);

    this.caspian =
      opts.caspian ??
      createCaspianPort({
        apiKey: opts.config.apiKey,
        baseUrl: opts.config.baseUrl,
      });

    this.caspianCpCircuit = new CircuitBreaker({
      ...opts.config.circuit,
      onOpen: () => this.metrics.incr("circuit.open"),
      onClose: () => this.metrics.incr("circuit.close"),
    });
    this.caspianOutboundCircuit = new CircuitBreaker({
      ...opts.config.circuit,
      onOpen: () => this.metrics.incr("circuit.open"),
      onClose: () => this.metrics.incr("circuit.close"),
    });
    const opencodeCircuit = new CircuitBreaker({
      ...opts.config.circuit,
      onOpen: () => this.metrics.incr("circuit.open"),
      onClose: () => this.metrics.incr("circuit.close"),
    });

    this.pipelineDeps = {
      config: opts.config,
      opencode: opts.opencode,
      sessions: new MemorySessionMap(),
      limiter: new CapacityLimiter(opts.config.limits),
      caspianCircuit: this.caspianCpCircuit,
      opencodeCircuit,
      metrics: this.metrics,
      reply: (messageId, text) => this.caspian.reply(messageId, text),
      onDelivered: (info) => {
        // Thread bind is CP-adjacent (tools need it mid-turn) but must never throw.
        try {
          const subject = resolveInboundSubject(info.subject, info.text);
          this.threads.remember({
            openCodeSessionId: info.sessionId,
            messageId: info.messageId,
            conversationId: info.conversationId,
            channel: info.channel,
            from: info.from,
            subject,
            inboxAddress:
              info.channel === "email" ? info.inboxAddress : undefined,
          });
          this.bindSession(info.conversationId, info.sessionId);

          if (!opts.config.notify.enabled) return;
          void nonCritical("inbound_notify", () =>
            Promise.resolve(
              opts.onInboundNotify?.({
                channel: info.channel,
                from: info.from,
                subject,
                text: info.text,
                sessionId: info.sessionId,
              }),
            ),
          );
        } catch (err) {
          console.warn("[caspian-opencode] on_delivered bind failed", err);
        }
      },
    };
  }

  threadForSession(sessionId: string): EmailThreadContext | undefined {
    return this.threads.getBySession(sessionId);
  }

  async listConnections(): Promise<EmailConnection[]> {
    return this.caspian.listConnections();
  }

  /** Status for connect skills: Caspian connections + plugin channels admit list. */
  async connectionStatus(): Promise<{
    connections: EmailConnection[];
    pluginChannels: string[];
    restartHint: string;
  }> {
    const connections = await this.listConnections();
    return {
      connections,
      pluginChannels: configuredChannels(),
      restartHint:
        "If you just changed channels in caspian.json, restart OpenCode so admit picks it up.",
    };
  }

  /**
   * Connect email (or reuse existing) and enable email in caspian.json channels.
   */
  async connectEmailChannel(opts?: {
    displayName?: string;
  }): Promise<{
    alreadyConnected: boolean;
    address: string;
    connectionId: string;
    pluginChannels: string[];
    restartRequired: boolean;
  }> {
    const connections = await this.listConnections();
    const email = connections.filter(
      (c) => c.channel === "email" && c.status === "active",
    );
    let alreadyConnected = false;
    let address = "";
    let connectionId = "";

    if (email[0]?.address && email[0]?.id) {
      alreadyConnected = true;
      address = email[0].address;
      connectionId = email[0].id;
    } else {
      if (!this.caspian.connectEmail) {
        throw new Error("connectEmail is not available on this Caspian client");
      }
      const conn = await this.caspian.connectEmail({
        displayName: opts?.displayName ?? this.config.displayName,
      });
      address = conn.address;
      connectionId = conn.id ?? "";
      this.inboxAddress = address;
      this.inboxConnectionId = connectionId || null;
    }

    const before = configuredChannels();
    const enabled = enableChannel("email", {
      email: {
        ...(typeof connectionId === "string" && connectionId
          ? { connectionId }
          : {}),
        ...(address ? { address } : {}),
      },
    });

    return {
      alreadyConnected,
      address,
      connectionId,
      pluginChannels: enabled.channels,
      restartRequired: !before.includes("email"),
    };
  }

  /**
   * Connect Telegram bot from env secrets and enable telegram in caspian.json.
   */
  async connectTelegramChannel(opts?: {
    cwd?: string;
    botToken?: string;
    displayName?: string;
  }): Promise<
    | {
        ok: true;
        alreadyConnected: boolean;
        connectionId: string;
        address?: string;
        tokenSource?: string;
        pluginChannels: string[];
        restartRequired: boolean;
      }
    | { ok: false; missingToken: true; setup: string }
  > {
    const connections = await this.listConnections();
    const tg = connections.filter(
      (c) => c.channel === "telegram" && c.status === "active",
    );

    if (tg[0]?.id) {
      const before = configuredChannels();
      const enabled = enableChannel("telegram", {
        telegram: { connectionId: tg[0].id },
      });
      return {
        ok: true,
        alreadyConnected: true,
        connectionId: tg[0].id,
        address: tg[0].address,
        pluginChannels: enabled.channels,
        restartRequired: !before.includes("telegram"),
      };
    }

    const resolved = opts?.botToken
      ? {
          value: opts.botToken,
          source: "env" as const,
          hintPaths: [] as string[],
        }
      : resolveTelegramBotToken({ cwd: opts?.cwd });

    if (!resolved.value) {
      return {
        ok: false,
        missingToken: true,
        setup: telegramTokenSetupMessage(resolved.hintPaths),
      };
    }

    if (!this.caspian.connectTelegram) {
      throw new Error("connectTelegram is not available on this Caspian client");
    }

    const conn = await this.caspian.connectTelegram({
      botToken: resolved.value,
      displayName: opts?.displayName ?? this.config.displayName,
    });

    const before = configuredChannels();
    const enabled = enableChannel("telegram", {
      telegram: { connectionId: conn.id },
    });

    return {
      ok: true,
      alreadyConnected: false,
      connectionId: conn.id,
      address: conn.address,
      tokenSource: resolved.source,
      pluginChannels: enabled.channels,
      restartRequired: !before.includes("telegram"),
    };
  }

  /**
   * Connect Discord (non-CP): reuse active, BYO bot token, or one-click install.
   * Enables discord in caspian.json admit list — restart required for CP admit.
   */
  async connectDiscordChannel(opts?: {
    cwd?: string;
    botToken?: string;
    displayName?: string;
    /** Prefer installDiscord OAuth even if a bot token exists. */
    preferInstall?: boolean;
  }): Promise<
    | {
        ok: true;
        alreadyConnected: boolean;
        mode: "existing" | "bot_token" | "install";
        connectionId: string;
        address?: string;
        status: string;
        authorizeUrl?: string;
        tokenSource?: string;
        pluginChannels: string[];
        restartRequired: boolean;
      }
    | { ok: false; error: string }
  > {
    const connections = await this.listConnections();
    const discord = connections.filter((c) => c.channel === "discord");
    const active = discord.filter((c) => c.status === "active");

    if (active[0]?.id) {
      const before = configuredChannels();
      const enabled = enableChannel("discord", {
        discord: { connectionId: active[0].id },
      });
      return {
        ok: true,
        alreadyConnected: true,
        mode: "existing",
        connectionId: active[0].id,
        address: active[0].address,
        status: active[0].status,
        authorizeUrl: active[0].authorize_url,
        pluginChannels: enabled.channels,
        restartRequired: !before.includes("discord"),
      };
    }

    const pending = discord.find((c) => c.authorize_url || c.status === "pending_oauth");
    if (pending?.id && pending.authorize_url && !opts?.botToken && !opts?.preferInstall) {
      const before = configuredChannels();
      const enabled = enableChannel("discord", {
        discord: { connectionId: pending.id },
      });
      return {
        ok: true,
        alreadyConnected: true,
        mode: "install",
        connectionId: pending.id,
        address: pending.address,
        status: pending.status,
        authorizeUrl: pending.authorize_url,
        pluginChannels: enabled.channels,
        restartRequired: !before.includes("discord"),
      };
    }

    const resolved = opts?.botToken
      ? {
          value: opts.botToken,
          source: "env" as const,
          hintPaths: [] as string[],
        }
      : resolveDiscordBotToken({ cwd: opts?.cwd });

    const useInstall = opts?.preferInstall === true || !resolved.value;

    if (!useInstall && resolved.value) {
      if (!this.caspian.connectDiscord) {
        return { ok: false, error: "connectDiscord is not available on this Caspian client" };
      }
      const conn = await this.caspian.connectDiscord({
        botToken: resolved.value,
        displayName: opts?.displayName ?? this.config.displayName,
      });
      const before = configuredChannels();
      const enabled = enableChannel("discord", {
        discord: { connectionId: conn.id },
      });
      return {
        ok: true,
        alreadyConnected: false,
        mode: "bot_token",
        connectionId: conn.id,
        address: conn.address,
        status: conn.status ?? "active",
        authorizeUrl: conn.authorize_url,
        tokenSource: resolved.source,
        pluginChannels: enabled.channels,
        restartRequired: !before.includes("discord"),
      };
    }

    if (!this.caspian.installDiscord) {
      return {
        ok: false,
        error:
          "installDiscord is not available. Set DISCORD_BOT_TOKEN and retry, or upgrade caspian-sdk.",
      };
    }

    const conn = await this.caspian.installDiscord({
      displayName: opts?.displayName ?? this.config.displayName,
    });
    const before = configuredChannels();
    const enabled = enableChannel("discord", {
      discord: { connectionId: conn.id },
    });
    return {
      ok: true,
      alreadyConnected: false,
      mode: "install",
      connectionId: conn.id,
      address: conn.address,
      status: conn.status ?? "pending_oauth",
      authorizeUrl: conn.authorize_url,
      pluginChannels: enabled.channels,
      restartRequired: !before.includes("discord"),
    };
  }

  /** Bind Caspian conversation ↔ OpenCode session (cross-channel sync). */
  bindSession(conversationId: string, openCodeSessionId: string): void {
    this.pipelineDeps.sessions.set(conversationId, openCodeSessionId);
  }

  /** List conversations/messages on configured channels (email + others). */
  async listInbox(opts: InboxListOptions = {}): Promise<InboxSnapshot> {
    return listInbox(this.caspian, this.config, opts);
  }

  async formatInbox(opts: InboxListOptions = {}): Promise<string> {
    return formatInboxSnapshot(await this.listInbox(opts));
  }

  /** Proactive NEW email — used by caspian_send_email / /caspian:email. */
  async sendEmail(input: SendEmailInput): Promise<SendEmailResult> {
    return sendEmail(
      {
        identity: this.config.email,
        listConnections: () => this.caspian.listConnections(),
        initiate: (id, to, text) => this.caspian.initiate(id, to, text),
        circuit: this.caspianOutboundCircuit,
        metrics: this.metrics,
        bindSession: (conv, ses) => this.bindSession(conv, ses),
      },
      {
        ...input,
        sessionFooter:
          input.sessionFooter ?? this.config.threading.sessionFooter,
      },
    );
  }

  /** Proactive Telegram DM — used by caspian_send_telegram / /caspian:telegram. */
  async sendTelegram(input: SendTelegramInput): Promise<SendTelegramResult> {
    return sendTelegram(
      {
        identity: this.config.telegram,
        listConnections: () => this.caspian.listConnections(),
        listConversations: (id) => this.caspian.listConversations(id),
        listMessages: (id) => this.caspian.listMessages(id),
        sendMessage: (id, text) => this.caspian.sendMessage(id, text),
        initiate: (id, to, text) => this.caspian.initiate(id, to, text),
        circuit: this.caspianOutboundCircuit,
        metrics: this.metrics,
        bindSession: (conv, ses) => this.bindSession(conv, ses),
      },
      {
        ...input,
        sessionFooter:
          input.sessionFooter ?? this.config.threading.sessionFooter,
      },
    );
  }

  /** Proactive Discord post — used by caspian_send_discord / /caspian:discord. */
  async sendDiscord(input: SendDiscordInput): Promise<SendDiscordResult> {
    return sendDiscord(
      {
        identity: this.config.discord,
        listConnections: () => this.caspian.listConnections(),
        listConversations: (id) => this.caspian.listConversations(id),
        listMessages: (id) => this.caspian.listMessages(id),
        sendMessage: (id, text) => this.caspian.sendMessage(id, text),
        initiate: (id, to, text) => this.caspian.initiate(id, to, text),
        circuit: this.caspianOutboundCircuit,
        metrics: this.metrics,
        bindSession: (conv, ses) => this.bindSession(conv, ses),
      },
      {
        ...input,
        sessionFooter:
          input.sessionFooter ?? this.config.threading.sessionFooter,
      },
    );
  }

  /**
   * Reply on an existing Caspian email thread (same Gmail conversation).
   * Prefer this over sendEmail when the OpenCode session came from inbound mail.
   */
  async replyEmail(input: ReplyEmailInput): Promise<ReplyEmailResult> {
    const body = input.body.trim();
    if (!body) throw new Error("Reply body must not be empty");

    const thread = this.threads.getBySession(input.openCodeSessionId);
    const messageId = input.messageId ?? thread?.messageId;
    if (!messageId) {
      throw new Error(
        "No inbound email thread for this OpenCode session. " +
          "Ask the user whether to send a new email with caspian_send_email, " +
          "or wait for/open an email session first.",
      );
    }

    const text =
      this.config.threading.sessionFooter
        ? appendSessionFooter(body, input.openCodeSessionId)
        : body;

    await this.caspianCpCircuit.exec(() => this.caspian.reply(messageId, text));
    this.metrics.incr("outbound.reply_ok");

    if (thread) {
      this.threads.remember({
        ...thread,
        messageId, // keep latest reply target
      });
    }

    return {
      ok: true,
      messageId,
      conversationId: thread?.conversationId ?? "",
      to: thread?.from,
      subject: thread?.subject ?? undefined,
      openCodeSessionId: input.openCodeSessionId,
    };
  }

  /** Provision / resolve email inbox when switch enabled. */
  async ensureEmail(): Promise<string | null> {
    // Prefer configured identity if already connected.
    try {
      const connections = await this.caspian.listConnections();
      const email = connections.filter(
        (c) => c.channel === "email" && c.status === "active",
      );
      if (this.config.email.connectionId) {
        const hit = email.find((c) => c.id === this.config.email.connectionId);
        if (hit?.address) {
          this.inboxAddress = hit.address;
          this.inboxConnectionId = hit.id;
          return hit.address;
        }
      }
      if (this.config.email.address) {
        const want = this.config.email.address.toLowerCase();
        const hit = email.find((c) => (c.address ?? "").toLowerCase() === want);
        if (hit?.address) {
          this.inboxAddress = hit.address;
          this.inboxConnectionId = hit.id;
          return hit.address;
        }
      }
      if (email[0]?.address) {
        this.inboxAddress = email[0].address;
        this.inboxConnectionId = email[0].id;
        if (!this.config.switches.autoConnectEmail) return email[0].address;
        // Already have an inbox — don't mint another.
        return email[0].address;
      }
    } catch (err) {
      console.warn("[caspian-opencode] listConnections failed", err);
    }

    if (!this.config.switches.autoConnectEmail) return null;
    if (!this.caspian.connectEmail) return null;
    try {
      const inbox = await this.caspian.connectEmail({
        displayName: this.config.displayName,
      });
      this.inboxAddress = inbox.address;
      this.inboxConnectionId = inbox.id ?? null;
      return inbox.address;
    } catch (err) {
      console.warn("[caspian-opencode] autoConnectEmail failed (non-cp)", err);
      return null;
    }
  }

  async start(): Promise<void> {
    if (!this.config.switches.enabled) return;

    this.caspian.onMessage(async (message) => {
      this.health.beat();
      const channel = message.channel ?? "unknown";
      const envelope = toEnvelope({
        id: message.id,
        conversationId: message.conversationId,
        connectionId: message.connectionId,
        channel,
        text: message.text,
        subject: message.subject,
        sender: message.sender,
        // Email listen filters only — never stamp email inbox onto Telegram/etc.
        inboxAddress: channel === "email" ? this.inboxAddress : undefined,
      });
      const replyFn = (id: string, text: string) =>
        id === message.id && typeof message.reply === "function"
          ? message.reply(text)
          : this.caspian.reply(id, text);

      try {
        await handleInbound(
          { ...this.pipelineDeps, reply: replyFn },
          envelope,
        );
      } catch (err) {
        console.error("[caspian-opencode] inbound handler error", err);
      }
    });

    this.health.start();
    this.heartbeatTimer = setInterval(() => {
      const snap = this.health.snapshot();
      this.metrics.incr("listen.heartbeat");
      if (snap.stale) this.metrics.incr("listen.heartbeat_stale");
    }, Math.min(5_000, this.config.heartbeatStaleMs / 2));

    try {
      await this.caspian.listen({ signal: this.controller.signal });
    } finally {
      this.stop();
    }
  }

  stop(): void {
    this.controller.abort();
    this.health.stop();
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }
}
