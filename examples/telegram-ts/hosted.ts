/* Telegram hosted by Caspian, TypeScript. Same handlers as bot.ts; the
   gateway owns inbound. This process never sees a Telegram Update: cx.run()
   polls GET /v1/events and drives the same pipeline.

   Hosted does not mint a BotFather bot. You still pass TELEGRAM_BOT_TOKEN. */
import { Caspian } from "caspian-sdk"
import { register } from "./app.ts"

const token = (process.env.TELEGRAM_BOT_TOKEN ?? "").trim()
const apiKey = (process.env.CASPIAN_API_KEY ?? "").trim()
if (token === "") {
  console.error("Set TELEGRAM_BOT_TOKEN (BotFather → /newbot), then rerun.")
  process.exit(1)
}
if (apiKey === "") {
  console.error("Set CASPIAN_API_KEY, then rerun.")
  process.exit(1)
}

const cx = new Caspian()
await cx.channels.add("telegram", { via: "hosted", bot_token: token })
register(cx)

console.log("hosted telegram (ts) — polling gateway /v1/events")
const results = await cx.run({ apiKey })
for (const result of results) {
  if (!(result as { ok: boolean }).ok) {
    console.error(JSON.stringify((result as { error: unknown }).error))
  }
}
