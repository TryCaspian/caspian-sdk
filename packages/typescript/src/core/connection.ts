import * as Schema from "effect/Schema"
import { ConnectionId } from "./ids.ts"
import { JsonObject } from "./json.ts"

export const Via = Schema.Literal("hosted", "self-host")
export type Via = typeof Via.Type

export const Connection = Schema.Struct({
  id: ConnectionId,
  channel: Schema.String,
  via: Schema.optional(Via),
  config: Schema.optionalWith(JsonObject, { default: () => ({}) }),
})
export type Connection = typeof Connection.Type
