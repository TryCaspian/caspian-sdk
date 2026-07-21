import { config } from "./config.js";
import { CommError } from "./errors.js";
import type {
  Agent,
  ClientOptions,
  Connection,
  ConnectOptions,
  Conversation,
  Customer,
  Domain,
  EventRecord,
  ListenOptions,
  WhatsappOnboarding,
} from "./types.js";

const logger = {
  warn: (...args: unknown[]) => console.warn("[caspian-sdk]", ...args),
  error: (...args: unknown[]) => console.error("[caspian-sdk]", ...args),
};

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
  ) {}

  /** Reply on whichever channel this message arrived from (auto-threaded). */
  reply(text?: string | null, html?: string | null): Promise<Record<string, unknown>> {
    return this.client.reply(this.id, text, html);
  }

  /**
   * Show a "thinking…" typing indicator on the channel (Discord/Telegram; no-op
   * where the platform has none). Fired automatically before your handler runs;
   * call again during long work to keep it alive.
   */
  typing(): Promise<Record<string, unknown>> {
    return this.client.typing(this.id);
  }
}

export type MessageHandler = (message: Message) => void | Promise<void>;

/**
 * One identity for your AI agent across every channel — behind a single
 * onMessage handler. Reads COMM_API_KEY / COMM_BASE_URL from the environment or
 * ./.env when not passed explicitly.
 */
export class CommClient {
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;
  private readonly handlers: MessageHandler[] = [];
  private ackMessage?: string;

