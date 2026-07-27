/**
 * Fault tests — wear/tear of dependencies. Remedy: fail-fast + degrade.
 */
import { describe, expect, test } from "bun:test";
import { resolveConfig } from "../src/config.ts";
import { toEnvelope } from "../src/email.ts";
import { handleInbound, type PipelineDeps } from "../src/pipeline.ts";
import { CircuitBreaker } from "../src/reliability/circuit.ts";
import { CapacityLimiter } from "../src/reliability/limits.ts";
import { Metrics } from "../src/reliability/metrics.ts";
import { MemorySessionMap } from "../src/session-map.ts";
import type { OpenCodePort } from "../src/ports.ts";

const email = toEnvelope({
  id: "msg_f",
  conversationId: "conv_f",
  channel: "email",
  text: "ping",
  subject: "x",
  sender: { address: "u@example.com" },
});

function baseDeps(
  opencode: OpenCodePort,
  reply: PipelineDeps["reply"],
): PipelineDeps {
  const cfg = resolveConfig({});
  return {
    config: cfg,
    opencode,
    sessions: new MemorySessionMap(),
    limiter: new CapacityLimiter(cfg.limits),
    caspianCircuit: new CircuitBreaker({ ...cfg.circuit }),
    opencodeCircuit: new CircuitBreaker({
      failureThreshold: 2,
      coolDownMs: 60_000,
      successThreshold: 1,
    }),
    metrics: new Metrics(),
    reply,
    sleep: async () => {},
    claimMessage: () => ({ duplicate: false }),
  };
}

describe("fault: OpenCode prompt failures", () => {
  test("degraded reply on prompt error", async () => {
    const replies: string[] = [];
    const deps = baseDeps(
      {
        session: {
          async create() {
            return { id: "ses_x" };
          },
          async prompt() {
            throw new Error("opencode down");
          },
        },
      },
      async (_id, text) => {
        replies.push(text);
      },
    );

    await expect(handleInbound(deps, email)).rejects.toThrow("opencode down");
    expect(replies[0]).toContain("error");
    expect(deps.metrics.get("inbound.prompt_fail")).toBe(1);
    expect(deps.metrics.get("outbound.degraded_reply")).toBe(1);
  });

  test("circuit opens after repeated OpenCode faults", async () => {
    const deps = baseDeps(
      {
        session: {
          async create() {
            return { id: "ses_y" };
          },
          async prompt() {
            throw new Error("boom");
          },
        },
      },
      async () => {},
    );

    await expect(handleInbound(deps, email)).rejects.toThrow();
    await expect(
      handleInbound(deps, toEnvelope({ ...email, id: "msg_f2" })),
    ).rejects.toThrow();
    expect(deps.opencodeCircuit.getState()).toBe("open");
  });
});

describe("fault: NonCP must not damage CP", () => {
  test("onDelivered throw does not fail prompt/reply", async () => {
    const replies: string[] = [];
    const deps = baseDeps(
      {
        session: {
          async create() {
            return { id: "ses_safe" };
          },
          async prompt() {
            return { parts: [{ type: "text", text: "hello" }] };
          },
        },
      },
      async (_id, text) => {
        replies.push(text);
      },
    );
    deps.onDelivered = () => {
      throw new Error("toast exploded");
    };

    await handleInbound(deps, email);
    expect(replies[0]).toContain("hello");
    expect(deps.metrics.get("inbound.prompt_ok")).toBe(1);
  });

  test("outbound circuit open leaves CP circuit closed", async () => {
    const cfg = resolveConfig({});
    const cp = new CircuitBreaker({
      failureThreshold: 2,
      coolDownMs: 60_000,
      successThreshold: 1,
    });
    const outbound = new CircuitBreaker({
      failureThreshold: 2,
      coolDownMs: 60_000,
      successThreshold: 1,
    });

    await expect(
      outbound.exec(async () => {
        throw new Error("send fail");
      }),
    ).rejects.toThrow("send fail");
    await expect(
      outbound.exec(async () => {
        throw new Error("send fail");
      }),
    ).rejects.toThrow("send fail");
    expect(outbound.getState()).toBe("open");
    expect(cp.getState()).toBe("closed");
    await expect(
      cp.exec(async () => "reply-ok"),
    ).resolves.toBe("reply-ok");
    void cfg;
  });
});

describe("fault: Caspian reply failures", () => {
  test("retries then marks reply_fail", async () => {
    let attempts = 0;
    const cfg = resolveConfig({ replyRetries: 2 });
    const metrics = new Metrics();
    const deps: PipelineDeps = {
      config: cfg,
      opencode: {
        session: {
          async create() {
            return { id: "ses_z" };
          },
          async prompt() {
            return { parts: [{ type: "text", text: "ok" }] };
          },
        },
      },
      sessions: new MemorySessionMap(),
      limiter: new CapacityLimiter(cfg.limits),
      caspianCircuit: new CircuitBreaker({ ...cfg.circuit }),
      opencodeCircuit: new CircuitBreaker({ ...cfg.circuit }),
      metrics,
      reply: async () => {
        attempts += 1;
        throw new Error("ntfy-style 503");
      },
      sleep: async () => {},
      claimMessage: () => ({ duplicate: false }),
    };

    await expect(handleInbound(deps, email)).rejects.toThrow();
    // 1 initial + 2 retries
    expect(attempts).toBe(3);
    expect(metrics.get("outbound.reply_fail")).toBe(1);
  });
});
