import { config } from "./config.js";
import crypto from "node:crypto";
import { AccountRequiredError, CommError, InsufficientCreditError, WebhookVerificationError } from "./errors.js";
import { InMemoryStateAdapter, type StateAdapter } from "./state.js";
export { AccountRequiredError, CommError, InsufficientCreditError, WebhookVerificationError };
import type {
  Agent,
  AutopayOptions,
  Block,
  ClientOptions,
  Connection,
  ConcurrencyStrategy,
  ConnectOptions,
  Conversation,
  Customer,
  Domain,
  EventRecord,
  ListenOptions,
  LoginOptions,
  Media,
  SpendLimitsOptions,
  StreamOptions,
  StreamStrategy,
  WhatsappOnboarding,
} from "./types.js";

const logger = {
  warn: (...args: unknown[]) => console.warn("[caspian-sdk]", ...args),
  error: (...args: unknown[]) => console.error("[caspian-sdk]", ...args),
};

function isRecord(value: unknown): value is Record<string, any> {
  return typeof value === "object" && value !== null;
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal?.aborted) return resolve();
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

/** An inbound message delivered to an onMessage handler. */
export class Message {
  constructor(
    readonly id: string,
    readonly conversationId: string,
    readonly connectionId: string,
    readonly customerId: string,
    readonly agentId: string,
    readonly channel: string,
    readonly sender: Record<string, unknown> | null,
    readonly subject: string | null,
    readonly text: string | null,
    readonly html: string | null,
    private readonly client: CommClient,
    /** File attachments received with the message (empty when none). */
    readonly media: Media[] = [],
  ) {}

  /**
   * Reply on whichever channel this message arrived from (auto-threaded).
   *
   * Pass `blocks` — provider-neutral rich blocks — to render natively on
   * Slack/Discord/Telegram/email and degrade to clean text elsewhere. Pass
   * `media` to attach files (images/documents).
   */
  reply(
    text?: string | null,
    html?: string | null,
    blocks?: Block[] | null,
    media?: Media[] | null,
  ): Promise<Record<string, unknown>> {
    return this.client.reply(this.id, text, html, blocks, media);
  }

  /**
   * Add an emoji reaction (tapback) to this message. Best-effort; no-op on
   * channels without a reaction API (needs Capability.REACTIONS).
   */
  react(emoji: string): Promise<Record<string, unknown>> {
    return this.client.react(this.id, emoji);
  }

  /**
   * Show a "thinking…" typing indicator on the channel (Discord/Telegram; no-op
   * where the platform has none). Fired automatically before your handler runs;
   * call again during long work to keep it alive.
   */
  typing(): Promise<Record<string, unknown>> {
    return this.client.typing(this.id);
  }

  /**
   * Start a streaming response. Returns a session that accepts token chunks and
   * progressively updates the reply on channels that support message editing
   * (Slack/Discord/Telegram). On channels that don't support editing, the final
   * concatenated text is sent as a single reply when finalized.
   */
  async stream(options: StreamOptions = {}): Promise<StreamSession> {
    const strategy = await this.client.getStreamStrategy(this.connectionId);
    return new StreamSession(this.id, this.client, strategy, options.editIntervalMs);
  }
}

/** StreamSession implementation. */
export class StreamSession {
  private chunks: string[] = [];
  private postedId?: string;
  private lastEdit = 0;
  private finalized = false;

  constructor(
    private readonly messageId: string,
    private readonly client: CommClient,
    private strategy: StreamStrategy = "final_only",
    private readonly editIntervalMs = 500,
  ) {}

  /** The full accumulated text so far. */
  get text(): string {
    return this.chunks.join("");
  }

  /** Append a token/chunk to the stream. May trigger a throttled network edit. */
  async append(chunk: string): Promise<void> {
    if (this.finalized) {
      throw new CommError(400, "stream already finalized");
    }
    this.chunks.push(chunk);

    if (this.strategy !== "post_edit") return;

    const now = Date.now();
    if (!this.postedId) {
      // First chunk: post initial reply
      try {
        const res = await this.client.reply(this.messageId, this.text);
        this.postedId = (res.id as string) || (res.message_id as string);
      } catch (err) {
        logger.warn("stream initial post failed; falling back to final_only", err);
      }
      
      if (!this.postedId) {
        this.strategy = "final_only";
      }
      this.lastEdit = now;
    } else if (now - this.lastEdit >= this.editIntervalMs) {
      // Throttled edit
      try {
        await this.client.editMessage(this.postedId, this.text);
      } catch (err) {
        logger.warn("stream edit failed; will retry on next chunk", err);
      }
      this.lastEdit = now;
    }
  }

