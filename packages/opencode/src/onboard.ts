/**
 * Zero-signup onboarding (anyone can start).
 *
 * Prefer Caspian CLI (`caspian init` / `uvx caspian-cli init`) — same path as docs.
 * Fallback: POST /v1/projects/sandbox (no auth) + persist credentials.
 *
 * Reliability: CLI primary, HTTP mint failover; never leave the user without a key
 * when the gateway is reachable.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

export const DEFAULT_GATEWAY = "https://api.trycaspianai.com";

/** Where users copy an existing project API key after signing in. */
export const DASHBOARD_LOGIN_URL = "https://dashboard.trycaspianai.com/login";

export interface Credentials {
  apiKey: string;
  baseUrl: string;
}

export type OnboardSource =
  | "env"
  | "dotenv"
  | "opencode-env"
  | "cli"
  | "sandbox-api";

export interface OnboardResult extends Credentials {
  source: OnboardSource;
  created: boolean;
}

export interface ShellResult {
  exitCode: number;
  stdout: string;
  stderr: string;
}

/** Bun `$` / OpenCode shell — injectable for tests. */
export type ShellRunner = (
  cmd: string[],
  opts?: { cwd?: string; timeoutMs?: number },
) => Promise<ShellResult>;

export interface OnboardOptions {
  /** Already-resolved key/url from config/env (checked first). */
  apiKey?: string;
  baseUrl?: string;
  /** Project/sandbox display name sent to mint APIs. */
  projectName?: string;
  /** Working directory for CLI `.env` write (OpenCode project dir). */
  cwd?: string;
  /** Where we also persist for OpenCode global reuse. */
  opencodeConfigDir?: string;
  gateway?: string;
  shell?: ShellRunner;
  fetchImpl?: (
    input: string | URL | Request,
    init?: RequestInit,
  ) => Promise<Response>;
  /** Skip CLI and go straight to HTTP mint (tests / constrained hosts). */
  preferHttpMint?: boolean;
  /**
   * Also try `uvx` / `pipx` bootstrap when `caspian` is not on PATH.
   * Default false — OpenCode cold start prefers fast HTTP sandbox mint
   * so a slow first-time uvx download cannot block the plugin.
   */
  tryUvxBootstrap?: boolean;
  /** Per CLI candidate timeout (ms). Default 12_000. */
  cliTimeoutMs?: number;
  env?: NodeJS.ProcessEnv;
  /** When false, do not mint — return null if missing. */
  autoCreate?: boolean;
  /** Override which(1) lookup (tests). */
  which?: (bin: string) => string | null;
}

function opencodeDir(override?: string): string {
  return override ?? join(homedir(), ".config", "opencode");
}

/** Parse KEY=VALUE lines (simple .env; no export/quotes expansion). */
export function parseDotEnv(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq <= 0) continue;
    const key = trimmed.slice(0, eq).trim();
    let value = trimmed.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    out[key] = value;
  }
  return out;
}

export function readCredentialsFromEnv(
  env: NodeJS.ProcessEnv = process.env,
): Credentials | null {
  const apiKey = env.CASPIAN_API_KEY || env.COMM_API_KEY;
  if (!apiKey) return null;
  const baseUrl =
    env.CASPIAN_BASE_URL || env.COMM_BASE_URL || DEFAULT_GATEWAY;
  return { apiKey, baseUrl: baseUrl.replace(/\/+$/, "") };
}

export function readCredentialsFromDotEnv(path: string): Credentials | null {
  if (!existsSync(path)) return null;
  try {
    const parsed = parseDotEnv(readFileSync(path, "utf-8"));
    const apiKey = parsed.CASPIAN_API_KEY || parsed.COMM_API_KEY;
    if (!apiKey) return null;
    const baseUrl =
      parsed.CASPIAN_BASE_URL || parsed.COMM_BASE_URL || DEFAULT_GATEWAY;
    return { apiKey, baseUrl: baseUrl.replace(/\/+$/, "") };
  } catch {
    return null;
  }
}

