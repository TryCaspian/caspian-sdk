/**
 * Ports — Context.Tag declarations. Core imports tags, never Layers.
 */
import * as Context from "effect/Context"
import type * as Effect from "effect/Effect"
import type { Command } from "./commands.ts"
import type { Connection } from "./connection.ts"
import type { AdapterError, CaspianError, DecodeError } from "./errors.ts"
import type { Event } from "./events.ts"

/** JSON body at the inbound edge. Decode in the adapter, not in the kernel. */
export type RawInbound = unknown

export type HostContext = {
  readonly skipped: ReadonlyArray<Event>
}

export type Sent = {
  readonly ok: true
}

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
    readonly ack: (
      event: Event,
      conn: Connection,
    ) => Effect.Effect<Sent, AdapterError>
    readonly execute: (
      command: Command,
      conn: Connection,
    ) => Effect.Effect<Sent, AdapterError>
  }
>() {}
