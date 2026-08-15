import { expect, test } from "bun:test"

const vectorsUrl = new URL("../../../vectors/step_vectors.json", import.meta.url)

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
    expect(vector).toBeTypeOf("object")
    expect(vector).not.toBeNull()
    if (typeof vector !== "object" || vector === null) {
      continue
    }
    expect("name" in vector).toBe(true)
    expect("app" in vector).toBe(true)
  }
})

test.todo("replay golden vectors through step() — Phase 2", () => {
  // Implemented in Phase 2 when `step()` exists.
})
