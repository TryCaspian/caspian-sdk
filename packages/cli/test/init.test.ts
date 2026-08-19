import { expect, test } from "bun:test"
import { resolve } from "node:path"
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import { parseArgv } from "../src/desugar.ts"
import { UsageError } from "../src/errors.ts"
import {
  AGENT_PLAYBOOK,
  NEW_PROJECT_TODO,
  parseInitChoice,
  parseProjectChoice,
  runInit,
  type InitIO,
} from "../src/init.ts"
import type { LoginFetch } from "../src/login.ts"
import { planIntent, type InitPlan } from "../src/plan.ts"

const sync = <A, E>(effect: Effect.Effect<A, E>): A => Effect.runSync(effect)

const left = <A, E extends { readonly reason: string }>(
  effect: Effect.Effect<A, E>,
): E => {
  const result = Effect.runSync(Effect.either(effect))
  if (Either.isRight(result)) {
    throw new Error("expected failure as data")
  }
  return result.left
}

const initPlan = (over: Partial<InitPlan> & Pick<InitPlan, "kind">): InitPlan => ({
  _tag: "Init",
  gateway: "https://gw.example",
  open: false,
  force: false,
  path: "",
  fresh: false,
  ...over,
})

test("bare init asks rather than defaulting to cli", () => {
  const plan = sync(planIntent(sync(parseArgv(["init"]))))
  expect(plan._tag).toBe("Init")
  if (plan._tag === "Init") {
    expect(plan.kind).toBe("ask")
    expect(plan.path).toBe("")
    expect(plan.fresh).toBe(false)
  }
})

test("init project path can be positional or --path", () => {
  const pos = sync(planIntent(sync(parseArgv(["init", "project", "./apps/bot"]))))
  expect(pos._tag).toBe("Init")
  if (pos._tag === "Init") {
    expect(pos.kind).toBe("project")
    expect(pos.path).toBe("./apps/bot")
  }
  const flagged = sync(
    planIntent(sync(parseArgv(["init", "project", "--path", "/tmp/app"]))),
  )
  expect(flagged._tag).toBe("Init")
  if (flagged._tag === "Init") {
    expect(flagged.path).toBe("/tmp/app")
  }
})

test("init project --new is the scaffold TODO, not a path", () => {
  const plan = sync(planIntent(sync(parseArgv(["init", "project", "--new"]))))
  expect(plan._tag).toBe("Init")
  if (plan._tag === "Init") {
    expect(plan.fresh).toBe(true)
    expect(plan.path).toBe("")
  }
  expect(
    left(parseArgv(["init", "project", "--new", "--path", "/tmp"])).reason,
  ).toContain("not both")
})

test("init project and agent are Plans", () => {
  const project = sync(planIntent(sync(parseArgv(["init", "project", "--open"]))))
  expect(project._tag).toBe("Init")
  if (project._tag === "Init") {
    expect(project.kind).toBe("project")
    expect(project.open).toBe(true)
  }
  const agent = sync(planIntent(sync(parseArgv(["init", "agent"]))))
  expect(agent._tag).toBe("Init")
  if (agent._tag === "Init") {
    expect(agent.kind).toBe("agent")
  }
})

test("init sandbox is not a kind", () => {
  expect(left(parseArgv(["init", "sandbox"])).reason).toContain(
    "caspian init [cli|project|agent]",
  )
})

test("parseInitChoice accepts numbers and names", () => {
  expect(parseInitChoice("1")).toBe("cli")
  expect(parseInitChoice(" project ")).toBe("project")
  expect(parseInitChoice("3")).toBe("agent")
  expect(parseInitChoice("nope")).toBeUndefined()
})

test("parseProjectChoice defaults to cwd, new is scaffold, else a path", () => {
  expect(parseProjectChoice("", "/work")).toEqual({ _tag: "dir", path: "/work" })
  expect(parseProjectChoice(".", "/work")).toEqual({ _tag: "dir", path: "/work" })
  expect(parseProjectChoice("new", "/work")).toEqual({ _tag: "new" })
  expect(parseProjectChoice("apps/bot", "/work")).toEqual({
    _tag: "dir",
    path: resolve("/work", "apps/bot"),
  })
})

const jsonResponse = (status: number, body: unknown): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  })

const approvedFetch: LoginFetch = async (input) => {
  if (String(input).endsWith("/start")) {
    return jsonResponse(200, {
      device_code: "dev_1",
      verification_uri_complete: "https://gw.example/device?code=ABCD",
      interval: 0,
    })
  }
  return jsonResponse(200, {
    status: "approved",
    api_key: "ck_live_1",
    project_id: "proj_1",
  })
}

