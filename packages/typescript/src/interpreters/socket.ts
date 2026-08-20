/**
 * Minimal socket abstraction shared by the Discord and Slack runners.
 *
 * `WebSocket` is a global in Bun and Node 22+, so holding a connection open
 * needs no dependency at all. The event-based API is wrapped into an awaitable
 * `receive()` so both runners read as ordinary loops rather than callback
 * pyramids, and so tests can inject a fake without a network.
 *
 * Frames that arrive while nothing is awaiting are queued, never dropped: a
 * platform can push several messages faster than a handler consumes them.
 */

export type GatewaySocket = {
  /** Resolves with the next frame, or rejects when the socket closes. */
  readonly receive: () => Promise<string>
  readonly send: (data: string) => Promise<void>
  readonly close: () => void
}

export type SocketOpener = (url: string) => Promise<GatewaySocket>

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
