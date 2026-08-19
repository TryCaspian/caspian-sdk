/**
 * caspian init — guided setup. Not sandbox mint.
 *
 *   caspian init          same as init cli
 *   caspian init cli      this machine: CLI secret in ~/.caspian/.env
 *   caspian init project  this repo: ./.env for the SDK (and CLI secret)
 *   caspian init agent    an AI agent: CLI secret + next channels / catalog / call
 *
 * Sign-in is the same device-auth as caspian login. The CLI secret is never
 * the project .env unless the kind is project.
 */
import * as Effect from "effect/Effect"
import { DASHBOARD_URL, type UsageError } from "./errors.ts"
import { runLogin, type LoginIO, type LoginResult } from "./login.ts"
import type { InitKind } from "./intent.ts"
import type { InitPlan, LoginPlan } from "./plan.ts"

export type SecretValues = {
  readonly CASPIAN_API_KEY: string
  readonly CASPIAN_BASE_URL: string
}

export type InitIO = {
  readonly login: LoginIO
  readonly writeCliSecret: (values: SecretValues) => Effect.Effect<void, UsageError>
  readonly writeProjectEnv: (values: SecretValues) => Effect.Effect<void, UsageError>
  readonly cliSecretPath: string
  readonly existingApiKey?: string
  readonly existingBaseUrl: string
}

export type InitResult = {
  readonly kind: InitKind
  readonly signedIn: boolean
  readonly api_key: string
  readonly project_id: string
  readonly lines: ReadonlyArray<string>
}

export const orientation = (kind: InitKind): string =>
  [
    "Setting up Caspian.",
    "",
    `  caspian init cli       this machine — CLI secret in ~/.caspian/.env${kind === "cli" ? "  ←" : ""}`,
    `  caspian init project   this repo — SDK key in ./.env${kind === "project" ? "  ←" : ""}`,
    `  caspian init agent     an AI agent — CLI secret, then channels / call${kind === "agent" ? "  ←" : ""}`,
  ].join("\n")

const nextSteps = (kind: InitKind, cliPath: string): ReadonlyArray<string> => {
  switch (kind) {
    case "cli":
      return [
        `CLI secret stored in ${cliPath} (not this repo's .env).`,
        "Next: caspian channels add telegram",
        `Add credit:  ${DASHBOARD_URL}`,
      ]
    case "project":
      return [
        `Wrote ./.env for the SDK. CLI secret: ${cliPath}`,
        "Keep ./.env out of git.",
        "Next: caspian channels add telegram",
        `Add credit:  ${DASHBOARD_URL}`,
      ]
    case "agent":
      return [
        `CLI secret stored in ${cliPath}.`,
        "Next:",
        "  caspian channels add telegram",
        "  caspian catalog",
        "  caspian call post --thread … --text …",
        "This repo's SDK can get a key with: caspian init project",
        `Add credit:  ${DASHBOARD_URL}`,
      ]
  }
}

const asLogin = (plan: InitPlan): LoginPlan => ({
  _tag: "Login",
  gateway: plan.gateway,
  open: plan.open,
})

export const runInit = (
  plan: InitPlan,
  io: InitIO,
): Effect.Effect<InitResult, UsageError> =>
  Effect.gen(function* () {
    const haveKey =
      io.existingApiKey !== undefined && io.existingApiKey !== ""
    const needLogin = plan.force || !haveKey

    let apiKey = io.existingApiKey ?? ""
    let baseUrl = io.existingBaseUrl
    let projectId = ""
    let signedIn = false

    if (needLogin) {
      const loginIo: LoginIO =
        haveKey && io.existingApiKey !== undefined
          ? { ...io.login, existingApiKey: io.existingApiKey }
          : io.login
      const result: LoginResult = yield* runLogin(asLogin(plan), loginIo)
      apiKey = result.api_key
      baseUrl = plan.gateway.replace(/\/$/, "")
      projectId = result.project_id
      signedIn = true
    }

    const values: SecretValues = {
      CASPIAN_API_KEY: apiKey,
      CASPIAN_BASE_URL: baseUrl,
    }
    yield* io.writeCliSecret(values)
    if (plan.kind === "project") {
      yield* io.writeProjectEnv(values)
    }

    const lines = [
      orientation(plan.kind),
      "",
      signedIn ? "Signed in." : "Using existing CASPIAN_API_KEY.",
      ...(projectId !== "" ? [`Project ${projectId}`] : []),
      ...nextSteps(plan.kind, io.cliSecretPath),
    ]
    return {
      kind: plan.kind,
      signedIn,
      api_key: apiKey,
      project_id: projectId,
      lines,
    }
  })
