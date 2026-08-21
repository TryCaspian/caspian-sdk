/* Telegram self-host webhook, TypeScript. Handlers live in app.ts.

   Unlike the Python SDK, channels.add() here does not register the webhook
   with Telegram yet — run the one curl in the README after starting this.

   Answer Telegram BEFORE running the handler: a response that only completes
   when the handler returns reads as a timeout, and Telegram redelivers the
   same update while a slow handler is still thinking. */
import { Caspian } from "caspian-sdk"
import { register } from "./app.ts"

const token = (process.env.TELEGRAM_BOT_TOKEN ?? "").trim()
const secret = (process.env.TELEGRAM_WEBHOOK_SECRET ?? "").trim()
const port = Number(process.env.PORT ?? "8080")
if (token === "") {
  console.error("Set TELEGRAM_BOT_TOKEN (BotFather → /newbot), then rerun.")
  process.exit(1)
}

const cx = new Caspian()
await cx.channels.add("telegram", {
  via: "self-host",
  bot_token: token,
  ...(secret === "" ? {} : { webhook_secret: secret }),
})
register(cx)

Bun.serve({
  port,
  fetch: async (request) => {
    if (request.method !== "POST") return new Response("ok")
    const body = await request.text()
    const headers: Record<string, string> = {}
    request.headers.forEach((value, key) => {
      headers[key] = value
    })
    void cx.handle("telegram", body, headers).then((results) => {
      for (const result of results) {
        if (!(result as { ok: boolean }).ok) {
          console.error(JSON.stringify((result as { error: unknown }).error))
        }
      }
    })
    return Response.json({ ok: true })
  },
})

console.log(`telegram self-host (ts) on :${port} — point the webhook here`)
