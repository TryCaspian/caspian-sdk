import * as Effect from "effect/Effect"
import * as Layer from "effect/Layer"
import type { Command } from "../core/commands.ts"
import { HostError } from "../core/errors.ts"
import type { Action, Event, Message } from "../core/events.ts"
import { HostPort, type HostContext } from "../core/ports.ts"
import { makeThread, type Thread } from "./thread.ts"

export type BHandler = (
  thread: Thread,
  event: Event,
  ctx: HostContext,
) => void | Promise<void>

export type MessageHandler = (
  thread: Thread,
  message: Message,
  ctx: HostContext,
) => void | Promise<void>

export type ActionHandler = (
  thread: Thread,
  action: Action,
  ctx: HostContext,
) => void | Promise<void>

export const bHostLayer = (
  handlers: ReadonlyMap<string, BHandler>,
): Layer.Layer<HostPort> =>
  Layer.succeed(HostPort, {
    run: (handlerId, event, ctx) =>
      Effect.gen(function* () {
        const fn = handlers.get(handlerId)
        if (fn === undefined) {
          return yield* Effect.fail(
            new HostError({
              reason: `no handler registered for ${handlerId}`,
              handlerId,
            }),
          )
        }
        const collected: Command[] = []
        const thread = makeThread(event.thread_id, (command) => {
          collected.push(command)
        })
        yield* Effect.tryPromise({
          try: () => Promise.resolve(fn(thread, event, ctx)),
          catch: (cause) =>
            new HostError({
              reason: cause instanceof Error ? cause.message : String(cause),
              handlerId,
            }),
        })
        return collected
      }),
  })
