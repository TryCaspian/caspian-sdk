import { expect, test } from "bun:test"
import * as Schema from "effect/Schema"
import {
  Event,
  action,
  channel,
  command,
  data,
  dm,
  evaluate,
  group,
  message,
} from "../src/core/index.ts"

const dmEvent = Schema.decodeUnknownSync(Event)({
  kind: "message",
  thread_id: "telegram:1",
  text: "hi",
  chat_kind: "dm",
  sender: "u",
  raw: {},
})

const actionEvent = Schema.decodeUnknownSync(Event)({
  kind: "action",
  thread_id: "telegram:1",
  data: "ok",
  sender: "u",
  raw: {},
})

test("kind predicate matches event.kind", () => {
  expect(evaluate(message(), dmEvent, "telegram")).toBe(true)
  expect(evaluate(action(), dmEvent, "telegram")).toBe(false)
  expect(evaluate(action(), actionEvent, "telegram")).toBe(true)
})

test("channel predicate uses the runner-supplied channel name", () => {
  expect(evaluate(channel("telegram"), dmEvent, "telegram")).toBe(true)
  expect(evaluate(channel("discord"), dmEvent, "telegram")).toBe(false)
})

test("chat_kind predicate is false on actions (no chat_kind field)", () => {
  expect(evaluate(dm(), dmEvent, "telegram")).toBe(true)
  expect(evaluate(group(), dmEvent, "telegram")).toBe(false)
  expect(evaluate(dm(), actionEvent, "telegram")).toBe(false)
})

test("command matches slash, bot suffix, and extra words", () => {
  const help = Schema.decodeUnknownSync(Event)({
    kind: "message",
    thread_id: "telegram:1",
    text: "/help@caspian_test_bot please",
    chat_kind: "dm",
    sender: "u",
    raw: {},
  })
  expect(evaluate(command("help"), help, "telegram")).toBe(true)
  expect(evaluate(command("help"), dmEvent, "telegram")).toBe(false)
})

test("data matches action payload", () => {
  expect(evaluate(data("ok"), actionEvent, "telegram")).toBe(true)
  expect(evaluate(data("story"), actionEvent, "telegram")).toBe(false)
})

test("and / or / not compose", () => {
  const pred = {
    op: "and" as const,
    left: message(),
    right: {
      op: "not" as const,
      inner: channel("discord"),
    },
  }
  expect(evaluate(pred, dmEvent, "telegram")).toBe(true)
  expect(evaluate(pred, dmEvent, "discord")).toBe(false)
  expect(
    evaluate(
      { op: "or", left: action(), right: message() },
      dmEvent,
      "telegram",
    ),
  ).toBe(true)
})
