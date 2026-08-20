export {
  decodeThreadId,
  encodeThreadId,
  overlapKey,
  type NativeThread,
} from "./slack/ids.ts"
export { parseSlackUpdate } from "./slack/parse.ts"
export { planAck, planCommand } from "./slack/execute.ts"
export { slackHttpLayer, slackLayer, verifySlack } from "./slack/layer.ts"
export type { PlannedCall } from "./plan.ts"

import {
  decodeThreadId,
  encodeThreadId,
  overlapKey,
} from "./slack/ids.ts"
import { parseSlackUpdate } from "./slack/parse.ts"
import { planAck, planCommand } from "./slack/execute.ts"

export const slack = () => ({
  name: "slack" as const,
  parse: parseSlackUpdate,
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
    "blocks",
    "react",
    "edit",
    "delete",
    "threading",
    "modals",
    "history",
  ],
  format: (text: string): string =>
    text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;"),
  openModal: undefined as never,
})
