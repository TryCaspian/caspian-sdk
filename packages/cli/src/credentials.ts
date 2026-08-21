/**
 * Where the CLI keeps its secret — not the project .env.
 *
 * Default: ~/.caspian/.env (override the directory with CASPIAN_HOME).
 * chmod 0700 on the directory, 0600 on the file.
 * Project ./.env is only for the SDK; caspian init project writes it.
 */
import {
  chmodSync,
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs"
import { homedir } from "node:os"
import { dirname, join } from "node:path"
import * as Effect from "effect/Effect"
import { DEFAULT_BASE_URL } from "caspian-sdk"
import { UsageError } from "./errors.ts"

export type EnvValues = {
  readonly CASPIAN_API_KEY: string
  readonly CASPIAN_BASE_URL: string
}

export type EnvMap = { readonly [key: string]: string | undefined }

export const parseDotenv = (text: string): { readonly [key: string]: string } => {
  const values: { [key: string]: string } = {}
  for (const line of text.split("\n")) {
    const trimmed = line.trim()
    if (trimmed === "" || trimmed.startsWith("#") || !trimmed.includes("=")) {
      continue
    }
    const at = trimmed.indexOf("=")
    const key = trimmed.slice(0, at).trim()
    const value = trimmed.slice(at + 1).trim().replace(/^['"]|['"]$/g, "")
    values[key] = value
  }
  return values
}

export const mergeEnv = (
  prev: string,
  values: { readonly [key: string]: string },
): string => {
  const keys = new Set(Object.keys(values))
  const lines = prev.split("\n").filter((line) => {
    const key = line.split("=", 1)[0]?.trim() ?? ""
    return key === "" || !keys.has(key)
  })
  while (lines.length > 0 && lines[lines.length - 1] === "") {
    lines.pop()
  }
  for (const [key, value] of Object.entries(values)) {
    lines.push(`${key}=${value}`)
  }
  return `${lines.join("\n")}\n`
}

/** Directory that holds the CLI secret file `.env`. */
export const caspianHome = (env: EnvMap = process.env): string => {
  const override = env["CASPIAN_HOME"]
  if (override !== undefined && override !== "") {
    return override.replace(/\/$/, "")
  }
  return join(homedir(), ".caspian")
}

export const cliSecretPath = (env: EnvMap = process.env): string =>
  join(caspianHome(env), ".env")

export const readFileText = (path: string): string => {
  try {
    return readFileSync(path, "utf8")
  } catch {
    return ""
  }
}

export const writeEnvFile = (
  path: string,
  values: { readonly [key: string]: string },
): void => {
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 })
  const prev = existsSync(path) ? readFileSync(path, "utf8") : ""
  writeFileSync(path, mergeEnv(prev, values), { mode: 0o600 })
  try {
    chmodSync(dirname(path), 0o700)
    chmodSync(path, 0o600)
  } catch {
    // Windows may ignore unix modes.
  }
}

export const writeEnvFileEffect = (
  path: string,
  values: { readonly [key: string]: string },
): Effect.Effect<void, UsageError> =>
  Effect.try({
    try: () => writeEnvFile(path, values),
    catch: (cause) =>
      new UsageError({
        reason: cause instanceof Error ? cause.message : String(cause),
      }),
  })

export const writeTextFileEffect = (
  path: string,
  text: string,
): Effect.Effect<void, UsageError> =>
  Effect.try({
    try: () => {
      mkdirSync(dirname(path), { recursive: true })
      writeFileSync(path, text)
    },
    catch: (cause) =>
      new UsageError({
        reason: cause instanceof Error ? cause.message : String(cause),
      }),
  })

const first = (
  sources: ReadonlyArray<(key: string) => string | undefined>,
  keys: ReadonlyArray<string>,
): string | undefined => {
  for (const source of sources) {
    for (const key of keys) {
      const value = source(key)
      if (value) return value
    }
  }
  return undefined
}

export type ResolvedCreds = {
  readonly apiKey: string | undefined
  readonly baseUrl: string
}

/**
 * Flag → process env → CLI secret → project .env.
 * The CLI secret is the default store; project .env is an SDK fallback.
 */
export const resolveCredentials = (input: {
  readonly flagKey?: string
  readonly flagGateway?: string
  readonly env?: EnvMap
  readonly cliEnvText?: string
  readonly projectEnvText?: string
  readonly defaultBaseUrl?: string
}): ResolvedCreds => {
  const env = input.env ?? {}
  const cli = parseDotenv(input.cliEnvText ?? "")
  const project = parseDotenv(input.projectEnvText ?? "")
  const apiKey = first(
    [
      () => (input.flagKey !== undefined && input.flagKey !== "" ? input.flagKey : undefined),
      (key) => env[key],
      (key) => cli[key],
      (key) => project[key],
    ],
    ["CASPIAN_API_KEY", "COMM_API_KEY"],
  )
  const baseUrl =
    first(
      [
        () =>
          input.flagGateway !== undefined && input.flagGateway !== ""
            ? input.flagGateway
            : undefined,
        (key) => env[key],
        (key) => cli[key],
        (key) => project[key],
      ],
      ["CASPIAN_BASE_URL", "COMM_BASE_URL"],
    ) ??
    input.defaultBaseUrl ??
    DEFAULT_BASE_URL
  return { apiKey, baseUrl }
}
