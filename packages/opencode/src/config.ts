import {
  DEFAULT_LIMITS,
  type LimitConfig,
} from "./reliability/limits.js";
import {
  DEFAULT_SWITCHES,
  type FeatureSwitches,
} from "./reliability/switches.js";
import {
  DEFAULT_EMAIL_IDENTITY,
  type EmailIdentityConfig,
} from "./identity.js";
import {
  DEFAULT_NOTIFY,
  type NotifyConfig,
} from "./notify.js";
import {
  DEFAULT_THINKING,
  type ThinkingConfig,
} from "./thinking.js";
import type { DiscordIdentityConfig } from "./discord-send.js";
import type { TelegramIdentityConfig } from "./telegram-send.js";
import {
  DEFAULT_THREADING,
  type ThreadingConfig,
} from "./threading.js";

/** v1: email only — other channels rejected at admit (blast-radius boundary). */
export const V1_CHANNELS = ["email"] as const;
export type V1Channel = (typeof V1_CHANNELS)[number];

export interface PluginConfig {
  apiKey?: string;
  baseUrl?: string;
  displayName: string;
  /** Sender allowlist; empty = all. */
  allowFrom: string[];
  /** Channel allowlist; v1 defaults to email only. */
  channels: string[];
  /**
   * Thread → OpenCode session mapping.
   * Default enabled: each Caspian conversation is its own OpenCode session.
   */
  threading: ThreadingConfig;
  /**
   * Which Caspian email identity to listen on / send from.
   * Empty filters = all email connections on this API key.
   */
  email: EmailIdentityConfig;
  /** Preferred Telegram connection (from caspian.json telegram.connectionId). */
  telegram: TelegramIdentityConfig;
  /** Preferred Discord connection (from caspian.json discord.connectionId). */
  discord: DiscordIdentityConfig;
  /** Alerts when inbound mail is delivered (toast + OS notification). */
  notify: NotifyConfig;
  /**
   * Include model reasoning ("thinking") in Caspian channel replies.
   * Default off globally; override per channel via `thinking.channels`.
   */
  thinking: ThinkingConfig;
  switches: FeatureSwitches;
  limits: LimitConfig;
  /** Heartbeat stale threshold (ms). */
  heartbeatStaleMs: number;
  /** Circuit breaker knobs. */
  circuit: {
    failureThreshold: number;
    coolDownMs: number;
    successThreshold: number;
  };
  /** Bounded retries on outbound reply. */
  replyRetries: number;
  /** Degraded message when capacity rejects. */
  degradedReplyText: string;
}