  /** Send the final version with all accumulated text. */
  async finalize(): Promise<Record<string, unknown> | null> {
    if (this.finalized) return null;
    this.finalized = true;
    const fullText = this.text;
    if (!fullText) return null;

    if (this.strategy === "post_edit" && this.postedId) {
      try {
        return await this.client.editMessage(this.postedId, fullText);
      } catch (err) {
        logger.warn("final stream edit failed; sending as new reply", err);
        return await this.client.reply(this.messageId, fullText);
      }
    }
    // final_only or post_edit where initial post failed
    return await this.client.reply(this.messageId, fullText);
  }
}

/**
 * A button tap delivered to an onInteraction handler. `value` is the callback
 * value on the tapped block button; `sourceMessage` is the message it was on.
 */
export class Interaction {
  constructor(
    readonly connectionId: string,
    readonly customerId: string,
    readonly agentId: string,
    readonly conversationId: string | null,
    readonly value: string | null,
    readonly sourceMessage: Record<string, any> | null,
    readonly sender: Record<string, unknown> | null,
    private readonly client: CommClient,
  ) {}

  /** Reply in the thread the button lived in (replies to the source message). */
  reply(
    text?: string | null,
    html?: string | null,
    blocks?: Block[] | null,
    media?: Media[] | null,
  ): Promise<Record<string, unknown>> {
    if (!this.sourceMessage) {
      throw new CommError(400, "interaction has no source message to reply to");
    }
    return this.client.reply(this.sourceMessage.id, text, html, blocks, media);
  }
}

/**
 * An emoji reaction delivered to an onReaction handler. `action` is "added" or
 * "removed"; `sourceMessage` is the message that was reacted to.
 */
export class Reaction {
  constructor(
    readonly connectionId: string,
    readonly customerId: string,
    readonly agentId: string,
    readonly emoji: string | null,
    readonly action: string,
    readonly sourceMessage: Record<string, any> | null,
    readonly sender: Record<string, unknown> | null,
    private readonly client: CommClient,
  ) {}
}

export type MessageHandler = (message: Message) => void | Promise<void>;
export type InteractionHandler = (interaction: Interaction) => void | Promise<void>;
export type ReactionHandler = (reaction: Reaction) => void | Promise<void>;

class MessageScheduler {
  private readonly queues = new Map<string, EventRecord[]>();
  private readonly running = new Set<string>();
  private pollTimeout: NodeJS.Timeout | null = null;
  private readonly debounced = new Map<
    string,
    { event: EventRecord; timer?: ReturnType<typeof setTimeout> }
  >();
  private readonly active = new Set<Promise<void>>();
  private closed = false;

  constructor(
    private readonly dispatch: (event: EventRecord) => Promise<void>,
    private readonly strategy: ConcurrencyStrategy,
    private readonly debounceMs: number,
  ) {
    if (!["queue", "debounce", "drop", "parallel"].includes(strategy)) {
      throw new TypeError("concurrency must be one of: queue, debounce, drop, parallel");
    }
    if (!Number.isFinite(debounceMs) || debounceMs < 0) {
      throw new TypeError("debounceMs must be a non-negative number");
    }
  }

  /** Execute conversationKey. */
  private conversationKey(event: EventRecord): string {
    const data = isRecord(event.data) ? event.data : {};
    const message = isRecord(data.message) ? data.message : {};
    return String(
      message.conversation_id ?? data.conversation_id ?? message.id ?? event.seq ?? "unknown",
    );
  }

  /** Execute submit. */
  async submit(event: EventRecord): Promise<void> {
    if (event.type !== "message.received") {
      await this.safeDispatch(event);
      return;
    }
    const key = this.conversationKey(event);
    if (this.strategy === "queue") this.enqueue(key, event);
    else if (this.strategy === "debounce") this.debounce(key, event);
    else if (this.strategy === "drop") this.drop(key, event);
    else this.track(this.safeDispatch(event));
  }

  /** Execute enqueue. */
  private enqueue(key: string, event: EventRecord): void {
    if (this.closed) return;
    const queue = this.queues.get(key) ?? [];
    queue.push(event);
    this.queues.set(key, queue);
    if (this.running.has(key)) return;
    this.running.add(key);
    this.track(this.drainQueue(key));
  }

  /** Execute drainQueue. */
  private async drainQueue(key: string): Promise<void> {
    for (;;) {
      const event = this.queues.get(key)?.shift();
      if (!event) {
        this.queues.delete(key);
        this.running.delete(key);
        return;
      }
      await this.safeDispatch(event);
    }
  }

  /** Execute debounce. */
  private debounce(key: string, event: EventRecord): void {
    if (this.closed) return;
    const previous = this.debounced.get(key);
    if (previous?.timer) clearTimeout(previous.timer);
    this.debounced.set(key, { event });
    if (!this.running.has(key)) this.startDebounceTimer(key);
  }

