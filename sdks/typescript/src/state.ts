import { StateLockTimeoutError } from "./errors.js";

export interface StateLock {
  release(): Promise<void>;
}

export interface StateAdapter {
  seen(eventId: string): Promise<boolean>;
  lock(conversationId: string): Promise<StateLock>;
}

export interface RedisStateClient {
  set(
    key: string,
    value: string,
    options: { NX?: boolean; EX?: number; PX?: number },
  ): Promise<string | null>;
  eval(
    script: string,
    options: { keys: string[]; arguments: string[] },
  ): Promise<unknown>;
  connect?(): Promise<void>;
  quit?(): Promise<void>;
}

export interface RedisStateOptions {
  namespace?: string;
  dedupTtlSeconds?: number;
  lockTtlSeconds?: number;
  lockWaitTimeoutSeconds?: number;
  lockRetryIntervalSeconds?: number;
}

type Expiry = { at: number; id: string };

class InMemoryLock implements StateLock {
  private released = false;

  constructor(
    private readonly adapter: InMemoryStateAdapter,
    private readonly conversationId: string,
    private readonly state: QueueState,
  ) {}

  async release(): Promise<void> {
    if (this.released) return;
    this.released = true;
    this.adapter.release(this.conversationId, this.state);
  }
}

type QueueState = {
  locked: boolean;
  waiters: Array<() => void>;
};

export class InMemoryStateAdapter implements StateAdapter {
  private readonly seenIds = new Map<string, number>();
  private readonly expiry: Expiry[] = [];
  private readonly queues = new Map<string, QueueState>();
  private readonly dedupTtlMs: number;

  constructor(options: { dedupTtlSeconds?: number } = {}) {
    const ttl = options.dedupTtlSeconds ?? 24 * 60 * 60;
    if (!Number.isFinite(ttl) || ttl <= 0) {
      throw new TypeError("dedupTtlSeconds must be positive");
    }
    this.dedupTtlMs = ttl * 1000;
  }

  async seen(eventId: string): Promise<boolean> {
    const now = Date.now();
    this.prune(now);
    const expiresAt = this.seenIds.get(eventId);
    if (expiresAt !== undefined && expiresAt > now) return true;
    const nextExpiry = now + this.dedupTtlMs;
    this.seenIds.set(eventId, nextExpiry);
    this.pushExpiry({ at: nextExpiry, id: eventId });
    return false;
  }

  async lock(conversationId: string): Promise<StateLock> {
    const state = this.queues.get(conversationId) ?? { locked: false, waiters: [] };
    this.queues.set(conversationId, state);
    if (!state.locked) {
      state.locked = true;
      return new InMemoryLock(this, conversationId, state);
    }
    await new Promise<void>((resolve) => state.waiters.push(resolve));
    return new InMemoryLock(this, conversationId, state);
  }

  release(conversationId: string, state: QueueState): void {
    const next = state.waiters.shift();
    if (next) {
      next();
      return;
    }
    state.locked = false;
    if (this.queues.get(conversationId) === state) this.queues.delete(conversationId);
  }

  private prune(now: number): void {
    while (this.expiry[0]?.at <= now) {
      const expired = this.popExpiry();
      if (this.seenIds.get(expired.id) === expired.at) this.seenIds.delete(expired.id);
    }
  }

  private pushExpiry(item: Expiry): void {
    this.expiry.push(item);
    let index = this.expiry.length - 1;
    while (index > 0) {
      const parent = Math.floor((index - 1) / 2);
      if (this.expiry[parent].at <= item.at) break;
      this.expiry[index] = this.expiry[parent];
      index = parent;
    }
    this.expiry[index] = item;
  }

  private popExpiry(): Expiry {
    const first = this.expiry[0];
    const last = this.expiry.pop() as Expiry;
    if (this.expiry.length) {
      let index = 0;
      while (true) {
        const left = index * 2 + 1;
        if (left >= this.expiry.length) break;
        const right = left + 1;
        const child =
          right < this.expiry.length && this.expiry[right].at < this.expiry[left].at
            ? right
            : left;
        if (this.expiry[child].at >= last.at) break;
        this.expiry[index] = this.expiry[child];
        index = child;
      }
      this.expiry[index] = last;
    }
    return first;
  }
}

