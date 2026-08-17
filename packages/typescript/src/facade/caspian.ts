import * as Effect from "effect/Effect"
import type { App, Overlap, Rule } from "../core/app.ts"
import type { Connection } from "../core/connection.ts"
import type { Predicate } from "../core/predicates.ts"
import {
  makeHostedInterpreter,
  type HostedInterpreter,
  type HostedOptions,
} from "../interpreters/hosted.ts"
import {
  makeMemoryInterpreter,
  type MemoryInterpreter,
} from "../interpreters/memory.ts"
import {
  makeProcessInterpreter,
  type ProcessInterpreter,
  type ProcessOptions,
} from "../interpreters/process.ts"
import { addChannel } from "../provision/add.ts"
import { desugarOnAction, desugarOnMessage } from "./desugar.ts"
import {
  type ActionHandler,
  type BHandler,
  type MessageHandler,
} from "./host.ts"
import { bHostLayer } from "./host.ts"
import type { OnActionOptions, OnMessageOptions } from "./options.ts"

export class Caspian {
  readonly #rules: Rule[] = []
  readonly #handlers = new Map<string, BHandler>()
  #process: ProcessInterpreter | undefined
  #hosted: HostedInterpreter | undefined
  #connectionCount = 0
  #messageCount = 0
  #actionCount = 0
  #useCount = 0

  get program(): App {
    return { rules: [...this.#rules] }
  }

  onMessage(handler: MessageHandler): this
  onMessage(options: OnMessageOptions, handler: MessageHandler): this
  onMessage(
    optionsOrHandler: OnMessageOptions | MessageHandler,
    maybeHandler?: MessageHandler,
  ): this {
    const { options, handler } = splitMessage(optionsOrHandler, maybeHandler)
    const handlerId = `onMessage:${this.#messageCount}`
    this.#messageCount += 1
    this.#rules.push(desugarOnMessage(options, handlerId))
    this.#handlers.set(handlerId, asMessageHandler(handler))
    return this
  }

  onAction(handler: ActionHandler): this
  onAction(options: OnActionOptions, handler: ActionHandler): this
  onAction(
    optionsOrHandler: OnActionOptions | ActionHandler,
    maybeHandler?: ActionHandler,
  ): this {
    const { options, handler } = splitAction(optionsOrHandler, maybeHandler)
    const handlerId = `onAction:${this.#actionCount}`
    this.#actionCount += 1
    this.#rules.push(desugarOnAction(options, handlerId))
    this.#handlers.set(handlerId, asActionHandler(handler))
    return this
  }

  use(
    input: { readonly predicate: Predicate; readonly overlap?: Overlap },
    handler: BHandler,
  ): this {
    const handlerId = `use:${this.#useCount}`
    this.#useCount += 1
    this.#rules.push({
      predicate: input.predicate,
      overlap: input.overlap ?? { policy: "queue", bound: 16 },
      handler_id: handlerId,
    })
    this.#handlers.set(handlerId, handler)
    return this
  }

  interpret(
    options: { readonly channel?: string } = {},
  ): Promise<MemoryInterpreter> {
    return Effect.runPromise(
      makeMemoryInterpreter(this.program, {
        channelName: options.channel ?? "",
        host: bHostLayer(this.#handlers),
      }),
    )
  }

  listen(
    options: Omit<ProcessOptions, "host" | "channelName"> & {
      readonly channel?: string
    },
  ): Promise<void> {
    return Effect.runPromise(
      makeProcessInterpreter(this.program, {
        channelName: options.channel ?? "",
        connection: options.connection,
        adapter: options.adapter,
        host: bHostLayer(this.#handlers),
        ...(options.secretToken === undefined
          ? {}
          : { secretToken: options.secretToken }),
        ...(options.secretHeader === undefined
          ? {}
          : { secretHeader: options.secretHeader }),
      }).pipe(
        Effect.tap((process) =>
          Effect.sync(() => {
            this.#process = process
          }),
        ),
        Effect.asVoid,
      ),
    )
  }

  run(
    options: Omit<HostedOptions, "host" | "channelName"> & {
      readonly channel?: string
    },
  ): Promise<void> {
    return Effect.runPromise(
      makeHostedInterpreter(this.program, {
        channelName: options.channel ?? "",
        connection: options.connection,
        adapter: options.adapter,
        webhookSecret: options.webhookSecret,
        host: bHostLayer(this.#handlers),
      }).pipe(
        Effect.tap((hosted) =>
          Effect.sync(() => {
            this.#hosted = hosted
          }),
        ),
        Effect.asVoid,
      ),
    )
  }

  readonly webhooks = {
    telegram: (request: Request): Promise<Response> => {
      const process = this.#process
      if (process === undefined) {
        return Promise.reject(new Error("listen() before webhooks.telegram"))
      }
      return Effect.runPromise(
        process.handle(request, {
          secretHeader: "X-Telegram-Bot-Api-Secret-Token",
        }),
      )
    },
    caspian: (request: Request): Promise<Response> => {
      const hosted = this.#hosted
      if (hosted === undefined) {
        return Promise.reject(new Error("run() before webhooks.caspian"))
      }
      return Effect.runPromise(
        hosted.handle(request, {
          signatureHeader: "X-Caspian-Signature",
        }),
      )
    },
  }

  readonly channels = {
    add: (channel: string, options: unknown): Promise<Connection> => {
      this.#connectionCount += 1
      const id = `conn:${this.#connectionCount}`
      return Effect.runPromise(addChannel(channel, options, id))
    },
  }
}

const asMessageHandler = (fn: MessageHandler): BHandler =>
  async (thread, event, ctx) => {
    if (event.kind !== "message") {
      throw new Error(`onMessage handler received ${event.kind}`)
    }
    await fn(thread, event, ctx)
  }

const asActionHandler = (fn: ActionHandler): BHandler =>
  async (thread, event, ctx) => {
    if (event.kind !== "action") {
      throw new Error(`onAction handler received ${event.kind}`)
    }
    await fn(thread, event, ctx)
  }

const splitMessage = (
  optionsOrHandler: OnMessageOptions | MessageHandler,
  maybeHandler: MessageHandler | undefined,
): { options: unknown; handler: MessageHandler } => {
  if (typeof optionsOrHandler === "function") {
    return { options: {}, handler: optionsOrHandler }
  }
  if (maybeHandler === undefined) {
    throw new TypeError("handler is required")
  }
  return { options: optionsOrHandler, handler: maybeHandler }
}

const splitAction = (
  optionsOrHandler: OnActionOptions | ActionHandler,
  maybeHandler: ActionHandler | undefined,
): { options: unknown; handler: ActionHandler } => {
  if (typeof optionsOrHandler === "function") {
    return { options: {}, handler: optionsOrHandler }
  }
  if (maybeHandler === undefined) {
    throw new TypeError("handler is required")
  }
  return { options: optionsOrHandler, handler: maybeHandler }
}
