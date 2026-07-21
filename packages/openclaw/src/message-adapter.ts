/** Outbound message adapter: minimal honest capabilities (plain text, threaded replies). */
import {
  createMessageReceiptFromOutboundResults,
  defineChannelMessageAdapter,
  type ChannelMessageSendParams,
} from "openclaw/plugin-sdk/channel-outbound";
import { CaspianBridge, type CaspianChannelConfig } from "./bridge.js";

function bridgeFor(cfg: ChannelMessageSendParams["cfg"]): CaspianBridge {
  const channelCfg = (cfg.channels?.caspian ?? {}) as CaspianChannelConfig;
  return new CaspianBridge(channelCfg);
}

export const caspianMessageAdapter = defineChannelMessageAdapter({
  id: "caspian",
  // TODO(contract): no live preview / streaming / media in v1 — declare nothing we can't prove.
  durableFinal: { capabilities: { text: true } },
  send: {
    async text(params: ChannelMessageSendParams) {
      const bridge = bridgeFor(params.cfg);
      try {
        // `to` is a session address: caspian:<sub>:<conversationId>; replyToId is a Caspian message id.
        const result = params.replyToId
          ? await bridge.sendReply(params.replyToId, params.text)
          : await bridge.sendToConversation(String(params.to).split(":").pop() ?? "", params.text);
        return {
          receipt: createMessageReceiptFromOutboundResults({
            results: [{
              channel: "caspian",
              messageId: String((result as { id?: unknown }).id ?? ""),
              conversationId: String((result as { conversation_id?: unknown }).conversation_id ?? ""),
            }],
            kind: "text",
            replyToId: params.replyToId ?? undefined,
          }),
        };
      } finally {
        bridge.stop();
      }
    },
  },
});
