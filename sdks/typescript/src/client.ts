import { config } from "./config.js";
import { AccountRequiredError, CommError, InsufficientCreditError } from "./errors.js";
import type {
  Agent,
  AutopayOptions,
  Block,
  ClientOptions,
  Connection,
  ConnectOptions,
  Conversation,
  Customer,
  Domain,
  EventRecord,
  ListenOptions,
  LoginOptions,
  Media,
  SpendLimitsOptions,
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
  private readonly interactionHandlers: InteractionHandler[] = [];
  private readonly reactionHandlers: ReactionHandler[] = [];
  private ackMessage?: string;
  private lastCreditWarning = 0;

  constructor(options: ClientOptions = {}) {
    const apiKey = config(options.apiKey, "CASPIAN_API_KEY");
    if (!apiKey) {
      throw new CommError(401, "No API key: pass apiKey or set CASPIAN_API_KEY (env or ./.env)");
    }
    this.apiKey = apiKey;
    this.baseUrl = (config(options.baseUrl, "CASPIAN_BASE_URL", "https://api.trycaspianai.com") as string)
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
      let detailValue: unknown;
      let detail: string;
      try {
        const body = (await response.json()) as { detail?: unknown };
        detailValue = body?.detail;
        if (body && body.detail != null) {
          // FastAPI validation errors put an array/object under `detail`.
          detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
        } else {
          detail = JSON.stringify(body);
        }
      } catch {
        detail = await response.text().catch(() => response.statusText);
      }
      // A paid channel needs a one-time developer sign-in first.
      if (
        response.status === 401 &&
        isRecord(detailValue) &&
        detailValue.reason === "account_required"
      ) {
        throw new AccountRequiredError(response.status, detailValue, this);
      }
      // A billing block (out of credit / spend cap) carries a structured body;
      // raise the typed error so callers can react in code.
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

  /**
   * Reply to a message. Pass `blocks` — a list of provider-neutral rich blocks
   * (heading, text, divider, image, fields, list, buttons, card) — to send a
   * rich message. Slack, Discord, Telegram and email render it natively; every
   * other channel degrades to clean text automatically.
   */
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

  /**
   * Add an emoji reaction (tapback) to a message (needs Capability.REACTIONS —
   * Slack/Telegram/Discord). Best-effort; a channel with no reaction API returns
   * `reacted: false` rather than erroring.
   */
  react(messageId: string, emoji: string): Promise<Record<string, unknown>> {
    return this.request("POST", `/v1/messages/${messageId}/react`, { json: { emoji } });
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

  // ---- Account sign-in (one-time, required before paid channels) -----------

  /**
   * Sign the developer in once to open a billing account for this project.
   *
   * Paid channels require a real account before any spend. This prints a URL for
   * the developer to open in a browser and resolves once they approve. The
   * project you've already built with is carried over — same API key, nothing
   * lost. After this, add credit with `topUp()` and connect paid channels
   * freely; the agent needs no further human sign-in.
   */
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

  // ---- Billing (pay-as-you-go credit) --------------------------------------

  /**
   * Current credit balance, spend, spend caps, and autopay state. Paid channels
   * draw down this balance; free channels (email, Telegram, Discord, Slack)
   * never do.
   */
  billing(): Promise<Record<string, unknown>> {
    return this.request("GET", "/v1/billing");
  }

  /**
   * Mint a hosted checkout link to add credit. Returns
   * `{ checkout_url, session_id, amount_cents, ... }` — open the URL (or hand it
   * to whoever holds the card). Credit lands seconds after payment; poll
   * `billing()` or watch for the `billing.credited` event. Minimum 100 cents.
   */
  topUp(amountCents = 2000): Promise<Record<string, unknown>> {
    return this.request("POST", "/v1/billing/topup", { json: { amount_cents: amountCents } });
  }

  /**
   * Cap spend so autopay/credit can't run away. `monthlyCapCents` caps total
   * monthly spend; `channelCaps` caps per channel (e.g. { whatsapp: 5000 }).
   * Returns the updated billing state.
   */
  setSpendLimits(opts: SpendLimitsOptions = {}): Promise<Record<string, unknown>> {
    const body: Record<string, unknown> = {};
    if (opts.monthlyCapCents !== undefined) body.monthly_cap_cents = opts.monthlyCapCents;
    if (opts.channelCaps !== undefined) body.channel_caps = opts.channelCaps;
    return this.request("PUT", "/v1/billing/limits", { json: body });
  }

  /**
   * Auto-refill the balance from a saved card when it drops below
   * `thresholdCents` (adds `topupCents`). Requires a card on file (complete one
   * `topUp()` checkout first) and a `monthlyCapCents` — an uncapped
   * auto-replenishing budget is not allowed. Pass `enabled: false` to turn off.
   */
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

  /**
   * Proactively send into an existing conversation (needs Capability.SEND).
   *
   * Pass `blocks` — provider-neutral rich blocks — to render natively on
   * Slack/Discord/Telegram/email and degrade to clean text elsewhere.
   */
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

  /**
   * Register a handler for button taps (interaction.received). The same handler
   * answers taps from every channel with interactive buttons (Slack, Discord,
   * Telegram).
   */
  onInteraction(handler: InteractionHandler): InteractionHandler {
    this.interactionHandlers.push(handler);
    return handler;
  }

  /** Register a handler for emoji reactions (reaction.received). */
  onReaction(handler: ReactionHandler): ReactionHandler {
    this.reactionHandlers.push(handler);
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
      m.media ?? [],
    );
  }

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
        // Paid channel used before the developer signed in, or the project is
        // out of credit / capped. Surface it loudly (e.g. in Claude Code) and
        // keep the loop alive so one blocked reply can't stop the listener.
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

  /** Print a prominent, rate-limited banner when a paid action needs sign-in. */
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

  /** Print a prominent, rate-limited banner when a paid reply is blocked. */
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
