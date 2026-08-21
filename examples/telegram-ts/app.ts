/* Shared Telegram handlers, TypeScript. The twin of ../telegram/app.py:
   same commands, same rules, so the two SDKs can be checked against each
   other with the same chat. */
import type { Action, Caspian, Message, Thread } from "caspian"

const PHOTO = "https://www.python.org/static/community_logos/python-logo.png"
const STORY = ["Once ", "upon ", "a time, ", "a bot ", "learned ", "to stream."]
const HELP = `\
/help     this menu + buttons
/reply    quote the message you sent
/send     a standalone post (not a reply)
/buttons  callback + url keyboard
/blocks   rich layout (Telegram renders as text)
/media    send a photo by URL
/typing   typing indicator, then a line
/story    stream a reply in place
/react    thumbs-up the message you sent
/pin      pin that message (groups)
/unpin    unpin it
/delete   delete that message
/forward  forward it to this same chat
/initiate cold-DM shape (same send on Telegram)

Anything else is echoed. Photos and files come back as media.
`

const MENU = [
  { label: "buttons", data: "buttons" },
  { label: "story", data: "story" },
  { label: "media", data: "media" },
  { label: "docs", url: "https://core.telegram.org/bots/api" },
]

const help = (thread: Thread) => thread.post(HELP, { actions: MENU })
const buttons = (thread: Thread) => thread.post("callback or url:", { actions: MENU })

const story = async (thread: Thread) => {
  const out = thread.stream({ minChars: 1, throttle: 0.25 })
  for (const chunk of STORY) {
    await out.append(chunk)
  }
  await out.close()
}

const media = (thread: Thread) =>
  thread.sendMedia({ type: "photo", url: PHOTO }, { caption: "by url" })

export function register(cx: Caspian): void {
  /* Specific commands first; echo last. */

  cx.onMessage({ channel: "telegram", command: ["start", "help"] }, async (thread) => {
    await help(thread)
  })

  cx.onMessage({ channel: "telegram", command: "reply" }, async (thread, msg) => {
    await thread.reply((msg as Message).message_id, "quoted.")
  })

  cx.onMessage({ channel: "telegram", command: "send" }, async (thread) => {
    await thread.send("standalone — not a reply to your message.")
  })

  cx.onMessage({ channel: "telegram", command: "buttons" }, async (thread) => {
    await buttons(thread)
  })

  cx.onMessage({ channel: "telegram", command: "blocks" }, async (thread) => {
    await thread.sendBlocks([], {
      text: "SendBlocks — Telegram has no native blocks, so this is text + keyboard.",
      actions: [{ label: "ok", data: "ok" }],
    })
  })

  cx.onMessage({ channel: "telegram", command: "media" }, async (thread) => {
    await media(thread)
  })

  cx.onMessage({ channel: "telegram", command: "typing", overlap: "drop" }, async (thread) => {
    await thread.typing()
    await thread.post("done thinking.")
  })

  cx.onMessage({ channel: "telegram", command: "story" }, async (thread) => {
    await story(thread)
  })

  cx.onMessage({ channel: "telegram", command: "react" }, async (thread, msg) => {
    await thread.react((msg as Message).message_id, "👍")
  })

  cx.onMessage({ channel: "telegram", command: "pin", kind: "group" }, async (thread, msg) => {
    await thread.pin((msg as Message).message_id)
  })

  cx.onMessage({ channel: "telegram", command: "pin", kind: "channel" }, async (thread, msg) => {
    await thread.pin((msg as Message).message_id)
  })

  cx.onMessage({ channel: "telegram", command: "pin", kind: "dm" }, async (thread) => {
    await thread.post("pin needs a group.")
  })

  cx.onMessage({ channel: "telegram", command: "unpin" }, async (thread, msg) => {
    await thread.unpin((msg as Message).message_id)
  })

  cx.onMessage({ channel: "telegram", command: "delete" }, async (thread, msg) => {
    await thread.delete((msg as Message).message_id)
  })

  cx.onMessage({ channel: "telegram", command: "forward" }, async (thread, msg) => {
    await thread.forward(thread.id, (msg as Message).message_id)
  })

  cx.onMessage({ channel: "telegram", command: "initiate" }, async (thread) => {
    await thread.initiate("hello — Initiate maps to sendMessage on Telegram.")
  })

  cx.onAction({ channel: "telegram", data: "buttons" }, async (thread) => {
    await buttons(thread)
  })

  /* Python uses overlap "stream" here; the TS core has no stream policy yet,
     and parallel admits identically. */
  cx.onAction({ channel: "telegram", data: "story", overlap: "parallel" }, async (thread) => {
    await story(thread)
  })

  cx.onAction({ channel: "telegram", data: "media" }, async (thread) => {
    await media(thread)
  })

  cx.onAction({ channel: "telegram" }, async (thread, event) => {
    await thread.post(`you picked ${(event as Action).data}`)
  })

  cx.onMessage({ channel: "telegram" }, async (thread, msg) => {
    const message = msg as Message
    if (message.attachments.length > 0) {
      const att = message.attachments[0]
      await thread.sendMedia(att, { caption: `got ${att.type}` })
      return
    }
    const text = message.text.trim()
    if (text === "") return
    const extra = message.reply_to ? ` (reply to ${message.reply_to})` : ""
    await thread.post(`${text}${extra}`)
  })
}
