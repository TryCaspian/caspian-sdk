/**
 * Argv → Intent. Pure Effect. No HTTP.
 *
 * Rejected duplicate send/follow paths fail with the one command to use.
 * Tokenization returns UsageError data — it does not throw.
 */
import { DEFAULT_BASE_URL } from "caspian-sdk"
import * as Effect from "effect/Effect"
import { UsageError } from "./errors.ts"
import type { Intent, Via } from "./intent.ts"

export { UsageError } from "./errors.ts"

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
  "usage: caspian <init|login|channels|call|catalog|threads>"

export const helpText = (): string =>
  `${USAGE}

  caspian init [cli|project|agent] [--open] [--gateway URL] [--force]
  caspian init project [PATH] [--path PATH] [--new]
  caspian login [--open] [--gateway URL]
  caspian channels add <channel> [--via hosted|self-host] [--name NAME]
  caspian channels ls
  caspian catalog
  caspian catalog search <query>
  caspian catalog get <id>
  caspian call <id> [--thread THREAD] [--text TEXT] [--file FILE]
  caspian threads ls [--channel CHANNEL]
  caspian threads tail [THREAD]

init asks cli / project / agent. Pass a kind to skip the question.
init cli stores the key in ~/.caspian/.env — not this repo's .env.
init project asks which folder (default: this one) and writes .env there.
  --new scaffolds a TypeScript/Python SDK app (TODO — not yet).
init agent writes CLI secret, ./.env, and .caspian/AGENT.md.

Hosted jobs (channels add/ls, call, threads) need a key:
  --api-key KEY [--gateway URL]
  or CASPIAN_API_KEY / CASPIAN_BASE_URL
  or caspian init / caspian login
  or sign up at https://dashboard.trycaspianai.com

catalog is the phone book. call is the only phone.
`

const fail = (reason: string): Effect.Effect<never, UsageError> =>
  Effect.fail(new UsageError({ reason }))

type Options = { readonly [key: string]: string | boolean }

const parseTokens = (
  argv: ReadonlyArray<string>,
): Effect.Effect<
  { readonly positional: string[]; readonly options: Options },
  UsageError
> => {
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
      if (name === "inbound" || name === "open" || name === "force" || name === "new") {
        options[name] = true
        continue
      }
      const next = argv[i + 1]
      if (next === undefined || next.startsWith("-")) {
        return fail(`missing value for --${name}`)
      }
      options[name] = next
      i += 1
      continue
    }
    positional.push(tok)
  }
  return Effect.succeed({ positional, options })
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
    return Effect.succeed({
      _tag: "Call",
      id,
      thread_id: str(options, "thread"),
      text: str(options, "text"),
      file: str(options, "file"),
    })
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
    return Effect.succeed({
      _tag: "Login",
      open: flag(options, "open"),
      gateway: str(options, "gateway", DEFAULT_BASE_URL),
    })
  }

  if (noun === "init") {
    const kindTok = positional[1]
    if (
      kindTok !== undefined &&
      kindTok !== "cli" &&
      kindTok !== "project" &&
      kindTok !== "agent"
    ) {
      return fail("use: caspian init [cli|project|agent]")
    }
    const kind = kindTok ?? "ask"
    const posPath = positional[2] ?? ""
    const flagPath = str(options, "path")
    if (kind !== "project" && posPath !== "") {
      return fail("use: caspian init [cli|project|agent]")
    }
    if (posPath !== "" && flagPath !== "" && posPath !== flagPath) {
      return fail("use one path: caspian init project <path>")
    }
    const path = posPath !== "" ? posPath : flagPath
    const fresh = flag(options, "new")
    if (fresh && kind !== "project" && kind !== "ask") {
      return fail("use: caspian init project --new")
    }
    if (fresh && path !== "") {
      return fail("use: caspian init project --new   or a path, not both")
    }
    return Effect.succeed({
      _tag: "Init",
      kind,
      open: flag(options, "open"),
      gateway: str(options, "gateway", DEFAULT_BASE_URL),
      force: flag(options, "force"),
      path,
      fresh,
    })
  }

  return fail(`${USAGE}  (not ${noun})`)
}

export type Parsed = {
  readonly intent: Intent
  readonly api_key: string
  readonly gateway: string
}

export const parseCli = (
  argv: ReadonlyArray<string>,
): Effect.Effect<Parsed, UsageError> =>
  Effect.gen(function* () {
    if (argv.length === 0) return yield* fail(USAGE)

    const head = argv[0]!
    if (head === "connect") {
      return yield* fail("use: caspian channels add")
    }
    if (head === "channels" && argv[1] === "watch") {
      return yield* fail("use: caspian threads tail")
    }
    if (head === "threads" && argv[1] === "reply") {
      return yield* fail("use: caspian call post --thread … --text …")
    }
    if (head !== "help" && !NOUNS.has(head) && !head.includes(".")) {
      return yield* fail("use: caspian call <id>  (caspian catalog search …)")
    }

    const { positional, options } = yield* parseTokens(argv)
    const intent = yield* toIntent(positional, options)
    return {
      intent,
      api_key: str(options, "api-key"),
      gateway: str(options, "gateway"),
    }
  })

export const parseArgv = (
  argv: ReadonlyArray<string>,
): Effect.Effect<Intent, UsageError> =>
  Effect.map(parseCli(argv), (parsed) => parsed.intent)
