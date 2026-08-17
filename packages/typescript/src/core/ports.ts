/**
 * Ports — Context.Tag declarations. Core imports tags, never Layers.
 */
import * as Context from "effect/Context"
import type * as Effect from "effect/Effect"
import type { Command } from "./commands.ts"
import type { Connection } from "./connection.ts"
import type { AdapterError, CaspianError } from "./errors.ts"
import type { Event } from "./events.ts"

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
    readonly parse: (raw: unknown) => Effect.Effect<ReadonlyArray<Event>>
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
