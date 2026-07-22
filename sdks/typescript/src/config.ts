import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

/** Parse ./.env (best-effort) so CASPIAN_API_KEY / CASPIAN_BASE_URL work with no setup. */
function dotenv(): Record<string, string> {
  const values: Record<string, string> = {};
  const path = join(process.cwd(), ".env");
  if (!existsSync(path)) return values;
  for (let line of readFileSync(path, "utf8").split(/\r?\n/)) {
    line = line.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const idx = line.indexOf("=");
    const key = line.slice(0, idx).trim();
    const value = line
      .slice(idx + 1)
      .trim()
      .replace(/^["']|["']$/g, "");
    values[key] = value;
  }
  return values;
}

/**
 * explicit arg > process.env > ./.env > fallback. Prefers the branded CASPIAN_*
 * name and falls back to the legacy COMM_* name for back-compat.
 */
export function config(
  explicit: string | undefined,
  envKey: string,
  fallback?: string,
): string | undefined {
  const keys = envKey.startsWith("CASPIAN_")
    ? [envKey, "COMM_" + envKey.slice("CASPIAN_".length)]
    : [envKey];
  const env = dotenv();
  if (explicit) return explicit;
  for (const k of keys) if (process.env[k]) return process.env[k];
  for (const k of keys) if (env[k]) return env[k];
  return fallback;
}
