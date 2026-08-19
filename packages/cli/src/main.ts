#!/usr/bin/env bun
/**
 * caspian — thin Effect client of the rewrite B surface.
 */
import {
  DEFAULT_BASE_URL,
  httpGatewayClient,
  type GatewayClient,
} from "caspian"
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import {
  cliSecretPath,
  readFileText,
  resolveCredentials,
  writeEnvFileEffect,
} from "./credentials.ts"
import { helpText, parseCli, UsageError } from "./desugar.ts"
import { DASHBOARD_URL, hostedNeeded } from "./errors.ts"
import { runInit } from "./init.ts"
import { runLogin, type LoginIO } from "./login.ts"
import { planIntent } from "./plan.ts"
import { runPlan } from "./run.ts"

const credsOf = (flagKey: string, flagGateway: string) =>
  resolveCredentials({
    flagKey,
    flagGateway,
    env: process.env,
    cliEnvText: readFileText(cliSecretPath()),
    projectEnvText: readFileText(".env"),
    defaultBaseUrl: DEFAULT_BASE_URL,
  })

const clientOf = (
  apiKeyFlag: string,
  gatewayFlag: string,
): Effect.Effect<GatewayClient, UsageError> => {
  const creds = credsOf(apiKeyFlag, gatewayFlag)
  if (creds.apiKey === undefined || creds.apiKey === "") {
    return Effect.fail(hostedNeeded())
  }
  return Effect.succeed(httpGatewayClient(creds.apiKey, creds.baseUrl))
}

const writeCliSecret = (values: {
  readonly CASPIAN_API_KEY: string
  readonly CASPIAN_BASE_URL: string
}) => writeEnvFileEffect(cliSecretPath(), values)

const writeProjectEnv = (values: {
  readonly CASPIAN_API_KEY: string
  readonly CASPIAN_BASE_URL: string
}) => writeEnvFileEffect(".env", values)

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

const loginIO = (existingApiKey?: string): LoginIO => ({
  fetch,
  wait: (ms) => Effect.sleep(`${ms} millis`),
  writeEnv: writeCliSecret,
  ...(existingApiKey !== undefined && existingApiKey !== ""
    ? { existingApiKey }
    : {}),
  openUrl,
  onUrl: (url) => {
    console.log("Sign in to Caspian:")
    console.log(`\n  ${url}\n`)
    console.log("Waiting for you to approve in the browser...")
  },
})

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
  const creds = credsOf(parsed.api_key, parsed.gateway)

  if (plan._tag === "Login") {
    const result = yield* runLogin(plan, loginIO(creds.apiKey))
    console.log(`\nSigned in. CLI secret: ${cliSecretPath()}`)
    if (result.project_id !== "") {
      console.log(`Project ${result.project_id}`)
    }
    console.log(`Next: caspian init   (or caspian channels add telegram)`)
    console.log(`Add credit:  ${DASHBOARD_URL}`)
    return
  }

  if (plan._tag === "Init") {
    const result = yield* runInit(plan, {
      login: loginIO(creds.apiKey),
      writeCliSecret,
      writeProjectEnv,
      cliSecretPath: cliSecretPath(),
      ...(creds.apiKey !== undefined && creds.apiKey !== ""
        ? { existingApiKey: creds.apiKey }
        : {}),
      existingBaseUrl: creds.baseUrl,
    })
    console.log(result.lines.join("\n"))
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
