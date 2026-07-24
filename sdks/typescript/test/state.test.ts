import { describe, expect, it } from "vitest";
import { CommClient } from "../src/client.js";
import { InMemoryStateAdapter, RedisStateAdapter } from "../src/state.js";

class FakeRedis {
  public store = new Map<string, string>();

  async set(key: string, value: string, arg1?: string, arg2?: any, arg3?: string): Promise<string | boolean | null> {
    const isNx = arg1 === "NX" || arg3 === "NX";
    if (isNx && this.store.has(key)) {
      return null;
    }
    this.store.set(key, String(value));
    return "OK";
  }

  async get(key: string): Promise<string | null> {
    return this.store.get(key) ?? null;
  }

  async del(key: string): Promise<number> {
    if (this.store.has(key)) {
      this.store.delete(key);
      return 1;
    }
    return 0;
  }

  async eval(_script: string, _numkeys: number, key: string, arg: string): Promise<number> {
    if (this.store.get(key) === arg) {
      this.store.delete(key);
      return 1;
    }
    return 0;
  }
}

describe("InMemoryStateAdapter", () => {
  it("deduplicates seen event IDs", async () => {
    const adapter = new InMemoryStateAdapter();
    expect(await adapter.seen("evt_1")).toBe(false);
    expect(await adapter.seen("evt_1")).toBe(true);
    expect(await adapter.seen("evt_2")).toBe(false);
  });

  it("serializes locks for the same conversation ID", async () => {
    const adapter = new InMemoryStateAdapter();
    const order: string[] = [];

    const task = async (name: string, delayMs: number) => {
      const lock = await adapter.lock("conv_1");
      try {
        order.push(`${name}_start`);
        await new Promise((r) => setTimeout(r, delayMs));
        order.push(`${name}_end`);
      } finally {
        await lock.release();
      }
    };

    const p1 = task("t1", 50);
    await new Promise((r) => setTimeout(r, 5));
    const p2 = task("t2", 10);

    await Promise.all([p1, p2]);
    expect(order).toEqual(["t1_start", "t1_end", "t2_start", "t2_end"]);
  });
});

describe("RedisStateAdapter", () => {
  it("deduplicates seen event IDs", async () => {
    const fakeRedis = new FakeRedis();
    const adapter = new RedisStateAdapter(fakeRedis);

    expect(await adapter.seen("evt_1")).toBe(false);
    expect(await adapter.seen("evt_1")).toBe(true);
    expect(await adapter.seen("evt_2")).toBe(false);
  });

  it("serializes locks for the same conversation ID", async () => {
    const fakeRedis = new FakeRedis();
    const adapter = new RedisStateAdapter(fakeRedis);
    const order: string[] = [];

    const task = async (name: string, delayMs: number) => {
      const lock = await adapter.lock("conv_1");
      try {
        order.push(`${name}_start`);
        await new Promise((r) => setTimeout(r, 50));
        order.push(`${name}_end`);
      } finally {
        await lock.release();
      }
    };

    const p1 = task("t1", 50);
    await new Promise((r) => setTimeout(r, 5));
    const p2 = task("t2", 10);

    await Promise.all([p1, p2]);
    expect(order).toEqual(["t1_start", "t1_end", "t2_start", "t2_end"]);
  });
});

describe("CommClient Dispatch Dedup", () => {
  it("drops duplicate event IDs during dispatchPending", async () => {
    const events = [
      {
        id: "evt_100",
        seq: 1,
        type: "message.received",
        data: {
          customer_id: "c1",
          agent_id: "a1",
          message: {
            id: "m1",
            conversation_id: "conv_1",
            connection_id: "conn_1",
            text: "first delivery",
          },
        },
      },
      {
        id: "evt_100",
        seq: 2,
        type: "message.received",
        data: {
          customer_id: "c1",
          agent_id: "a1",
          message: {
            id: "m1",
            conversation_id: "conv_1",
            connection_id: "conn_1",
            text: "second delivery duplicate",
          },
        },
      },
    ];

    const mockFetch = async (input: RequestInfo | URL) => {
      const urlStr = String(input);
      if (urlStr.includes("/v1/events")) {
        const url = new URL(urlStr);
        const afterSeq = Number(url.searchParams.get("after_seq") ?? 0);
        const batch = afterSeq >= 2 ? [] : events;
        return new Response(JSON.stringify(batch), { status: 200 });
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    };

    const stateAdapter = new InMemoryStateAdapter();
    const client = new CommClient({
      apiKey: "test",
      baseUrl: "http://gw.test",
      fetch: mockFetch as typeof fetch,
      stateAdapter,
    });

    const received: string[] = [];
    client.onMessage((m) => {
      if (m.text) received.push(m.text);
    });

    await client.dispatchPending(0);
    expect(received).toEqual(["first delivery"]);
  });
});