  /** Execute startDebounceTimer. */
  private startDebounceTimer(key: string): void {
    const pending = this.debounced.get(key);
    if (!pending) return;
    pending.timer = setTimeout(() => {
      const current = this.debounced.get(key);
      if (current !== pending || this.closed || this.running.has(key)) return;
      this.debounced.delete(key);
      this.running.add(key);
      this.track(this.runDebounce(key, current.event));
    }, this.debounceMs);
  }

  /** Execute runDebounce. */
  private async runDebounce(key: string, event: EventRecord): Promise<void> {
    await this.safeDispatch(event);
    this.running.delete(key);
    const pending = this.debounced.get(key);
    if (!pending) return;
    if (this.closed) {
      if (pending.timer) clearTimeout(pending.timer);
      this.debounced.delete(key);
      this.running.add(key);
      await this.runDebounce(key, pending.event);
      return;
    }
    this.startDebounceTimer(key);
  }

  /** Execute drop. */
  private drop(key: string, event: EventRecord): void {
    if (this.closed || this.running.has(key)) return;
    this.running.add(key);
    this.track(
      this.safeDispatch(event).finally(() => {
        this.running.delete(key);
      }),
    );
  }

  /** Execute track. */
  private track(task: Promise<void>): void {
    this.active.add(task);
    void task.finally(() => this.active.delete(task));
  }

  /** Execute safeDispatch. */
  private async safeDispatch(event: EventRecord): Promise<void> {
    try {
      await this.dispatch(event);
    } catch (err) {
      logger.error("event dispatch failed; continuing", err);
    }
  }

  /** Execute close. */
  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    for (const [key, item] of this.debounced) {
      if (item.timer) clearTimeout(item.timer);
      if (this.running.has(key)) continue;
      this.debounced.delete(key);
      this.running.add(key);
      this.track(this.runDebounce(key, item.event));
    }
    await Promise.all([...this.active]);
  }
}

/**
 * One identity for your AI agent across every channel — behind a single
 * onMessage handler. Reads CASPIAN_API_KEY / CASPIAN_BASE_URL from the environment or
 * ./.env when not passed explicitly.
 */
export class CommClient {
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;
  private readonly handlers: MessageHandler[] = [];
  private strategyCache = new Map<string, StreamStrategy>();
  private readonly interactionHandlers: InteractionHandler[] = [];
  private readonly reactionHandlers: ReactionHandler[] = [];
  private ackMessage?: string;
  private lastCreditWarning = 0;
  private readonly state: StateAdapter;

  constructor(options: ClientOptions = {}) {
    const apiKey = config(options.apiKey, "CASPIAN_API_KEY");
    if (!apiKey) {
      throw new CommError(401, "No API key: pass apiKey or set CASPIAN_API_KEY (env or ./.env)");
    }
    this.apiKey = apiKey;
    this.baseUrl = (config(options.baseUrl, "CASPIAN_BASE_URL", "https://api.trycaspianai.com") as string)
      .replace(/\/+$/, "");
    this.timeoutMs = (options.timeout ?? 30) * 1000;
    this.fetchImpl = options.fetch ?? (fetch.bind(globalThis) as typeof fetch);
    this.state = options.state ?? new InMemoryStateAdapter();
  }

  // ---- HTTP ----------------------------------------------------------------

  private async request<T = any>(
    method: string,
    path: string,
    opts: { json?: unknown; params?: Record<string, unknown> } = {},
  ): Promise<T> {
    const url = new URL(this.baseUrl + path);
    if (opts.params) {
      for (const [key, value] of Object.entries(opts.params)) {
        if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
      }
    }
    const headers: Record<string, string> = { Authorization: `Bearer ${this.apiKey}` };
    if (opts.json !== undefined) headers["Content-Type"] = "application/json";

    const response = await this.fetchImpl(url, {
      method,
      headers,
      body: opts.json !== undefined ? JSON.stringify(opts.json) : undefined,
      signal: AbortSignal.timeout(this.timeoutMs),
    });

    if (response.status >= 400) {
      let detailValue: unknown;
      let detail: string;
      try {
        const body = (await response.json()) as { detail?: unknown };
        detailValue = body?.detail;
        if (body && body.detail != null) {
          detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
        } else {
          detail = JSON.stringify(body);
        }
      } catch {
        detail = await response.text().catch(() => response.statusText);
      }
      if (
        response.status === 401 &&
        isRecord(detailValue) &&
        detailValue.reason === "account_required"
      ) {
        throw new AccountRequiredError(response.status, detailValue, this);
      }
      if (
        (response.status === 402 || response.status === 429) &&
        isRecord(detailValue) &&
        ["insufficient_credit", "monthly_cap_reached", "channel_cap_reached"].includes(
          detailValue.reason,
        )
      ) {
        throw new InsufficientCreditError(response.status, detailValue, this);
      }
      throw new CommError(response.status, detail);
    }
    if (response.status === 204) return null as T;
    return (await response.json()) as T;
  }