const ioOf = (
  partial: Partial<InitIO> & {
    readonly writeCliSecret: InitIO["writeCliSecret"]
    readonly writeProjectEnv: InitIO["writeProjectEnv"]
  },
): InitIO => ({
  login: {
    fetch: async () => {
      throw new Error("login should not run when a key already exists")
    },
    wait: () => Effect.void,
    writeEnv: () => Effect.void,
  },
  writePlaybook: () => Effect.void,
  chooseKind: () =>
    Effect.fail(new UsageError({ reason: "should not ask" })),
  chooseProject: () => Effect.succeed({ _tag: "dir", path: "/tmp/proj" }),
  cwd: "/tmp/proj",
  cliSecretPath: "/tmp/caspian-secret/.env",
  existingApiKey: "ck_existing",
  existingBaseUrl: "https://gw.example",
  ...partial,
})

test("init cli with an existing key writes the CLI secret, not project .env", async () => {
  const cli: Array<Record<string, string>> = []
  const project: Array<{ dir: string; values: Record<string, string> }> = []
  const result = await Effect.runPromise(
    runInit(
      initPlan({ kind: "cli" }),
      ioOf({
        writeCliSecret: (values) =>
          Effect.sync(() => {
            cli.push(values)
          }),
        writeProjectEnv: (dir, values) =>
          Effect.sync(() => {
            project.push({ dir, values })
          }),
      }),
    ),
  )
  expect(result.signedIn).toBe(false)
  expect(cli[0]?.["CASPIAN_API_KEY"]).toBe("ck_existing")
  expect(project).toEqual([])
  expect(result.lines.join("\n")).toContain("not this repo's .env")
})

test("bare init asks, then runs the chosen kind", async () => {
  const project: Array<{ dir: string; values: Record<string, string> }> = []
  const result = await Effect.runPromise(
    runInit(
      initPlan({ kind: "ask" }),
      ioOf({
        chooseKind: () => Effect.succeed("project"),
        writeCliSecret: () => Effect.void,
        writeProjectEnv: (dir, values) =>
          Effect.sync(() => {
            project.push({ dir, values })
          }),
      }),
    ),
  )
  expect(result.kind).toBe("project")
  expect(project[0]?.dir).toBe("/tmp/proj")
  expect(project[0]?.values["CASPIAN_API_KEY"]).toBe("ck_existing")
})

test("init project writes CLI secret and .env in the chosen folder", async () => {
  const cli: Array<Record<string, string>> = []
  const project: Array<{ dir: string; values: Record<string, string> }> = []
  const result = await Effect.runPromise(
    runInit(
      initPlan({ kind: "project", path: "/srv/bot" }),
      ioOf({
        chooseProject: () =>
          Effect.fail(new UsageError({ reason: "should not ask path" })),
        writeCliSecret: (values) =>
          Effect.sync(() => {
            cli.push(values)
          }),
        writeProjectEnv: (dir, values) =>
          Effect.sync(() => {
            project.push({ dir, values })
          }),
      }),
    ),
  )
  expect(cli[0]?.["CASPIAN_API_KEY"]).toBe("ck_existing")
  expect(project[0]?.dir).toBe("/srv/bot")
  expect(result.projectPath).toBe("/srv/bot")
  expect(result.lines.join("\n")).toContain("/srv/bot/.env")
})

test("init project --new records the SDK scaffold TODO and does not write .env", async () => {
  const project: Array<unknown> = []
  const result = await Effect.runPromise(
    runInit(
      initPlan({ kind: "project", fresh: true }),
      ioOf({
        writeCliSecret: () => Effect.void,
        writeProjectEnv: (dir, values) =>
          Effect.sync(() => {
            project.push({ dir, values })
          }),
      }),
    ),
  )
  expect(result.scaffoldTodo).toBe(true)
  expect(project).toEqual([])
  expect(result.lines.join("\n")).toContain(NEW_PROJECT_TODO)
  expect(result.lines.join("\n")).toContain("TypeScript SDK")
})

test("init agent writes CLI secret, repo .env, and the agent playbook", async () => {
  const cli: Array<Record<string, string>> = []
  const project: Array<{ dir: string; values: Record<string, string> }> = []
  const playbooks: string[] = []
  const result = await Effect.runPromise(
    runInit(
      initPlan({ kind: "agent" }),
      {
        ...ioOf({
          writeCliSecret: (values) =>
            Effect.sync(() => {
              cli.push(values)
            }),
          writeProjectEnv: (dir, values) =>
            Effect.sync(() => {
              project.push({ dir, values })
            }),
          writePlaybook: (text) =>
            Effect.sync(() => {
              playbooks.push(text)
            }),
        }),
        login: {
          fetch: approvedFetch,
          wait: () => Effect.void,
          writeEnv: () => Effect.void,
        },
        existingApiKey: "",
      },
    ),
  )
  expect(result.signedIn).toBe(true)
  expect(result.api_key).toBe("ck_live_1")
  expect(cli[0]?.["CASPIAN_API_KEY"]).toBe("ck_live_1")
  expect(project[0]?.dir).toBe("/tmp/proj")
  expect(playbooks[0]).toBe(AGENT_PLAYBOOK)
  expect(result.lines.join("\n")).toContain(".caspian/AGENT.md")
})
