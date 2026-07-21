/** Channel plugin: account lifecycle + inbound pump into the OpenClaw runtime. */
import { createChatChannelPlugin, type ChannelGatewayStartContext } from "openclaw/plugin-sdk/channel-core";
import { CaspianBridge, type CaspianChannelConfig, type InboundEnvelope } from "./bridge.js";
import { caspianMessageAdapter } from "./message-adapter.js";

export const caspianPlugin = createChatChannelPlugin({
  id: "caspian",
  message: caspianMessageAdapter,
  gateway: {
    async startAccount(ctx: ChannelGatewayStartContext) {
      const cfg = (ctx.cfg.channels?.caspian ?? {}) as CaspianChannelConfig;
      const bridge = new CaspianBridge(cfg);
      ctx.log?.info?.("caspian: starting listen loop");
      await bridge.start(async (envelope: InboundEnvelope) => {
        await ctx.runtime.channel.inbound.run({
          channel: "caspian",
          accountId: ctx.accountId,
          raw: envelope,
          adapter: {
            // Envelope is already normalized by the bridge; pass it through.
            ingest: (raw: unknown) => raw,
          },
        });
      }, ctx.abortSignal);
    },
  },
});
