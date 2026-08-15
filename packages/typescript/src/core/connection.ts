import * as Schema from "effect/Schema"
import { ConnectionId } from "./ids.ts"
import { JsonObject } from "./json.ts"

export const Connection = Schema.Struct({
  id: ConnectionId,
  channel: Schema.String,
  config: Schema.optionalWith(JsonObject, { default: () => ({}) }),
})
export type Connection = typeof Connection.Type
