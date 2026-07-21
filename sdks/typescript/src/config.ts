import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

/** Parse ./.env (best-effort) so COMM_API_KEY / COMM_BASE_URL work with no setup. */
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

/** explicit arg > process.env > ./.env > fallback. */
export function config(
  explicit: string | undefined,
  envKey: string,
  fallback?: string,
): string | undefined {
  return explicit ?? process.env[envKey] ?? dotenv()[envKey] ?? fallback;
}
