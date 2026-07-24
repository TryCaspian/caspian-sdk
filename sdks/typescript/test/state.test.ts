import { describe, expect, it } from "vitest";
import { InMemoryStateAdapter, RedisStateAdapter } from "../src/state.js";
import Redis from "ioredis-mock";

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

describe("InMemoryStateAdapter", () => {
  it("should track seen events with max limits", async () => {
    const adapter = new InMemoryStateAdapter(2);
    expect(await adapter.seen("evt_1")).toBe(false);
    expect(await adapter.seen("evt_1")).toBe(true);

    expect(await adapter.seen("evt_2")).toBe(false);
    expect(await adapter.seen("evt_3")).toBe(false);

    // evt_1 should be evicted
    expect(await adapter.seen("evt_1")).toBe(false);
  });

  it("should acquire and release locks correctly", async () => {
    const adapter = new InMemoryStateAdapter();
    const result: string[] = [];

    const worker1 = async () => {
      const lock = await adapter.lock("conv_1");
      result.push("w1_start");
      await sleep(50);
      result.push("w1_end");
      lock.release();
    };

    const worker2 = async () => {
      const lock = await adapter.lock("conv_1");
      result.push("w2_start");
      result.push("w2_end");
      lock.release();
    };

    const p1 = worker1();
    await sleep(5); // ensure worker1 grabs lock first
    const p2 = worker2();
    
    await Promise.all([p1, p2]);

    expect(result).toEqual(["w1_start", "w1_end", "w2_start", "w2_end"]);
  });
});

describe("RedisStateAdapter", () => {
  it("should track seen events correctly via redis", async () => {
    // @ts-expect-error ioredis-mock works as ioredis instance
    const client = new Redis();
    // @ts-expect-error
    const adapter = new RedisStateAdapter(client);

    expect(await adapter.seen("evt_1")).toBe(false);
    expect(await adapter.seen("evt_1")).toBe(true);
    
    await client.flushall();
    await client.quit();
  });

  it("should acquire and release locks correctly via redis", async () => {
    // @ts-expect-error ioredis-mock works as ioredis instance
    const client = new Redis();
    // @ts-expect-error
    const adapter = new RedisStateAdapter(client);
    
    const result: string[] = [];

    const worker1 = async () => {
      const lock = await adapter.lock("conv_2");
      result.push("w1_start");
      await sleep(50);
      result.push("w1_end");
      await lock.release();
    };

    const worker2 = async () => {
      const lock = await adapter.lock("conv_2");
      result.push("w2_start");
      result.push("w2_end");
      await lock.release();
    };

    const p1 = worker1();
    await sleep(5); // ensure worker1 grabs lock first
    const p2 = worker2();
    
    await Promise.all([p1, p2]);

    expect(result).toEqual(["w1_start", "w1_end", "w2_start", "w2_end"]);
    
    await client.flushall();
    await client.quit();
  });
});
