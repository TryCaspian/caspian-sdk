/**
 * Ports — Context.Tag declarations. Core imports tags, never Layers.
 */
import * as Context from "effect/Context"
import type * as Effect from "effect/Effect"
import type { Command } from "./commands.ts"
import type { CaspianError } from "./errors.ts"
import type { Event } from "./events.ts"

export type HostContext = {
  readonly skipped: ReadonlyArray<Event>
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
