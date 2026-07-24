export interface LockHandle {
  release(): Promise<void> | void;
}

export interface StateAdapter {
  seen(eventId: string, ttl?: number): boolean | Promise<boolean>;
  lock(conversationId: string, ttl?: number): Promise<LockHandle> | LockHandle;
}

export class InMemoryStateAdapter implements StateAdapter {
  private seenMap = new Map<string, number>();
  private locks = new Map<string, Array<() => void>>();

  seen(eventId: string, ttl = 86400): boolean {
    const now = Date.now();
    this.cleanupExpired(now);
    if (this.seenMap.has(eventId)) {
      return true;
    }
    this.seenMap.set(eventId, now + ttl * 1000);
    return false;
  }

  private cleanupExpired(now: number): void {
    for (const [key, exp] of this.seenMap.entries()) {
      if (exp <= now) {
        this.seenMap.delete(key);
      }
    }
  }

  async lock(conversationId: string, _ttl = 30): Promise<LockHandle> {
    let queue = this.locks.get(conversationId);
    if (!queue) {
      queue = [];
      this.locks.set(conversationId, queue);
    }

    if (queue.length > 0) {
      await new Promise<void>((resolve) => {
        queue!.push(resolve);
      });
    } else {
      // First lock holder placeholder
      queue.push(() => {});
    }

    let released = false;
    return {
      release: () => {
        if (released) return;
        released = true;
        const currentQueue = this.locks.get(conversationId);
        if (!currentQueue) return;

        currentQueue.shift();
        if (currentQueue.length > 0) {
          const next = currentQueue[0];
          next();
        } else {
          this.locks.delete(conversationId);
        }
      },
    };
  }
}

export class RedisStateAdapter implements StateAdapter {
  constructor(
    private readonly redisClient: any,
    private readonly prefix = "caspian:",
  ) {}

  async seen(eventId: string, ttl = 86400): Promise<boolean> {
    const key = `${this.prefix}seen:${eventId}`;
    const ttlSeconds = Math.max(1, Math.floor(ttl));
    let res: any;
    try {
      res = await this.redisClient.set(key, "1", "EX", ttlSeconds, "NX");
    } catch {
      res = await this.redisClient.set(key, "1", "NX", "EX", ttlSeconds);
    }
    if (res === "OK" || res === true || res === 1) {
      return false;
    }
    return true;
  }

  async lock(conversationId: string, ttl = 30): Promise<LockHandle> {
    const key = `${this.prefix}lock:${conversationId}`;
    const token = Math.random().toString(36).substring(2) + Date.now().toString(36);
    const ttlSeconds = Math.max(1, Math.floor(ttl));
    const start = Date.now();
    const timeout = ttl * 1000;
    let acquired = false;

    while (!acquired) {
      let res: any;
      try {
        res = await this.redisClient.set(key, token, "EX", ttlSeconds, "NX");
      } catch {
        res = await this.redisClient.set(key, token, "NX", "EX", ttlSeconds);
      }
      if (res === "OK" || res === true || res === 1) {
        acquired = true;
        break;
      }
      if (Date.now() - start >= timeout) {
        break;
      }
      await new Promise((r) => setTimeout(r, 50));
    }

    let released = false;
    return {
      release: async () => {
        if (released) return;
        released = true;
        if (!acquired) return;

        const luaScript = `
          if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
          else
            return 0
          end
        `;
        try {
          if (typeof this.redisClient.eval === "function") {
            await this.redisClient.eval(luaScript, 1, key, token);
          } else {
            const val = await this.redisClient.get(key);
            if (val === token) {
              if (typeof this.redisClient.del === "function") {
                await this.redisClient.del(key);
              } else if (typeof this.redisClient.delete === "function") {
                await this.redisClient.delete(key);
              }
            }
          }
        } catch {
          const val = await this.redisClient.get?.(key);
          if (val === token) {
            await (this.redisClient.del || this.redisClient.delete)?.(key);
          }
        }
      },
    };
  }
}
