/**
 * Fail-fast circuit breaker for CP dependencies (Caspian API, OpenCode prompt).
 * Failover preference: retry while half-open; open = reject immediately (fail-fast).
 */

export type CircuitState = "closed" | "open" | "half_open";

export interface CircuitOptions {
  /** Failures before opening. */
  failureThreshold: number;
  /** How long to stay open before a probe (ms). */
  coolDownMs: number;
  /** Successes in half-open before closing. */
  successThreshold: number;
  now?: () => number;
  onOpen?: () => void;
  onClose?: () => void;
}

export class CircuitBreaker {
  private failures = 0;
  private successes = 0;
  private openedAt = 0;
  private state: CircuitState = "closed";
  private readonly now: () => number;

  constructor(private readonly opts: CircuitOptions) {
    this.now = opts.now ?? Date.now;
  }

  getState(): CircuitState {
    if (this.state === "open" && this.now() - this.openedAt >= this.opts.coolDownMs) {
      this.state = "half_open";
      this.successes = 0;
    }
    return this.state;
  }

  /** Throw if open (fail-fast). */
  assertClosed(): void {
    const s = this.getState();
    if (s === "open") {
      throw new CircuitOpenError("circuit open — fail-fast");
    }
  }

  recordSuccess(): void {
    if (this.getState() === "half_open") {
      this.successes += 1;
      if (this.successes >= this.opts.successThreshold) {
        this.state = "closed";
        this.failures = 0;
        this.opts.onClose?.();
      }
      return;
    }
    this.failures = 0;
  }

  recordFailure(): void {
    this.failures += 1;
    this.successes = 0;
    if (this.failures >= this.opts.failureThreshold) {
      this.state = "open";
      this.openedAt = this.now();
      this.opts.onOpen?.();
    }
  }

  async exec<T>(fn: () => Promise<T>): Promise<T> {
    this.assertClosed();
    try {
      const result = await fn();
      this.recordSuccess();
      return result;
    } catch (err) {
      this.recordFailure();
      throw err;
    }
  }
}

export class CircuitOpenError extends Error {
  readonly code = "circuit_open" as const;
  constructor(message: string) {
    super(message);
    this.name = "CircuitOpenError";
  }
}
