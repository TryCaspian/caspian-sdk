/**
 * Provision — mint a Connection. Not A, not an adapter, not inbound.
 *
 * `via` is required: "hosted" | "self-host". Omitting via is a DecodeError.
 */
import * as Effect from "effect/Effect"
import * as Schema from "effect/Schema"
import { Connection } from "../core/connection.ts"
import { ProvisionError, type DecodeError } from "../core/errors.ts"
import { ConnectionId } from "../core/ids.ts"
import type { JsonObject } from "../core/json.ts"
import { decodeStrict } from "../core/parse.ts"

const HostedAdd = Schema.Struct({
  via: Schema.Literal("hosted"),
  displayName: Schema.optional(Schema.String),
})

const SelfHostAdd = Schema.Struct({
  via: Schema.Literal("self-host"),
  botToken: Schema.String,
  webhookUrl: Schema.optional(Schema.String),
  inbound: Schema.optionalWith(Schema.Boolean, { default: () => true }),
})

export const ChannelAddOptions = Schema.Union(HostedAdd, SelfHostAdd)
export type ChannelAddOptions = typeof ChannelAddOptions.Type

export const decodeChannelAdd = decodeStrict(ChannelAddOptions)

const connectionOf = (input: unknown): Connection =>
  Schema.decodeUnknownSync(Connection)(input)

export const addChannel = (
  channel: string,
  options: unknown,
  id = "conn:1",
): Effect.Effect<Connection, DecodeError | ProvisionError> =>
  Effect.gen(function* () {
    const decoded = yield* decodeChannelAdd(options)
    if (decoded.via === "self-host") {
      if (decoded.inbound && decoded.webhookUrl === undefined) {
        return yield* Effect.fail(
          new ProvisionError({
            reason: "self-host inbound requires webhookUrl",
          }),
        )
      }
      const config: JsonObject = {
        botToken: decoded.botToken,
        inbound: decoded.inbound,
        ...(decoded.webhookUrl === undefined
          ? {}
          : { webhookUrl: decoded.webhookUrl }),
      }
      return connectionOf({
        id: Schema.decodeUnknownSync(ConnectionId)(id),
        channel,
        via: "self-host",
        config,
      })
    }
    return connectionOf({
      id: Schema.decodeUnknownSync(ConnectionId)(id),
      channel,
      via: "hosted",
      config:
        decoded.displayName === undefined
          ? {}
          : { displayName: decoded.displayName },
    })
  })
