import { randomUUID } from "crypto";
import type { Redis } from "ioredis";

export interface LockHandle {
  release(): void | Promise<void>;
}

export interface StateAdapter {
  /**
   * Record the event_id as seen.
   * Returns true if the event was already seen, false if it was newly added.
   * Must be atomic to prevent duplicates in multi-instance setups.
   */
  seen(eventId: string): Promise<boolean>;

  /**
   * Acquire a lock for the given conversation ID.
   * Returns a handle with a release() method that must be called.
   */
  lock(conversationId: string): Promise<LockHandle>;
}

/**
 * Default in-memory state adapter.
 */
export class InMemoryStateAdapter implements StateAdapter {
  private events = new Map<string, null>();
  private locks = new Map<string, Promise<void>>();
  
  constructor(private maxEvents: number = 1000) {}

  /** Execute seen. */
  async seen(eventId: string): Promise<boolean> {
    if (this.events.has(eventId)) {
      return true;
    }
    
    this.events.set(eventId, null);
    if (this.events.size > this.maxEvents) {
      // Map preserves insertion order, so the first key is the oldest
      const firstKey = this.events.keys().next().value;
      if (firstKey !== undefined) {
        this.events.delete(firstKey);
      }
    }
    
    return false;
  }

  /** Execute lock. */
  async lock(conversationId: string): Promise<LockHandle> {
    // Simple Promise queue for in-memory locking
    const currentLock = this.locks.get(conversationId) || Promise.resolve();
    
    let releaseLock!: () => void;
    const nextLock = new Promise<void>((resolve) => {
      releaseLock = resolve;
    });
    
    const chained = currentLock.then(() => nextLock);
    this.locks.set(conversationId, chained);

    await currentLock;
    
    return {
      release: () => {
        if (this.locks.get(conversationId) === chained) {
          this.locks.delete(conversationId);
        }
        releaseLock();
      }
    };
  }
}

/**
 * Redis-backed state adapter for distributed locking.
 */
export class RedisStateAdapter implements StateAdapter {
  constructor(
    private client: Redis,
    private keyPrefix: string = "caspian:",
    private dedupTtlSeconds: number = 86400,
    private lockTtlSeconds: number = 30
  ) {}

  /** Execute seen. */
  async seen(eventId: string): Promise<boolean> {
    const key = `${this.keyPrefix}seen:${eventId}`;
    const result = await this.client.set(key, "1", "EX", this.dedupTtlSeconds, "NX");
    return result !== "OK";
  }

  /** Execute lock. */
  async lock(conversationId: string): Promise<LockHandle> {
    const key = `${this.keyPrefix}lock:${conversationId}`;
    const token = randomUUID();
    
    // Blocking spin-lock (since basic Redis doesn't block on NX naturally)
    const start = Date.now();
    while (true) {
      const result = await this.client.set(key, token, "EX", this.lockTtlSeconds, "NX");
      if (result === "OK") {
        break;
      }
      if (Date.now() - start > 10000) {
        throw new Error(`Timeout acquiring lock for ${conversationId}`);
      }
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    
    // Heartbeat to renew lock while held
    const renewalInterval = setInterval(async () => {
      try {
        const script = `
          if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("expire", KEYS[1], ARGV[2])
          else
            return 0
          end
        `;
        await this.client.eval(script, 1, key, token, this.lockTtlSeconds);
      } catch (err) {
        // Ignore renewal errors (e.g. network blips) - if it fails completely, it just expires
      }
    }, (this.lockTtlSeconds * 1000) / 2);

    return {
      release: async () => {
        clearInterval(renewalInterval);
        // Safe release via Lua script (only delete if token matches)
        const script = `
          if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
          else
            return 0
          end
        `;
        await this.client.eval(script, 1, key, token);
      }
    };
  }
}
