export {
  decodeThreadId,
  encodeThreadId,
  overlapKey,
  type NativeThread,
} from "./telegram/ids.ts"
export { parseTelegramUpdate } from "./telegram/parse.ts"
export {
  asHttpJson,
  planAck,
  planCommand,
  planPoll,
  planTurn,
  type TelegramCall,
} from "./telegram/execute.ts"
export { telegramHttpLayer, type TelegramFetch } from "./telegram/http.ts"
export {
  formatTelegram,
  telegramLayer,
  verifyTelegram,
} from "./telegram/layer.ts"
export { executeTurn } from "./turn.ts"

import {
  decodeThreadId,
  encodeThreadId,
  overlapKey,
} from "./telegram/ids.ts"
import { parseTelegramUpdate } from "./telegram/parse.ts"
import { planAck, planCommand, planPoll, planTurn } from "./telegram/execute.ts"
import { formatTelegram, telegramCapabilities } from "./telegram/layer.ts"

export const telegram = () => ({
  name: "telegram" as const,
  parse: parseTelegramUpdate,
  overlapKey,
  encodeThreadId,
  decodeThreadId,
  planTurn,
  planCommand,
  planAck,
  planPoll,
  capabilities: telegramCapabilities,
  format: formatTelegram,
  openModal: undefined as never,
})
