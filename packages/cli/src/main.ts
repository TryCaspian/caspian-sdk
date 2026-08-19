#!/usr/bin/env bun
/**
 * caspian — thin Effect client of the rewrite B surface.
 */
import { readFileSync } from "node:fs"
import {
  DEFAULT_BASE_URL,
  httpGatewayClient,
  type GatewayClient,
} from "caspian"
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import { helpText, parseArgv, UsageError } from "./desugar.ts"
import { runIntent } from "./run.ts"

const readDotenv = (text: string): { readonly [key: string]: string } => {
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

const resolve = (
  keys: ReadonlyArray<string>,
  fallback?: string,
): string | undefined => {
  let dotenv: { readonly [key: string]: string } = {}
  try {
    dotenv = readDotenv(readFileSync(".env", "utf8"))
  } catch {
    dotenv = {}
  }
  for (const source of [
    (key: string) => process.env[key],
    (key: string) => dotenv[key],
  ]) {
    for (const key of keys) {
      const value = source(key)
      if (value) return value
    }
  }
  return fallback
}

const clientOf = (): Effect.Effect<GatewayClient, UsageError> => {
  const apiKey = resolve(["CASPIAN_API_KEY", "COMM_API_KEY"])
  const baseUrl = resolve(
    ["CASPIAN_BASE_URL", "COMM_BASE_URL"],
    DEFAULT_BASE_URL,
  )
  if (apiKey === undefined) {
    return Effect.fail(
      new UsageError({
        reason: "No CASPIAN_API_KEY found. Run: caspian init",
      }),
    )
  }
  return Effect.succeed(httpGatewayClient(apiKey, baseUrl ?? DEFAULT_BASE_URL))
}

const print = (value: unknown): void => {
  if (typeof value === "string") {
    console.log(value)
    return
  }
  console.log(JSON.stringify(value, null, 2))
}

const program = Effect.gen(function* () {
  const argv = process.argv.slice(2)
  if (argv[0] === "--help" || argv[0] === "-h" || argv[0] === "help") {
    console.log(helpText())
    return
  }
  const intent = yield* parseArgv(argv)
  if (intent._tag === "Init") {
    const existing = resolve(["CASPIAN_API_KEY", "COMM_API_KEY"])
    if (existing !== undefined && !intent.force) {
      console.log("CASPIAN_API_KEY already configured in .env (use --force to replace).")
      return
    }
    const gateway = intent.gateway.replace(/\/$/, "")
    const response = yield* Effect.tryPromise({
      try: () =>
        fetch(`${gateway}/v1/projects/sandbox`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ name: intent.name }),
        }),
      catch: (cause) =>
        new UsageError({
          reason: cause instanceof Error ? cause.message : String(cause),
        }),
    })
    const text = yield* Effect.tryPromise({
      try: () => response.text(),
      catch: (cause) =>
        new UsageError({
          reason: cause instanceof Error ? cause.message : String(cause),
        }),
    })
    if (!response.ok) {
      return yield* Effect.fail(
        new UsageError({ reason: `Error ${response.status}: ${text}` }),
      )
    }
    const data = JSON.parse(text) as { api_key?: string; project_id?: string }
    const prev = yield* Effect.tryPromise({
      try: async () => {
        const file = Bun.file(".env")
        return (await file.exists()) ? file.text() : ""
      },
      catch: (cause) =>
        new UsageError({
          reason: cause instanceof Error ? cause.message : String(cause),
        }),
    })
    const env = readDotenv(prev)
    const next = {
      ...env,
      CASPIAN_API_KEY: data.api_key ?? "",
      CASPIAN_BASE_URL: gateway,
    }
    const body =
      Object.entries(next)
        .map(([key, value]) => `${key}=${value}`)
        .join("\n") + "\n"
    yield* Effect.tryPromise({
      try: () => Bun.write(".env", body),
      catch: (cause) =>
        new UsageError({
          reason: cause instanceof Error ? cause.message : String(cause),
        }),
    })
    console.log(`Project ${data.project_id ?? ""} created.`)
    console.log("Wrote CASPIAN_API_KEY and CASPIAN_BASE_URL to .env")
    console.log("Next: caspian channels add telegram")
    return
  }

  const result = yield* runIntent(intent, yield* clientOf())
  if (intent._tag === "Login") {
    const record = result as { readonly [key: string]: unknown }
    const url =
      record["verification_uri_complete"] ?? record["verification_uri"] ?? ""
    console.log("Sign in to Caspian (one-time - enables paid channels):")
    console.log(`\n  ${String(url)}\n`)
    return
  }
  print(result)
})

const result = await Effect.runPromise(Effect.either(program))
if (Either.isLeft(result)) {
  console.error(result.left.reason)
  process.exit(1)
}