  constructor(options: ClientOptions = {}) {
    const apiKey = config(options.apiKey, "COMM_API_KEY");
    if (!apiKey) {
      throw new CommError(401, "No API key: pass apiKey or set COMM_API_KEY (env or ./.env)");
    }
    this.apiKey = apiKey;
    this.baseUrl = (config(options.baseUrl, "COMM_BASE_URL", "http://127.0.0.1:8000") as string)
      .replace(/\/+$/, "");
    this.timeoutMs = (options.timeout ?? 30) * 1000;
    this.fetchImpl = options.fetch ?? globalThis.fetch;
    if (!this.fetchImpl) {
      throw new CommError(0, "global fetch is unavailable — use Node >= 18 or pass options.fetch");
    }
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
      let detail: string;
      try {
        const body = (await response.json()) as { detail?: unknown };
        if (body && body.detail != null) {
          // FastAPI validation errors put an array/object under `detail`.
          detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
        } else {
          detail = JSON.stringify(body);
        }
      } catch {
        detail = await response.text().catch(() => response.statusText);
      }
      throw new CommError(response.status, detail);
    }
    if (response.status === 204) return null as T;
    return (await response.json()) as T;
  }

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

  /**
   * A ready-to-inject system-prompt block telling your agent how to behave on
   * each channel you've connected (Slack threads, WhatsApp 24h window, SMS
   * length, formatting, etc.). Append it to your agent's system prompt — or
   * ignore it and write your own. Empty string if nothing is connected yet.
   */
  behaviorPrompt(): Promise<string> {
    return this.getText("/v1/behavior-prompt");
  }

  /** The behaviour guide for a single channel (e.g. "slack", "discord"). */
  channelGuide(channel: string): Promise<string> {
    return this.getText(`/v1/channels/${channel}/guide`);
  }

  // ---- Resources -----------------------------------------------------------

  createCustomer(name: string): Promise<Customer> {
    return this.request("POST", "/v1/customers", { json: { name } });
  }

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

  /**
   * Connect an email inbox. Pass `domain` for a verified custom domain and
   * `username` to pick the exact local part (custom domains only).
   */
  connectEmail(
    opts: ConnectOptions & { domain?: string; username?: string } = {},
  ): Promise<Connection> {
    const { domain, username, ...rest } = opts;
    return this.connect("email", rest, { domain: domain ?? null, username: username ?? null });
  }

  /** Connect a Telegram bot. Get a token from @BotFather; we do the rest. */
  connectTelegram(opts: ConnectOptions & { botToken: string }): Promise<Connection> {
    const { botToken, ...rest } = opts;
    return this.connect("telegram", rest, { bot_token: botToken });
  }

  /**
   * Register a custom subdomain (e.g. agents.example.com). Returns the DNS
   * records to add at the registrar; poll getDomain() until active.
   */
  addDomain(domain: string): Promise<Domain> {
    return this.request("POST", "/v1/domains", { json: { domain } });
  }

  listDomains(): Promise<Domain[]> {
    return this.request("GET", "/v1/domains");
  }

  getDomain(domainId: string): Promise<Domain> {
    return this.request("GET", `/v1/domains/${domainId}`);
  }

  /**
   * Connect an SMS/voice phone line. `provider` picks the backend when more than
   * one is configured (e.g. gsm-modem, or a hosted provider); omit for default.
   */
  connectPhone(opts: ConnectOptions & { provider?: string } = {}): Promise<Connection> {
    const { provider, ...rest } = opts;
    return this.connect("phone", rest, { provider: provider ?? null });
  }

  /**
   * Connect a WhatsApp number. `provider` picks the backend when more than one is
   * configured, `provider` picks one explicitly; omit for the default.
   */
  connectWhatsapp(opts: ConnectOptions & { provider?: string } = {}): Promise<Connection> {
    const { provider, ...rest } = opts;
    return this.connect("whatsapp", rest, { provider: provider ?? null });
  }

  /**
   * Begin Meta WhatsApp Embedded Signup for one of your customers. Returns
   * { session, launcher_url, expires_in }. Hand launcher_url to whoever owns the
   * WhatsApp Business account: they click through Meta's popup once and their
   * number is provisioned onto this agent. Poll getConnection() until active.
   */
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

  /** Connect an iMessage line (Caspian hosted). */
  connectImessage(opts: ConnectOptions = {}): Promise<Connection> {
    return this.connect("imessage", opts);
  }

  /** Connect an RCS Business Messaging sender (Caspian hosted). */
  connectRcs(opts: ConnectOptions = {}): Promise<Connection> {
    return this.connect("rcs", opts);
  }

  /**
   * Connect a Discord identity. Either a bot (`botToken` from
   * discord.com/developers) OR a channel `webhookUrl` for a per-agent identity
   * with a custom `username`/`avatarUrl` (no bot needed).
   */
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

  /**
   * One-click install of the gateway's shared Discord bot (no bot token). Returns
   * a connection with an `authorize_url`. Pass `displayName` to give the bot YOUR
   * custom name in that server.
   */
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

  /**
   * Start a Slack install with your OWN Slack app (create one at
   * api.slack.com/apps and pass its client id/secret/signing secret). Returns a
   * connection with an `authorize_url`; the workspace owner clicks it to approve.
   */
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

  /**
   * One-click install of the gateway's shared Slack app (no app to create).
   * Returns a connection with an `authorize_url` ("Add to Slack"). Pass
   * `displayName` and `iconUrl` to post under YOUR own name + icon.
   */
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

  /**
   * Start installation of a bring-your-own GitHub App. The App must use the
   * gateway's setup/webhook URLs and subscribe to issue_comment events.
   */
  connectGitHub(
    opts: ConnectOptions & {
      githubAppId: string;
      githubAppSlug: string;
      githubPrivateKey: string;
      githubWebhookSecret: string;
      receiveMode?: "mentions" | "all";
    },
  ): Promise<Connection> {
    const {
      githubAppId,
      githubAppSlug,
      githubPrivateKey,
      githubWebhookSecret,
      receiveMode,
      ...rest
    } = opts;
    return this.connect("github", { ...rest, wait: false }, {
      github_app_id: githubAppId,
      github_app_slug: githubAppSlug,
      github_private_key: githubPrivateKey,
      github_webhook_secret: githubWebhookSecret,
      receive_mode: receiveMode ?? "mentions",
    });
  }

  /** One-click installation of the gateway's shared GitHub App. */
  installGitHub(
    opts: {
      customerId?: string;
      agentId?: string;
      displayName?: string;
      receiveMode?: "mentions" | "all";
    } = {},
  ): Promise<Connection> {
    return this.request("POST", "/v1/connections/github/install", {
      json: {
        customer_id: opts.customerId ?? null,
        agent_id: opts.agentId ?? null,
        display_name: opts.displayName ?? null,
        receive_mode: opts.receiveMode ?? "mentions",
      },
    });
  }

  /**
   * Change the name/icon the agent posts under, after connecting — no re-install.
   * Slack: next message; Discord shared bot: re-sets the per-server nickname.
   */
  updateBranding(
    connectionId: string,
    opts: { displayName?: string; iconUrl?: string } = {},
  ): Promise<Connection> {
    return this.request("PATCH", `/v1/connections/${connectionId}`, {
      json: { display_name: opts.displayName ?? null, icon_url: opts.iconUrl ?? null },
    });
  }

  /**
   * Connect an X (Twitter) account as a reactive DM bot. Bring the account's
   * OAuth tokens: `accessToken` + `userId`, and `accessSecret` for a
   * bring-your-own account. People DM the account and the agent replies.
   */
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

  /**
   * One-click connect of an X account as a DM bot — no tokens to paste. Returns a
   * connection with an `authorize_url` ("Sign in with X").
   */
  installX(opts: { customerId?: string; agentId?: string } = {}): Promise<Connection> {
    return this.request("POST", "/v1/connections/x/install", {
      json: { customer_id: opts.customerId ?? null, agent_id: opts.agentId ?? null },
    });
  }

  /** Start an Instagram DM install (OAuth). Returns a connection with an authorize_url. */
  connectInstagram(opts: ConnectOptions = {}): Promise<Connection> {
    return this.connect("instagram", { ...opts, wait: false });
  }

  /** Start a Facebook Messenger install (OAuth). Returns a connection with an authorize_url. */
  connectFacebook(opts: ConnectOptions = {}): Promise<Connection> {
    return this.connect("facebook", { ...opts, wait: false });
  }

  getConnection(connectionId: string): Promise<Connection> {
    return this.request("GET", `/v1/connections/${connectionId}`);
  }

  listConversations(connectionId?: string): Promise<Conversation[]> {
    return this.request("GET", "/v1/conversations", {
      params: connectionId ? { connection_id: connectionId } : undefined,
    });
  }

  listMessages(conversationId: string): Promise<Record<string, unknown>[]> {
    return this.request("GET", `/v1/conversations/${conversationId}/messages`);
  }

  reply(
    messageId: string,
    text?: string | null,
    html?: string | null,
  ): Promise<Record<string, unknown>> {
    return this.request("POST", `/v1/messages/${messageId}/reply`, {
      json: { text: text ?? null, html: html ?? null },
    });
  }

  /**
   * Show a "thinking…" indicator on the channel a message arrived on
   * (Discord/Telegram; no-op where unsupported). Best-effort.
   */
  typing(messageId: string): Promise<Record<string, unknown>> {
    return this.request("POST", `/v1/messages/${messageId}/typing`);
  }

  /** Receive events by push instead of (or alongside) polling. */
  setWebhook(url: string, secret?: string): Promise<Record<string, unknown>> {
    return this.request("PUT", "/v1/webhook", { json: { url, secret: secret ?? null } });
  }

  getWebhook(): Promise<Record<string, unknown>> {
    return this.request("GET", "/v1/webhook");
  }

  /** Configured transports and their capabilities. */
  channels(): Promise<Record<string, unknown>[]> {
    return this.request("GET", "/v1/channels");
  }

  /** Proactively send into an existing conversation (needs Capability.SEND). */
  sendMessage(
    conversationId: string,
    text?: string | null,
    html?: string | null,
  ): Promise<Record<string, unknown>> {
    return this.request("POST", `/v1/conversations/${conversationId}/messages`, {
      json: { text: text ?? null, html: html ?? null },
    });
  }

  /** Cold-start a conversation (needs Capability.INITIATE — user account). */
  initiate(connectionId: string, recipient: string, text: string): Promise<Record<string, unknown>> {
    return this.request("POST", `/v1/connections/${connectionId}/initiate`, {
      json: { recipient, text },
    });
  }

  /** Pull history from before the connection (needs Capability.BACKFILL). */
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

  events(opts: { afterSeq?: number; limit?: number; type?: string } = {}): Promise<EventRecord[]> {
    const params: Record<string, unknown> = {
      after_seq: opts.afterSeq ?? 0,
      limit: opts.limit ?? 100,
    };
    if (opts.type) params.type = opts.type;
    return this.request("GET", "/v1/events", { params });
  }

  // ---- Event handling ------------------------------------------------------

  /** Register a handler. The same handler answers every channel you connect. */
  onMessage(handler: MessageHandler): MessageHandler {
    this.handlers.push(handler);
    return handler;
  }

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
    );
  }

  private async dispatchEvent(event: EventRecord): Promise<void> {
    if (event.type !== "message.received") return;
    const message = this.buildMessage(event.data);
    if (this.handlers.length) {
      // Show a "thinking…" indicator up front; best-effort, never blocks dispatch.
      try {
        await message.typing();
      } catch {
        /* ignore */
      }
      // Optional instant acknowledgement (listen({ ack })) for channels with no
      // typing indicator; the real answer follows from the handler.
      if (this.ackMessage) {
        try {
          await message.reply(this.ackMessage);
        } catch (err) {
          logger.error(`ack reply failed for message ${message.id}`, err);
        }
      }
    }
    for (const handler of this.handlers) {
      try {
        await handler(message);
      } catch (err) {
        logger.error(`onMessage handler failed for message ${message.id}; continuing`, err);
      }
    }
  }

  /**
   * Process all currently available events once. Returns the last seen seq.
   * Handler exceptions are caught per message, so this always drains the queue.
   */
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

  /**
   * Poll the event stream forever, dispatching inbound messages to handlers.
   * Resilient by design: a handler that throws is logged and skipped, and a
   * failed poll is retried with exponential backoff. Pass an AbortSignal to stop.
   */
  async listen(opts: ListenOptions = {}): Promise<void> {
    if (opts.ack !== undefined) this.ackMessage = opts.ack;
    const pollMs = (opts.pollInterval ?? 1) * 1000;
    const maxBackoffMs = (opts.maxBackoff ?? 30) * 1000;
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
        await this.dispatchEvent(event);
        seq = event.seq; // advance only after the dispatch attempt
      }
    }
  }

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
