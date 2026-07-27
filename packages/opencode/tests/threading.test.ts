import { describe, expect, test } from "bun:test";
import { resolveConfig } from "../src/config.ts";
import { toEnvelope } from "../src/email.ts";
import { handleInbound, type PipelineDeps } from "../src/pipeline.ts";
import { CircuitBreaker } from "../src/reliability/circuit.ts";
import { CapacityLimiter } from "../src/reliability/limits.ts";
import { Metrics } from "../src/reliability/metrics.ts";
import { MemorySessionMap } from "../src/session-map.ts";
import {
  DEFAULT_THREADING,
  sessionMapKey,
  sessionTitle,
} from "../src/threading.ts";
import type { OpenCodePort } from "../src/ports.ts";

const msgA = toEnvelope({
  id: "m1",
  conversationId: "conv_a",
  channel: "email",
  text: "hi",
  subject: "Hello thread",
  sender: { address: "a@example.com" },
});

const msgB = toEnvelope({
  id: "m2",
  conversationId: "conv_b",
  channel: "email",
  text: "yo",
  subject: "Other",
  sender: { address: "b@example.com" },
});

describe("sessionMapKey / sessionTitle", () => {
  test("threaded: key is conversationId", () => {
    expect(sessionMapKey(DEFAULT_THREADING, msgA)).toBe("conv_a");
    expect(sessionTitle(DEFAULT_THREADING, msgA)).toBe(
      "email: Hello thread · a@example.com",
    );
  });

  test("shared: key is sharedSessionKey", () => {
    const shared = {
      enabled: false,
      sharedSessionKey: "caspian:shared",
      sessionFooter: true,
    };
    expect(sessionMapKey(shared, msgA)).toBe("caspian:shared");
    expect(sessionMapKey(shared, msgB)).toBe("caspian:shared");
    expect(sessionTitle(shared, msgA)).toBe("caspian:shared");
  });
});

describe("threading in pipeline", () => {
  function makeDeps(threadingEnabled: boolean) {
    const titles: string[] = [];
    let n = 0;
    const sessions = new MemorySessionMap();
    const opencode: OpenCodePort = {
      session: {
        async create({ body }) {
          n += 1;
          titles.push(body.title ?? "");
          return { id: `ses_${n}` };
        },
        async prompt() {
          return { parts: [{ type: "text", text: "ok" }] };
        },
      },
    };
    const cfg = resolveConfig({
      threading: { enabled: threadingEnabled },
    });
    const deps: PipelineDeps = {
      config: cfg,
      opencode,
      sessions,
      limiter: new CapacityLimiter(cfg.limits),
      caspianCircuit: new CircuitBreaker({ ...cfg.circuit }),
      opencodeCircuit: new CircuitBreaker({ ...cfg.circuit }),
      metrics: new Metrics(),
      reply: async () => {},
      claimMessage: () => ({ duplicate: false }),
    };
    return { deps, titles, sessions };
  }

  test("default: different conversations → different sessions", async () => {
    const { deps, titles, sessions } = makeDeps(true);
    await handleInbound(deps, msgA);
    await handleInbound(deps, msgB);
    expect(sessions.size()).toBe(2);
    expect(sessions.get("conv_a")).toBe("ses_1");
    expect(sessions.get("conv_b")).toBe("ses_2");
    expect(titles[0]).toContain("Hello thread");
  });

  test("default: same conversation follow-up reuses session", async () => {
    const { deps, sessions } = makeDeps(true);
    await handleInbound(deps, msgA);
    await handleInbound(
      deps,
      toEnvelope({
        id: "m1b",
        conversationId: "conv_a",
        channel: "email",
        text: "follow up",
        subject: "Hello thread",
        sender: { address: "a@example.com" },
      }),
    );
    expect(sessions.size()).toBe(1);
    expect(sessions.get("conv_a")).toBe("ses_1");
  });

  test("threading.enabled=false: all mail shares one session", async () => {
    const { deps, sessions } = makeDeps(false);
    await handleInbound(deps, msgA);
    await handleInbound(deps, msgB);
    expect(sessions.size()).toBe(1);
    expect(sessions.get("caspian:shared")).toBe("ses_1");
  });
});
