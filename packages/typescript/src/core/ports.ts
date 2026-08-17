/**
 * Ports — Context.Tag declarations. Core imports tags, never Layers.
 */
import * as Context from "effect/Context"
import type * as Effect from "effect/Effect"
import type { Command } from "./commands.ts"
import type { Connection } from "./connection.ts"
import type { AdapterError, CaspianError, DecodeError } from "./errors.ts"
import type { Event } from "./events.ts"
import type { ThreadId } from "./ids.ts"
import type { Json, JsonObject } from "./json.ts"

/** JSON body at the inbound edge. Decode in the adapter, not in the kernel. */
export type RawInbound = unknown

export type StreamSink = {
  readonly can_stream: boolean
  readonly emit: (command: Command) => Effect.Effect<string, AdapterError>
}

export type HostContext = {
  readonly skipped: ReadonlyArray<Event>
  readonly sink?: StreamSink
}

export type Sent = {
  readonly ok: true
  readonly message_id: string
  readonly raw: JsonObject
}

export const emptySent = (): Sent => ({
  ok: true,
  message_id: "",
  raw: {},
})

export class HostPort extends Context.Tag("caspian/HostPort")<
  HostPort,
  {
    readonly run: (
      handlerId: string,
      event: Event,
      ctx: HostContext,
    ) => Effect.Effect<ReadonlyArray<Command>, CaspianError>
  }
>() {}

export class AdapterPort extends Context.Tag("caspian/AdapterPort")<
  AdapterPort,
  {
    readonly name: string
    /** Fail with DecodeError when this channel's inbound is malformed. Unknown Telegram update types still succeed as `[]`. */
    readonly parse: (
      raw: RawInbound,
    ) => Effect.Effect<ReadonlyArray<Event>, DecodeError>
    readonly overlapKey: (event: Event) => string
    readonly verify: (raw: RawInbound, conn: Connection) => boolean
    readonly acknowledge: (
      event: Event,
      conn: Connection,
    ) => Effect.Effect<Sent, AdapterError>
    readonly execute: (
      command: Command,
      conn: Connection,
    ) => Effect.Effect<Sent, AdapterError>
    readonly capabilities: () => ReadonlyArray<string>
    readonly format: (text: string) => string
    readonly poll?: (
      offset: number,
      conn: Connection,
    ) => Effect.Effect<Sent, AdapterError>
  }
>() {}

export class ThreadStore extends Context.Tag("caspian/ThreadStore")<
  ThreadStore,
  {
    readonly recent: (
      threadId: ThreadId,
      limit: number,
      current: Event,
    ) => Effect.Effect<ReadonlyArray<Event>>
    readonly getState: (
      threadId: ThreadId,
      key: string,
    ) => Effect.Effect<Json | undefined>
    readonly setState: (
      threadId: ThreadId,
      key: string,
      value: Json,
    ) => Effect.Effect<void>
  }
>() {}
