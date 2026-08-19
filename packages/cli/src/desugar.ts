/**
 * Argv → Intent. Pure Effect. No HTTP.
 *
 * Rejected duplicate send/follow paths fail with the one command to use.
 */
import * as Effect from "effect/Effect"
import * as Schema from "effect/Schema"
import type { Intent, Via } from "./intent.ts"

export class UsageError extends Schema.TaggedError<UsageError>()("UsageError", {
  reason: Schema.String,
}) {}

const NOUNS = new Set([
  "channels",
  "call",
  "catalog",
  "threads",
  "login",
  "init",
  "help",
])

export const USAGE =
  "usage: caspian <channels|call|catalog|threads|login|init>"

export const helpText = (): string =>
  `${USAGE}

  caspian login
  caspian init [--gateway URL] [--name NAME]
  caspian channels add <channel> [--via hosted|self-host] [--name NAME]
  caspian channels ls
  caspian catalog
  caspian catalog search <query>
  caspian catalog get <id>
  caspian call <id> [--thread THREAD] [--text TEXT] [--file FILE]
  caspian threads ls [--channel CHANNEL]
  caspian threads tail [THREAD]

catalog is the phone book. call is the only phone.
`

const fail = (reason: string): Effect.Effect<never, UsageError> =>
  Effect.fail(new UsageError({ reason }))

type Options = { readonly [key: string]: string | boolean }

const parseTokens = (
  argv: ReadonlyArray<string>,
): { readonly positional: string[]; readonly options: Options } => {
  const positional: string[] = []
  const options: { [key: string]: string | boolean } = {}
  for (let i = 0; i < argv.length; i++) {
    const tok = argv[i]!
    if (tok === "--help" || tok === "-h") {
      options["help"] = true
      continue
    }
    if (tok.startsWith("--")) {
      const name = tok.slice(2)
      if (name === "no-inbound") {
        options["inbound"] = false
        continue
      }
      if (name === "inbound" || name === "open" || name === "force") {
        options[name] = true
        continue
      }
      const next = argv[i + 1]
      if (next === undefined || next.startsWith("-")) {
        throw new UsageError({ reason: `missing value for --${name}` })
      }
      options[name] = next
      i += 1
      continue
    }
    positional.push(tok)
  }
  return { positional, options }
}

const str = (options: Options, key: string, fallback = ""): string => {
  const value = options[key]
  return typeof value === "string" ? value : fallback
}

const flag = (options: Options, key: string): boolean => options[key] === true

const viaOf = (options: Options): Via | undefined => {
  const via = str(options, "via", "hosted")
  if (via === "hosted" || via === "self-host") return via
  return undefined
}

const toIntent = (
  positional: ReadonlyArray<string>,
  options: Options,
): Effect.Effect<Intent, UsageError> => {
  const noun = positional[0]
  if (noun === undefined) return fail(USAGE)
  if (flag(options, "help") || noun === "help") {
    return fail(helpText())
  }

  if (noun === "channels") {
    const verb = positional[1]
    if (verb === "add") {
      const channel = positional[2]
      if (channel === undefined) return fail("use: caspian channels add <channel>")
      const via = viaOf(options)
      if (via === undefined) {
        return fail('via must be "hosted" or "self-host"')
      }
      const inbound = options["inbound"]
      return Effect.succeed({
        _tag: "ChannelsAdd",
        channel,
        via,
        display_name: str(options, "name"),
        bot_token: str(options, "bot-token"),
        webhook_url: str(options, "webhook-url"),
        inbound: inbound === false ? false : true,
      })
    }
    if (verb === "ls") {
      return Effect.succeed({ _tag: "ChannelsLs" })
    }
    return fail("use: caspian channels add|ls")
  }

  if (noun === "call") {
    const id = positional[1]
    if (id === undefined) return fail("use: caspian call <id>")
    const args: { [key: string]: string } = {}
    const thread = str(options, "thread")
    const text = str(options, "text")
    const file = str(options, "file")
    if (thread !== "") args["thread_id"] = thread
    if (text !== "") args["text"] = text
    if (file !== "") args["file"] = file
    return Effect.succeed({ _tag: "Call", id, args })
  }

  if (noun === "catalog") {
    const verb = positional[1]
    if (verb === undefined) return Effect.succeed({ _tag: "CatalogList" })
    if (verb === "search") {
      const query = positional[2]
      if (query === undefined) return fail("use: caspian catalog search <query>")
      return Effect.succeed({ _tag: "CatalogSearch", query })
    }
    if (verb === "get") {
      const id = positional[2]
      if (id === undefined) return fail("use: caspian catalog get <id>")
      return Effect.succeed({ _tag: "CatalogGet", id })
    }
    return fail("use: caspian catalog [search|get]")
  }

  if (noun === "threads") {
    const verb = positional[1]
    if (verb === "ls") {
      return Effect.succeed({
        _tag: "ThreadsLs",
        channel: str(options, "channel"),
      })
    }
    if (verb === "tail") {
      return Effect.succeed({
        _tag: "ThreadsTail",
        thread_id: positional[2] ?? "",
      })
    }
    return fail("use: caspian threads ls|tail")
  }

  if (noun === "login") {
    return Effect.succeed({ _tag: "Login", open: flag(options, "open") })
  }

  if (noun === "init") {
    return Effect.succeed({
      _tag: "Init",
      gateway: str(options, "gateway", "https://api.trycaspianai.com"),
      name: str(options, "name", "sandbox"),
      force: flag(options, "force"),
    })
  }

  return fail(`${USAGE}  (not ${noun})`)
}

export const parseArgv = (
  argv: ReadonlyArray<string>,
): Effect.Effect<Intent, UsageError> => {
  if (argv.length === 0) return fail(USAGE)

  const head = argv[0]!
  if (head === "connect") {
    return fail("use: caspian channels add")
  }
  if (head === "channels" && argv[1] === "watch") {
    return fail("use: caspian threads tail")
  }
  if (head === "threads" && argv[1] === "reply") {
    return fail("use: caspian call post --thread … --text …")
  }
  if (head !== "help" && !NOUNS.has(head) && !head.includes(".")) {
    return fail("use: caspian call <id>  (caspian catalog search …)")
  }

  try {
    const { positional, options } = parseTokens(argv)
    return toIntent(positional, options)
  } catch (error) {
    if (error instanceof UsageError) return Effect.fail(error)
    return fail(error instanceof Error ? error.message : String(error))
  }
}
