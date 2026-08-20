import * as Effect from "effect/Effect"
import * as ParseResult from "effect/ParseResult"
import * as Schema from "effect/Schema"
import { DecodeError } from "./errors.ts"

export const strictParseOptions = {
  onExcessProperty: "error",
} as const

export const decodeStrict =
  <A, I>(schema: Schema.Schema<A, I>) =>
  (input: unknown): Effect.Effect<A, DecodeError> =>
    Schema.decodeUnknown(schema, strictParseOptions)(input).pipe(
      Effect.mapError(
        (issue) =>
          new DecodeError({
            reason: ParseResult.TreeFormatter.formatErrorSync(issue),
          }),
      ),
    )
