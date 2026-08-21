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
export type { Action, Attachment, Block, Command, Event, Message } from "./core/index.ts"
export type { HandleResult } from "./interpreters/process.ts"
export type { Thread, Stream } from "./facade/thread.ts"
export { AdapterError, DecodeError, ProvisionError } from "./core/errors.ts"
export type { ToolPreset, ToolSet, ToolsOptions } from "./tools/derive.ts"
export {
  hostedHttpLayer,
  hostedLayer,
} from "./interpreters/hosted.ts"
export type { HostedCall, HostedFetch } from "./interpreters/hosted.ts"

/**
 * Hosted mode against the real Caspian gateway.
 *
 * Prefer these over the older `hostedLayer`/`hostedHttpLayer` above, which post
 * every command to /v1/outbox: an endpoint the gateway does not expose.
 */
export {
  DEFAULT_BASE_URL,
  fakeGatewayClient,
  httpGatewayClient,
} from "./hosted/client.ts"
export type { GatewayClient, GatewayRequest, GatewayResponse } from "./hosted/client.ts"
export { gatewayPoller, parseBatch, parseEvent } from "./hosted/inbound.ts"
export type { Poller, PollerOptions } from "./hosted/inbound.ts"
export { conversationOf, toRequest } from "./hosted/outbound.ts"
export { gatewayAdapterLayer } from "./hosted/adapter.ts"
