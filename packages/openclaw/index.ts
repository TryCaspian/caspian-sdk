// openclaw-caspian entrypoint — registers the Caspian channel with OpenClaw.
import { defineChannelPluginEntry } from "openclaw/plugin-sdk/channel-core";
import { caspianPlugin } from "./src/channel.js";

export default defineChannelPluginEntry({
  id: "caspian",
  name: "Caspian",
  description:
    "One agent identity across every channel Caspian connects — iMessage (no Mac), WhatsApp Business, phone/SMS, email, Slack, Discord, Telegram, Instagram.",
  plugin: caspianPlugin,
});