/** Upsert keys into a .env file (same semantics as caspian-cli `_write_env`). */
export function writeDotEnv(path: string, values: Record<string, string>): void {
  const dir = dirname(path);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  const existing = existsSync(path)
    ? readFileSync(path, "utf-8").split("\n")
    : [];
  const keys = new Set(Object.keys(values));
  const lines = existing.filter((line) => {
    const k = line.split("=", 1)[0]?.trim();
    return k !== undefined && k.length > 0 && !keys.has(k) && line.trim() !== "";
  });
  for (const [key, value] of Object.entries(values)) {
    lines.push(`${key}=${value}`);
  }
  writeFileSync(path, lines.join("\n") + "\n", "utf-8");
}

function applyToProcessEnv(creds: Credentials, env: NodeJS.ProcessEnv): void {
  env.CASPIAN_API_KEY = creds.apiKey;
  env.CASPIAN_BASE_URL = creds.baseUrl;
}

export function maskApiKey(apiKey: string): string {
  const k = apiKey.trim();
  if (k.length <= 12) return "***";
  return `${k.slice(0, 8)}…${k.slice(-4)}`;
}

export function credentialPaths(opts?: {
  cwd?: string;
  opencodeConfigDir?: string;
}): { opencodeEnv: string; projectEnv: string } {
  const cwd = opts?.cwd ?? process.cwd();
  const configDir = opencodeDir(opts?.opencodeConfigDir);
  return {
    opencodeEnv: join(configDir, "caspian.env"),
    projectEnv: join(cwd, ".env"),
  };
}

export interface CredentialsStatus {
  configured: boolean;
  apiKeyMasked?: string;
  baseUrl: string;
  source?: OnboardSource | "none";
  paths: { opencodeEnv: string; projectEnv: string };
  dashboardLoginUrl: string;
  /** Human guidance when not configured. */
  nextSteps: string[];
}

/** Inspect whether Caspian credentials are already available (no mint). */
export function getCredentialsStatus(
  opts: Pick<
    OnboardOptions,
    "apiKey" | "baseUrl" | "cwd" | "opencodeConfigDir" | "gateway" | "env"
  > = {},
): CredentialsStatus {
  const env = opts.env ?? process.env;
  const gateway = (opts.gateway ?? opts.baseUrl ?? DEFAULT_GATEWAY).replace(
    /\/+$/,
    "",
  );
  const paths = credentialPaths({
    cwd: opts.cwd,
    opencodeConfigDir: opts.opencodeConfigDir,
  });

  const nextSteps = [
    `A) Instant sandbox (no signup): call caspian_setup_credentials with mode=sandbox`,
    `B) Existing account: sign in at ${DASHBOARD_LOGIN_URL}, copy a project API key, then call caspian_setup_credentials with mode=paste and apiKey=…`,
    `Credentials are written to ${paths.opencodeEnv} (and project .env). Never put keys in caspian.json.`,
  ];

  if (opts.apiKey) {
    return {
      configured: true,
      apiKeyMasked: maskApiKey(opts.apiKey),
      baseUrl: (opts.baseUrl ?? gateway).replace(/\/+$/, ""),
      source: "env",
      paths,
      dashboardLoginUrl: DASHBOARD_LOGIN_URL,
      nextSteps: [
        "Credentials present. Next: caspian_connect_email (then restart OpenCode if asked).",
      ],
    };
  }

  const fromEnv = readCredentialsFromEnv(env);
  if (fromEnv) {
    return {
      configured: true,
      apiKeyMasked: maskApiKey(fromEnv.apiKey),
      baseUrl: fromEnv.baseUrl,
      source: "env",
      paths,
      dashboardLoginUrl: DASHBOARD_LOGIN_URL,
      nextSteps: [
        "Credentials present. Next: caspian_connect_email (then restart OpenCode if asked).",
      ],
    };
  }

  const fromOpenCode = readCredentialsFromDotEnv(paths.opencodeEnv);
  if (fromOpenCode) {
    return {
      configured: true,
      apiKeyMasked: maskApiKey(fromOpenCode.apiKey),
      baseUrl: fromOpenCode.baseUrl,
      source: "opencode-env",
      paths,
      dashboardLoginUrl: DASHBOARD_LOGIN_URL,
      nextSteps: [
        "Credentials present in caspian.env. Next: caspian_connect_email (then restart if asked).",
      ],
    };
  }

  const fromProject = readCredentialsFromDotEnv(paths.projectEnv);
  if (fromProject) {
    return {
      configured: true,
      apiKeyMasked: maskApiKey(fromProject.apiKey),
      baseUrl: fromProject.baseUrl,
      source: "dotenv",
      paths,
      dashboardLoginUrl: DASHBOARD_LOGIN_URL,
      nextSteps: [
        "Credentials present in project .env. Next: caspian_connect_email (then restart if asked).",
      ],
    };
  }

  return {
    configured: false,
    baseUrl: gateway,
    source: "none",
    paths,
    dashboardLoginUrl: DASHBOARD_LOGIN_URL,
    nextSteps,
  };
}

