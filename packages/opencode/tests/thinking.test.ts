import { describe, expect, test } from "bun:test";
import { resolveConfig } from "../src/config.ts";
import {
  extractAssistantText,
  thinkingEnabledForChannel,
} from "../src/thinking.ts";

describe("thinking config", () => {
  test("defaults to disabled globally", () => {
    const cfg = resolveConfig({});
    expect(cfg.thinking.enabled).toBe(false);
    expect(thinkingEnabledForChannel(cfg.thinking, "telegram")).toBe(false);
    expect(thinkingEnabledForChannel(cfg.thinking, "email")).toBe(false);
  });

  test("per-channel override wins", () => {
    const cfg = resolveConfig({
      thinking: {
        enabled: false,
        channels: { telegram: true, email: false },
      },
    });
    expect(thinkingEnabledForChannel(cfg.thinking, "telegram")).toBe(true);
    expect(thinkingEnabledForChannel(cfg.thinking, "email")).toBe(false);
    expect(thinkingEnabledForChannel(cfg.thinking, "slack")).toBe(false);
  });

  test("global enabled applies when channel unset", () => {
    const cfg = resolveConfig({ thinking: { enabled: true } });
    expect(thinkingEnabledForChannel(cfg.thinking, "telegram")).toBe(true);
  });
});

describe("extractAssistantText", () => {
  const result = {
    parts: [
      {
        type: "reasoning",
        text: "The user sent a greeting via Telegram…",
      },
      { type: "text", text: "Hey Dipanshu! How’s it going?" },
    ],
  };

  test("strips reasoning by default", () => {
    expect(extractAssistantText(result)).toBe("Hey Dipanshu! How’s it going?");
  });

  test("includes reasoning when requested", () => {
    const text = extractAssistantText(result, { includeThinking: true });
    expect(text).toContain("(thinking)");
    expect(text).toContain("greeting via Telegram");
    expect(text).toContain("Hey Dipanshu!");
  });

  test("does not treat reasoning as text via loose .text check", () => {
    expect(
      extractAssistantText({
        parts: [{ type: "reasoning", text: "secret thoughts" }],
      }),
    ).toBe("");
  });
});
