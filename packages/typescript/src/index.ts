/**
 * Caspian TypeScript SDK (rewrite).
 *
 * App code imports this barrel. Do not import src/core from application code.
 */
export { Caspian } from "./facade/caspian.ts"
export type {
  ActionHandler,
  MessageHandler,
} from "./facade/host.ts"
export type { OnActionOptions, OnMessageOptions } from "./facade/options.ts"
export type { Thread } from "./facade/thread.ts"
export type { Action, Command, Event, Message } from "./core/index.ts"