/** Persist key + default base URL to caspian.env and project .env. */
export function persistCredentials(
  creds: Credentials,
  opts?: { cwd?: string; opencodeConfigDir?: string; env?: NodeJS.ProcessEnv },
): { opencodeEnv: string; projectEnv: string } {
  const paths = credentialPaths(opts);
  const values = {
    CASPIAN_API_KEY: creds.apiKey,
    CASPIAN_BASE_URL: creds.baseUrl.replace(/\/+$/, "") || DEFAULT_GATEWAY,
  };
  writeDotEnv(paths.opencodeEnv, values);
  writeDotEnv(paths.projectEnv, values);
  applyToProcessEnv(
    { apiKey: values.CASPIAN_API_KEY, baseUrl: values.CASPIAN_BASE_URL },
    opts?.env ?? process.env,
  );
  return paths;
}

/**
 * Interactive-friendly credential setup for OpenCode tools/skills.
 * - sandbox: mint a free key (no dashboard account)
 * - paste: save a key from the dashboard
 */
export async function setupCredentials(
  opts: {
    mode: "sandbox" | "paste";
    apiKey?: string;
    baseUrl?: string;
    projectName?: string;
    cwd?: string;
    opencodeConfigDir?: string;
    gateway?: string;
    fetchImpl?: OnboardOptions["fetchImpl"];
    env?: NodeJS.ProcessEnv;
  },
): Promise<{
  ok: true;
  created: boolean;
  source: OnboardSource;
  apiKeyMasked: string;
  baseUrl: string;
  paths: { opencodeEnv: string; projectEnv: string };
  restartRequired: true;
  message: string;
}> {
  const gateway = (opts.gateway ?? opts.baseUrl ?? DEFAULT_GATEWAY).replace(
    /\/+$/,
    "",
  );
  const projectName = opts.projectName ?? "opencode";

  let apiKey: string;
  let source: OnboardSource;
  let created: boolean;

  if (opts.mode === "paste") {
    const pasted = opts.apiKey?.trim();
    if (!pasted) {
      throw new Error(
        `apiKey is required for mode=paste. Sign in at ${DASHBOARD_LOGIN_URL}, copy a project API key, then retry.`,
      );
    }
    apiKey = pasted;
    source = "env";
    created = false;
  } else {
    const minted = await mintSandboxKey({
      gateway,
      projectName,
      fetchImpl: opts.fetchImpl,
    });
    apiKey = minted.apiKey;
    source = "sandbox-api";
    created = true;
  }

  const creds = { apiKey, baseUrl: gateway };
  const paths = persistCredentials(creds, {
    cwd: opts.cwd,
    opencodeConfigDir: opts.opencodeConfigDir,
    env: opts.env,
  });

  return {
    ok: true,
    created,
    source,
    apiKeyMasked: maskApiKey(apiKey),
    baseUrl: gateway,
    paths,
    restartRequired: true,
    message:
      opts.mode === "sandbox"
        ? "Sandbox API key created and saved (no signup)."
        : "API key saved from paste.",
  };
}

