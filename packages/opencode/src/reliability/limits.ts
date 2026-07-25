/**
 * Capacity controls — rate + concurrency isolation.
 * Prefer fail-fast rejection over unbounded queues (reduces blast radius).
 */

export interface LimitConfig {
  /** Max concurrent inbound handlers globally. */
  globalConcurrency: number;
  /** Max concurrent handlers per conversation (isolation). */
  perConversationConcurrency: number;
  /** Max admits per conversation per window. */
  perConversationRate: number;
  /** Rate window length (ms). */
  rateWindowMs: number;
}

export const DEFAULT_LIMITS: LimitConfig = {
  globalConcurrency: 8,
  perConversationConcurrency: 1,
  perConversationRate: 30,
  rateWindowMs: 60_000,
};

export type AcquireResult =
  | { ok: true; release: () => void }
  | { ok: false; reason: "global" | "conversation" | "rate" };

export class CapacityLimiter {
  private globalInFlight = 0;
  private readonly convInFlight = new Map<string, number>();
  private readonly convHits = new Map<string, number[]>();

  constructor(private readonly cfg: LimitConfig) {}

  tryAcquire(conversationId: string, now = Date.now()): AcquireResult {
    // Rate first — cheap reject.
    const hits = (this.convHits.get(conversationId) ?? []).filter(
      (t) => now - t < this.cfg.rateWindowMs,
    );
    if (hits.length >= this.cfg.perConversationRate) {
      this.convHits.set(conversationId, hits);
      return { ok: false, reason: "rate" };
    }

    const conv = this.convInFlight.get(conversationId) ?? 0;
    if (conv >= this.cfg.perConversationConcurrency) {
      return { ok: false, reason: "conversation" };
    }
    if (this.globalInFlight >= this.cfg.globalConcurrency) {
      return { ok: false, reason: "global" };
    }

    hits.push(now);
    this.convHits.set(conversationId, hits);
    this.globalInFlight += 1;
    this.convInFlight.set(conversationId, conv + 1);

    let released = false;
    return {
      ok: true,
      release: () => {
        if (released) return;
        released = true;
        this.globalInFlight = Math.max(0, this.globalInFlight - 1);
        const c = (this.convInFlight.get(conversationId) ?? 1) - 1;
        if (c <= 0) this.convInFlight.delete(conversationId);
        else this.convInFlight.set(conversationId, c);
      },
    };
  }

  /** Test / health introspection. */
  stats() {
    return {
      globalInFlight: this.globalInFlight,
      conversations: this.convInFlight.size,
    };
  }
}
