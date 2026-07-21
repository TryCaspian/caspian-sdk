/** A connection to one channel (email inbox, Slack app, Discord bot, ...). */
export interface Connection {
  id: string;
  status: string;
  channel?: string;
  address?: string;
  /** Present for OAuth channels (Slack/Discord/X/Instagram/Facebook) — hand it to the user. */
  authorize_url?: string;
  error?: string | null;
  display_name?: string | null;
  [key: string]: unknown;
}

export interface Customer {
  id: string;
  name: string;
  [key: string]: unknown;
}

export interface Agent {
  id: string;
  name: string;
  [key: string]: unknown;
}

export interface Domain {
  id: string;
  domain: string;
  status?: string;
  [key: string]: unknown;
}

export interface Conversation {
  id: string;
  [key: string]: unknown;
}

/** One item from the event stream (GET /v1/events). */
export interface EventRecord {
  seq: number;
  type: string;
  data?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface WhatsappOnboarding {
  session: string;
  launcher_url: string;
  expires_in: number;
  [key: string]: unknown;
}

export interface ClientOptions {
  /** Falls back to COMM_API_KEY (env or ./.env). */
  apiKey?: string;
  /** Falls back to COMM_BASE_URL (env or ./.env), then https://api.trycaspianai.com. */
  baseUrl?: string;
  /** Per-request timeout in seconds (default 30). */
  timeout?: number;
  /** Inject a custom fetch (for testing). Defaults to global fetch. */
  fetch?: typeof fetch;
}

/** Shared options for every connect_* call. */
export interface ConnectOptions {
  customerId?: string;
  agentId?: string;
  displayName?: string;
  capabilities?: string[];
  /** Wait for provisioning to finish (default true; false for OAuth channels). */
  wait?: boolean;
  /** Provisioning wait timeout in seconds (default 60). */
  timeout?: number;
  /** Poll interval while provisioning, in seconds (default 0.5). */
  pollInterval?: number;
}

export interface LoginOptions {
  /** Seconds between device-token polls (default: value the gateway suggests). */
  pollInterval?: number;
  /** Give up waiting for approval after this many seconds (default 600). */
  timeout?: number;
}

export interface SpendLimitsOptions {
  /** Cap total monthly spend, in cents. */
  monthlyCapCents?: number;
  /** Cap per-channel spend, in cents (e.g. { whatsapp: 5000 }). */
  channelCaps?: Record<string, number>;
}

export interface AutopayOptions {
  /** Turn autopay on (default) or off. */
  enabled?: boolean;
  /** Refill when the balance drops below this, in cents. */
  thresholdCents?: number;
  /** Amount to add on each auto-refill, in cents. */
  topupCents?: number;
  /** Required monthly spend cap, in cents — autopay can't run uncapped. */
  monthlyCapCents?: number;
}

export interface ListenOptions {
  /** Start from this event seq instead of "newest at startup". */
  fromSeq?: number;
  /** Seconds between polls when idle (default 1). */
  pollInterval?: number;
  /** Max backoff in seconds after repeated poll failures (default 30). */
  maxBackoff?: number;
  /** Abort to stop the loop gracefully. */
  signal?: AbortSignal;
  /**
   * Send an instant acknowledgement reply (e.g. "On it, one moment…") the moment
   * a message arrives, before your handler runs. Useful on channels with no
   * typing indicator (X, SMS, email); the real answer follows from the handler.
   */
  ack?: string;
}
