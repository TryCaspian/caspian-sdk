import crypto from "node:crypto";

export interface LockHandle {
  acquired: boolean;
  release: () => Promise<void>;
}

export interface StateAdapter {
  /**
   * Atomic deduplication check.
   * Returns true if eventId is new (and claims it). Returns false if duplicate.
   */
  seen(eventId: string): Promise<boolean>;

  /**
   * Best-effort per-conversation lock.
   * Returns a LockHandle with acquired: true/false and a release() cleanup function.
   */
  lock(conversationId: string): Promise<LockHandle>;
}

export interface InMemoryStateAdapterOptions {
  /** Maximum size of the seen set before oldest entries are evicted. Default 10000. */
  maxSize?: number;
}

/**
 * Default zero-config in-memory state adapter with bounded deduplication set (FIFO eviction)
 * and per-conversation locks.
 */
export class InMemoryStateAdapter implements StateAdapter {
  private readonly maxSize: number;
  private readonly seenSet = new Set<string>();
  private readonly lockedConversations = new Set<string>();

  constructor(options: InMemoryStateAdapterOptions = {}) {
    const maxSize = options.maxSize ?? 10000;
    if (maxSize <= 0) {
      throw new Error("maxSize must be positive");
    }
    this.maxSize = maxSize;
  }

  async seen(eventId: string): Promise<boolean> {
    if (this.seenSet.has(eventId)) {
      return false;
    }
    if (this.seenSet.size >= this.maxSize) {
      const oldest = this.seenSet.keys().next().value;
      if (oldest !== undefined) {
        this.seenSet.delete(oldest);
      }
    }
    this.seenSet.add(eventId);
    return true;
  }

  async lock(conversationId: string): Promise<LockHandle> {
    if (this.lockedConversations.has(conversationId)) {
      return {
        acquired: false,
        release: async () => {},
      };
    }
    this.lockedConversations.add(conversationId);
    return {
      acquired: true,
      release: async () => {
        this.lockedConversations.delete(conversationId);
      },
    };
  }
}

const LUA_RELEASE_LOCK = `
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
`;

export interface RedisStateAdapterOptions {
  /** Deduplication key expiration in seconds. Default 86400 (24h). */
  seenTtl?: number;
  /** Per-conversation lock TTL in seconds. Default 30s. */
  lockTtl?: number;
}

/**
 * Redis-backed state adapter for multi-instance / distributed deployments.
 */
export class RedisStateAdapter implements StateAdapter {
  private readonly redis: any;
  private readonly seenTtl: number;
  private readonly lockTtl: number;

  constructor(clientOrOptions?: any, options: RedisStateAdapterOptions = {}) {
    /**
     * seenTtl: Default 86400s (24h). Channel providers (Slack, Discord, Telegram, WhatsApp)
     * retry failed webhook deliveries up to 24 hours. 24h ensures robust dedup across retry
     * windows without unbounded key growth in Redis.
     *
     * lockTtl: Default 30s. Long enough for typical agent handler runtimes (including LLM calls),
     * while short enough that if a worker process crashes, the conversation lock auto-expires
     * quickly without deadlocking the conversation indefinitely.
     */
    this.seenTtl = options.seenTtl ?? 86400;
    this.lockTtl = options.lockTtl ?? 30;

    if (clientOrOptions && typeof clientOrOptions.set === "function") {
      this.redis = clientOrOptions;
    } else {
      try {
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        const RedisModule = require("ioredis");
        const Redis = RedisModule.default || RedisModule;
        this.redis = new Redis(clientOrOptions);
      } catch (err) {
        throw new Error(
          "ioredis is required to use RedisStateAdapter. Install it with 'npm install ioredis'.",
        );
      }
    }
  }

  async seen(eventId: string): Promise<boolean> {
    const key = `event:${eventId}`;
    const res = await this.redis.set(key, "1", "NX", "EX", this.seenTtl);
    return res === "OK";
  }

  async lock(conversationId: string): Promise<LockHandle> {
    const key = `lock:${conversationId}`;
    const token =
      typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : crypto.randomBytes(16).toString("hex");
    const res = await this.redis.set(key, token, "NX", "EX", this.lockTtl);
    const acquired = res === "OK";

    if (!acquired) {
      return {
        acquired: false,
        release: async () => {},
      };
    }

    return {
      acquired: true,
      release: async () => {
        try {
          await this.redis.eval(LUA_RELEASE_LOCK, 1, key, token);
        } catch {
          /* TTL safety net if network release fails */
        }
      },
    };
  }
}