  /** Execute getText. */
  private async getText(path: string): Promise<string> {
    const response = await this.fetchImpl(new URL(this.baseUrl + path), {
      method: "GET",
      headers: { Authorization: `Bearer ${this.apiKey}` },
      signal: AbortSignal.timeout(this.timeoutMs),
    });
    if (response.status >= 400) {
      throw new CommError(response.status, await response.text().catch(() => response.statusText));
    }
    return response.text();
  }

  // ---- Platform behaviour guides (opt-in) ----------------------------------

  /** Execute behaviorPrompt. */
  behaviorPrompt(): Promise<string> {
    return this.getText("/v1/behavior-prompt");
  }

  /** Execute channelGuide. */
  channelGuide(channel: string): Promise<string> {
    return this.getText(`/v1/channels/${channel}/guide`);
  }

  // ---- Resources -----------------------------------------------------------

  /** Execute createCustomer. */
  createCustomer(name: string): Promise<Customer> {
    return this.request("POST", "/v1/customers", { json: { name } });
  }

  /** Execute createAgent. */
  createAgent(name: string): Promise<Agent> {
    return this.request("POST", "/v1/agents", { json: { name } });
  }

  private async connect(
    channel: string,
    opts: ConnectOptions = {},
    channelFields: Record<string, unknown> = {},
  ): Promise<Connection> {
    let connection = await this.request<Connection>("POST", `/v1/connections/${channel}`, {
      json: {
        customer_id: opts.customerId ?? null,
        agent_id: opts.agentId ?? null,
        display_name: opts.displayName ?? null,
        capabilities: opts.capabilities ?? null,
        ...channelFields,
      },
    });
    if (opts.wait === false) return connection;

    const deadline = Date.now() + (opts.timeout ?? 60) * 1000;
    const pollMs = (opts.pollInterval ?? 0.5) * 1000;
    while (connection.status === "provisioning") {
      if (Date.now() >= deadline) {
        throw new CommError(408, `connection ${connection.id} still provisioning`);
      }
      await sleep(pollMs);
      connection = await this.getConnection(connection.id);
    }
    if (connection.status === "failed") {
      throw new CommError(502, `provisioning failed: ${connection.error ?? ""}`);
    }
    return connection;
  }

  connectEmail(
    opts: ConnectOptions & { domain?: string; username?: string } = {},
  ): Promise<Connection> {
    const { domain, username, ...rest } = opts;
    return this.connect("email", rest, { domain: domain ?? null, username: username ?? null });
  }

  /** Execute connectTelegram. */
  connectTelegram(opts: ConnectOptions & { botToken: string }): Promise<Connection> {
    const { botToken, ...rest } = opts;
    return this.connect("telegram", rest, { bot_token: botToken });
  }

  /** Execute addDomain. */
  addDomain(domain: string): Promise<Domain> {
    return this.request("POST", "/v1/domains", { json: { domain } });
  }

  /** Execute listDomains. */
  listDomains(): Promise<Domain[]> {
    return this.request("GET", "/v1/domains");
  }

  /** Execute getDomain. */
  getDomain(domainId: string): Promise<Domain> {
    return this.request("GET", `/v1/domains/${domainId}`);
  }

  /** Execute connectPhone. */
  connectPhone(opts: ConnectOptions & { provider?: string } = {}): Promise<Connection> {
    const { provider, ...rest } = opts;
    return this.connect("phone", rest, { provider: provider ?? null });
  }

  /** Execute connectWhatsapp. */
  connectWhatsapp(opts: ConnectOptions & { provider?: string } = {}): Promise<Connection> {
    const { provider, ...rest } = opts;
    return this.connect("whatsapp", rest, { provider: provider ?? null });
  }

  startWhatsappOnboarding(
    opts: { customerId?: string; agentId?: string; displayName?: string; capabilities?: string[] } = {},
  ): Promise<WhatsappOnboarding> {
    const body: Record<string, unknown> = {};
    if (opts.customerId !== undefined) body.customer_id = opts.customerId;
    if (opts.agentId !== undefined) body.agent_id = opts.agentId;
    if (opts.displayName !== undefined) body.display_name = opts.displayName;
    if (opts.capabilities !== undefined) body.capabilities = opts.capabilities;
    return this.request("POST", "/v1/connections/whatsapp/onboarding-session", { json: body });
  }

  /** Execute connectImessage. */
  connectImessage(opts: ConnectOptions = {}): Promise<Connection> {
    return this.connect("imessage", opts);
  }

