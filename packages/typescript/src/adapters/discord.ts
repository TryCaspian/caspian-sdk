export {
  decodeThreadId,
  encodeThreadId,
  overlapKey,
  type NativeThread,
} from "./discord/ids.ts"
export { parseDiscordUpdate } from "./discord/parse.ts"
export { planAck, planCommand } from "./discord/execute.ts"
export { discordHttpLayer, discordLayer } from "./discord/layer.ts"
export type { PlannedCall } from "./plan.ts"

import {
  decodeThreadId,
  encodeThreadId,
  overlapKey,
} from "./discord/ids.ts"
import { parseDiscordUpdate } from "./discord/parse.ts"
import { planAck, planCommand } from "./discord/execute.ts"

export const discord = () => ({
  name: "discord" as const,
  parse: parseDiscordUpdate,
  overlapKey,
  encodeThreadId,
  decodeThreadId,
  planCommand,
  planAck,
  capabilities: (): ReadonlyArray<string> => [
    "receive",
    "reply",
    "send",
    "media",
    "buttons",
    "embeds",
    "react",
    "edit",
    "delete",
    "typing",
    "modals",
    "pin",
  ],
  format: (text: string): string => text.replaceAll("`", "\\`"),
  openModal: undefined as never,
})
