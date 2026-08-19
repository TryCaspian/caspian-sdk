import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import { parseArgv } from "../src/desugar.ts"
import { runInit } from "../src/init.ts"
import type { LoginFetch } from "../src/login.ts"
import { planIntent } from "../src/plan.ts"

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

test("init defaults to cli setup, not sandbox mint", () => {
  const plan = sync(planIntent(sync(parseArgv(["init"]))))
  expect(plan._tag).toBe("Init")
  if (plan._tag === "Init") {
    expect(plan.kind).toBe("cli")
    expect(plan.force).toBe(false)
  }
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

test("init cli with an existing key writes the CLI secret, not project .env", async () => {
  const cli: Array<Record<string, string>> = []
  const project: Array<Record<string, string>> = []
  const result = await Effect.runPromise(
    runInit(
      {
        _tag: "Init",
        kind: "cli",
        gateway: "https://gw.example",
        open: false,
        force: false,
      },
      {
        login: {
          fetch: async () => {
            throw new Error("login should not run when a key already exists")
          },
          wait: () => Effect.void,
          writeEnv: () => Effect.void,
        },
        writeCliSecret: (values) =>
          Effect.sync(() => {
            cli.push(values)
          }),
        writeProjectEnv: (values) =>
          Effect.sync(() => {
            project.push(values)
          }),
        cliSecretPath: "/tmp/caspian-secret/.env",
        existingApiKey: "ck_existing",
        existingBaseUrl: "https://gw.example",
      },
    ),
  )
  expect(result.signedIn).toBe(false)
  expect(cli[0]?.["CASPIAN_API_KEY"]).toBe("ck_existing")
  expect(project).toEqual([])
  expect(result.lines.join("\n")).toContain("not this repo's .env")
  expect(result.lines.join("\n")).toContain("caspian init project")
})

test("init project writes CLI secret and ./.env", async () => {
  const cli: Array<Record<string, string>> = []
  const project: Array<Record<string, string>> = []
  await Effect.runPromise(
    runInit(
      {
        _tag: "Init",
        kind: "project",
        gateway: "https://gw.example",
        open: false,
        force: false,
      },
      {
        login: {
          fetch: async () => {
            throw new Error("login should not run when a key already exists")
          },
          wait: () => Effect.void,
          writeEnv: () => Effect.void,
        },
        writeCliSecret: (values) =>
          Effect.sync(() => {
            cli.push(values)
          }),
        writeProjectEnv: (values) =>
          Effect.sync(() => {
            project.push(values)
          }),
        cliSecretPath: "/tmp/caspian-secret/.env",
        existingApiKey: "ck_existing",
        existingBaseUrl: "https://gw.example",
      },
    ),
  )
  expect(cli[0]?.["CASPIAN_API_KEY"]).toBe("ck_existing")
  expect(project[0]?.["CASPIAN_API_KEY"]).toBe("ck_existing")
})

test("init agent without a key signs in and does not write project .env", async () => {
  const cli: Array<Record<string, string>> = []
  const project: Array<Record<string, string>> = []
  const result = await Effect.runPromise(
    runInit(
      {
        _tag: "Init",
        kind: "agent",
        gateway: "https://gw.example",
        open: false,
        force: false,
      },
      {
        login: {
          fetch: approvedFetch,
          wait: () => Effect.void,
          writeEnv: () => Effect.void,
        },
        writeCliSecret: (values) =>
          Effect.sync(() => {
            cli.push(values)
          }),
        writeProjectEnv: (values) =>
          Effect.sync(() => {
            project.push(values)
          }),
        cliSecretPath: "/tmp/caspian-secret/.env",
        existingBaseUrl: "https://gw.example",
      },
    ),
  )
  expect(result.signedIn).toBe(true)
  expect(result.api_key).toBe("ck_live_1")
  expect(cli[0]?.["CASPIAN_API_KEY"]).toBe("ck_live_1")
  expect(project).toEqual([])
  const text = result.lines.join("\n")
  expect(text).toContain("caspian channels add")
  expect(text).toContain("caspian catalog")
  expect(text).toContain("caspian call")
  expect(text).toContain("caspian init project")
})
