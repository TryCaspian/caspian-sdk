import { expect, test } from "bun:test"
import {
  drainTransition,
  idleOverlapState,
  overlapTransition,
} from "../src/core/index.ts"

const busy = (queued = 0, skipped_count = 0) => ({
  status: "busy" as const,
  queued,
  skipped_count,
})

test("queue from idle executes and marks busy", () => {
  const result = overlapTransition(idleOverlapState, "queue", 16)
  expect(result.decision).toBe("execute")
  expect(result.new_state.status).toBe("busy")
})

test("queue while busy enqueues", () => {
  const result = overlapTransition(busy(), "queue", 16)
  expect(result.decision).toBe("enqueue")
  expect(result.new_state.queued).toBe(1)
  expect(result.new_state.skipped_count).toBe(1)
})

test("queue at bound drops", () => {
  const result = overlapTransition(busy(3), "queue", 3)
  expect(result.decision).toBe("drop")
  expect(result.new_state).toEqual(busy(3))
})

test("drop from idle executes", () => {
  const result = overlapTransition(idleOverlapState, "drop", 16)
  expect(result.decision).toBe("execute")
  expect(result.new_state.status).toBe("busy")
})

test("drop while busy always drops", () => {
  for (const queued of [0, 1, 5, 16]) {
    const state = busy(queued, queued)
    const result = overlapTransition(state, "drop", 16)
    expect(result.decision).toBe("drop")
    expect(result.new_state).toEqual(state)
  }
})

test("parallel always executes and leaves state unchanged", () => {
  const state = busy(5, 5)
  const result = overlapTransition(state, "parallel", 16)
  expect(result.decision).toBe("execute")
  expect(result.new_state).toEqual(state)
})

test("debounce while busy keeps queued at 1", () => {
  const result = overlapTransition(busy(1, 4), "debounce", 16)
  expect(result.decision).toBe("enqueue")
  expect(result.new_state.queued).toBe(1)
  expect(result.new_state.skipped_count).toBe(5)
})

test("drain with queued work executes and reports skipped", () => {
  const result = drainTransition(busy(2, 2), "queue")
  expect(result.decision).toBe("execute")
  expect(result.skipped_count).toBe(2)
  expect(result.new_state).toEqual({
    status: "busy",
    queued: 0,
    skipped_count: 0,
  })
})

test("drain with empty queue goes idle", () => {
  const result = drainTransition(busy(0), "queue")
  expect(result.decision).toBe("drop")
  expect(result.new_state.status).toBe("idle")
})
