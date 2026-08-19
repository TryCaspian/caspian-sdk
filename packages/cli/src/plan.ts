/**
 * Intent → Plan. The denotation. Pure. No HTTP.
 *
 * argv parses into Intent (syntax). This module says what that Intent *means*:
 * a gateway request, a local catalog value, or a login (device-auth). run.ts
 * is one interpreter of Plan; dry-run is just returning the Plan.
 */
import {
  toRequest,
  type Command,
  type GatewayRequest,
} from "caspian"
import * as Effect from "effect/Effect"
import { getCatalog, loadCatalog, searchCatalog, type CatalogEntry } from "./catalog.ts"
import { UsageError } from "./errors.ts"
import type { Call, ChannelsAdd, Intent } from "./intent.ts"

/** Gateway list endpoints cap limit at 500; stay inside that. */
export const TAIL_LIMIT = 100

const HOSTED_INSTALL = new Set(["slack", "discord", "x", "github"])

export type Json =
  | string
  | number
  | boolean
  | null
  | ReadonlyArray<Json>
  | { readonly [key: string]: Json }

export type GatewayPlan = {
  readonly _tag: "Gateway"
  readonly request: GatewayRequest
  readonly filterChannel: string
}

export type LocalPlan = {
  readonly _tag: "Local"
  readonly value: Json
}

export type LoginPlan = {
  readonly _tag: "Login"
  readonly gateway: string
  readonly open: boolean
}

export type Plan = GatewayPlan | LocalPlan | LoginPlan

const fail = (reason: string): Effect.Effect<never, UsageError> =>
  Effect.fail(new UsageError({ reason }))

const fileName = (path: string): string => {
  const at = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"))
  return at < 0 ? path : path.slice(at + 1)
}

const asCommand = (
  intent: Call,
  entry: CatalogEntry,
): Effect.Effect<Command, UsageError> => {
  if (intent.thread_id === "") {
    return fail("use: caspian call <id> --thread …")
  }
  if (entry.command_tag === "Post") {
    return Effect.succeed({
      tag: "Post",
      thread_id: intent.thread_id,
      text: intent.text,
      actions: [],
      standalone: true,
    } as unknown as Command)
  }
  if (entry.command_tag === "SendMedia") {
    const kind = entry.method === "sendPhoto" ? "photo" : "file"
    return Effect.succeed({
      tag: "SendMedia",
      thread_id: intent.thread_id,
      attachment: {
        type: kind,
        url: intent.file,
        file_id: "",
        filename: fileName(intent.file),
        mime_type: "",
        size_bytes: 0,
        caption: intent.text,
      },
      caption: intent.text,
    } as unknown as Command)
  }
  return fail(`${entry.command_tag} is not available in hosted mode`)
}

const channelsAdd = (intent: ChannelsAdd): Effect.Effect<Plan, UsageError> => {
  if (intent.via === "self-host") {
    if (intent.bot_token === "") {
      return fail(
        `Self-host ${JSON.stringify(intent.channel)} requires --bot-token. ` +
          "Omit --via for hosted (Caspian owns the identity).",
      )
    }
    return Effect.succeed({
      _tag: "Local",
      value: {
        channel: intent.channel,
        via: "self-host",
        webhook_url: intent.webhook_url,
        inbound: intent.inbound,
      },
    })
  }
  const path = HOSTED_INSTALL.has(intent.channel)
    ? `/v1/connections/${intent.channel}/install`
    : `/v1/connections/${intent.channel}`
  const body = intent.display_name
    ? { wait: true, display_name: intent.display_name }
    : { wait: true }
  return Effect.succeed({
    _tag: "Gateway",
    request: { method: "POST", path, body },
    filterChannel: "",
  })
}

const callPlan = (intent: Call): Effect.Effect<Plan, UsageError> =>
  Effect.gen(function* () {
    const entry = yield* getCatalog(intent.id)
    const command = yield* asCommand(intent, entry)
    const request = yield* toRequest(command).pipe(
      Effect.mapError((error) => new UsageError({ reason: error.reason })),
    )
    return {
      _tag: "Gateway" as const,
      request,
      filterChannel: "",
    }
  })

export const planIntent = (intent: Intent): Effect.Effect<Plan, UsageError> => {
  switch (intent._tag) {
    case "ChannelsAdd":
      return channelsAdd(intent)
    case "ChannelsLs":
      return Effect.succeed({
        _tag: "Gateway",
        request: { method: "GET", path: "/v1/connections" },
        filterChannel: "",
      })
    case "CatalogList":
      return Effect.succeed({
        _tag: "Local",
        value: loadCatalog() as unknown as Json,
      })
    case "CatalogSearch":
      return Effect.succeed({
        _tag: "Local",
        value: searchCatalog(intent.query) as unknown as Json,
      })
    case "CatalogGet":
      return Effect.gen(function* () {
        const entry = yield* getCatalog(intent.id)
        return { _tag: "Local" as const, value: entry as unknown as Json }
      })
    case "Call":
      return callPlan(intent)
    case "ThreadsLs":
      return Effect.succeed({
        _tag: "Gateway",
        request: { method: "GET", path: "/v1/conversations" },
        filterChannel: intent.channel,
      })
    case "ThreadsTail":
      return Effect.succeed({
        _tag: "Gateway",
        request: {
          method: "GET",
          path: "/v1/events",
          params: { after_seq: "0", limit: String(TAIL_LIMIT) },
        },
        filterChannel: "",
      })
    case "Login":
      return Effect.succeed({
        _tag: "Login",
        gateway: intent.gateway,
        open: intent.open,
      })
  }
}