async function defaultShell(
  cmd: string[],
  opts?: { cwd?: string; timeoutMs?: number },
): Promise<ShellResult> {
  const timeoutMs = opts?.timeoutMs ?? 12_000;
  const proc = Bun.spawn(cmd, {
    cwd: opts?.cwd,
    stdout: "pipe",
    stderr: "pipe",
    env: process.env,
  });
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    try {
      proc.kill();
    } catch {
      // ignore
    }
  }, timeoutMs);
  try {
    const [stdout, stderr, exitCode] = await Promise.all([
      new Response(proc.stdout).text(),
      new Response(proc.stderr).text(),
      proc.exited,
    ]);
    if (timedOut) {
      return {
        exitCode: 124,
        stdout,
        stderr: (stderr + "\n[caspian-opencode] CLI init timed out").trim(),
      };
    }
    return { exitCode, stdout, stderr };
  } finally {
    clearTimeout(timer);
  }
}

function defaultWhich(bin: string): string | null {
  try {
    return Bun.which(bin) ?? null;
  } catch {
    return null;
  }
}

/**
 * Run `caspian init` (optionally uvx/pipx) in cwd.
 * Returns credentials if `.env` was written / already present.
 */
export async function initViaCli(
  opts: {
    cwd: string;
    gateway: string;
    projectName: string;
    shell: ShellRunner;
    /** Include uvx/pipx candidates. Default: only if caspian is on PATH… see ensureCredentials. */
    includeUvx?: boolean;
    /** If true (default), skip all candidates when `caspian` is not on PATH. */
    requireCaspianOnPath?: boolean;
    timeoutMs?: number;
    which?: (bin: string) => string | null;
  },
): Promise<Credentials | null> {
  const which = opts.which ?? defaultWhich;
  const caspianPath = which("caspian");
  if (opts.requireCaspianOnPath !== false && !caspianPath) {
    return null;
  }

  const args = [
    "init",
    "--gateway",
    opts.gateway,
    "--name",
    opts.projectName,
  ];

  const candidates: string[][] = [];
  if (caspianPath || opts.requireCaspianOnPath === false) {
    candidates.push(["caspian", ...args]);
  }
  if (opts.includeUvx) {
    // Package name is caspian-cli; executable is `caspian`.
    candidates.push(["uvx", "--from", "caspian-cli", "caspian", ...args]);
    candidates.push([
      "pipx",
      "run",
      "--spec",
      "caspian-cli",
      "caspian",
      ...args,
    ]);
  }

  for (const cmd of candidates) {
    try {
      const result = await opts.shell(cmd, {
        cwd: opts.cwd,
        timeoutMs: opts.timeoutMs ?? 12_000,
      });
      if (result.exitCode === 0) {
        const creds = readCredentialsFromDotEnv(join(opts.cwd, ".env"));
        if (creds) return creds;
      }
    } catch {
      // command missing — try next
    }
  }
  return null;
}

/** Direct sandbox mint — same API as `caspian init` (no signup). */
export async function mintSandboxKey(
  opts: {
    gateway: string;
    projectName: string;
    fetchImpl?: (
      input: string | URL | Request,
      init?: RequestInit,
    ) => Promise<Response>;
  },
): Promise<{ projectId: string; apiKey: string }> {
  const fetchImpl = opts.fetchImpl ?? globalThis.fetch.bind(globalThis);
  const gateway = opts.gateway.replace(/\/+$/, "");
  const res = await fetchImpl(`${gateway}/v1/projects/sandbox`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: opts.projectName }),
    signal: AbortSignal.timeout(30_000),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(
      `sandbox mint failed: ${res.status} ${res.statusText} ${body}`.trim(),
    );
  }
  const data = (await res.json()) as { project_id?: string; api_key?: string };
  if (!data.api_key) {
    throw new Error("sandbox mint response missing api_key");
  }
  return {
    projectId: data.project_id ?? "unknown",
    apiKey: data.api_key,
  };
}

