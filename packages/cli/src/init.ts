/**
 * caspian init — guided setup. Not sandbox mint.
 *
 *   caspian init          asks: cli, project, or agent
 *   caspian init cli      this machine: CLI secret in ~/.caspian/.env
 *   caspian init project  asks which folder (default: cwd) and writes .env there
 *   caspian init project --new
 *     TODO(init-project-scaffold): create a TypeScript SDK or Python SDK app
 *     (package files, sample bot). Not implemented yet.
 *   caspian init agent    an AI agent: CLI secret + ./.env + .caspian/AGENT.md
 *
 * Sign-in is the same device-auth as caspian login.
 */
import { resolve } from "node:path"
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

export type ProjectTarget =
  | { readonly _tag: "dir"; readonly path: string }
  | { readonly _tag: "new" }

export const projectPathPrompt = (cwd: string): string =>
  [
    "Where is the project?",
    "",
    `  Enter        this folder (${cwd})`,
    "  <path>       another folder",
    "  new          scaffold TypeScript/Python SDK project (TODO — not yet)",
    "",
    `Project path [${cwd}]: `,
  ].join("\n")

export const parseProjectChoice = (raw: string, cwd: string): ProjectTarget => {
  const token = raw.trim()
  if (token === "" || token === ".") return { _tag: "dir", path: cwd }
  if (token.toLowerCase() === "new") return { _tag: "new" }
  return { _tag: "dir", path: resolve(cwd, token) }
}

/** TODO(init-project-scaffold): TypeScript SDK / Python SDK app files. */
export const NEW_PROJECT_TODO = [
  "TODO: scaffold a new Caspian app (TypeScript SDK or Python SDK) is not implemented yet.",
  "Use an existing folder for now:",
  "  caspian init project              (asks; default is this folder)",
  "  caspian init project --path DIR",
].join("\n")

export type InitIO = {
  readonly login: LoginIO
  readonly writeCliSecret: (
    values: SecretValues,
  ) => Effect.Effect<void, UsageError>
  readonly writeProjectEnv: (
    dir: string,
    values: SecretValues,
  ) => Effect.Effect<void, UsageError>
  readonly writePlaybook: (text: string) => Effect.Effect<void, UsageError>
  readonly chooseKind: () => Effect.Effect<InitKind, UsageError>
  readonly chooseProject: (cwd: string) => Effect.Effect<ProjectTarget, UsageError>
  readonly cwd: string
  readonly cliSecretPath: string
  readonly existingApiKey?: string
  readonly existingBaseUrl: string
}

export type InitResult = {
  readonly kind: InitKind
  readonly signedIn: boolean
  readonly api_key: string
  readonly project_id: string
  readonly projectPath: string
  readonly scaffoldTodo: boolean
  readonly lines: ReadonlyArray<string>
}

export const orientation = (kind: InitKind): string =>
  [
    "Setting up Caspian.",
    "",
    `  caspian init cli       this machine — CLI secret in ~/.caspian/.env${kind === "cli" ? "  ←" : ""}`,
    `  caspian init project   this repo — SDK key in <dir>/.env${kind === "project" ? "  ←" : ""}`,
    `  caspian init agent     an AI agent — CLI + ./.env + .caspian/AGENT.md${kind === "agent" ? "  ←" : ""}`,
  ].join("\n")

const nextSteps = (
  kind: InitKind,
  cliPath: string,
  projectPath: string,
  scaffoldTodo: boolean,
): ReadonlyArray<string> => {
  switch (kind) {
    case "cli":
      return [
        `CLI secret stored in ${cliPath} (not this repo's .env).`,
        "Next: caspian channels add telegram",
        `Add credit:  ${DASHBOARD_URL}`,
      ]
    case "project":
      if (scaffoldTodo) {
        return [
          `CLI secret stored in ${cliPath}.`,
          NEW_PROJECT_TODO,
        ]
      }
      return [
        `Wrote ${projectPath}/.env for the SDK. CLI secret: ${cliPath}`,
        "Keep .env out of git.",
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

const projectTargetOf = (
  plan: InitPlan,
  io: InitIO,
  kind: InitKind,
): Effect.Effect<ProjectTarget, UsageError> => {
  if (kind !== "project") {
    return Effect.succeed({ _tag: "dir", path: io.cwd })
  }
  if (plan.fresh) return Effect.succeed({ _tag: "new" })
  if (plan.path !== "") {
    return Effect.succeed(parseProjectChoice(plan.path, io.cwd))
  }
  return io.chooseProject(io.cwd)
}

export const runInit = (
  plan: InitPlan,
  io: InitIO,
): Effect.Effect<InitResult, UsageError> =>
  Effect.gen(function* () {
    const kind: InitKind =
      plan.kind === "ask" ? yield* io.chooseKind() : plan.kind
    const target = yield* projectTargetOf(plan, io, kind)
    const scaffoldTodo = kind === "project" && target._tag === "new"
    const projectPath = target._tag === "dir" ? target.path : ""

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
    if ((kind === "project" || kind === "agent") && !scaffoldTodo) {
      const dir = kind === "agent" ? io.cwd : projectPath
      yield* io.writeProjectEnv(dir, values)
    }
    if (kind === "agent") {
      yield* io.writePlaybook(AGENT_PLAYBOOK)
    }

    const lines = [
      orientation(kind),
      "",
      signedIn ? "Signed in." : "Using existing CASPIAN_API_KEY.",
      ...(projectId !== "" ? [`Project ${projectId}`] : []),
      ...nextSteps(kind, io.cliSecretPath, projectPath, scaffoldTodo),
    ]
    return {
      kind,
      signedIn,
      api_key: apiKey,
      project_id: projectId,
      projectPath,
      scaffoldTodo,
      lines,
    }
  })

export const failAsk = (): UsageError => new UsageError({ reason: ASK_NEEDED })