  /** Execute connectRcs. */
  connectRcs(opts: ConnectOptions = {}): Promise<Connection> {
    return this.connect("rcs", opts);
  }

  connectDiscord(
    opts: ConnectOptions & {
      botToken?: string;
      webhookUrl?: string;
      username?: string;
      avatarUrl?: string;
    } = {},
  ): Promise<Connection> {
    const { botToken, webhookUrl, username, avatarUrl, ...rest } = opts;
    return this.connect("discord", rest, {
      bot_token: botToken ?? null,
      webhook_url: webhookUrl ?? null,
      username: username ?? null,
      avatar_url: avatarUrl ?? null,
    });
  }

  installDiscord(
    opts: { customerId?: string; agentId?: string; displayName?: string } = {},
  ): Promise<Connection> {
    return this.request("POST", "/v1/connections/discord/install", {
      json: {
        customer_id: opts.customerId ?? null,
        agent_id: opts.agentId ?? null,
        display_name: opts.displayName ?? null,
      },
    });
  }

  connectSlack(
    opts: ConnectOptions & {
      slackClientId?: string;
      slackClientSecret?: string;
      slackSigningSecret?: string;
    } = {},
  ): Promise<Connection> {
    const { slackClientId, slackClientSecret, slackSigningSecret, ...rest } = opts;
    return this.connect("slack", { ...rest, wait: false }, {
      slack_client_id: slackClientId ?? null,
      slack_client_secret: slackClientSecret ?? null,
      slack_signing_secret: slackSigningSecret ?? null,
    });
  }

  installSlack(
    opts: { customerId?: string; agentId?: string; displayName?: string; iconUrl?: string } = {},
  ): Promise<Connection> {
    return this.request("POST", "/v1/connections/slack/install", {
      json: {
        customer_id: opts.customerId ?? null,
        agent_id: opts.agentId ?? null,
        display_name: opts.displayName ?? null,
        icon_url: opts.iconUrl ?? null,
      },
    });
  }

  updateBranding(
    connectionId: string,
    opts: { displayName?: string; iconUrl?: string } = {},
  ): Promise<Connection> {
    return this.request("PATCH", `/v1/connections/${connectionId}`, {
      json: { display_name: opts.displayName ?? null, icon_url: opts.iconUrl ?? null },
    });
  }

  connectX(
    opts: ConnectOptions & {
      accessToken: string;
      userId: string;
      accessSecret?: string;
      username?: string;
    },
  ): Promise<Connection> {
    const { accessToken, userId, accessSecret, username, ...rest } = opts;
    return this.connect("x", rest, {
      access_token: accessToken,
      user_id: userId,
      access_secret: accessSecret ?? null,
      username: username ?? null,
    });
  }

  /** Execute installX. */
  installX(opts: { customerId?: string; agentId?: string } = {}): Promise<Connection> {
    return this.request("POST", "/v1/connections/x/install", {
      json: { customer_id: opts.customerId ?? null, agent_id: opts.agentId ?? null },
    });
  }

  /** Execute connectInstagram. */
  connectInstagram(opts: ConnectOptions = {}): Promise<Connection> {
    return this.connect("instagram", { ...opts, wait: false });
  }

  /** Execute connectFacebook. */
  connectFacebook(opts: ConnectOptions = {}): Promise<Connection> {
    return this.connect("facebook", { ...opts, wait: false });
  }

  /** Execute getConnection. */
  getConnection(connectionId: string): Promise<Connection> {
    return this.request("GET", `/v1/connections/${connectionId}`);
  }

  /** Execute listConversations. */
  listConversations(connectionId?: string): Promise<Conversation[]> {
    return this.request("GET", "/v1/conversations", {
      params: connectionId ? { connection_id: connectionId } : undefined,
    });
  }

  /** Execute listMessages. */
  listMessages(conversationId: string): Promise<Record<string, unknown>[]> {
    return this.request("GET", `/v1/conversations/${conversationId}/messages`);
  }

  reply(
    messageId: string,
    text?: string | null,
    html?: string | null,
    blocks?: Block[] | null,
    media?: Media[] | null,
  ): Promise<Record<string, unknown>> {
    return this.request("POST", `/v1/messages/${messageId}/reply`, {
      json: { text: text ?? null, html: html ?? null, blocks: blocks ?? null, media: media ?? null },
    });
  }

  /** Execute react. */
  react(messageId: string, emoji: string): Promise<Record<string, unknown>> {
    return this.request("POST", `/v1/messages/${messageId}/react`, { json: { emoji } });
  }

  /** Execute typing. */
  typing(messageId: string): Promise<Record<string, unknown>> {
    return this.request("POST", `/v1/messages/${messageId}/typing`);
  }

