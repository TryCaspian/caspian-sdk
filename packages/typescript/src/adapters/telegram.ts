export {
  decodeThreadId,
  encodeThreadId,
  overlapKey,
  type NativeThread,
} from "./telegram/ids.ts"
export { parseTelegramUpdate } from "./telegram/parse.ts"
export {
  planAck,
  planCommand,
  planTurn,
  type TelegramCall,
} from "./telegram/execute.ts"
export { telegramLayer } from "./telegram/layer.ts"
export { executeTurn } from "./turn.ts"

import {
  decodeThreadId,
  encodeThreadId,
  overlapKey,
} from "./telegram/ids.ts"
import { parseTelegramUpdate } from "./telegram/parse.ts"
import { planAck, planCommand, planTurn } from "./telegram/execute.ts"

export const telegram = () => ({
  name: "telegram" as const,
  parse: parseTelegramUpdate,
  overlapKey,
  encodeThreadId,
  decodeThreadId,
  planTurn,
  planCommand,
  planAck,
  capabilities: (): ReadonlyArray<string> => [
    "receive",
    "reply",
    "send",
    "buttons",
  ],
  format: (text: string): string => text,
  openModal: undefined as never,
})
