/**
 * Read/merge ~/.config/opencode/caspian.json (non-secret plugin config).
 * Used by connect skills/tools to enable channels after Caspian connect.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

export function caspianJsonPath(configDir?: string): string {
  const dir = configDir ?? join(homedir(), ".config", "opencode");
  return join(dir, "caspian.json");
}

export function readCaspianJson(
  path = caspianJsonPath(),
): Record<string, unknown> {
  if (!existsSync(path)) return {};
  try {
    const raw = JSON.parse(readFileSync(path, "utf-8")) as unknown;
    return typeof raw === "object" && raw !== null && !Array.isArray(raw)
      ? (raw as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

export function writeCaspianJson(
  patch: Record<string, unknown>,
  path = caspianJsonPath(),
): Record<string, unknown> {
  const dir = dirname(path);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  const current = readCaspianJson(path);
  const next = deepMerge(current, patch);
  writeFileSync(path, JSON.stringify(next, null, 2) + "\n", "utf-8");
  return next;
}

function deepMerge(
  base: Record<string, unknown>,
  patch: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...base };
  for (const [k, v] of Object.entries(patch)) {
    if (
      v &&
      typeof v === "object" &&
      !Array.isArray(v) &&
      typeof out[k] === "object" &&
      out[k] !== null &&
      !Array.isArray(out[k])
    ) {
      out[k] = deepMerge(
        out[k] as Record<string, unknown>,
        v as Record<string, unknown>,
      );
    } else {
      out[k] = v;
    }
  }
  return out;
}

/** Ensure channel is in config.channels (admit allowlist). */
export function enableChannel(
  channel: string,
  extras?: Record<string, unknown>,
  path = caspianJsonPath(),
): { config: Record<string, unknown>; channels: string[]; restartedNeeded: true } {
  const current = readCaspianJson(path);
  const existing = Array.isArray(current.channels)
    ? current.channels.filter((x): x is string => typeof x === "string")
    : ["email"];
  const channels = existing.includes(channel)
    ? existing
    : [...existing, channel];
  const patch: Record<string, unknown> = { channels, ...(extras ?? {}) };
  const config = writeCaspianJson(patch, path);
  return {
    config,
    channels: Array.isArray(config.channels)
      ? (config.channels as string[])
      : channels,
    restartedNeeded: true,
  };
}

export function configuredChannels(path = caspianJsonPath()): string[] {
  const current = readCaspianJson(path);
  if (Array.isArray(current.channels)) {
    return current.channels.filter((x): x is string => typeof x === "string");
  }
  return ["email"];
}