  editMessage(
    messageId: string,
    text?: string | null,
    html?: string | null,
    blocks?: Block[] | null,
  ): Promise<Record<string, unknown>> {
    return this.request("PATCH", `/v1/messages/${messageId}`, {
      json: { text: text ?? null, html: html ?? null, blocks: blocks ?? null },
    });
  }

  /** Execute invalidateStrategyCache. */
  invalidateStrategyCache(connectionId: string): void {
    this.strategyCache.delete(connectionId);
  }

  /** Execute getStreamStrategy. */
  async getStreamStrategy(connectionId: string): Promise<StreamStrategy> {
    if (this.strategyCache.has(connectionId)) {
      return this.strategyCache.get(connectionId)!;
    }
    
    let strategy: StreamStrategy = "final_only";
    try {
      const conn = await this.getConnection(connectionId);
      const caps = (conn.capabilities as string[]) || [];
      if (caps.includes("edit_outbound")) {
        strategy = "post_edit";
      }
    } catch (err) {
      logger.warn("streaming strategy lookup failed; falling back to final_only", err);
    }
    
    this.strategyCache.set(connectionId, strategy);
    return strategy;
  }

  /** Execute setWebhook. */
  setWebhook(url: string, secret?: string): Promise<Record<string, unknown>> {
    return this.request("PUT", "/v1/webhook", { json: { url, secret: secret ?? null } });
  }

  /** Execute getWebhook. */
  getWebhook(): Promise<Record<string, unknown>> {
    return this.request("GET", "/v1/webhook");
  }

  /** Execute channels. */
  channels(): Promise<Record<string, unknown>[]> {
    return this.request("GET", "/v1/channels");
  }

  /** Execute login. */
  async login(opts: LoginOptions = {}): Promise<Record<string, unknown>> {
    const start = await this.request<any>("POST", "/v1/auth/device/start", {
      json: { api_key: this.apiKey },
    });
    const url = start.verification_uri_complete ?? start.verification_uri;
    const interval = opts.pollInterval ?? start.interval ?? 5;
    process.stderr.write(
      "\n  Sign in to Caspian to enable paid channels (one-time):\n" +
        `    ${url}\n` +
        "  Waiting for the developer to approve in the browser...\n\n",
    );
    const deadline = Date.now() + (opts.timeout ?? 600) * 1000;
    while (Date.now() < deadline) {
      const result = await this.request<any>("POST", "/v1/auth/device/token", {
        json: { device_code: start.device_code },
      });
      const status = result.status;
      if (status === "approved") {
        process.stderr.write("  Signed in. Add credit to start using paid channels.\n");
        return result;
      }
      if (status === "expired" || status === "not_found") {
        throw new CommError(408, `device login ${status}`);
      }
      await sleep(interval * 1000);
    }
    throw new CommError(408, "device login timed out");
  }

  /** Execute billing. */
  billing(): Promise<Record<string, unknown>> {
    return this.request("GET", "/v1/billing");
  }

  /** Execute topUp. */
  topUp(amountCents = 2000): Promise<Record<string, unknown>> {
    return this.request("POST", "/v1/billing/topup", { json: { amount_cents: amountCents } });
  }

  /** Execute setSpendLimits. */
  setSpendLimits(opts: SpendLimitsOptions = {}): Promise<Record<string, unknown>> {
    const body: Record<string, unknown> = {};
    if (opts.monthlyCapCents !== undefined) body.monthly_cap_cents = opts.monthlyCapCents;
    if (opts.channelCaps !== undefined) body.channel_caps = opts.channelCaps;
    return this.request("PUT", "/v1/billing/limits", { json: body });
  }

  /** Execute setAutopay. */
  setAutopay(opts: AutopayOptions = {}): Promise<Record<string, unknown>> {
    return this.request("PUT", "/v1/billing/autopay", {
      json: {
        enabled: opts.enabled ?? true,
        threshold_cents: opts.thresholdCents ?? null,
        topup_cents: opts.topupCents ?? null,
        monthly_cap_cents: opts.monthlyCapCents ?? null,
      },
    });
  }

  sendMessage(
    conversationId: string,
    text?: string | null,
    html?: string | null,
    blocks?: Block[] | null,
    media?: Media[] | null,
  ): Promise<Record<string, unknown>> {
    return this.request("POST", `/v1/conversations/${conversationId}/messages`, {
      json: { text: text ?? null, html: html ?? null, blocks: blocks ?? null, media: media ?? null },
    });
  }

  /** Execute initiate. */
  initiate(connectionId: string, recipient: string, text: string): Promise<Record<string, unknown>> {
    return this.request("POST", `/v1/connections/${connectionId}/initiate`, {
      json: { recipient, text },
    });
  }

  /** Execute backfill. */
  backfill(conversationId: string, limit = 50): Promise<Record<string, unknown>> {
    return this.request("POST", `/v1/conversations/${conversationId}/backfill`, { json: { limit } });
  }

