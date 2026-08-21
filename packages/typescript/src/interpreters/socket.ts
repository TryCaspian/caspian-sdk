/**
 * Socket inbound: a held-open connection that feeds RawInbound to a sink.
 *
 * `WebSocket` is a global in Bun and Node 22+. The event-based API is wrapped
 * into awaitable `receive()` so the session reads as an ordinary loop, and so
 * tests can inject a fake without a network.
 *
 * Frames that arrive while nothing is awaiting are queued, never dropped.
 */

import * as Effect from "effect/Effect"
import { socketDriver, type SocketDriver } from "../adapters/socket.ts"
import type { Transport } from "./transport.ts"

export { socketDriver }

export type GatewaySocket = {
  /** Resolves with the next frame, or rejects when the socket closes. */
  readonly receive: () => Promise<string>
  readonly send: (data: string) => Promise<void>
  readonly close: () => void
}

export type SocketOpener = (url: string) => Promise<GatewaySocket>

export type SocketSink = (
  body: unknown,
  headers?: { readonly [key: string]: string },
) => Effect.Effect<ReadonlyArray<unknown>>

/** Fatal: retrying cannot help (bad token). Stop the loop. */
export class SocketFatal extends Error {
  readonly _tag = "SocketFatal"
}

/** Opens a real WebSocket and adapts it to GatewaySocket. */
export const openWebSocket: SocketOpener = (url) =>
  new Promise<GatewaySocket>((resolveOpen, rejectOpen) => {
    const socket = new WebSocket(url)
    const queued: string[] = []
    const waiting: Array<{
      resolve: (value: string) => void
      reject: (error: Error) => void
    }> = []
    let closed: Error | undefined

    const deliver = (frame: string): void => {
      const next = waiting.shift()
      if (next === undefined) {
        queued.push(frame)
        return
      }
      next.resolve(frame)
    }

    const fail = (error: Error): void => {
      closed = error
      while (waiting.length > 0) {
        waiting.shift()?.reject(error)
      }
    }

    socket.addEventListener("message", (event: MessageEvent) => {
      deliver(typeof event.data === "string" ? event.data : String(event.data))
    })
    socket.addEventListener("close", () => fail(new Error("socket closed")))
    socket.addEventListener("error", () => fail(new Error("socket error")))
    socket.addEventListener("open", () =>
      resolveOpen({
        receive: () =>
          new Promise<string>((resolve, reject) => {
            const buffered = queued.shift()
            if (buffered !== undefined) {
              resolve(buffered)
              return
            }
            if (closed !== undefined) {
              reject(closed)
              return
            }
            waiting.push({ resolve, reject })
          }),
        send: async (data: string) => {
          socket.send(data)
        },
        close: () => socket.close(),
      }),
    )
    socket.addEventListener("error", () => rejectOpen(new Error("socket failed to open")))
  })

/** Sleep, used for reconnect backoff. */
export const delay = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms))

const asRecord = (value: unknown): { readonly [key: string]: unknown } =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as { readonly [key: string]: unknown })
    : {}

export class SocketSession {
  #eventsSeen = 0
  #stop = false

  constructor(
    readonly driver: SocketDriver,
    readonly sink: SocketSink,
    readonly options: {
      readonly transport: Transport
      readonly open?: SocketOpener
      readonly log?: (message: string) => void
    },
  ) {}

  stop(): void {
    this.#stop = true
  }

  /** Tests restart a session after maxEvents; not part of the public SDK. */
  restart(): void {
    this.#stop = false
  }

  run(
    options: { readonly maxEvents?: number } = {},
  ): Effect.Effect<ReadonlyArray<unknown>> {
    return Effect.promise(async () => {
      this.#stop = false
      const collected: unknown[] = []
      let backoff = 1000
      while (!this.#stop) {
        try {
          await this.#runOnce(options.maxEvents, collected)
          backoff = 1000
        } catch (error) {
          if (error instanceof SocketFatal) {
            this.options.log?.(`fatal socket error, not retrying: ${String(error)}`)
            return collected
          }
          if (this.#stop) {
            break
          }
          this.options.log?.(
            `socket dropped (${String(error)}); reconnecting in ${backoff / 1000}s`,
          )
          await delay(backoff)
          backoff = Math.min(backoff * 2, 60_000)
        }
      }
      return collected
    })
  }

  async #runOnce(maxEvents: number | undefined, collected: unknown[]): Promise<void> {
    const dispatched = await Effect.runPromise(
      this.options.transport.dispatch(this.driver.openPlan()),
    )
    const opened = this.driver.urlOf(dispatched)
    if (opened.fatal !== undefined && opened.fatal.length > 0) {
      throw new SocketFatal(opened.fatal)
    }
    const url = opened.url ?? ""
    if (url === "") {
      throw new Error("no socket url")
    }
    const open = this.options.open ?? openWebSocket
    const socket = await open(url)
    let cancelHeartbeat: (() => void) | undefined
    try {
      while (!this.#stop) {
        let raw: string
        try {
          raw = await socket.receive()
        } catch {
          throw new Error("socket closed")
        }
        let frame: { readonly [key: string]: unknown }
        try {
          frame = asRecord(JSON.parse(raw) as unknown)
        } catch {
          continue
        }
        const decision = this.driver.onFrame(frame)
        for (const payload of decision.send ?? []) {
          await socket.send(payload)
        }
        if (decision.heartbeatInterval !== undefined) {
          cancelHeartbeat?.()
          cancelHeartbeat = this.#startHeartbeat(socket, decision.heartbeatInterval)
        }
        if (decision.fatal !== undefined && decision.fatal.length > 0) {
          throw new SocketFatal(decision.fatal)
        }
        if (decision.sink !== undefined) {
          this.#eventsSeen += 1
          collected.push(...(await Effect.runPromise(this.sink(decision.sink, {}))))
          if (maxEvents !== undefined && this.#eventsSeen >= maxEvents) {
            this.#stop = true
            return
          }
        }
        if (decision.reconnect === true) {
          return
        }
      }
    } finally {
      cancelHeartbeat?.()
      socket.close()
    }
  }

  #startHeartbeat(socket: GatewaySocket, intervalSec: number): () => void {
    let live = true
    const beat = async (): Promise<void> => {
      await delay(intervalSec * 1000 * Math.random())
      while (live && !this.#stop) {
        try {
          const payload = this.driver.heartbeatPayload()
          if (payload !== undefined) {
            await socket.send(payload)
          }
        } catch {
          return
        }
        await delay(intervalSec * 1000)
      }
    }
    void beat()
    return () => {
      live = false
    }
  }
}
