/**
 * Critical / non-critical divider (CP item).
 * Non-CP features must never throw into the inbound→reply path.
 */

export interface FeatureSwitches {
  /** Global kill switch — stops listen + handling. CP. */
  enabled: boolean;
  /**
   * If no API key, create a sandbox Caspian project (CLI init → HTTP mint failover).
   * Zero-signup onboarding for first-time users. Non-CP to mint failures.
   */
  autoOnboard: boolean;
  /** Connect email on plugin start if no inbox yet. Non-CP to auto-connect failures. */
  autoConnectEmail: boolean;
  /** Send typing indicators. Non-CP. */
  typing: boolean;
  /** When capacity saturated, reply with a short degraded message instead of dropping. CP-adjacent. */
  degradedCapacityReply: boolean;
  /** Persist session map across process restarts (future). Non-CP for v1. */
  durableSessionMap: boolean;
}

export const DEFAULT_SWITCHES: FeatureSwitches = {
  enabled: true,
  autoOnboard: true,
  autoConnectEmail: true,
  typing: false,
  degradedCapacityReply: true,
  durableSessionMap: false,
};

/** Run a non-CP side effect; swallow errors so CP is undisturbed. */
export async function nonCritical(
  label: string,
  fn: () => void | Promise<void>,
  onError?: (err: unknown) => void,
): Promise<void> {
  try {
    await fn();
  } catch (err) {
    onError?.(err);
    // Intentionally swallowed — non-CP.
    console.warn(`[caspian-opencode] non-cp:${label}`, err);
  }
}