/**
 * Ensure the process has Caspian credentials.
 * Creates a sandbox project when none exist (CLI first, HTTP failover).
 */
export async function ensureCredentials(
  opts: OnboardOptions = {},
): Promise<OnboardResult | null> {
  const env = opts.env ?? process.env;
  const gateway = (opts.gateway ?? opts.baseUrl ?? DEFAULT_GATEWAY).replace(
    /\/+$/,
    "",
  );
  const cwd = opts.cwd ?? process.cwd();
  const configDir = opencodeDir(opts.opencodeConfigDir);
  const projectName = opts.projectName ?? "opencode";
  const autoCreate = opts.autoCreate !== false;
  const shell = opts.shell ?? defaultShell;

  // 1) Explicit / process env
  if (opts.apiKey) {
    const creds = {
      apiKey: opts.apiKey,
      baseUrl: (opts.baseUrl ?? gateway).replace(/\/+$/, ""),
    };
    applyToProcessEnv(creds, env);
    return { ...creds, source: "env", created: false };
  }
  const fromEnv = readCredentialsFromEnv(env);
  if (fromEnv) {
    return { ...fromEnv, source: "env", created: false };
  }

  // 2) OpenCode global env file
  const opencodeEnvPath = join(configDir, "caspian.env");
  const fromOpenCode = readCredentialsFromDotEnv(opencodeEnvPath);
  if (fromOpenCode) {
    applyToProcessEnv(fromOpenCode, env);
    return { ...fromOpenCode, source: "opencode-env", created: false };
  }

  // 3) Project .env (may already exist from prior `caspian init`)
  const projectEnvPath = join(cwd, ".env");
  const fromProject = readCredentialsFromDotEnv(projectEnvPath);
  if (fromProject) {
    applyToProcessEnv(fromProject, env);
    return { ...fromProject, source: "dotenv", created: false };
  }

  if (!autoCreate) return null;

  // 4) CLI init only when `caspian` is on PATH (or tryUvxBootstrap).
  // OpenCode cold start must not wait on a first-time `uvx` download.
  if (!opts.preferHttpMint) {
    const viaCli = await initViaCli({
      cwd,
      gateway,
      projectName,
      shell,
      includeUvx: opts.tryUvxBootstrap === true,
      requireCaspianOnPath: opts.tryUvxBootstrap !== true,
      timeoutMs: opts.cliTimeoutMs ?? 12_000,
      which: opts.which,
    });
    if (viaCli) {
      applyToProcessEnv(viaCli, env);
      // Mirror into OpenCode config so future projects find the key.
      writeDotEnv(opencodeEnvPath, {
        CASPIAN_API_KEY: viaCli.apiKey,
        CASPIAN_BASE_URL: viaCli.baseUrl,
      });
      return { ...viaCli, source: "cli", created: true };
    }
  }

  // 5) Primary zero-setup path when no CLI: same mint API the CLI uses
  const minted = await mintSandboxKey({
    gateway,
    projectName,
    fetchImpl: opts.fetchImpl,
  });
  const creds: Credentials = { apiKey: minted.apiKey, baseUrl: gateway };
  writeDotEnv(projectEnvPath, {
    CASPIAN_API_KEY: creds.apiKey,
    CASPIAN_BASE_URL: creds.baseUrl,
  });
  writeDotEnv(opencodeEnvPath, {
    CASPIAN_API_KEY: creds.apiKey,
    CASPIAN_BASE_URL: creds.baseUrl,
  });
  applyToProcessEnv(creds, env);
  return { ...creds, source: "sandbox-api", created: true };
}
