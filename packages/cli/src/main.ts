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
import { helpText, parseCli, UsageError } from "./desugar.ts"
import { DASHBOARD_URL, hostedNeeded } from "./errors.ts"
import { runLogin } from "./login.ts"
import { planIntent } from "./plan.ts"
import { runPlan } from "./run.ts"

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

const clientOf = (
  apiKeyFlag: string,
  gatewayFlag: string,
): Effect.Effect<GatewayClient, UsageError> => {
  const apiKey = apiKeyFlag || resolve(["CASPIAN_API_KEY", "COMM_API_KEY"])
  const baseUrl =
    gatewayFlag ||
    resolve(["CASPIAN_BASE_URL", "COMM_BASE_URL"], DEFAULT_BASE_URL)
  if (apiKey === undefined || apiKey === "") {
    return Effect.fail(hostedNeeded())
  }
  return Effect.succeed(httpGatewayClient(apiKey, baseUrl ?? DEFAULT_BASE_URL))
}

const mergeEnv = (
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

const writeEnv = (values: {
  readonly CASPIAN_API_KEY: string
  readonly CASPIAN_BASE_URL: string
}): Effect.Effect<void, UsageError> =>
  Effect.tryPromise({
    try: async () => {
      const file = Bun.file(".env")
      const prev = (await file.exists()) ? await file.text() : ""
      await Bun.write(".env", mergeEnv(prev, values))
    },
    catch: (cause) =>
      new UsageError({
        reason: cause instanceof Error ? cause.message : String(cause),
      }),
  })

const openUrl = (url: string): void => {
  const command =
    process.platform === "darwin"
      ? ["open", url]
      : process.platform === "win32"
        ? ["cmd", "/c", "start", "", url]
        : ["xdg-open", url]
  try {
    Bun.spawn(command, { stdout: "ignore", stderr: "ignore", stdin: "ignore" })
  } catch {
    // Printing the URL is enough; opening the browser is best-effort.
  }
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
  const parsed = yield* parseCli(argv)
  const plan = yield* planIntent(parsed.intent)

  if (plan._tag === "Login") {
    const result = yield* runLogin(plan, {
      fetch,
      wait: (ms) => Effect.sleep(`${ms} millis`),
      writeEnv,
      existingApiKey: resolve(["CASPIAN_API_KEY", "COMM_API_KEY"]),
      openUrl,
      onUrl: (url) => {
        console.log("Sign in to Caspian:")
        console.log(`\n  ${url}\n`)
        console.log("Waiting for you to approve in the browser...")
      },
    })
    console.log("\nSigned in. Wrote CASPIAN_API_KEY and CASPIAN_BASE_URL to .env")
    if (result.project_id !== "") {
      console.log(`Project ${result.project_id}`)
    }
    console.log(`Next: add credit in the dashboard:  ${DASHBOARD_URL}`)
    return
  }

  if (plan._tag === "Local") {
    print(yield* runPlan(plan))
    return
  }

  print(yield* runPlan(plan, yield* clientOf(parsed.api_key, parsed.gateway)))
})

const result = await Effect.runPromise(Effect.either(program))
if (Either.isLeft(result)) {
  console.error(result.left.reason)
  process.exit(1)
}
