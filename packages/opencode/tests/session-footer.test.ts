import { describe, expect, test } from "bun:test";
import { resolveConfig } from "../src/config.ts";
import { formatEmailPrompt, toEnvelope } from "../src/email.ts";
import { handleInbound, type PipelineDeps } from "../src/pipeline.ts";
import { CircuitBreaker } from "../src/reliability/circuit.ts";
import { CapacityLimiter } from "../src/reliability/limits.ts";
import { Metrics } from "../src/reliability/metrics.ts";
import {
  appendSessionFooter,
  extractSessionId,
  stripSessionFooter,
} from "../src/session-footer.ts";
import { MemorySessionMap } from "../src/session-map.ts";
import { formatOutboundText } from "../src/outbound.ts";
import type { OpenCodePort } from "../src/ports.ts";

describe("session footer helpers", () => {
  test("append / extract / strip", () => {
    const stamped = appendSessionFooter("Hello alive", "ses_abc123");
    expect(stamped).toContain("caspian-opencode:session=ses_abc123");
    expect(extractSessionId(stamped)).toBe("ses_abc123");
    expect(stripSessionFooter(stamped)).toBe("Hello alive");
  });

  test("extract from quoted Gmail-style reply", () => {
    const quoted = [
      "Thanks!",
      "",
      "> On Sat someone wrote:",
      "> hello",
      "> ---",
      "> caspian-opencode:session=ses_0691abc",
    ].join("\n");
    expect(extractSessionId(quoted)).toBe("ses_0691abc");
  });
});

describe("outbound stamps footer", () => {
  test("formatOutboundText includes session when enabled", () => {
    const text = formatOutboundText({
      to: "a@b.com",
      body: "I am alive",
      openCodeSessionId: "ses_tui1",
      sessionFooter: true,
    });
    expect(text).toContain("I am alive");
    expect(text).toContain("caspian-opencode:session=ses_tui1");
  });
});

describe("inbound routes by footer session", () => {
  test("reuses stamped OpenCode session instead of creating", async () => {
    const prompts: Array<{ id: string; text: string }> = [];
    const replies: string[] = [];
    let creates = 0;
    const sessions = new MemorySessionMap();
    const opencode: OpenCodePort = {
      session: {
        async create() {
          creates += 1;
          return { id: "ses_should_not" };
        },
        async prompt({ path, body }) {
          prompts.push({ id: path.id, text: body.parts[0]?.text ?? "" });
          return { parts: [{ type: "text", text: "got your reply" }] };
        },
      },
    };
    const cfg = resolveConfig({});
    const deps: PipelineDeps = {
      config: cfg,
      opencode,
      sessions,
      limiter: new CapacityLimiter(cfg.limits),
      caspianCircuit: new CircuitBreaker({ ...cfg.circuit }),
      opencodeCircuit: new CircuitBreaker({ ...cfg.circuit }),
      metrics: new Metrics(),
      reply: async (_id, text) => {
        replies.push(text);
      },
      claimMessage: () => ({ duplicate: false }),
    };

    const body = appendSessionFooter(
      "Replying to your earlier note",
      "ses_0691fromtui",
    );
    await handleInbound(
      deps,
      toEnvelope({
        id: "msg_in",
        conversationId: "conv_gmail_1",
        channel: "email",
        text: body,
        subject: "Re: alive",
        sender: { address: "dipanshuhappy@gmail.com" },
      }),
    );

    expect(creates).toBe(0);
    expect(prompts[0]?.id).toBe("ses_0691fromtui");
    expect(prompts[0]?.text).toContain("Synced OpenCode session: ses_0691fromtui");
    expect(prompts[0]?.text).not.toContain("caspian-opencode:session=");
    expect(replies[0]).toContain("caspian-opencode:session=ses_0691fromtui");
    expect(sessions.get("conv_gmail_1")).toBe("ses_0691fromtui");
  });
});

describe("formatEmailPrompt", () => {
  test("surfaces sync session and strips footer from body", () => {
    const text = formatEmailPrompt(
      toEnvelope({
        id: "m",
        conversationId: "c",
        channel: "email",
        text: appendSessionFooter("hi there", "ses_x"),
        subject: "S",
        sender: { address: "a@b.com" },
      }),
    );
    expect(text).toContain("Synced OpenCode session: ses_x");
    expect(text).toContain("hi there");
    expect(text).not.toContain("caspian-opencode:session=");
  });
});
