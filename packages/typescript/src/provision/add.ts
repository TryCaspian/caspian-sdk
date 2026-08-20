/**
 * Provision — mint a Connection. Not A, not an adapter, not inbound.
 *
 * `via` is required: "hosted" | "self-host". Omitting via is a DecodeError.
 *
 * The public option names are snake_case (`bot_token`, `signing_secret`,
 * `app_token`), identical to the Python SDK, so the docs are written once for
 * both languages. camelCase is tolerated on input. Internally the config is
 * stored camelCase, which is what the adapters read.
 *
 * Like Python, unknown keys pass through into the connection config: adapters
 * define what credentials they need (signing_secret, app_secret, account_sid,
 * ...), and provisioning does not maintain a parallel list of them.
 */
import * as Effect from "effect/Effect"
import * as Schema from "effect/Schema"
import { Connection } from "../core/connection.ts"
import { DecodeError, ProvisionError } from "../core/errors.ts"
import { ConnectionId } from "../core/ids.ts"
import type { JsonObject } from "../core/json.ts"

const camel = (key: string): string =>
  key.replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase())

const asRecord = (value: unknown): { readonly [key: string]: unknown } | undefined =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as { readonly [key: string]: unknown })
    : undefined

const connectionOf = (input: unknown): Connection =>
  Schema.decodeUnknownSync(Connection)(input)

export const addChannel = (
  channel: string,
  options: unknown,
  id = "conn:1",
): Effect.Effect<Connection, DecodeError | ProvisionError> =>
  Effect.gen(function* () {
    const record = asRecord(options)
    if (record === undefined) {
      return yield* Effect.fail(
        new DecodeError({ reason: "channels.add options must be an object" }),
      )
    }
    const via = record.via
    if (via !== "hosted" && via !== "self-host") {
      return yield* Effect.fail(
        new DecodeError({ reason: 'via must be "hosted" or "self-host"' }),
      )
    }

    // Everything except via flows into config, snake_case folded to camelCase.
    const config: { [key: string]: unknown } = {}
    for (const [key, value] of Object.entries(record)) {
      if (key === "via" || value === undefined) continue
      config[camel(key)] = value
    }
    if (config.inbound === undefined) config.inbound = true

    if (via === "self-host") {
      const token = config.botToken
      if (typeof token !== "string" || token === "") {
        return yield* Effect.fail(
          new ProvisionError({
            reason:
              `Self-host ${JSON.stringify(channel)} requires bot_token. ` +
              "Omit `via` for hosted (Caspian owns the identity).",
          }),
        )
      }
    }

    return connectionOf({
      id: Schema.decodeUnknownSync(ConnectionId)(id),
      channel,
      via,
      config: config as JsonObject,
    })
  })
