import { describe, expect, test } from "bun:test";
import {
  escapeAppleScript,
  escapePowerShell,
  formatInboundNotify,
  showOpenCodeToast,
} from "../src/notify.ts";

describe("notify helpers", () => {
  test("escapePowerShell", () => {
    expect(escapePowerShell("it's ok")).toBe("it''s ok");
  });

  test("formatInboundNotify", () => {
    expect(
      formatInboundNotify({
        from: "a@b.com",
        subject: "Hi",
      }),
    ).toEqual({
      title: "Caspian email",
      message: "From a@b.com — Hi",
      variant: "info",
    });
    expect(formatInboundNotify({}).message).toContain("unknown");
    expect(formatInboundNotify({}).message).toContain("(no subject)");
    expect(
      formatInboundNotify({
        channel: "telegram",
        from: "123",
        text: "hello there",
      }),
    ).toEqual({
      title: "Caspian Telegram",
      message: "From 123 — hello there",
      variant: "info",
    });
  });

  test("escapeAppleScript", () => {
    expect(escapeAppleScript('say "hi" \\ ok')).toBe('say \\"hi\\" \\\\ ok');
  });

  test("showOpenCodeToast uses body shape", async () => {
    const calls: unknown[] = [];
    const ok = await showOpenCodeToast(
      {
        tui: {
          showToast: async (args: unknown) => {
            calls.push(args);
          },
        },
      },
      { title: "T", message: "M", variant: "info" },
    );
    expect(ok).toBe(true);
    expect(calls[0]).toEqual({
      body: {
        title: "T",
        message: "M",
        variant: "info",
        duration: 5000,
      },
    });
  });
});
