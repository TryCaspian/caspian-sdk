/**
 * Resolve secrets from env / .env / opencode caspian.env (never caspian.json).
 */

import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { parseDotEnv } from "./onboard.js";

const TELEGRAM_KEYS = [
  "TELEGRAM_BOT_TOKEN",
  "CASPIAN_TELEGRAM_BOT_TOKEN",
  "COMM_TELEGRAM_BOT_TOKEN",
] as const;

export type SecretSource =
  | "env"
  | "project-dotenv"
  | "opencode-env"
  | "missing";

export interface ResolvedSecret {
  value: string | null;
  source: SecretSource;
  /** Paths the user can edit when missing. */
  hintPaths: string[];
}

function readKeyFromDotEnv(path: string, keys: readonly string[]): string | null {
  if (!existsSync(path)) return null;
  try {
    const parsed = parseDotEnv(readFileSync(path, "utf-8"));
    for (const k of keys) {
      const v = parsed[k]?.trim();
      if (v) return v;
    }
  } catch {
    return null;
  }
  return null;
}

export function resolveTelegramBotToken(opts?: {
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  opencodeConfigDir?: string;
}): ResolvedSecret {
  const env = opts?.env ?? process.env;
  const cwd = opts?.cwd ?? process.cwd();
  const ocDir = opts?.opencodeConfigDir ?? join(homedir(), ".config", "opencode");
  const projectEnv = join(cwd, ".env");
  const ocEnv = join(ocDir, "caspian.env");
  const hintPaths = [projectEnv, ocEnv];

  for (const k of TELEGRAM_KEYS) {
    const v = env[k]?.trim();
    if (v) return { value: v, source: "env", hintPaths };
  }

  const fromProject = readKeyFromDotEnv(projectEnv, TELEGRAM_KEYS);
  if (fromProject) {
    return { value: fromProject, source: "project-dotenv", hintPaths };
  }

  const fromOc = readKeyFromDotEnv(ocEnv, TELEGRAM_KEYS);
  if (fromOc) {
    return { value: fromOc, source: "opencode-env", hintPaths };
  }

  return { value: null, source: "missing", hintPaths };
}

export function telegramTokenSetupMessage(hintPaths: string[]): string {
  return [
    "Telegram bot token not found.",
    "Create a bot with @BotFather, then store the token in ONE of:",
    ...hintPaths.map((p, i) => `  ${i + 1}. ${p}  →  TELEGRAM_BOT_TOKEN=...`),
    "Or export TELEGRAM_BOT_TOKEN in the environment.",
    "Do not paste the token into chat if you can avoid it.",
    "Then run /caspian:connect-telegram again.",
  ].join("\n");
}

const DISCORD_KEYS = [
  "DISCORD_BOT_TOKEN",
  "CASPIAN_DISCORD_BOT_TOKEN",
  "COMM_DISCORD_BOT_TOKEN",
] as const;

export function resolveDiscordBotToken(opts?: {
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  opencodeConfigDir?: string;
}): ResolvedSecret {
  const env = opts?.env ?? process.env;
  const cwd = opts?.cwd ?? process.cwd();
  const ocDir = opts?.opencodeConfigDir ?? join(homedir(), ".config", "opencode");
  const projectEnv = join(cwd, ".env");
  const ocEnv = join(ocDir, "caspian.env");
  const hintPaths = [projectEnv, ocEnv];

  for (const k of DISCORD_KEYS) {
    const v = env[k]?.trim();
    if (v) return { value: v, source: "env", hintPaths };
  }

  const fromProject = readKeyFromDotEnv(projectEnv, DISCORD_KEYS);
  if (fromProject) {
    return { value: fromProject, source: "project-dotenv", hintPaths };
  }

  const fromOc = readKeyFromDotEnv(ocEnv, DISCORD_KEYS);
  if (fromOc) {
    return { value: fromOc, source: "opencode-env", hintPaths };
  }

  return { value: null, source: "missing", hintPaths };
}

export function discordTokenSetupMessage(hintPaths: string[]): string {
  return [
    "No Discord bot token found (optional if using one-click install).",
    "BYO bot: create an application at https://discord.com/developers → Bot → token, then store in ONE of:",
    ...hintPaths.map((p, i) => `  ${i + 1}. ${p}  →  DISCORD_BOT_TOKEN=...`),
    "Or run /caspian:connect-discord without a token to use installDiscord() (authorize URL).",
  ].join("\n");
}
