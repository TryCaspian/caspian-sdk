import { describe, expect, test } from "bun:test";
import { CircuitBreaker } from "../src/reliability/circuit.ts";
import { Metrics } from "../src/reliability/metrics.ts";
import {
  TELEGRAM_BOT_DM_NOTE,
  normalizeTelegramRecipient,
  sendTelegram,
  telegramRecipientsMatch,
} from "../src/telegram-send.ts";

function deps(overrides: {
  conversations?: Record<string, unknown>[];
  messages?: Record<string, unknown>[];
  initiateFail?: boolean;
} = {}) {
  const sent: Array<{ conversationId: string; text: string }> = [];
  const initiated: Array<{ to: string; text: string }> = [];
  return {
    sent,
    initiated,
    port: {
      identity: {},
      listConnections: async () => [
        {
          id: "conn_tg",
          channel: "telegram",
          status: "active",
          address: "@mybot",
        },
      ],
      listConversations: async () => overrides.conversations ?? [],
      listMessages: async () => overrides.messages ?? [],
      sendMessage: async (conversationId: string, text: string) => {
        sent.push({ conversationId, text });
        return { ok: true };
      },
      initiate: async (_id: string, to: string, text: string) => {
        if (overrides.initiateFail) {
          throw new Error("422 initiate not granted");
        }
        initiated.push({ to, text });
        return { conversation_id: "conv_new", status: "queued" };
      },
      circuit: new CircuitBreaker({
        failureThreshold: 5,
        coolDownMs: 1000,
        successThreshold: 1,
      }),
      metrics: new Metrics(),
    },
  };
}

describe("normalizeTelegramRecipient", () => {
  test("adds @ to username", () => {
    expect(normalizeTelegramRecipient("dipanshuhappy")).toBe("@dipanshuhappy");
    expect(normalizeTelegramRecipient("@dipanshuhappy")).toBe("@dipanshuhappy");
  });

  test("keeps numeric chat id", () => {
    expect(normalizeTelegramRecipient("123456789")).toBe("123456789");
  });
});

describe("sendTelegram", () => {
  test("sends on existing conversation matched by sender", async () => {
    const { port, sent } = deps({
      conversations: [{ id: "conv_1" }],
      messages: [
        { sender: { address: "dipanshuhappy" }, text: "hi bot" },
      ],
    });
    const result = await sendTelegram(port, {
      to: "@dipanshuhappy",
      body: "Hello back",
    });
    expect(result.mode).toBe("conversation");
    expect(sent[0]).toEqual({
      conversationId: "conv_1",
      text: "Hello back",
    });
    expect(result.note).toBe(TELEGRAM_BOT_DM_NOTE);
  });

  test("falls back to initiate when no prior chat", async () => {
    const { port, initiated } = deps({ conversations: [] });
    const result = await sendTelegram(port, {
      to: "someone_new",
      body: "Hi",
    });
    expect(result.mode).toBe("initiate");
    expect(initiated[0]?.to).toBe("@someone_new");
    expect(result.note).toContain("message your bot first");
  });

  test("surfaces bot DM note when initiate fails", async () => {
    const { port } = deps({ conversations: [], initiateFail: true });
    await expect(
      sendTelegram(port, { to: "@ghost", body: "Hi" }),
    ).rejects.toThrow(/message your bot first/i);
  });

  test("telegramRecipientsMatch ignores @", () => {
    expect(telegramRecipientsMatch("@A", "a")).toBe(true);
  });
});
