import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import { decodeApp } from "../src/core/index.ts"
import {
  decodeOnMessageOptions,
  desugarOnAction,
  desugarOnMessage,
} from "../src/facade/desugar.ts"

const vectorsUrl = new URL("../../../vectors/desugar_vectors.json", import.meta.url)

const runEither = <A, E>(effect: Effect.Effect<A, E>) =>
  Effect.runSync(Effect.either(effect))

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

test("golden desugar vectors replay options into App", async () => {
  const file = Bun.file(vectorsUrl)
  expect(await file.exists()).toBe(true)
  const vectors: unknown = await file.json()
  expect(Array.isArray(vectors)).toBe(true)
  if (!Array.isArray(vectors)) {
    return
  }
  for (const vector of vectors) {
    expect(isRecord(vector)).toBe(true)
    if (!isRecord(vector)) {
      continue
    }
    const method = vector.method
    const handlerId = vector.handler_id
    expect(typeof handlerId).toBe("string")
    if (typeof handlerId !== "string") {
      continue
    }
    const options = vector.options
    const rule =
      method === "onAction"
        ? desugarOnAction(options, handlerId)
        : desugarOnMessage(options, handlerId)
    const expected = runEither(decodeApp(vector.expected_app))
    expect(Either.isRight(expected), String(vector.name)).toBe(true)
    if (Either.isLeft(expected)) {
      continue
    }
    expect({ rules: [rule] }, String(vector.name)).toEqual(expected.right)
  }
})

test("onMessage rejects extra option keys", () => {
  const result = runEither(decodeOnMessageOptions({ kind: "dm", evil: true }))
  expect(Either.isLeft(result)).toBe(true)
  if (Either.isRight(result)) {
    return
  }
  expect(result.left._tag).toBe("DecodeError")
})

test("onMessage rejects kind text (event kind is not an option)", () => {
  const result = runEither(decodeOnMessageOptions({ kind: "text" }))
  expect(Either.isLeft(result)).toBe(true)
})
