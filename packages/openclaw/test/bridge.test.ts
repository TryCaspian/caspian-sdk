import { describe, expect, it } from "vitest";
import { admits, resolveConfig, toEnvelope } from "../src/bridge.js";

const inbound = {
  id: "msg_1", conversationId: "conv_9", channel: "slack",
  text: "hello", sender: { address: "U123", name: "Dana" },
};

describe("toEnvelope", () => {
  it("maps a Caspian message into a session-keyed envelope", () => {
    const e = toEnvelope(inbound);
    expect(e.sessionKey).toBe("caspian:slack:conv_9");
    expect(e.messageId).toBe("msg_1");
    expect(e.senderAddress).toBe("U123");
    expect(e.text).toBe("hello");
  });
  it("tolerates missing fields", () => {
    const e = toEnvelope({ id: "m", conversationId: "c" });
    expect(e.sessionKey).toBe("caspian:unknown:c");
    expect(e.text).toBe("");
  });
});

describe("admits", () => {
  const e = toEnvelope(inbound);
  it("admits everything by default", () => {
    expect(admits({}, e)).toBe(true);
  });
  it("filters by sub-channel allowlist", () => {
    expect(admits({ channels: ["telegram"] }, e)).toBe(false);
    expect(admits({ channels: ["slack"] }, e)).toBe(true);
  });
  it("filters by sender allowlist", () => {
    expect(admits({ allowFrom: ["U999"] }, e)).toBe(false);
    expect(admits({ allowFrom: ["U123"] }, e)).toBe(true);
  });
});

describe("resolveConfig", () => {
  it("prefers explicit config over env", () => {
    process.env.COMM_API_KEY = "comm_env_key";
    expect(resolveConfig({ apiKey: "comm_cfg_key" }).apiKey).toBe("comm_cfg_key");
    expect(resolveConfig({}).apiKey).toBe("comm_env_key");
    delete process.env.COMM_API_KEY;
  });
});
