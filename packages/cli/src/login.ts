/**
 * Device-auth login. No sandbox key.
 *
 * POST /v1/auth/device/start (no Authorization) → show the verification URL →
 * poll /v1/auth/device/token until approved → write CASPIAN_API_KEY.
 * Matches server/src/comm_gateway/routes/device.py (RFC 8628).
 */
import * as Effect from "effect/Effect"
import { UsageError } from "./errors.ts"
import type { LoginPlan } from "./plan.ts"

export type LoginFetch = (
  input: string,
  init?: {
    method?: string
    headers?: Record<string, string>
    body?: string
  },
) => Promise<Response>

export type LoginIO = {
  readonly fetch: LoginFetch
  readonly wait: (ms: number) => Effect.Effect<void>
  readonly writeEnv: (values: {
    readonly CASPIAN_API_KEY: string
    readonly CASPIAN_BASE_URL: string
  }) => Effect.Effect<void, UsageError>
  readonly onUrl?: (url: string) => void
  readonly openUrl?: (url: string) => void
  /** Optional existing key so sign-in can bind this project to the account. */
  readonly existingApiKey?: string
  readonly maxPolls?: number
}

export type LoginResult = {
  readonly url: string
  readonly api_key: string
  readonly project_id: string
}

const asRecord = (value: unknown): { readonly [key: string]: unknown } =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as { readonly [key: string]: unknown })
    : {}

const str = (record: { readonly [key: string]: unknown }, key: string): string => {
  const value = record[key]
  return typeof value === "string" ? value : ""
}

const num = (record: { readonly [key: string]: unknown }, key: string, fallback: number): number => {
  const value = record[key]
  return typeof value === "number" ? value : fallback
}

const postJson = (
  fetchImpl: LoginFetch,
  url: string,
  body: { readonly [key: string]: string },
): Effect.Effect<{ readonly [key: string]: unknown }, UsageError> =>
  Effect.tryPromise({
    try: async () => {
      const response = await fetchImpl(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      })
      const text = await response.text()
      if (!response.ok) {
        throw new Error(`Error ${response.status}: ${text.slice(0, 200)}`)
      }
      const parsed: unknown = JSON.parse(text)
      return asRecord(parsed)
    },
    catch: (cause) =>
      new UsageError({
        reason: cause instanceof Error ? cause.message : String(cause),
      }),
  })

export const runLogin = (
  plan: LoginPlan,
  io: LoginIO,
): Effect.Effect<LoginResult, UsageError> =>
  Effect.gen(function* () {
    const gateway = plan.gateway.replace(/\/$/, "")
    const startBody =
      io.existingApiKey !== undefined && io.existingApiKey !== ""
        ? { api_key: io.existingApiKey }
        : {}
    const start = yield* postJson(
      io.fetch,
      `${gateway}/v1/auth/device/start`,
      startBody,
    )
    const url =
      str(start, "verification_uri_complete") || str(start, "verification_uri")
    if (url === "") {
      return yield* Effect.fail(
        new UsageError({ reason: "login start did not return a verification URL" }),
      )
    }
    const deviceCode = str(start, "device_code")
    if (deviceCode === "") {
      return yield* Effect.fail(
        new UsageError({ reason: "login start did not return a device_code" }),
      )
    }
    if (io.onUrl !== undefined) io.onUrl(url)
    if (plan.open && io.openUrl !== undefined) io.openUrl(url)

    const intervalMs = Math.max(0, num(start, "interval", 5) * 1000)
    const maxPolls = io.maxPolls ?? 120
    for (let i = 0; i < maxPolls; i++) {
      const token = yield* postJson(io.fetch, `${gateway}/v1/auth/device/token`, {
        device_code: deviceCode,
      })
      const status = str(token, "status")
      if (status === "approved") {
        const apiKey = str(token, "api_key")
        if (apiKey === "") {
          return yield* Effect.fail(
            new UsageError({ reason: "login approved but no api_key returned" }),
          )
        }
        const projectId = str(token, "project_id")
        yield* io.writeEnv({
          CASPIAN_API_KEY: apiKey,
          CASPIAN_BASE_URL: gateway,
        })
        return { url, api_key: apiKey, project_id: projectId }
      }
      if (status === "expired" || status === "not_found") {
        return yield* Effect.fail(
          new UsageError({ reason: `Login ${status}. Run caspian login again.` }),
        )
      }
      yield* io.wait(intervalMs)
    }
    return yield* Effect.fail(
      new UsageError({ reason: "Login timed out. Run caspian login again." }),
    )
  })
