import { describe, expect, test } from "bun:test";
import {
  DISCORD_CHANNEL_NOTE,
  normalizeDiscordRecipient,
  sendDiscord,
} from "../src/discord-send.ts";
import { CircuitBreaker } from "../src/reliability/circuit.ts";
import { Metrics } from "../src/reliability/metrics.ts";
import { resolveDiscordBotToken } from "../src/secrets.ts";
import { resolveConfig } from "../src/config.ts";
import { admits, formatInboundPrompt, toEnvelope } from "../src/email.ts";
import { formatInboundNotify } from "../src/notify.ts";

function deps(overrides: {
  conversations?: Record<string, unknown>[];
  status?: string;
  initiateFail?: boolean;
} = {}) {
  const initiated: Array<{ to: string; text: string }> = [];
  const sent: Array<{ conversationId: string; text: string }> = [];
  return {
    initiated,
    sent,
    port: {
      identity: {},
      listConnections: async () => [
        {
          id: "conn_dc",
          channel: "discord",
          status: overrides.status ?? "active",
          address: "Bot#1234",
        },
      ],
      listConversations: async () => overrides.conversations ?? [],
      listMessages: async () => [],
      sendMessage: async (conversationId: string, text: string) => {
        sent.push({ conversationId, text });
        return { ok: true };
      },
      initiate: async (_id: string, to: string, text: string) => {
        if (overrides.initiateFail) throw new Error("403 missing access");
        initiated.push({ to, text });
        return { conversation_id: "conv_dc", status: "queued" };
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

describe("normalizeDiscordRecipient", () => {
  test("accepts snowflake and <#id> mention form", () => {
    expect(normalizeDiscordRecipient("123456789012345678")).toBe(
      "123456789012345678",
    );
    expect(normalizeDiscordRecipient("<#123456789012345678>")).toBe(
      "123456789012345678",
    );
  });

  test("rejects usernames", () => {
    expect(() => normalizeDiscordRecipient("@someone")).toThrow(/snowflake/i);
  });
});

describe("sendDiscord", () => {
  test("initiates to channel id", async () => {
    const { port, initiated } = deps();
    const result = await sendDiscord(port, {
      to: "123456789012345678",
      body: "hello discord",
    });
    expect(result.mode).toBe("initiate");
    expect(initiated[0]?.to).toBe("123456789012345678");
    expect(result.note).toBe(DISCORD_CHANNEL_NOTE);
  });

  test("rejects inactive connection", async () => {
    const { port } = deps({ status: "pending_oauth" });
    await expect(
      sendDiscord(port, { to: "123456789012345678", body: "hi" }),
    ).rejects.toThrow(/active/i);
  });

  test("surfaces channel note on failure", async () => {
    const { port } = deps({ initiateFail: true });
    await expect(
      sendDiscord(port, { to: "123456789012345678", body: "hi" }),
    ).rejects.toThrow(/Copy Channel ID/i);
  });
});

describe("discord admit + framing + notify", () => {
  test("admits discord when channels includes discord", () => {
    const cfg = resolveConfig({ channels: ["email", "discord"] });
    const env = toEnvelope({
      id: "m",
      conversationId: "c",
      channel: "discord",
      text: "yo",
      sender: { address: "u1", name: "Ada" },
    });
    expect(admits(cfg, env)).toBe(true);
    expect(admits(resolveConfig({}), env)).toBe(false);
  });

  test("frames discord inbound without email tools", () => {
    const text = formatInboundPrompt(
      toEnvelope({
        id: "m",
        conversationId: "c",
        channel: "discord",
        text: "ping",
        sender: { address: "99" },
      }),
    );
    expect(text).toContain("[caspian:discord]");
    expect(text).toContain("Channel: Discord (not email)");
    expect(text).toContain("Do NOT call caspian_inbox");
  });

  test("notify title for discord", () => {
    expect(
      formatInboundNotify({
        channel: "discord",
        from: "Ada",
        text: "hello",
      }).title,
    ).toBe("Caspian Discord");
  });

  test("resolveDiscordBotToken from env", () => {
    const got = resolveDiscordBotToken({
      cwd: "/tmp/nope",
      env: { DISCORD_BOT_TOKEN: " tok " } as NodeJS.ProcessEnv,
      opencodeConfigDir: "/tmp/nope-oc",
    });
    expect(got.value).toBe("tok");
    expect(got.source).toBe("env");
  });
});
