/**
 * caspian init — guided setup. Not sandbox mint.
 *
 *   caspian init          asks: cli, project, or agent
 *   caspian init cli      this machine: CLI secret in ~/.caspian/.env
 *   caspian init project  this repo: ./.env for the SDK (and CLI secret)
 *   caspian init agent    an AI agent: CLI secret + ./.env + .caspian/AGENT.md
 *
 * Sign-in is the same device-auth as caspian login.
 */
import * as Effect from "effect/Effect"
import { DASHBOARD_URL, UsageError } from "./errors.ts"
import { runLogin, type LoginIO, type LoginResult } from "./login.ts"
import type { InitKind } from "./intent.ts"
import type { InitPlan, LoginPlan } from "./plan.ts"

export type SecretValues = {
  readonly CASPIAN_API_KEY: string
  readonly CASPIAN_BASE_URL: string
}

export const AGENT_PLAYBOOK_PATH = ".caspian/AGENT.md"

export const AGENT_PLAYBOOK = `# Caspian — for the agent

A CLI secret is on this machine (\`~/.caspian/.env\`). This repo also has \`./.env\`
for SDK code. Never print the key.

| Job | Command |
|---|---|
| Discover | \`caspian catalog\` / \`search "…"\` / \`get <id>\` |
| Do | \`caspian call <id> --thread …\` |
| Identity | \`caspian channels add <channel>\` / \`ls\` |
| Threads | \`caspian threads ls\` / \`tail\` |

\`call\` is the only send path. Do not invent \`caspian slack post\` or \`threads reply\`.
Hosted is the default. Self-host: \`--via self-host --bot-token\`.
`

export const ASK_PROMPT = [
  "What are you setting up?",
  "",
  "  1) cli      this machine — global CLI secret (~/.caspian/.env)",
  "  2) project  this repo — ./.env for the SDK",
  "  3) agent    an AI agent — CLI secret, ./.env, and .caspian/AGENT.md",
  "",
  "Choice [1/2/3]: ",
].join("\n")

export const ASK_NEEDED = [
  "What are you setting up?",
  "",
  "  caspian init cli       this machine — ~/.caspian/.env",
  "  caspian init project   this repo — ./.env for the SDK",
  "  caspian init agent     an AI agent — CLI + ./.env + .caspian/AGENT.md",
  "",
  "Re-run with one of those, or run caspian init in a terminal to choose.",
].join("\n")

export const parseInitChoice = (raw: string): InitKind | undefined => {
  const token = raw.trim().toLowerCase()
  if (token === "1" || token === "cli") return "cli"
  if (token === "2" || token === "project") return "project"
  if (token === "3" || token === "agent") return "agent"
  return undefined
}

export type InitIO = {
  readonly login: LoginIO
  readonly writeCliSecret: (
    values: SecretValues,
  ) => Effect.Effect<void, UsageError>
  readonly writeProjectEnv: (
    values: SecretValues,
  ) => Effect.Effect<void, UsageError>
  readonly writePlaybook: (text: string) => Effect.Effect<void, UsageError>
  readonly chooseKind: () => Effect.Effect<InitKind, UsageError>
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
    `  caspian init agent     an AI agent — CLI + ./.env + .caspian/AGENT.md${kind === "agent" ? "  ←" : ""}`,
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
        `CLI secret stored in ${cliPath}. Wrote ./.env for the SDK.`,
        `Wrote ${AGENT_PLAYBOOK_PATH} (how the agent should call caspian).`,
        "Next:",
        "  caspian channels add telegram",
        "  caspian catalog",
        "  caspian call post --thread … --text …",
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
    const kind: InitKind =
      plan.kind === "ask" ? yield* io.chooseKind() : plan.kind

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
    if (kind === "project" || kind === "agent") {
      yield* io.writeProjectEnv(values)
    }
    if (kind === "agent") {
      yield* io.writePlaybook(AGENT_PLAYBOOK)
    }

    const lines = [
      orientation(kind),
      "",
      signedIn ? "Signed in." : "Using existing CASPIAN_API_KEY.",
      ...(projectId !== "" ? [`Project ${projectId}`] : []),
      ...nextSteps(kind, io.cliSecretPath),
    ]
    return {
      kind,
      signedIn,
      api_key: apiKey,
      project_id: projectId,
      lines,
    }
  })

export const failAsk = (): UsageError => new UsageError({ reason: ASK_NEEDED })
