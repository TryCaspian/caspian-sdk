/**
 * Capacity tests — resource exhaustion / artificial limits.
 */
import { describe, expect, test } from "bun:test";
import { resolveConfig } from "../src/config.ts";
import { toEnvelope } from "../src/email.ts";
import { handleInbound, type PipelineDeps } from "../src/pipeline.ts";
import { CircuitBreaker } from "../src/reliability/circuit.ts";
import { CapacityLimiter } from "../src/reliability/limits.ts";
import { Metrics } from "../src/reliability/metrics.ts";
import { MemorySessionMap } from "../src/session-map.ts";

const email = toEnvelope({
  id: "msg_c",
  conversationId: "conv_c",
  channel: "email",
  text: "load",
  subject: "x",
  sender: { address: "u@example.com" },
});

describe("capacity: saturated → degraded reply", () => {
  test("rejects with degraded message when conversation busy", async () => {
    const cfg = resolveConfig({
      limits: {
        globalConcurrency: 8,
        perConversationConcurrency: 1,
        perConversationRate: 100,
        rateWindowMs: 60_000,
      },
    });
    const limiter = new CapacityLimiter(cfg.limits);
    const held = limiter.tryAcquire("conv_c");
    expect(held.ok).toBe(true);

    const replies: string[] = [];
    const metrics = new Metrics();
    const deps: PipelineDeps = {
      config: cfg,
      opencode: {
        session: {
          async create() {
            return { id: "ses" };
          },
          async prompt() {
            return { parts: [{ type: "text", text: "should not run" }] };
          },
        },
      },
      sessions: new MemorySessionMap(),
      limiter,
      caspianCircuit: new CircuitBreaker({ ...cfg.circuit }),
      opencodeCircuit: new CircuitBreaker({ ...cfg.circuit }),
      metrics,
      reply: async (_id, text) => {
        replies.push(text);
      },
      claimMessage: () => ({ duplicate: false }),
    };

    await handleInbound(deps, email);
    expect(replies[0]).toBe(cfg.degradedReplyText);
    expect(metrics.get("inbound.rejected_capacity")).toBe(1);
    expect(metrics.get("outbound.degraded_reply")).toBe(1);

    if (held.ok) held.release();
  });

  test("isolation: other conversation still proceeds", async () => {
    const cfg = resolveConfig({
      limits: {
        globalConcurrency: 8,
        perConversationConcurrency: 1,
        perConversationRate: 100,
        rateWindowMs: 60_000,
      },
    });
    const limiter = new CapacityLimiter(cfg.limits);
    const held = limiter.tryAcquire("conv_busy");
    expect(held.ok).toBe(true);

    let prompted = false;
    const deps: PipelineDeps = {
      config: cfg,
      opencode: {
        session: {
          async create() {
            return { id: "ses_other" };
          },
          async prompt() {
            prompted = true;
            return { parts: [{ type: "text", text: "ok" }] };
          },
        },
      },
      sessions: new MemorySessionMap(),
      limiter,
      caspianCircuit: new CircuitBreaker({ ...cfg.circuit }),
      opencodeCircuit: new CircuitBreaker({ ...cfg.circuit }),
      metrics: new Metrics(),
      reply: async () => {},
      claimMessage: () => ({ duplicate: false }),
    };

    await handleInbound(
      deps,
      toEnvelope({
        id: "msg_other",
        conversationId: "conv_other",
        channel: "email",
        text: "hi",
        subject: "y",
        sender: { address: "b@example.com" },
      }),
    );
    expect(prompted).toBe(true);
    if (held.ok) held.release();
  });
});
