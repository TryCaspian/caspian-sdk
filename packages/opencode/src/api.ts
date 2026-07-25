/**
 * Programmatic helpers for tests / embedding. Not the OpenCode plugin entry —
 * OpenCode loads `./index` and requires every export there to be a function.
 */

export { CaspianOpenCodeBridge } from "./bridge.js";
export { resolveConfig } from "./config.js";
export {
  DASHBOARD_LOGIN_URL,
  DEFAULT_GATEWAY,
  ensureCredentials,
  getCredentialsStatus,
  initViaCli,
  maskApiKey,
  mintSandboxKey,
  persistCredentials,
  setupCredentials,
} from "./onboard.js";
export { handleInbound } from "./pipeline.js";
export {
  admits,
  toEnvelope,
  formatInboundPrompt,
  formatEmailPrompt,
} from "./email.js";
export { sendEmail, formatOutboundText } from "./outbound.js";
export {
  sessionMapKey,
  sessionTitle,
  DEFAULT_THREADING,
} from "./threading.js";
export {
  appendSessionFooter,
  extractSessionId,
  stripSessionFooter,
} from "./session-footer.js";
export { formatInboxSnapshot, listInbox } from "./inbox.js";
export {
  DEFAULT_THINKING,
  extractAssistantText,
  thinkingEnabledForChannel,
} from "./thinking.js";
export {
  TELEGRAM_BOT_DM_NOTE,
  normalizeTelegramRecipient,
  sendTelegram,
} from "./telegram-send.js";
export {
  DISCORD_CHANNEL_NOTE,
  normalizeDiscordRecipient,
  sendDiscord,
} from "./discord-send.js";