const RELEASE_LOCK = `
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
`;

class RedisLock implements StateLock {
  private released = false;

  constructor(
    private readonly client: RedisStateClient,
    private readonly key: string,
    private readonly token: string,
  ) {}

  async release(): Promise<void> {
    if (this.released) return;
    this.released = true;
    await this.client.eval(RELEASE_LOCK, {
      keys: [this.key],
      arguments: [this.token],
    });
  }
}

export class RedisStateAdapter implements StateAdapter {
  private readonly namespace: string;
  private readonly dedupTtlSeconds: number;
  private readonly lockTtlMs: number;
  private readonly lockWaitTimeoutMs: number;
  private readonly lockRetryIntervalMs: number;

  constructor(
    private readonly client: RedisStateClient,
    options: RedisStateOptions = {},
  ) {
    this.namespace = options.namespace ?? "caspian";
    this.dedupTtlSeconds = options.dedupTtlSeconds ?? 24 * 60 * 60;
    const lockTtlSeconds = options.lockTtlSeconds ?? 30;
    const lockWaitTimeoutSeconds = options.lockWaitTimeoutSeconds ?? 30;
    const lockRetryIntervalSeconds = options.lockRetryIntervalSeconds ?? 0.05;
    if (!this.namespace) throw new TypeError("namespace must not be empty");
    if (!Number.isInteger(this.dedupTtlSeconds) || this.dedupTtlSeconds <= 0) {
      throw new TypeError("dedupTtlSeconds must be a positive integer");
    }
    if (!Number.isFinite(lockTtlSeconds) || lockTtlSeconds <= 0) {
      throw new TypeError("lockTtlSeconds must be positive");
    }
    if (!Number.isFinite(lockWaitTimeoutSeconds) || lockWaitTimeoutSeconds < 0) {
      throw new TypeError("lockWaitTimeoutSeconds must be non-negative");
    }
    if (!Number.isFinite(lockRetryIntervalSeconds) || lockRetryIntervalSeconds <= 0) {
      throw new TypeError("lockRetryIntervalSeconds must be positive");
    }
    this.lockTtlMs = lockTtlSeconds * 1000;
    this.lockWaitTimeoutMs = lockWaitTimeoutSeconds * 1000;
    this.lockRetryIntervalMs = lockRetryIntervalSeconds * 1000;
  }

  static async fromUrl(url: string, options: RedisStateOptions = {}): Promise<RedisStateAdapter> {
    try {
      const moduleName: string = "redis";
      const { createClient } = await import(moduleName);
      const client = createClient({ url }) as RedisStateClient;
      await client.connect?.();
      return new RedisStateAdapter(client, options);
    } catch (error) {
      if (error instanceof Error && /Cannot find (module|package)/.test(error.message)) {
        throw new Error(
          "Redis support requires the optional 'redis' dependency. Install it with: npm install redis",
        );
      }
      throw error;
    }
  }

  async seen(eventId: string): Promise<boolean> {
    const result = await this.client.set(`${this.namespace}:seen:${eventId}`, "1", {
      NX: true,
      EX: this.dedupTtlSeconds,
    });
    return result === null;
  }

  async lock(conversationId: string): Promise<StateLock> {
    const key = `${this.namespace}:lock:${conversationId}`;
    const token = crypto.randomUUID();
    const deadline = Date.now() + this.lockWaitTimeoutMs;
    while (true) {
      const result = await this.client.set(key, token, { NX: true, PX: this.lockTtlMs });
      if (result !== null) return new RedisLock(this.client, key, token);
      const remaining = deadline - Date.now();
      if (remaining <= 0) throw new StateLockTimeoutError(conversationId);
      await new Promise((resolve) => setTimeout(resolve, Math.min(this.lockRetryIntervalMs, remaining)));
    }
  }
}