export const DEFAULT_CONFIG: PluginConfig = {
  displayName: "OpenCode Agent",
  allowFrom: [],
  channels: [...V1_CHANNELS],
  threading: { ...DEFAULT_THREADING },
  email: { ...DEFAULT_EMAIL_IDENTITY },
  telegram: {},
  discord: {},
  notify: { ...DEFAULT_NOTIFY },
  thinking: {
    enabled: DEFAULT_THINKING.enabled,
    channels: { ...DEFAULT_THINKING.channels },
  },
  switches: { ...DEFAULT_SWITCHES },
  limits: { ...DEFAULT_LIMITS },
  heartbeatStaleMs: 30_000,
  circuit: {
    failureThreshold: 5,
    coolDownMs: 15_000,
    successThreshold: 2,
  },
  replyRetries: 2,
  degradedReplyText:
    "The agent is at capacity right now. Please try again in a moment.",
};

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Merge file/env overrides onto defaults. Pure — easy to unit test. */
export function resolveConfig(
  raw: Record<string, unknown> = {},
  env: NodeJS.ProcessEnv = process.env,
): PluginConfig {
  const switchesRaw = isRecord(raw.switches) ? raw.switches : {};
  const limitsRaw = isRecord(raw.limits) ? raw.limits : {};
  const circuitRaw = isRecord(raw.circuit) ? raw.circuit : {};
  const threadingRaw = isRecord(raw.threading) ? raw.threading : {};
  const emailRaw = isRecord(raw.email) ? raw.email : {};
  const telegramRaw = isRecord(raw.telegram) ? raw.telegram : {};
  const discordRaw = isRecord(raw.discord) ? raw.discord : {};
  const notifyRaw = isRecord(raw.notify) ? raw.notify : {};
  const thinkingRaw = isRecord(raw.thinking) ? raw.thinking : {};
  const thinkingChannelsRaw = isRecord(thinkingRaw.channels)
    ? thinkingRaw.channels
    : {};
  const thinkingChannels: Record<string, boolean> = {};
  for (const [k, v] of Object.entries(thinkingChannelsRaw)) {
    if (typeof v === "boolean") thinkingChannels[k.toLowerCase()] = v;
  }

  return {
    apiKey:
      (typeof raw.apiKey === "string" ? raw.apiKey : undefined) ??
      env.CASPIAN_API_KEY ??
      env.COMM_API_KEY,
    baseUrl:
      (typeof raw.baseUrl === "string" ? raw.baseUrl : undefined) ??
      env.CASPIAN_BASE_URL ??
      env.COMM_BASE_URL,
    displayName:
      typeof raw.displayName === "string"
        ? raw.displayName
        : DEFAULT_CONFIG.displayName,
    allowFrom: Array.isArray(raw.allowFrom)
      ? raw.allowFrom.filter((x): x is string => typeof x === "string")
      : [],
    channels: Array.isArray(raw.channels)
      ? raw.channels.filter((x): x is string => typeof x === "string")
      : [...V1_CHANNELS],
    threading: {
      enabled:
        typeof threadingRaw.enabled === "boolean"
          ? threadingRaw.enabled
          : DEFAULT_THREADING.enabled,
      sharedSessionKey:
        typeof threadingRaw.sharedSessionKey === "string" &&
        threadingRaw.sharedSessionKey.length > 0
          ? threadingRaw.sharedSessionKey
          : DEFAULT_THREADING.sharedSessionKey,
      sessionFooter:
        typeof threadingRaw.sessionFooter === "boolean"
          ? threadingRaw.sessionFooter
          : DEFAULT_THREADING.sessionFooter,
    },
    email: {
      connectionId:
        typeof emailRaw.connectionId === "string"
          ? emailRaw.connectionId
          : undefined,
      address:
        typeof emailRaw.address === "string" ? emailRaw.address : undefined,
      listenConnectionIds: Array.isArray(emailRaw.listenConnectionIds)
        ? emailRaw.listenConnectionIds.filter(
            (x): x is string => typeof x === "string",
          )
        : [],
      listenAddresses: Array.isArray(emailRaw.listenAddresses)
        ? emailRaw.listenAddresses.filter(
            (x): x is string => typeof x === "string",
          )
        : [],
    },
    telegram: {
      connectionId:
        typeof telegramRaw.connectionId === "string"
          ? telegramRaw.connectionId
          : undefined,
    },
    discord: {
      connectionId:
        typeof discordRaw.connectionId === "string"
          ? discordRaw.connectionId
          : undefined,
    },
    notify: {
      enabled:
        typeof notifyRaw.enabled === "boolean"
          ? notifyRaw.enabled
          : DEFAULT_NOTIFY.enabled,
      toast:
        typeof notifyRaw.toast === "boolean"
          ? notifyRaw.toast
          : DEFAULT_NOTIFY.toast,
      system:
        typeof notifyRaw.system === "boolean"
          ? notifyRaw.system
          : DEFAULT_NOTIFY.system,
    },
    thinking: {
      enabled:
        typeof thinkingRaw.enabled === "boolean"
          ? thinkingRaw.enabled
          : DEFAULT_THINKING.enabled,
      channels: thinkingChannels,
    },
    switches: {
      enabled:
        typeof switchesRaw.enabled === "boolean"
          ? switchesRaw.enabled
          : DEFAULT_SWITCHES.enabled,
      autoOnboard:
        typeof switchesRaw.autoOnboard === "boolean"
          ? switchesRaw.autoOnboard
          : DEFAULT_SWITCHES.autoOnboard,
      autoConnectEmail:
        typeof switchesRaw.autoConnectEmail === "boolean"
          ? switchesRaw.autoConnectEmail
          : DEFAULT_SWITCHES.autoConnectEmail,
      typing:
        typeof switchesRaw.typing === "boolean"
          ? switchesRaw.typing
          : DEFAULT_SWITCHES.typing,
      degradedCapacityReply:
        typeof switchesRaw.degradedCapacityReply === "boolean"
          ? switchesRaw.degradedCapacityReply
          : DEFAULT_SWITCHES.degradedCapacityReply,
      durableSessionMap:
        typeof switchesRaw.durableSessionMap === "boolean"
          ? switchesRaw.durableSessionMap
          : DEFAULT_SWITCHES.durableSessionMap,
    },
    limits: {
      globalConcurrency:
        typeof limitsRaw.globalConcurrency === "number"
          ? limitsRaw.globalConcurrency
          : DEFAULT_LIMITS.globalConcurrency,
      perConversationConcurrency:
        typeof limitsRaw.perConversationConcurrency === "number"
          ? limitsRaw.perConversationConcurrency
          : DEFAULT_LIMITS.perConversationConcurrency,
      perConversationRate:
        typeof limitsRaw.perConversationRate === "number"
          ? limitsRaw.perConversationRate
          : DEFAULT_LIMITS.perConversationRate,
      rateWindowMs:
        typeof limitsRaw.rateWindowMs === "number"
          ? limitsRaw.rateWindowMs
          : DEFAULT_LIMITS.rateWindowMs,
    },
    heartbeatStaleMs:
      typeof raw.heartbeatStaleMs === "number"
        ? raw.heartbeatStaleMs
        : DEFAULT_CONFIG.heartbeatStaleMs,
    circuit: {
      failureThreshold:
        typeof circuitRaw.failureThreshold === "number"
          ? circuitRaw.failureThreshold
          : DEFAULT_CONFIG.circuit.failureThreshold,
      coolDownMs:
        typeof circuitRaw.coolDownMs === "number"
          ? circuitRaw.coolDownMs
          : DEFAULT_CONFIG.circuit.coolDownMs,
      successThreshold:
        typeof circuitRaw.successThreshold === "number"
          ? circuitRaw.successThreshold
          : DEFAULT_CONFIG.circuit.successThreshold,
    },
    replyRetries:
      typeof raw.replyRetries === "number"
        ? raw.replyRetries
        : DEFAULT_CONFIG.replyRetries,
    degradedReplyText:
      typeof raw.degradedReplyText === "string"
        ? raw.degradedReplyText
        : DEFAULT_CONFIG.degradedReplyText,
  };
}
