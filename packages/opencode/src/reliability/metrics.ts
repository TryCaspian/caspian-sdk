/**
 * Unified counters for CP SLOs. Cheap, always-on, in-process.
 * Export later (OpenCode log / Prometheus) without changing call sites.
 */

export type MetricName =
  | "inbound.received"
  | "inbound.duplicate"
  | "inbound.admitted"
  | "inbound.rejected_admit"
  | "inbound.rejected_capacity"
  | "inbound.prompt_ok"
  | "inbound.prompt_fail"
  | "outbound.reply_ok"
  | "outbound.reply_fail"
  | "outbound.send_ok"
  | "outbound.send_fail"
  | "outbound.degraded_reply"
  | "listen.heartbeat"
  | "listen.heartbeat_stale"
  | "circuit.open"
  | "circuit.close"
  | "noncp.error";

export class Metrics {
  private readonly counts = new Map<MetricName, number>();
  private readonly timings: number[] = [];

  incr(name: MetricName, by = 1): void {
    this.counts.set(name, (this.counts.get(name) ?? 0) + by);
  }

  observeMs(ms: number): void {
    this.timings.push(ms);
    // Cap memory — keep a rolling window.
    if (this.timings.length > 2000) this.timings.splice(0, this.timings.length - 1000);
  }

  get(name: MetricName): number {
    return this.counts.get(name) ?? 0;
  }

  snapshot(): Record<string, number> {
    const out: Record<string, number> = {};
    for (const [k, v] of this.counts) out[k] = v;
    if (this.timings.length) {
      const sorted = [...this.timings].sort((a, b) => a - b);
      const p99 = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.99))] ?? 0;
      out["inbound.handler_ms_p99"] = p99;
      out["inbound.handler_ms_count"] = sorted.length;
    }
    return out;
  }
}
