import * as Effect from "effect/Effect"
import type { App, Overlap, Rule } from "../core/app.ts"
import type { Connection } from "../core/connection.ts"
import { ProvisionError } from "../core/errors.ts"
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
import { PollingRunner } from "../interpreters/polling.ts"
import {
  makeProcessInterpreter,
  type HandleResult,
  type ProcessInterpreter,
  type ProcessOptions,
} from "../interpreters/process.ts"
import { adapterLayerFor } from "../interpreters/registry.ts"
import { SmtpTransport } from "../interpreters/smtp.ts"
import {
  defaultMultiplex,
  type Transport,
} from "../interpreters/transport.ts"
import { VoiceResponder } from "../interpreters/voice.ts"
import { addChannel } from "../provision/add.ts"
import { deriveTools, splitToolsArgs, type ToolsOptions, type ToolSet } from "../tools/derive.ts"
import { desugarOnAction, desugarOnMessage } from "./desugar.ts"
import {
  type ActionHandler,
  type BHandler,
  type MessageHandler,
} from "./host.ts"
import { bHostLayer } from "./host.ts"
import type { OnActionOptions, OnMessageOptions } from "./options.ts"
import type { Thread } from "./thread.ts"

export type CaspianOptions = {
  readonly transport?: Transport
  readonly dispatch?: boolean
}

type InterpreterReady =
  | { readonly ok: false; readonly error: ProvisionError }
  | { readonly ok: true; readonly process: ProcessInterpreter }

export class Caspian {
  readonly #rules: Rule[] = []
  readonly #handlers = new Map<string, BHandler>()
  readonly #connections = new Map<string, Connection>()
  readonly #interpreters = new Map<
    string,
    { readonly process: ProcessInterpreter; readonly ruleCount: number }
  >()
  readonly #transport: Transport | undefined
  #process: ProcessInterpreter | undefined
  #hosted: HostedInterpreter | undefined
  #connectionCount = 0
  #messageCount = 0
  #actionCount = 0
  #useCount = 0

  constructor(options: CaspianOptions = {}) {
    this.#transport =
      options.dispatch === false
        ? undefined
        : (options.transport ??
          defaultMultiplex(fetch, {
            smtp: new SmtpTransport(),
            twiml: new VoiceResponder(),
          }))
  }

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
        ...(options.transport === undefined
          ? {}
          : { transport: options.transport }),
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
    add: async (channel: string, options: unknown): Promise<Connection> => {
      this.#connectionCount += 1
      const id = `conn:${this.#connectionCount}`
      const connection = await Effect.runPromise(addChannel(channel, options, id))
      this.#connections.set(channel, connection)
      this.#interpreters.delete(channel)
      return connection
    },
  }

  handle(
    channel: string,
    body: unknown,
    headers: { readonly [key: string]: string } = {},
  ): Promise<ReadonlyArray<HandleResult>> {
    return this.#withProcess(channel, (process) =>
      process.handleRaw(body, headers),
    )
  }

  poll(
    channel: string,
    options: {
      readonly transport?: Transport
      readonly maxIterations?: number
      readonly offset?: number
    } = {},
  ): Promise<ReadonlyArray<HandleResult>> {
    return this.#withProcess(channel, (process) => {
      const connection = this.#connections.get(channel)
      const adapter = adapterLayerFor(channel)
      if (connection === undefined || adapter === undefined) {
        return Effect.succeed([
          {
            ok: false as const,
            error: new ProvisionError({
              reason: `No adapter for ${JSON.stringify(channel)}`,
            }),
          },
        ])
      }
      const transport = options.transport ?? this.#transport
      if (transport === undefined) {
        return Effect.succeed([
          {
            ok: false as const,
            error: new ProvisionError({
              reason: "poll() requires a transport",
            }),
          },
        ])
      }
      const runner = new PollingRunner(
        adapter,
        connection,
        (rawBody, rawHeaders) => process.handleRaw(rawBody, rawHeaders),
        transport,
        options.offset ?? 0,
      )
      return runner.runForever({
        maxIterations: options.maxIterations ?? 1,
      }) as Effect.Effect<ReadonlyArray<HandleResult>>
    })
  }

  #withProcess(
    channel: string,
    run: (
      process: ProcessInterpreter,
    ) => Effect.Effect<ReadonlyArray<HandleResult>>,
  ): Promise<ReadonlyArray<HandleResult>> {
    return Effect.runPromise(
      this.#ready(channel).pipe(
        Effect.flatMap((ready) => {
          if (!ready.ok) {
            return Effect.succeed<ReadonlyArray<HandleResult>>([
              { ok: false, error: ready.error },
            ])
          }
          return run(ready.process)
        }),
      ),
    )
  }

  #ready(channel: string): Effect.Effect<InterpreterReady> {
    const connection = this.#connections.get(channel)
    if (connection === undefined) {
      return Effect.succeed({
        ok: false,
        error: new ProvisionError({
          reason: `No connection for ${JSON.stringify(channel)}; call channels.add first`,
        }),
      })
    }
    if (connection.via === "hosted") {
      return Effect.succeed({
        ok: false,
        error: new ProvisionError({
          reason: `Inbound for ${JSON.stringify(channel)} is owned by the gateway; use run() or webhooks.caspian`,
        }),
      })
    }
    const adapter = adapterLayerFor(channel)
    if (adapter === undefined) {
      return Effect.succeed({
        ok: false,
        error: new ProvisionError({
          reason: `No adapter for ${JSON.stringify(channel)}`,
        }),
      })
    }
    const cached = this.#interpreters.get(channel)
    if (cached !== undefined && cached.ruleCount === this.#rules.length) {
      return Effect.succeed({
        ok: true,
        process: cached.process,
      })
    }
    const host = bHostLayer(this.#handlers)
    const transport = this.#transport
    const program = this.program
    const interpreters = this.#interpreters
    const ruleCount = this.#rules.length
    return makeProcessInterpreter(program, {
      channelName: channel,
      connection,
      adapter,
      host,
      ...(transport === undefined ? {} : { transport }),
    }).pipe(
      Effect.map((process): InterpreterReady => {
        interpreters.set(channel, { process, ruleCount })
        return { ok: true, process }
      }),
    )
  }

  tools(thread: Thread, options?: ToolsOptions): ToolSet
  tools(options?: ToolsOptions): ToolSet
  tools(
    threadOrOptions?: Thread | ToolsOptions,
    maybeOptions?: ToolsOptions,
  ): ToolSet {
    const { thread, preset } = splitToolsArgs(threadOrOptions, maybeOptions)
    return deriveTools(thread, preset)
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
