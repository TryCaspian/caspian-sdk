import { describe, expect, test } from "bun:test";
import { resolveConfig } from "../src/config.ts";
import {
  admits,
  formatEmailPrompt,
  formatInboundPrompt,
  toEnvelope,
} from "../src/email.ts";

const base = {
  id: "msg_1",
  conversationId: "conv_9",
  channel: "email",
  text: "hello",
  subject: "Ping",
  sender: { address: "a@example.com", name: "Ada" },
};

describe("toEnvelope", () => {
  test("maps email into session-keyed envelope", () => {
    const e = toEnvelope(base);
    expect(e.sessionKey).toBe("caspian:email:conv_9");
    expect(e.senderAddress).toBe("a@example.com");
    expect(e.subject).toBe("Ping");
  });

  test("tolerates missing fields", () => {
    const e = toEnvelope({ id: "m", conversationId: "c" });
    expect(e.sessionKey).toBe("caspian:unknown:c");
    expect(e.text).toBe("");
  });
});

describe("admits (email blast-radius boundary)", () => {
  const cfg = resolveConfig({});
  const email = toEnvelope(base);

  test("admits email by default", () => {
    expect(admits(cfg, email)).toBe(true);
  });

  test("rejects non-email in v1", () => {
    const slack = toEnvelope({ ...base, channel: "slack" });
    expect(admits(cfg, slack)).toBe(false);
  });

  test("admits telegram when channels includes telegram", () => {
    const multi = resolveConfig({ channels: ["email", "telegram"] });
    const tg = toEnvelope({
      ...base,
      channel: "telegram",
      sender: { address: "12345", name: "tg-user" },
      subject: null,
    });
    expect(admits(multi, tg)).toBe(true);
  });

  test("rejects telegram when channels is email-only", () => {
    const tg = toEnvelope({
      ...base,
      channel: "telegram",
      sender: { address: "12345" },
    });
    expect(admits(cfg, tg)).toBe(false);
  });

  test("does not apply email identity filters to telegram", () => {
    const locked = resolveConfig({
      channels: ["email", "telegram"],
      email: {
        connectionId: "conn_email_only",
        address: "agent@agents.trycaspianai.com",
        listenConnectionIds: ["conn_email_only"],
        listenAddresses: ["agent@agents.trycaspianai.com"],
      },
    });
    const tg = toEnvelope({
      ...base,
      channel: "telegram",
      connectionId: "conn_tg",
      inboxAddress: null,
      sender: { address: "999" },
    });
    expect(admits(locked, tg)).toBe(true);
    const otherEmail = toEnvelope({
      ...base,
      connectionId: "conn_other",
      inboxAddress: "other@agents.trycaspianai.com",
    });
    expect(admits(locked, otherEmail)).toBe(false);
  });

  test("filters allowFrom", () => {
    const locked = resolveConfig({ allowFrom: ["other@example.com"] });
    expect(admits(locked, email)).toBe(false);
    const ok = resolveConfig({ allowFrom: ["a@example.com"] });
    expect(admits(ok, email)).toBe(true);
  });
});

describe("formatInboundPrompt", () => {
  test("includes from/subject/body for email", () => {
    const text = formatEmailPrompt(toEnvelope(base));
    expect(text).toContain("[caspian:email]");
    expect(text).toContain("a@example.com");
    expect(text).toContain("Subject: Ping");
    expect(text).toContain("hello");
    expect(text).toContain("assistant text only");
  });

  test("frames telegram without email tools / inbox", () => {
    const text = formatInboundPrompt(
      toEnvelope({
        ...base,
        channel: "telegram",
        subject: null,
        sender: { address: "12345", name: "dipanshuhappy" },
        text: "hi from tg",
      }),
    );
    expect(text).toContain("[caspian:telegram]");
    expect(text).toContain("Channel: Telegram (not email)");
    expect(text).toContain("hi from tg");
    expect(text).not.toContain("[caspian:email]");
    expect(text).not.toContain("Subject:");
    expect(text).toContain("Do NOT call caspian_inbox");
  });
});
