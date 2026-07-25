/**
 * Listen-loop heartbeat — CP health check.
 * Stale heartbeat ⇒ listen is dead or hung (anomaly early detection).
 */

export interface HealthSnapshot {
  listening: boolean;
  lastHeartbeatAt: number | null;
  stale: boolean;
  ageMs: number | null;
}

export class ListenHealth {
  private lastHeartbeatAt: number | null = null;
  private listening = false;

  constructor(
    private readonly staleAfterMs: number,
    private readonly now: () => number = Date.now,
  ) {}

  start(): void {
    this.listening = true;
    this.beat();
  }

  stop(): void {
    this.listening = false;
  }

  beat(): void {
    this.lastHeartbeatAt = this.now();
  }

  snapshot(): HealthSnapshot {
    const now = this.now();
    const ageMs =
      this.lastHeartbeatAt === null ? null : now - this.lastHeartbeatAt;
    const stale =
      this.listening &&
      (this.lastHeartbeatAt === null ||
        (ageMs !== null && ageMs > this.staleAfterMs));
    return {
      listening: this.listening,
      lastHeartbeatAt: this.lastHeartbeatAt,
      stale,
      ageMs,
    };
  }
}
