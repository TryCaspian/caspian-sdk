/**
 * Channel catalog — one vocabulary for names, inbound, bot-token, capabilities.
 * Keep in lockstep with caspian/catalog.py.
 */
export const Capability = {
  RECEIVE: "receive",
  REPLY: "reply",
  SEND: "send",
  MEDIA: "media",
  BUTTONS: "buttons",
  BLOCKS: "blocks",
  EMBEDS: "embeds",
  EDIT: "edit",
  DELETE: "delete",
  REACT: "react",
  TYPING: "typing",
  PIN: "pin",
  FORWARD: "forward",
  THREADING: "threading",
  MEMBERSHIP: "membership",
  MODALS: "modals",
  HISTORY: "history",
  DM: "dm",
  VOICE: "voice",
  TTS: "tts",
  RECEIPTS: "receipts",
} as const

export type Capability = (typeof Capability)[keyof typeof Capability]
export type ChannelName =
  | "telegram"
  | "slack"
  | "discord"
  | "email"
  | "whatsapp"
  | "messenger"
  | "sms"
  | "voice"
  | "imessage"
  | "x"
  | "linear"

export type InboundMode = "webhook" | "socket" | "poll"
export type BotTokenWhen = "always" | "self-host"

export type ChannelRow = {
  readonly inbound: ReadonlySet<InboundMode>
  readonly botToken: BotTokenWhen
  readonly capabilities: ReadonlySet<Capability>
  readonly socket?: "discord" | "slack"
}

const caps = (...items: Capability[]): ReadonlySet<Capability> => new Set(items)

export const CHANNELS: { readonly [K in ChannelName]: ChannelRow } = {
  telegram: {
    inbound: new Set(["webhook", "poll"]),
    botToken: "always",
    capabilities: caps(
      Capability.RECEIVE,
      Capability.REPLY,
      Capability.SEND,
      Capability.MEDIA,
      Capability.BUTTONS,
      Capability.EDIT,
      Capability.DELETE,
      Capability.REACT,
      Capability.TYPING,
      Capability.PIN,
      Capability.FORWARD,
      Capability.THREADING,
      Capability.MEMBERSHIP,
    ),
  },
  slack: {
    inbound: new Set(["webhook", "socket"]),
    botToken: "self-host",
    socket: "slack",
    capabilities: caps(
      Capability.RECEIVE,
      Capability.REPLY,
      Capability.SEND,
      Capability.MEDIA,
      Capability.BUTTONS,
      Capability.BLOCKS,
      Capability.REACT,
      Capability.EDIT,
      Capability.DELETE,
      Capability.THREADING,
      Capability.MODALS,
      Capability.HISTORY,
    ),
  },
  discord: {
    inbound: new Set(["socket"]),
    botToken: "self-host",
    socket: "discord",
    capabilities: caps(
      Capability.RECEIVE,
      Capability.REPLY,
      Capability.SEND,
      Capability.MEDIA,
      Capability.BUTTONS,
      Capability.EMBEDS,
      Capability.REACT,
      Capability.EDIT,
      Capability.DELETE,
      Capability.TYPING,
      Capability.MODALS,
      Capability.PIN,
    ),
  },
  email: {
    inbound: new Set(["webhook"]),
    botToken: "self-host",
    capabilities: caps(
      Capability.RECEIVE,
      Capability.REPLY,
      Capability.SEND,
      Capability.MEDIA,
      Capability.THREADING,
    ),
  },
  whatsapp: {
    inbound: new Set(["webhook"]),
    botToken: "self-host",
    capabilities: caps(
      Capability.RECEIVE,
      Capability.REPLY,
      Capability.SEND,
      Capability.MEDIA,
      Capability.BUTTONS,
      Capability.REACT,
      Capability.RECEIPTS,
    ),
  },
  messenger: {
    inbound: new Set(["webhook"]),
    botToken: "self-host",
    capabilities: caps(
      Capability.RECEIVE,
      Capability.REPLY,
      Capability.SEND,
      Capability.MEDIA,
      Capability.BUTTONS,
      Capability.TYPING,
    ),
  },
  sms: {
    inbound: new Set(["webhook"]),
    botToken: "self-host",
    capabilities: caps(
      Capability.RECEIVE,
      Capability.REPLY,
      Capability.SEND,
      Capability.MEDIA,
    ),
  },
  voice: {
    inbound: new Set(["webhook"]),
    botToken: "self-host",
    capabilities: caps(
      Capability.RECEIVE,
      Capability.SEND,
      Capability.VOICE,
      Capability.TTS,
    ),
  },
  imessage: {
    inbound: new Set(["webhook"]),
    botToken: "self-host",
    capabilities: caps(
      Capability.RECEIVE,
      Capability.REPLY,
      Capability.SEND,
      Capability.MEDIA,
    ),
  },
  x: {
    inbound: new Set(["webhook"]),
    botToken: "self-host",
    capabilities: caps(
      Capability.RECEIVE,
      Capability.SEND,
      Capability.REPLY,
      Capability.DM,
    ),
  },
  linear: {
    inbound: new Set(["webhook"]),
    botToken: "self-host",
    capabilities: caps(
      Capability.RECEIVE,
      Capability.REPLY,
      Capability.SEND,
      Capability.THREADING,
    ),
  },
}

export const capabilitiesOf = (channel: ChannelName): ReadonlyArray<string> =>
  [...CHANNELS[channel].capabilities]

export const socketChannels = (): ReadonlyArray<ChannelName> =>
  (Object.keys(CHANNELS) as ChannelName[]).filter(
    (name) => CHANNELS[name].socket !== undefined,
  )
