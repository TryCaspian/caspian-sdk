import { describe, expect, it } from "vitest";
import RedisMock from "ioredis-mock";
import { CommClient, InMemoryStateAdapter, RedisStateAdapter } from "../src/index.js";
import type { EventRecord } from "../src/types.js";

function mockClient(handler: (req: Request) => Response, state?: any): CommClient {
  const fetchImpl = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const req = new Request(input, init);
    return handler(req);
  };
  return new CommClient({
    apiKey: "test_key",
    baseUrl: "http://gw.test",
    fetch: fetchImpl as typeof fetch,
    state,
  });
}

function makeMessageEvent(seq: number, conversationId: string, text: string, msgId?: string): EventRecord {
  return {
    seq,
    type: "message.received",
    data: {
      message: {
        id: msgId ?? `msg_${seq}`,
        conversation_id: conversationId,
        connection_id: "conn_1",
        text,
      },
    },
  };
}

describe("InMemoryStateAdapter", () => {
  it("deduplicates seen events", async () => {
    const adapter = new InMemoryStateAdapter();
    expect(await adapter.seen("evt_1")).toBe(true);
    expect(await adapter.seen("evt_1")).toBe(false);
    expect(await adapter.seen("evt_2")).toBe(true);
  });

  it("evicts oldest items when maxSize is exceeded", async () => {
    const adapter = new InMemoryStateAdapter({ maxSize: 2 });
    expect(await adapter.seen("evt_1")).toBe(true);
    expect(await adapter.seen("evt_2")).toBe(true);

    // Max size 2 reached. Inserting evt_3 evicts evt_1.
    expect(await adapter.seen("evt_3")).toBe(true);
    expect(await adapter.seen("evt_2")).toBe(false); // still in cache
    expect(await adapter.seen("evt_1")).toBe(true);  // evicted, claimed again
  });

  it("handles per-conversation locking", async () => {
    const adapter = new InMemoryStateAdapter();
    const lock1 = await adapter.lock("conv_1");
    expect(lock1.acquired).toBe(true);

    const lock2 = await adapter.lock("conv_1");
    expect(lock2.acquired).toBe(false);

    await lock1.release();

    const lock3 = await adapter.lock("conv_1");
    expect(lock3.acquired).toBe(true);
    await lock3.release();
  });
});

describe("RedisStateAdapter", () => {
  it("deduplicates events via SET NX EX", async () => {
    const redis = new RedisMock();
    const adapter = new RedisStateAdapter(redis, { seenTtl: 60 });

    expect(await adapter.seen("evt_100")).toBe(true);
    expect(await adapter.seen("evt_100")).toBe(false);

    const val = await redis.get("event:evt_100");
    expect(val).toBe("1");
  });

  it("manages per-conversation locks and Lua script release", async () => {
    const redis = new RedisMock();
    const adapter = new RedisStateAdapter(redis, { lockTtl: 10 });

    const lock1 = await adapter.lock("conv_99");
    expect(lock1.acquired).toBe(true);
    expect(await redis.exists("lock:conv_99")).toBe(1);

    const lock2 = await adapter.lock("conv_99");
    expect(lock2.acquired).toBe(false);

    await lock1.release();
    expect(await redis.exists("lock:conv_99")).toBe(0);

    const lock3 = await adapter.lock("conv_99");
    expect(lock3.acquired).toBe(true);
    await lock3.release();
  });

  it("throws clear error when ioredis is unavailable", () => {
    const createWithoutRedis = () => {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const Module = require("module");
      const orig = Module.prototype.require;
      Module.prototype.require = function (id: string) {
        if (id === "ioredis") throw new Error("MODULE_NOT_FOUND");
        // eslint-disable-next-line prefer-rest-params
        return orig.apply(this, arguments);
      };
      try {
        new RedisStateAdapter();
      } finally {
        Module.prototype.require = orig;
      }
    };
    expect(createWithoutRedis).toThrow("ioredis is required");
  });
});

describe("CommClient Dispatch Integration", () => {
  it("deduplicates identical events during dispatch", async () => {
    const eventsReceived: string[] = [];
    const client = mockClient(() => new Response(JSON.stringify({}), { status: 200 }));

    client.onMessage((msg) => {
      eventsReceived.push(msg.id);
    });

    const event = makeMessageEvent(1, "conv_1", "Hello", "msg_dup");

    await (client as any).dispatchEvent(event);
    await (client as any).dispatchEvent(event);

    expect(eventsReceived).toEqual(["msg_dup"]);
  });

  it("enforces per-conversation lock under serial queue strategy", async () => {
    const eventsRun: string[] = [];
    let resolveEvt1Started: () => void = () => {};
    const evt1Started = new Promise<void>((r) => {
      resolveEvt1Started = r;
    });

    let resolveEvt1Finish: () => void = () => {};
    const evt1Blocked = new Promise<void>((r) => {
      resolveEvt1Finish = r;
    });

    const adapter = new InMemoryStateAdapter();
    const client = mockClient(() => new Response(JSON.stringify({}), { status: 200 }), adapter);

    client.onMessage(async (msg) => {
      eventsRun.push(msg.id);
      if (msg.id === "m1") {
        resolveEvt1Started();
        await evt1Blocked;
      }
    });

    const evt1 = makeMessageEvent(1, "conv_a", "Msg 1", "m1");
    const evt2 = makeMessageEvent(2, "conv_a", "Msg 2", "m2");
    const evt3 = makeMessageEvent(3, "conv_a", "Msg 3", "m3");

    // Start dispatching evt1 with queue strategy
    const p1 = (client as any).dispatchEvent(evt1, "queue");

    // Wait until evt1 is executing inside handler holding lock
    await evt1Started;

    // Dispatch evt2 while evt1 holds lock -> must be skipped under queue strategy
    await (client as any).dispatchEvent(evt2, "queue");
    expect(eventsRun).toEqual(["m1"]);

    // Unblock evt1
    resolveEvt1Finish();
    await p1;

    // Dispatch evt3 after evt1 releases lock -> must acquire and run
    await (client as any).dispatchEvent(evt3, "queue");
    expect(eventsRun).toEqual(["m1", "m3"]);
  });

  it("bypasses per-conversation lock under parallel strategy", async () => {
    const eventsRun: string[] = [];
    let resolveEvt1Started: () => void = () => {};
    const evt1Started = new Promise<void>((r) => {
      resolveEvt1Started = r;
    });

    let resolveEvt1Finish: () => void = () => {};
    const evt1Blocked = new Promise<void>((r) => {
      resolveEvt1Finish = r;
    });

    const adapter = new InMemoryStateAdapter();
    const client = mockClient(() => new Response(JSON.stringify({}), { status: 200 }), adapter);

    client.onMessage(async (msg) => {
      eventsRun.push(msg.id);
      if (msg.id === "m1") {
        resolveEvt1Started();
        await evt1Blocked;
      }
    });

    const evt1 = makeMessageEvent(1, "conv_a", "Msg 1", "m1");
    const evt2 = makeMessageEvent(2, "conv_a", "Msg 2", "m2");

    // Start dispatching evt1 with parallel strategy
    const p1 = (client as any).dispatchEvent(evt1, "parallel");

    // Wait until evt1 is executing inside handler
    await evt1Started;

    // Dispatch evt2 with parallel strategy -> lock is bypassed so evt2 runs concurrently
    await (client as any).dispatchEvent(evt2, "parallel");
    expect(eventsRun).toEqual(["m1", "m2"]);

    // Unblock evt1
    resolveEvt1Finish();
    await p1;
  });
});
