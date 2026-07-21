/**
 * Minimal ambient declarations for the OpenClaw plugin SDK subpaths this plugin
 * imports. The real `openclaw` package is a large peer dependency that is not
 * installed in this repo, so these declarations exist ONLY to typecheck the thin
 * host-facing glue files (index.ts, setup-entry.ts, src/channel.ts,
 * src/message-adapter.ts).
 *
 * Every shape here mirrors documented OpenClaw APIs:
 *   - https://docs (docs/plugins/sdk-entrypoints.md)      defineChannelPluginEntry, defineSetupPluginEntry
 *   - https://docs (docs/plugins/sdk-channel-plugins.md)  createChatChannelPlugin, gateway.startAccount
 *   - https://docs (docs/plugins/sdk-channel-outbound.md) defineChannelMessageAdapter, createMessageReceiptFromOutboundResults
 *   - https://docs (docs/plugins/sdk-channel-inbound.md)  runtime.channel.inbound.run
 *
 * They are intentionally loose (structural, with index signatures) so the real
 * SDK types win at install time and this file never masks a genuine type error
 * in the pure bridge (src/bridge.ts), which imports none of these modules.
 */

declare module "openclaw/plugin-sdk/channel-core" {
  /** The resolved OpenClaw config passed to channel hooks. */
  export interface OpenClawConfig {
    channels?: Record<string, any>;
    [key: string]: unknown;
  }

  /** Documented runtime surface used from inside gateway.startAccount. */
  export interface OpenClawChannelRuntime {
    channel: {
      inbound: {
        // docs/plugins/sdk-channel-inbound.md — runtime.channel.inbound.run(...)
        run(input: {
          channel: string;
          accountId: string;
          raw: unknown;
          adapter: {
            ingest: (raw: unknown) => unknown;
            resolveTurn?: (...args: unknown[]) => unknown;
          };
        }): Promise<void>;
      };
    };
    [key: string]: unknown;
  }

  /** Context handed to `gateway.startAccount` for a long-running receive loop. */
  export interface ChannelGatewayStartContext {
    cfg: OpenClawConfig;
    accountId: string;
    abortSignal: AbortSignal;
    runtime: OpenClawChannelRuntime;
    setStatus?: (next: unknown) => void;
    log?: {
      info?: (message: string) => void;
      warn?: (message: string, err?: unknown) => void;
      error?: (message: string, err?: unknown) => void;
    };
  }

  export function createChatChannelPlugin<T = unknown>(options: Record<string, unknown>): T;

  export function defineChannelPluginEntry(entry: {
    id: string;
    name: string;
    description: string;
    plugin: unknown;
    configSchema?: unknown;
    setRuntime?: (runtime: unknown) => void;
    registerCliMetadata?: (api: unknown) => void;
    registerFull?: (api: unknown) => void;
  }): unknown;

  export function defineSetupPluginEntry(plugin: unknown): unknown;
}

declare module "openclaw/plugin-sdk/channel-outbound" {
  /** Params passed to a message adapter's `send.text` (and `send.media`). */
  export interface ChannelMessageSendParams {
    cfg: { channels?: Record<string, any>; [key: string]: unknown };
    to: string;
    text: string;
    accountId?: string | null;
    replyToId?: string | null;
    threadId?: string | number | null;
    mediaUrl?: string | null;
    signal?: AbortSignal;
  }

  export interface MessageReceipt {
    [key: string]: unknown;
  }

  export function createMessageReceiptFromOutboundResults(input: {
    results: Array<{ channel: string; messageId?: string; conversationId?: string }>;
    kind: string;
    threadId?: string;
    replyToId?: string;
  }): MessageReceipt;

  export function defineChannelMessageAdapter(adapter: {
    id: string;
    durableFinal: { capabilities: Record<string, boolean> };
    send: {
      text: (params: ChannelMessageSendParams) => Promise<{ receipt: MessageReceipt }>;
      media?: (params: ChannelMessageSendParams) => Promise<{ receipt: MessageReceipt }>;
    };
  }): unknown;
}