  testEmail(
    opts: { text?: string; subject?: string; connectionId?: string } = {},
  ): Promise<Record<string, unknown>> {
    const body: Record<string, unknown> = {
      text: opts.text ?? "Hello from the comm test sender.",
      subject: opts.subject ?? "Test email",
    };
    if (opts.connectionId) body.connection_id = opts.connectionId;
    return this.request("POST", "/v1/test-emails", { json: body });
  }

  /** Execute events. */
  events(opts: { afterSeq?: number; limit?: number; type?: string } = {}): Promise<EventRecord[]> {
    const params: Record<string, unknown> = {
      after_seq: opts.afterSeq ?? 0,
      limit: opts.limit ?? 100,
    };
    if (opts.type) params.type = opts.type;
    return this.request("GET", "/v1/events", { params });
  }

  // ---- Event handling ------------------------------------------------------

  /** Execute onMessage. */
  onMessage(handler: MessageHandler): MessageHandler {
    this.handlers.push(handler);
    return handler;
  }

  /** Execute onInteraction. */
  onInteraction(handler: InteractionHandler): InteractionHandler {
    this.interactionHandlers.push(handler);
    return handler;
  }

  /** Execute onReaction. */
  onReaction(handler: ReactionHandler): ReactionHandler {
    this.reactionHandlers.push(handler);
    return handler;
  }

  /** Execute buildMessage. */
  private buildMessage(data: any): Message {
    const m = data.message;
    return new Message(
      m.id,
      m.conversation_id,
      m.connection_id,
      data.customer_id ?? "",
      data.agent_id ?? "",
      m.channel ?? "email",
      m.sender ?? null,
      m.subject ?? null,
      m.text ?? null,
      m.html ?? null,
      this,
      m.media ?? [],
    );
  }

  /** Execute dispatchInteraction. */
  private async dispatchInteraction(data: any): Promise<void> {
    const interaction = new Interaction(
      data.connection_id ?? "",
      data.customer_id ?? "",
      data.agent_id ?? "",
      data.conversation_id ?? null,
      data.value ?? null,
      data.source_message ?? null,
      data.sender ?? null,
      this,
    );
    for (const handler of this.interactionHandlers) {
      try {
        await handler(interaction);
      } catch (err) {
        if (err instanceof AccountRequiredError) this.warnAccountRequired(err);
        else if (err instanceof InsufficientCreditError) this.warnOutOfCredit(err);
        else logger.error("onInteraction handler failed; continuing", err);
      }
    }
  }

  /** Execute dispatchReaction. */
  private async dispatchReaction(data: any): Promise<void> {
    const reaction = new Reaction(
      data.connection_id ?? "",
      data.customer_id ?? "",
      data.agent_id ?? "",
      data.emoji ?? null,
      data.action ?? "added",
      data.source_message ?? null,
      data.sender ?? null,
      this,
    );
    for (const handler of this.reactionHandlers) {
      try {
        await handler(reaction);
      } catch (err) {
        logger.error("onReaction handler failed; continuing", err);
      }
    }
  }

  /** Execute dispatchEvent. */
  private async dispatchEvent(event: EventRecord): Promise<void> {
    if (event.type === "interaction.received") {
      await this.dispatchInteraction(event.data);
      return;
    }
    if (event.type === "reaction.received") {
      await this.dispatchReaction(event.data);
      return;
    }
    if (event.type !== "message.received") return;
    const message = this.buildMessage(event.data);
    if (this.handlers.length) {
      try {
        await message.typing();
      } catch {
      }
      if (this.ackMessage) {
        try {
          await message.reply(this.ackMessage);
        } catch (err) {
          if (err instanceof AccountRequiredError) {
            this.warnAccountRequired(err);
          } else if (err instanceof InsufficientCreditError) {
            this.warnOutOfCredit(err);
          } else {
            logger.error(`ack reply failed for message ${message.id}`, err);
          }
        }
      }
    }
    for (const handler of this.handlers) {
      try {
        await handler(message);
      } catch (err) {
        if (err instanceof AccountRequiredError) {
          this.warnAccountRequired(err);
        } else if (err instanceof InsufficientCreditError) {
          this.warnOutOfCredit(err);
        } else {
          logger.error(`onMessage handler failed for message ${message.id}; continuing`, err);
        }
      }
    }
  }

  /** Execute warnAccountRequired. */
  private warnAccountRequired(err: AccountRequiredError): void {
    const now = Date.now();
    if (now - this.lastCreditWarning < 60_000) return;
    this.lastCreditWarning = now;
    const lines = [
      "",
      "  ┌─────────────────────────────────────────────────────────────┐",
      "  │  Caspian: SIGN-IN REQUIRED for paid channels                 │",
      "  └─────────────────────────────────────────────────────────────┘",
      `  ${err.detail}`,
      "  Run:  comm login          (or client.login() in code)",
      "",
    ];
    process.stderr.write(lines.join("\n") + "\n");
  }

