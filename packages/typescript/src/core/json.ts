/**
 * JSON values allowed on opaque payloads (`raw`, tool args, thread state).
 * Not `unknown`: only JSON, parsed at the boundary.
 */
import * as Schema from "effect/Schema"

export type Json =
  | string
  | number
  | boolean
  | null
  | ReadonlyArray<Json>
  | { readonly [key: string]: Json }

export const Json: Schema.Schema<Json> = Schema.suspend(() =>
  Schema.Union(
    Schema.String,
    Schema.Number,
    Schema.Boolean,
    Schema.Null,
    Schema.Array(Json),
    Schema.Record({ key: Schema.String, value: Json }),
  ),
)

export const JsonObject = Schema.Record({ key: Schema.String, value: Json })
export type JsonObject = typeof JsonObject.Type
