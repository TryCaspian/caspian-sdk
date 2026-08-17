/**
 * Channel → adapter Layer. Interpreters may import adapters; the facade may not.
 */
import * as Layer from "effect/Layer"
import { AdapterPort } from "../core/ports.ts"
import { discordLayer } from "../adapters/discord.ts"
import { emailLayer } from "../adapters/email.ts"
import { imessageLayer } from "../adapters/imessage.ts"
import { linearLayer } from "../adapters/linear.ts"
import { messengerLayer } from "../adapters/messenger.ts"
import { slackLayer } from "../adapters/slack.ts"
import { smsLayer } from "../adapters/sms.ts"
import { telegramLayer } from "../adapters/telegram.ts"
import { voiceLayer } from "../adapters/voice.ts"
import { whatsappLayer } from "../adapters/whatsapp.ts"
import { xLayer } from "../adapters/x.ts"

export const adapterLayerFor = (
  channel: string,
): Layer.Layer<AdapterPort> | undefined => {
  switch (channel) {
    case "telegram":
      return telegramLayer([])
    case "discord":
      return discordLayer([])
    case "slack":
      return slackLayer([])
    case "voice":
      return voiceLayer([])
    case "email":
      return emailLayer([])
    case "sms":
      return smsLayer([])
    case "whatsapp":
      return whatsappLayer([])
    case "messenger":
      return messengerLayer([])
    case "imessage":
      return imessageLayer([])
    case "x":
      return xLayer([])
    case "linear":
      return linearLayer([])
    default:
      return undefined
  }
}
