import { describe, expect, test } from "bun:test";
import {
  CircuitBreaker,
  CircuitOpenError,
} from "../src/reliability/circuit.ts";
import { CapacityLimiter } from "../src/reliability/limits.ts";
import { ListenHealth } from "../src/reliability/health.ts";

describe("CircuitBreaker (fail-fast CP)", () => {
  test("opens after threshold and rejects", async () => {
    let now = 0;
    const c = new CircuitBreaker({
      failureThreshold: 2,
      coolDownMs: 1000,
      successThreshold: 1,
      now: () => now,
    });

    await expect(c.exec(async () => {
      throw new Error("boom");
    })).rejects.toThrow("boom");
    await expect(c.exec(async () => {
      throw new Error("boom");
    })).rejects.toThrow("boom");

    expect(c.getState()).toBe("open");
    expect(() => c.assertClosed()).toThrow(CircuitOpenError);

    now += 1000;
    expect(c.getState()).toBe("half_open");
    await c.exec(async () => "ok");
    expect(c.getState()).toBe("closed");
  });
});

describe("CapacityLimiter (isolation)", () => {
  test("enforces per-conversation concurrency", () => {
    const lim = new CapacityLimiter({
      globalConcurrency: 8,
      perConversationConcurrency: 1,
      perConversationRate: 100,
      rateWindowMs: 60_000,
    });
    const a = lim.tryAcquire("c1");
    expect(a.ok).toBe(true);
    const b = lim.tryAcquire("c1");
    expect(b.ok).toBe(false);
    if (!b.ok) expect(b.reason).toBe("conversation");
    if (a.ok) a.release();
    expect(lim.tryAcquire("c1").ok).toBe(true);
  });

  test("enforces global concurrency", () => {
    const lim = new CapacityLimiter({
      globalConcurrency: 1,
      perConversationConcurrency: 1,
      perConversationRate: 100,
      rateWindowMs: 60_000,
    });
    const a = lim.tryAcquire("c1");
    expect(a.ok).toBe(true);
    const b = lim.tryAcquire("c2");
    expect(b.ok).toBe(false);
    if (!b.ok) expect(b.reason).toBe("global");
  });

  test("enforces rate limit", () => {
    const lim = new CapacityLimiter({
      globalConcurrency: 8,
      perConversationConcurrency: 1,
      perConversationRate: 2,
      rateWindowMs: 60_000,
    });
    const t0 = 1_000_000;
    const a = lim.tryAcquire("c1", t0);
    if (a.ok) a.release();
    const b = lim.tryAcquire("c1", t0 + 1);
    if (b.ok) b.release();
    const c = lim.tryAcquire("c1", t0 + 2);
    expect(c.ok).toBe(false);
    if (!c.ok) expect(c.reason).toBe("rate");
  });
});

describe("ListenHealth", () => {
  test("detects stale heartbeat", () => {
    let now = 0;
    const h = new ListenHealth(100, () => now);
    h.start();
    expect(h.snapshot().stale).toBe(false);
    now = 150;
    expect(h.snapshot().stale).toBe(true);
    h.beat();
    expect(h.snapshot().stale).toBe(false);
  });
});
