import * as Effect from "effect/Effect"
import type { Command } from "../core/commands.ts"
import type { Connection } from "../core/connection.ts"
import type { AdapterError } from "../core/errors.ts"
import type { Event } from "../core/events.ts"
import { AdapterPort } from "../core/ports.ts"

export const executeTurn = (
  event: Event,
  commands: ReadonlyArray<Command>,
  conn: Connection,
): Effect.Effect<void, AdapterError, AdapterPort> =>
  Effect.gen(function* () {
    const adapter = yield* AdapterPort
    yield* adapter.ack(event)
    yield* Effect.forEach(
      commands,
      (command) => adapter.execute(command, conn),
      { discard: true },
    )
  })
