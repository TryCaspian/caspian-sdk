import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import * as Either from "effect/Either"
import * as Schema from "effect/Schema"
import {
  App,
  Command,
  Event,
  decodeApp,
  decodeEvent,
  emptyStepState,
  step,
} from "../src/core/index.ts"
import type { StepState } from "../src/core/index.ts"

const vectorsUrl = new URL("../../../vectors/step_vectors.json", import.meta.url)

const runEither = <A, E>(effect: Effect.Effect<A, E>) =>
  Effect.runSync(Effect.either(effect))

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const channelNameOf = (vector: Record<string, unknown>): string =>
  typeof vector.channel_name === "string" ? vector.channel_name : ""

const asBoolean = (value: unknown, label: string): boolean => {
  expect(typeof value, label).toBe("boolean")
  return value === true
}

const asNumber = (value: unknown, label: string): number => {
  expect(typeof value, label).toBe("number")
  return typeof value === "number" ? value : 0
}

test("golden vector fixture is present for Phase 2 kernel replay", async () => {
  const file = Bun.file(vectorsUrl)
  expect(await file.exists()).toBe(true)

  const vectors: unknown = await file.json()
  expect(Array.isArray(vectors)).toBe(true)
  if (!Array.isArray(vectors)) {
    return
  }
  expect(vectors.length).toBeGreaterThan(0)
  for (const vector of vectors) {
    expect(isRecord(vector)).toBe(true)
    if (!isRecord(vector)) {
      continue
    }
    expect("name" in vector).toBe(true)
    expect("app" in vector).toBe(true)
  }
})

test("every golden vector App and Event round-trips through Schema", async () => {
  const vectors: unknown = await Bun.file(vectorsUrl).json()
  expect(Array.isArray(vectors)).toBe(true)
  if (!Array.isArray(vectors)) {
    return
  }

  for (const vector of vectors) {
    expect(isRecord(vector)).toBe(true)
    if (!isRecord(vector)) {
      continue
    }

    const appResult = runEither(decodeApp(vector.app))
    expect(Either.isRight(appResult), String(vector.name)).toBe(true)
    if (Either.isLeft(appResult)) {
      continue
    }
    expect(Schema.encodeSync(App)(appResult.right) as unknown).toEqual(vector.app)

    if ("event" in vector) {
      const eventResult = runEither(decodeEvent(vector.event))
      expect(Either.isRight(eventResult), String(vector.name)).toBe(true)
      if (Either.isLeft(eventResult)) {
        continue
      }
      expect(Schema.encodeSync(Event)(eventResult.right) as unknown).toEqual(vector.event)
    }

    if ("events" in vector && Array.isArray(vector.events)) {
      for (const event of vector.events) {
        const eventResult = runEither(decodeEvent(event))
        expect(Either.isRight(eventResult), String(vector.name)).toBe(true)
        if (Either.isLeft(eventResult)) {
          continue
        }
        expect(Schema.encodeSync(Event)(eventResult.right) as unknown).toEqual(event)
      }
    }

    if ("expected_commands" in vector && Array.isArray(vector.expected_commands)) {
      for (const command of vector.expected_commands) {
        const decoded = Schema.decodeUnknownSync(Command, {
          onExcessProperty: "error",
        })(command)
        expect(Schema.encodeSync(Command)(decoded) as unknown).toEqual(command)
      }
    }
  }
})

test("replay golden vectors through step()", async () => {
  const vectors: unknown = await Bun.file(vectorsUrl).json()
  expect(Array.isArray(vectors)).toBe(true)
  if (!Array.isArray(vectors)) {
    return
  }

  for (const vector of vectors) {
    expect(isRecord(vector)).toBe(true)
    if (!isRecord(vector)) {
      continue
    }

    const appResult = runEither(decodeApp(vector.app))
    expect(Either.isRight(appResult), String(vector.name)).toBe(true)
    if (Either.isLeft(appResult)) {
      continue
    }
    const app = appResult.right
    const channelName = channelNameOf(vector)

    if ("event" in vector) {
      const eventResult = runEither(decodeEvent(vector.event))
      expect(Either.isRight(eventResult), String(vector.name)).toBe(true)
      if (Either.isLeft(eventResult)) {
        continue
      }
      const result = step(emptyStepState, eventResult.right, app, { channelName })
      expect(result.dropped, String(vector.name)).toBe(
        asBoolean(vector.expected_dropped, `${String(vector.name)} expected_dropped`),
      )
      expect(Schema.encodeSync(Schema.Array(Command))(result.commands) as unknown).toEqual(
        vector.expected_commands,
      )
    }

    if ("events" in vector && Array.isArray(vector.events) && Array.isArray(vector.expected_results)) {
      let state: StepState = emptyStepState
      for (const [index, eventJson] of vector.events.entries()) {
        const eventResult = runEither(decodeEvent(eventJson))
        expect(Either.isRight(eventResult), `${String(vector.name)} event ${index}`).toBe(true)
        if (Either.isLeft(eventResult)) {
          continue
        }
        const result = step(state, eventResult.right, app, { channelName })
        state = result.state
        const expected = vector.expected_results[index]
        expect(isRecord(expected), `${String(vector.name)} result ${index}`).toBe(true)
        if (!isRecord(expected)) {
          continue
        }
        expect(result.commands.length, `${String(vector.name)} event ${index}`).toBe(
          asNumber(expected.commands_count, `${String(vector.name)} commands_count`),
        )
        expect(result.dropped, `${String(vector.name)} event ${index}`).toBe(
          asBoolean(expected.dropped, `${String(vector.name)} dropped`),
        )
      }
    }
  }
})
