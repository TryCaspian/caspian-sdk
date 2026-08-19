import { join } from "node:path"
import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import { DASHBOARD_URL, hostedNeeded } from "../src/errors.ts"
import { parseArgv } from "../src/desugar.ts"
import { planIntent } from "../src/plan.ts"
import { runPlan } from "../src/run.ts"

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

const hostedJobs = [
  ["channels", "add", "telegram"],
  ["channels", "ls"],
  ["call", "post", "--thread", "telegram:1", "--text", "hi"],
  ["threads", "ls"],
  ["threads", "tail"],
] as const

test("hostedNeeded names flags, env, and signup", () => {
  const reason = hostedNeeded().reason
  expect(reason).toContain("--api-key")
  expect(reason).toContain("--gateway")
  expect(reason).toContain("CASPIAN_API_KEY")
  expect(reason).toContain("CASPIAN_BASE_URL")
  expect(reason).toContain(DASHBOARD_URL)
  expect(reason.includes("connect")).toBe(false)
  expect(reason).toContain("caspian login")
  expect(reason).not.toContain("caspian init")
})

for (const argv of hostedJobs) {
  test(`hosted job ${argv[0]} ${argv[1] ?? ""} needs credentials, not a throw`, () => {
    const plan = sync(planIntent(sync(parseArgv([...argv]))))
    expect(plan._tag).toBe("Gateway")
    const error = left(runPlan(plan))
    expect(error._tag).toBe("UsageError")
    expect(error.reason).toContain(DASHBOARD_URL)
    expect(error.reason).toContain("CASPIAN_API_KEY")
    expect(error.reason).toContain("--api-key")
    expect(error.reason).toContain("--gateway")
  })
}

test("catalog does not need the hosted gateway", () => {
  const plan = sync(planIntent(sync(parseArgv(["catalog"]))))
  expect(plan._tag).toBe("Local")
  const out = sync(runPlan(plan))
  expect(JSON.stringify(out)).toContain("telegram.send-photo")
})

test("self-host channels add does not need the hosted gateway", () => {
  const plan = sync(
    planIntent(
      sync(
        parseArgv([
          "channels",
          "add",
          "telegram",
          "--via",
          "self-host",
          "--bot-token",
          "123:abc",
        ]),
      ),
    ),
  )
  expect(plan._tag).toBe("Local")
  expect(sync(runPlan(plan))).toEqual({
    channel: "telegram",
    via: "self-host",
    webhook_url: "",
    inbound: true,
  })
})

const bareEnv = (): Record<string, string> => {
  const env: Record<string, string> = {}
  for (const [key, value] of Object.entries(process.env)) {
    if (
      value !== undefined &&
      key !== "CASPIAN_API_KEY" &&
      key !== "COMM_API_KEY"
    ) {
      env[key] = value
    }
  }
  return env
}

test("binary catalog works without a hosted key", async () => {
  const proc = Bun.spawn(["bun", "src/main.ts", "catalog"], {
    cwd: join(import.meta.dir, ".."),
    env: bareEnv(),
    stdout: "pipe",
    stderr: "pipe",
  })
  const out = await new Response(proc.stdout).text()
  const err = await new Response(proc.stderr).text()
  expect(await proc.exited).toBe(0)
  expect(out).toContain("telegram.send-photo")
  expect(err).toBe("")
})

test("binary call without a key asks to pass, env, or sign up", async () => {
  const proc = Bun.spawn(
    ["bun", "src/main.ts", "call", "post", "--thread", "telegram:1", "--text", "hi"],
    {
      cwd: join(import.meta.dir, ".."),
      env: bareEnv(),
      stdout: "pipe",
      stderr: "pipe",
    },
  )
  const err = await new Response(proc.stderr).text()
  expect(await proc.exited).toBe(1)
  expect(err).toContain("--api-key")
  expect(err).toContain("CASPIAN_API_KEY")
  expect(err).toContain("--gateway")
  expect(err).toContain(DASHBOARD_URL)
  expect(err).toContain("caspian login")
  expect(err).not.toContain("caspian init")
})
