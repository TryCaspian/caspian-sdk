import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import { parseArgv } from "../src/desugar.ts"
import { DASHBOARD_URL, hostedNeeded } from "../src/errors.ts"
import { runLogin, type LoginFetch } from "../src/login.ts"
import { planIntent } from "../src/plan.ts"

const sync = <A, E>(effect: Effect.Effect<A, E>): A => Effect.runSync(effect)

test("init is setup, not a rejected alias for login", () => {
  const plan = sync(planIntent(sync(parseArgv(["init"]))))
  expect(plan._tag).toBe("Init")
})

test("login is a Plan that does not need an API key", () => {
  const plan = sync(planIntent(sync(parseArgv(["login", "--gateway", "https://gw.example"]))))
  expect(plan._tag).toBe("Login")
  if (plan._tag === "Login") {
    expect(plan.gateway).toBe("https://gw.example")
    expect(plan.open).toBe(false)
  }
})

test("hostedNeeded points at init/login, not sandbox", () => {
  const reason = hostedNeeded().reason
  expect(reason).toContain("caspian login")
  expect(reason).toContain("caspian init")
  expect(reason).toContain("~/.caspian/.env")
  expect(reason).toContain(DASHBOARD_URL)
  expect(reason).not.toContain("sandbox")
})

const jsonResponse = (status: number, body: unknown): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  })

test("login starts device auth, polls, and writes the key", async () => {
  const calls: Array<{ method: string; url: string; body: unknown }> = []
  const written: Array<Record<string, string>> = []
  const fetchImpl: LoginFetch = async (input, init) => {
    const url = String(input)
    const body = init?.body === undefined ? undefined : JSON.parse(String(init.body))
    calls.push({ method: init?.method ?? "GET", url, body })
    if (url.endsWith("/v1/auth/device/start")) {
      expect(init?.headers && "authorization" in (init.headers as object)).toBe(false)
      return jsonResponse(200, {
        device_code: "dev_1",
        verification_uri_complete: "https://gw.example/device?code=ABCD-EFGH",
        interval: 0,
      })
    }
    if (url.endsWith("/v1/auth/device/token")) {
      if (calls.filter((c) => c.url.endsWith("/token")).length === 1) {
        return jsonResponse(200, { status: "pending" })
      }
      return jsonResponse(200, {
        status: "approved",
        api_key: "ck_live_1",
        project_id: "proj_1",
      })
    }
    return jsonResponse(404, {})
  }

  const result = await Effect.runPromise(
    runLogin(
      { _tag: "Login", gateway: "https://gw.example", open: false },
      {
        fetch: fetchImpl,
        wait: () => Effect.void,
        writeEnv: (values) =>
          Effect.sync(() => {
            written.push(values)
          }),
      },
    ),
  )

  expect(calls[0]?.url).toBe("https://gw.example/v1/auth/device/start")
  expect(calls[0]?.body).toEqual({})
  expect(result.url).toBe("https://gw.example/device?code=ABCD-EFGH")
  expect(written[0]?.["CASPIAN_API_KEY"]).toBe("ck_live_1")
  expect(written[0]?.["CASPIAN_BASE_URL"]).toBe("https://gw.example")
})

test("login with an existing key binds it on start, still without Authorization", async () => {
  const bodies: unknown[] = []
  const fetchImpl: LoginFetch = async (input, init) => {
    const url = String(input)
    const headers = init?.headers ?? {}
    expect(
      Object.keys(headers).some((key) => key.toLowerCase() === "authorization"),
    ).toBe(false)
    bodies.push(
      init?.body === undefined ? undefined : JSON.parse(String(init.body)),
    )
    if (url.endsWith("/start")) {
      return jsonResponse(200, {
        device_code: "dev_1",
        verification_uri_complete: "https://gw.example/device?code=ABCD-EFGH",
        interval: 0,
      })
    }
    return jsonResponse(200, {
      status: "approved",
      api_key: "ck_live_1",
      project_id: "proj_1",
    })
  }

  await Effect.runPromise(
    runLogin(
      { _tag: "Login", gateway: "https://gw.example", open: false },
      {
        fetch: fetchImpl,
        wait: () => Effect.void,
        writeEnv: () => Effect.void,
        existingApiKey: "ck_existing",
      },
    ),
  )

  expect(bodies[0]).toEqual({ api_key: "ck_existing" })
})

test("login expired is UsageError data", async () => {
  const fetchImpl: LoginFetch = async (input) => {
    if (String(input).endsWith("/start")) {
      return jsonResponse(200, {
        device_code: "dev_1",
        verification_uri: "https://gw.example/device",
        interval: 0,
      })
    }
    return jsonResponse(200, { status: "expired" })
  }
  const result = await Effect.runPromise(
    Effect.either(
      runLogin(
        { _tag: "Login", gateway: "https://gw.example", open: false },
        {
          fetch: fetchImpl,
          wait: () => Effect.void,
          writeEnv: () => Effect.void,
        },
      ),
    ),
  )
  expect(Either.isLeft(result)).toBe(true)
  if (Either.isLeft(result)) {
    expect(result.left.reason).toContain("expired")
  }
})

test("login --open is parsed onto the Plan", () => {
  const plan = sync(planIntent(sync(parseArgv(["login", "--open"]))))
  expect(plan._tag).toBe("Login")
  if (plan._tag === "Login") {
    expect(plan.open).toBe(true)
  }
})
