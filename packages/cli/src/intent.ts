/**
 * CLI intents — programs as data. Argv desugars here; plan.ts denotes.
 *
 * Call fields are closed (no string bag). Absent flags are empty strings so
 * the type is total; planIntent rejects combinations the catalog cannot run.
 */
export type Via = "hosted" | "self-host"

export type ChannelsAdd = {
  readonly _tag: "ChannelsAdd"
  readonly channel: string
  readonly via: Via
  readonly display_name: string
  readonly bot_token: string
  readonly webhook_url: string
  readonly inbound: boolean
}

export type ChannelsLs = {
  readonly _tag: "ChannelsLs"
}

export type Call = {
  readonly _tag: "Call"
  readonly id: string
  readonly thread_id: string
  readonly text: string
  readonly file: string
}

export type CatalogList = {
  readonly _tag: "CatalogList"
}

export type CatalogSearch = {
  readonly _tag: "CatalogSearch"
  readonly query: string
}

export type CatalogGet = {
  readonly _tag: "CatalogGet"
  readonly id: string
}

export type ThreadsLs = {
  readonly _tag: "ThreadsLs"
  readonly channel: string
}

export type ThreadsTail = {
  readonly _tag: "ThreadsTail"
  readonly thread_id: string
}

export type Login = {
  readonly _tag: "Login"
  readonly open: boolean
  readonly gateway: string
}

export type InitKind = "cli" | "project" | "agent"

/** Bare `caspian init` asks; a kind skips the question. */
export type InitTarget = InitKind | "ask"

export type Init = {
  readonly _tag: "Init"
  readonly kind: InitTarget
  readonly open: boolean
  readonly gateway: string
  readonly force: boolean
  /** For project: folder to write ./.env into. Empty means ask (default: cwd). */
  readonly path: string
  /** For project: scaffold a new TS/Python SDK app — not implemented yet. */
  readonly fresh: boolean
}

export type Intent =
  | ChannelsAdd
  | ChannelsLs
  | Call
  | CatalogList
  | CatalogSearch
  | CatalogGet
  | ThreadsLs
  | ThreadsTail
  | Login
  | Init
