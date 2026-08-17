import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import { parseTelegramUpdate } from "../src/adapters/telegram.ts"
import type { Sent } from "../src/core/ports.ts"
import { Caspian } from "../src/facade/caspian.ts"
import { RecordingTransport } from "../src/interpreters/transport.ts"

const selfHost = {
  via: "self-host" as const,
  botToken: "TESTTOKEN",
  webhookUrl: "https://example.com/tg",
}

const dmUpdate = {
  update_id: 1,
  message: {
    message_id: 10,
    from: { id: 42, username: "alice" },
    chat: { id: 123, type: "private" },
    text: "hello",
  },
}

class PollTransport extends RecordingTransport {
  override dispatch(sent: Sent) {
    this.dispatched.push(sent)
    if (sent.raw.native === "getUpdates") {
      return Effect.succeed({
        ok: true as const,
        message_id: "",
        raw: { result: [dmUpdate] },
      })
    }
    return Effect.succeed({
      ok: true as const,
      message_id: "rec_1",
      raw: sent.raw,
    })
  }
}

test("cx.handle runs verify → parse → handler → transport", async () => {
  const transport = new RecordingTransport()
  const cx = new Caspian({ transport })
  await cx.channels.add("telegram", selfHost)
  cx.onMessage({ channel: "telegram" }, async (thread, msg) => {
    await thread.post(`echo:${msg.text}`)
  })
  const results = await cx.handle("telegram", dmUpdate)
  expect(results.some((result) => result.ok)).toBe(true)
  expect(transport.dispatched.some((sent) => sent.raw.native === "sendMessage")).toBe(
    true,
  )
})

test("cx.handle without channels.add is ProvisionError", async () => {
  const cx = new Caspian({ dispatch: false })
  const results = await cx.handle("telegram", dmUpdate)
  expect(results).toHaveLength(1)
  expect(results[0]?.ok).toBe(false)
  if (results[0]?.ok === false) {
    expect(results[0].error._tag).toBe("ProvisionError")
  }
})

test("cx.poll feeds getUpdates into the same handle pipeline", async () => {
  const rec = new PollTransport()
  const cx = new Caspian({ transport: rec })
  await cx.channels.add("telegram", selfHost)
  cx.onMessage({ channel: "telegram" }, async (thread, msg) => {
    await thread.post(`echo:${msg.text}`)
  })
  const results = await cx.poll("telegram", { maxIterations: 1 })
  expect(rec.dispatched.some((sent) => sent.raw.native === "getUpdates")).toBe(true)
  expect(rec.dispatched.some((sent) => sent.raw.native === "sendMessage")).toBe(true)
  expect(results.length).toBeGreaterThan(0)
})

test("telegram parse membership, edited, and attachments", () => {
  const join = parseTelegramUpdate({
    update_id: 9,
    message: {
      message_id: 1,
      chat: { id: -100, type: "supergroup" },
      new_chat_members: [{ id: 7 }],
    },
  })
  expect(join[0]?.kind).toBe("member_join")
  if (join[0]?.kind === "member_join") {
    expect(join[0].member).toBe("7")
  }

  const edited = parseTelegramUpdate({
    update_id: 10,
    edited_message: {
      message_id: 4,
      chat: { id: 123, type: "private" },
      from: { id: 42 },
      text: "later",
    },
  })
  expect(edited[0]?.kind).toBe("edited")
  if (edited[0]?.kind === "edited") {
    expect(edited[0].text).toBe("later")
    expect(edited[0].message_id).toBe("4")
  }

  const photo = parseTelegramUpdate({
    update_id: 11,
    message: {
      message_id: 5,
      chat: { id: 123, type: "private" },
      from: { username: "alice" },
      caption: "pic",
      photo: [{ file_id: "small" }, { file_id: "big", file_size: 12 }],
      reply_to_message: { message_id: 2 },
    },
  })
  expect(photo[0]?.kind).toBe("message")
  if (photo[0]?.kind === "message") {
    expect(photo[0].attachments[0]?.type).toBe("photo")
    expect(photo[0].attachments[0]?.file_id).toBe("big")
    expect(photo[0].reply_to).toBe("2")
    expect(photo[0].message_id).toBe("5")
  }
})
