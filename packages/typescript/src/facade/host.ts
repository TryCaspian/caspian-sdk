import * as Effect from "effect/Effect"
import * as Layer from "effect/Layer"
import type { Command } from "../core/commands.ts"
import { HostError } from "../core/errors.ts"
import type { Action, Event, Message } from "../core/events.ts"
import { HostPort, ThreadStore, type HostContext } from "../core/ports.ts"
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

export const emptyThreadStoreLayer: Layer.Layer<ThreadStore> = Layer.succeed(
  ThreadStore,
  {
    recent: () => Effect.succeed([]),
    getState: () => Effect.succeed(undefined),
    setState: () => Effect.void,
  },
)

export const bHostLayer = (
  handlers: ReadonlyMap<string, BHandler>,
): Layer.Layer<HostPort, never, ThreadStore> =>
  Layer.effect(
    HostPort,
    Effect.gen(function* () {
      const store = yield* ThreadStore
      return {
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
            const thread = makeThread(
              event.thread_id,
              (command) => {
                collected.push(command)
              },
              {
                recent: (limit, current) =>
                  Effect.runPromise(store.recent(event.thread_id, limit, current)),
                getState: (key) =>
                  Effect.runPromise(store.getState(event.thread_id, key)),
                setState: (key, value) =>
                  Effect.runPromise(store.setState(event.thread_id, key, value)),
              },
              event,
            )
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
      }
    }),
  )