  /** Execute warnOutOfCredit. */
  private warnOutOfCredit(err: InsufficientCreditError): void {
    const now = Date.now();
    if (now - this.lastCreditWarning < 60_000) return;
    this.lastCreditWarning = now;
    const balance = err.balanceCents;
    const bal = typeof balance === "number" ? `$${(balance / 100).toFixed(2)}` : "unknown";
    let dash = "https://dashboard.trycaspianai.com";
    for (const option of err.paymentOptions) {
      const url = (option as Record<string, unknown>).url;
      if (typeof url === "string") {
        dash = url;
        break;
      }
    }
    const lines = [
      "",
      "  ┌─────────────────────────────────────────────────────────────┐",
      "  │  Caspian: OUT OF CREDIT - your agent could not reply         │",
      "  └─────────────────────────────────────────────────────────────┘",
      `  ${err.detail}`,
      `  Balance: ${bal}`,
      `  Add credit in the dashboard:  ${dash}`,
      "",
    ];
    process.stderr.write(lines.join("\n") + "\n");
  }

  async handleWebhook(
    body: string | Buffer,
    signature: string,
    secret: string
  ): Promise<void> {
    const bodyBuffer = Buffer.isBuffer(body) ? body : Buffer.from(body, "utf-8");
    const hmac = crypto.createHmac("sha256", secret);
    hmac.update(bodyBuffer);
    const expected = hmac.digest("hex");

    const expectedBuf = Buffer.from(expected, "ascii");
    const signatureBuf = Buffer.from(signature, "ascii");
    if (expectedBuf.length !== signatureBuf.length || !crypto.timingSafeEqual(expectedBuf, signatureBuf)) {
      throw new WebhookVerificationError();
    }

    let event: Record<string, any>;
    try {
      event = JSON.parse(bodyBuffer.toString("utf-8"));
    } catch (err: any) {
      throw new CommError(400, "invalid JSON payload");
    }

    const eventId = event.id as string | undefined;
    if (eventId && (await this.state.seen(eventId))) {
      return;
    }

    const convId = (event.data?.conversation_id as string) || "default";
    const lock = await this.state.lock(convId);

    try {
      await this.dispatchEvent(event as EventRecord);
    } finally {
      await lock.release();
    }
  }

  /** Execute dispatchPending. */
  async dispatchPending(afterSeq = 0): Promise<number> {
    let lastSeq = afterSeq;
    for (;;) {
      const batch = await this.events({ afterSeq: lastSeq });
      if (!batch.length) return lastSeq;
      for (const event of batch) {
        lastSeq = event.seq;
        await this.dispatchEvent(event);
      }
    }
  }

  /** Execute listen. */
  async listen(opts: ListenOptions = {}): Promise<void> {
    if (opts.ack !== undefined) this.ackMessage = opts.ack;
    const pollMs = (opts.pollInterval ?? 1) * 1000;
    const maxBackoffMs = (opts.maxBackoff ?? 30) * 1000;
    const scheduler = new MessageScheduler(
      (event) => this.dispatchEvent(event),
      opts.concurrency ?? "queue",
      opts.debounceMs ?? 500,
    );
    try {
      let seq = opts.fromSeq ?? (await this.latestSeq(opts.signal));
      let backoff = pollMs;
      while (!opts.signal?.aborted) {
        let batch: EventRecord[];
        try {
          batch = await this.events({ afterSeq: seq });
        } catch (err) {
          if (opts.signal?.aborted) return;
          logger.warn(`gateway poll failed; retrying in ${(backoff / 1000).toFixed(1)}s`, err);
          await sleep(backoff, opts.signal);
          backoff = Math.min(backoff * 2, maxBackoffMs);
          continue;
        }
        backoff = pollMs;
        if (!batch.length) {
          await sleep(pollMs, opts.signal);
          continue;
        }
        for (const event of batch) {
          await scheduler.submit(event);
          seq = event.seq;
        }
      }
    } finally {
      await scheduler.close();
    }
  }

  /** Execute latestSeq. */
  private async latestSeq(signal?: AbortSignal): Promise<number> {
    while (!signal?.aborted) {
      try {
        let seq = 0;
        for (;;) {
          const batch = await this.events({ afterSeq: seq, limit: 500 });
          if (!batch.length) return seq;
          seq = batch[batch.length - 1].seq;
        }
      } catch (err) {
        logger.warn("could not read starting cursor; retrying in 2s", err);
        await sleep(2000, signal);
      }
    }
    return 0;
  }
}
