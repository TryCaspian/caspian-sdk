import { describe, expect, it } from "vitest";
import {
  CommClient,
  InMemoryStateAdapter,
  RedisStateAdapter,
  StateLockTimeoutError,
} from "../src/index.js";
import type { StateAdapter, StateLock } from "../src/index.js";

class FakeRedis {
  now = 0;
  values = new Map<string, { value: string; expiresAt?: number }>();

  advance(seconds: number) {
    this.now += seconds;
  }

  async set(
    key: string,
    value: string,
    options: { NX?: boolean; EX?: number; PX?: number },
  ): Promise<string | null> {
    this.prune();
    if (options.NX && this.values.has(key)) return null;
    const ttl = options.EX ?? (options.PX === undefined ? undefined : options.PX / 1000);
    this.values.set(key, { value, expiresAt: ttl === undefined ? undefined : this.now + ttl });
    return "OK";
  }

  async eval(
    _script: string,
    options: { keys: string[]; arguments: string[] },
  ): Promise<number> {
    this.prune();
    const key = options.keys[0];
    const token = options.arguments[0];
    if (key && this.values.get(key)?.value === token) {
      this.values.delete(key);
      return 1;
    }
    return 0;
  }

  private prune() {
    for (const [key, value] of this.values) {
      if (value.expiresAt !== undefined && value.expiresAt <= this.now) this.values.delete(key);
    }
  }
}

class RecordingState implements StateAdapter {
  eventIds = new Set<string>();
  locked: string[] = [];
  released: string[] = [];

  async seen(eventId: string): Promise<boolean> {
    const duplicate = this.eventIds.has(eventId);
    this.eventIds.add(eventId);
    return duplicate;
  }

  async lock(conversationId: string): Promise<StateLock> {
    this.locked.push(conversationId);
    let released = false;
    return {
      release: async () => {
        if (released) return;
        released = true;
        this.released.push(conversationId);
      },
    };
  }
}

describe("CommClient state integration", () => {
  it("deduplicates and locks every event type", async () => {
    const state = new RecordingState();
    const message = {
      seq: 1,
      type: "message.received",
      data: {
        message: {
          id: "message",
          conversation_id: "message-conversation",
          connection_id: "connection",
        },
      },
    };
    const events = [
      message,
      message,
      {
        seq: 2,
        type: "interaction.received",
        data: { conversation_id: "interaction-conversation" },
      },
      {
        seq: 3,
        type: "reaction.received",
        data: {
          action: "added",
          source_message: { conversation_id: "reaction-conversation" },
        },
      },
    ];
    const fetchImpl: typeof fetch = async (input) => {
      const url = new URL(String(input));
      const body =
        url.pathname === "/v1/events"
          ? url.searchParams.get("after_seq") === "0"
            ? events
            : []
          : {};
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    };
    const client = new CommClient({
      apiKey: "test",
      baseUrl: "https://example.test",
      fetch: fetchImpl,
      state,
    });
    const handled: string[] = [];
    client.onMessage((event) => handled.push(`message:${event.conversationId}`));
    client.onInteraction((event) => handled.push(`interaction:${event.conversationId}`));
    client.onReaction((event) => handled.push(`reaction:${event.action}`));

    await expect(client.dispatchPending()).resolves.toBe(3);

    expect(handled).toEqual([
      "message:message-conversation",
      "interaction:interaction-conversation",
      "reaction:added",
    ]);
    expect(state.locked).toEqual([
      "message-conversation",
      "interaction-conversation",
      "reaction-conversation",
    ]);
    expect(state.released).toEqual(state.locked);
  });

  it.each(["seen", "lock", "release"] as const)("continues when state %s fails", async (failure) => {
    const state: StateAdapter = {
      seen: async () => {
        if (failure === "seen") throw new Error("state unavailable");
        return false;
      },
      lock: async () => {
        if (failure === "lock") throw new Error("state unavailable");
        return {
          release: async () => {
            if (failure === "release") throw new Error("state unavailable");
          },
        };
      },
    };
    const fetchImpl: typeof fetch = async (input) => {
      const url = new URL(String(input));
      const body =
        url.pathname === "/v1/events" && url.searchParams.get("after_seq") === "0"
          ? [
              {
                seq: 1,
                type: "message.received",
                data: {
                  message: {
                    id: "message",
                    conversation_id: "conversation",
                    connection_id: "connection",
                  },
                },
              },
            ]
          : [];
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    };
    const client = new CommClient({
      apiKey: "test",
      baseUrl: "https://example.test",
      fetch: fetchImpl,
      state,
    });

    await expect(client.dispatchPending()).resolves.toBe(1);
  });
});

describe("InMemoryStateAdapter", () => {
  it("deduplicates event ids", async () => {
    const state = new InMemoryStateAdapter();
    await expect(state.seen("event")).resolves.toBe(false);
    await expect(state.seen("event")).resolves.toBe(true);
  });

  it("accepts an event again after its deduplication TTL expires", async () => {
    const state = new InMemoryStateAdapter({ dedupTtlSeconds: 0.01 });
    await expect(state.seen("event")).resolves.toBe(false);
    await expect(state.seen("event")).resolves.toBe(true);
    await new Promise((resolve) => setTimeout(resolve, 25));
    await expect(state.seen("event")).resolves.toBe(false);
  });

  it("serializes a conversation in FIFO order", async () => {
    const state = new InMemoryStateAdapter();
    const first = await state.lock("conversation");
    const order: string[] = ["first"];
    const secondPromise = state.lock("conversation").then(async (lock) => {
      order.push("second");
      await lock.release();
    });

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(order).toEqual(["first"]);
    await first.release();
    await secondPromise;
    expect(order).toEqual(["first", "second"]);
  });

  it("makes release idempotent", async () => {
    const state = new InMemoryStateAdapter();
    const first = await state.lock("conversation");
    const secondPromise = state.lock("conversation");
    await first.release();
    await first.release();
    const second = await secondPromise;
    await second.release();
  });
});

describe("RedisStateAdapter", () => {
  it("uses atomic deduplication with expiry", async () => {
    const redis = new FakeRedis();
    const state = new RedisStateAdapter(redis, { dedupTtlSeconds: 10 });
    await expect(state.seen("event")).resolves.toBe(false);
    await expect(state.seen("event")).resolves.toBe(true);
    redis.advance(11);
    await expect(state.seen("event")).resolves.toBe(false);
  });

  it("does not release another owner's lock", async () => {
    const redis = new FakeRedis();
    const first = new RedisStateAdapter(redis, {
      lockTtlSeconds: 1,
      lockWaitTimeoutSeconds: 0,
    });
    const second = new RedisStateAdapter(redis, {
      lockTtlSeconds: 1,
      lockWaitTimeoutSeconds: 0,
    });
    const firstLock = await first.lock("conversation");
    redis.advance(2);
    const secondLock = await second.lock("conversation");
    await firstLock.release();
    expect(redis.values.has("caspian:lock:conversation")).toBe(true);
    await secondLock.release();
    expect(redis.values.has("caspian:lock:conversation")).toBe(false);
  });

  it("times out when a lock is held", async () => {
    const redis = new FakeRedis();
    const state = new RedisStateAdapter(redis, { lockWaitTimeoutSeconds: 0 });
    const held = await state.lock("conversation");
    await expect(state.lock("conversation")).rejects.toBeInstanceOf(StateLockTimeoutError);
    await held.release();
  });
});
