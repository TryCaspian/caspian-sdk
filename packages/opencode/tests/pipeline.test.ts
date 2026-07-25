import { describe, expect, test } from "bun:test";
import { resolveConfig } from "../src/config.ts";
import { toEnvelope } from "../src/email.ts";
import { handleInbound, type PipelineDeps } from "../src/pipeline.ts";
import { CircuitBreaker } from "../src/reliability/circuit.ts";
import { CapacityLimiter } from "../src/reliability/limits.ts";
import { Metrics } from "../src/reliability/metrics.ts";
import { MemorySessionMap } from "../src/session-map.ts";
import type { OpenCodePort } from "../src/ports.ts";

function makeDeps(overrides: Partial<PipelineDeps> = {}): PipelineDeps & {
  replies: Array<{ id: string; text: string }>;
  prompts: string[];
} {
  const replies: Array<{ id: string; text: string }> = [];
  const prompts: string[] = [];
  const sessions = new MemorySessionMap();
  let sessionN = 0;

  const opencode: OpenCodePort = {
    session: {
      async create() {
        sessionN += 1;
        return { id: `ses_${sessionN}` };
      },
      async prompt({ body }) {
        prompts.push(body.parts[0]?.text ?? "");
        return {
          parts: [{ type: "text", text: "Agent says hi" }],
        };
      },
    },
  };

  const cfg = resolveConfig({});
  const metrics = new Metrics();
  const base: PipelineDeps = {
    config: cfg,
    opencode,
    sessions,
    limiter: new CapacityLimiter(cfg.limits),
    caspianCircuit: new CircuitBreaker({ ...cfg.circuit }),
    opencodeCircuit: new CircuitBreaker({ ...cfg.circuit }),
    metrics,
    reply: async (id, text) => {
      replies.push({ id, text });
    },
    sleep: async () => {},
    // Tests use unique message ids; don't touch the real dedupe file.
    claimMessage: () => ({ duplicate: false }),
  };

  return { ...base, ...overrides, replies, prompts, metrics: overrides.metrics ?? metrics };
}

const email = toEnvelope({
  id: "msg_1",
  conversationId: "conv_1",
  channel: "email",
  text: "help",
  subject: "Need help",
  sender: { address: "u@example.com" },
});

describe("handleInbound CP happy path", () => {
  test("prompt + reply", async () => {
    const deps = makeDeps();
    await handleInbound(deps, email);
    expect(deps.prompts.length).toBe(1);
    expect(deps.prompts[0]).toContain("u@example.com");
    expect(deps.replies[0]?.id).toBe("msg_1");
    expect(deps.replies[0]?.text).toContain("Agent says hi");
    expect(deps.replies[0]?.text).toContain("caspian-opencode:session=ses_1");
    expect(deps.metrics.get("inbound.prompt_ok")).toBe(1);
    expect(deps.metrics.get("outbound.reply_ok")).toBe(1);
    expect(deps.sessions.get("conv_1")).toBe("ses_1");
  });

  test("reuses session for same conversation", async () => {
    const deps = makeDeps();
    await handleInbound(deps, email);
    await handleInbound(
      deps,
      toEnvelope({
        id: "msg_2",
        conversationId: "conv_1",
        channel: "email",
        text: "again",
        subject: "Need help",
        sender: { address: "u@example.com" },
      }),
    );
    expect(deps.sessions.get("conv_1")).toBe("ses_1");
    expect(deps.prompts.length).toBe(2);
  });

  test("rejects slack at admit", async () => {
    const deps = makeDeps();
    await handleInbound(
      deps,
      toEnvelope({
        id: "msg_1",
        conversationId: "conv_1",
        channel: "slack",
        text: "help",
        subject: "Need help",
        sender: { address: "u@example.com" },
      }),
    );
    expect(deps.prompts.length).toBe(0);
    expect(deps.metrics.get("inbound.rejected_admit")).toBe(1);
  });

  test("strips reasoning parts from channel reply by default", async () => {
    const deps = makeDeps({
      config: resolveConfig({ channels: ["email", "telegram"] }),
      opencode: {
        session: {
          async create() {
            return { id: "ses_think" };
          },
          async prompt() {
            return {
              parts: [
                { type: "reasoning", text: "I should greet them." },
                { type: "text", text: "Hey!" },
              ],
            };
          },
        },
      },
    });
    await handleInbound(
      deps,
      toEnvelope({
        id: "msg_think",
        conversationId: "conv_think",
        channel: "telegram",
        text: "hi",
        sender: { address: "1" },
      }),
    );
    expect(deps.replies[0]?.text).toContain("Hey!");
    expect(deps.replies[0]?.text).not.toContain("I should greet");
  });

  test("binds + notifies before prompt; telegram prompt is not email-framed", async () => {
    const order: string[] = [];
    const deps = makeDeps({
      config: resolveConfig({ channels: ["email", "telegram"] }),
      onDelivered: (info) => {
        order.push(`delivered:${info.channel}`);
      },
    });
    const origPrompt = deps.opencode.session.prompt.bind(deps.opencode.session);
    deps.opencode.session.prompt = async (args) => {
      order.push("prompt");
      return origPrompt(args);
    };

    await handleInbound(
      deps,
      toEnvelope({
        id: "msg_tg",
        conversationId: "conv_tg",
        channel: "telegram",
        text: "hi tg",
        sender: { address: "999" },
      }),
    );

    expect(order).toEqual(["delivered:telegram", "prompt"]);
    expect(deps.prompts[0]).toContain("[caspian:telegram]");
    expect(deps.prompts[0]).not.toContain("[caspian:email]");
    expect(deps.replies[0]?.id).toBe("msg_tg");
  });
});
