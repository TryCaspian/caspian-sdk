import { expect, test } from "bun:test"
import * as Effect from "effect/Effect"
import { getCatalog, loadCatalog, searchCatalog } from "../src/catalog.ts"
import { parseArgv } from "../src/desugar.ts"
import type { CatalogGet, CatalogSearch } from "../src/intent.ts"

const sync = <A, E>(effect: Effect.Effect<A, E>): A => Effect.runSync(effect)

test("catalog lists post and telegram.send-photo", () => {
  const ids = new Set(loadCatalog().map((entry) => entry.id))
  expect(ids.has("post")).toBe(true)
  expect(ids.has("telegram.send-photo")).toBe(true)
  expect(ids.has("slack.post")).toBe(false)
})

test("catalog search photo", () => {
  const hits = searchCatalog("send a photo")
  expect(hits.some((entry) => entry.id === "telegram.send-photo")).toBe(true)
})

test("catalog get", () => {
  const entry = sync(getCatalog("telegram.send-photo"))
  expect(entry.command_tag).toBe("SendMedia")
})

test("argv catalog does not invoke", () => {
  expect(sync(parseArgv(["catalog", "search", "send a photo"]))).toEqual({
    _tag: "CatalogSearch",
    query: "send a photo",
  } satisfies CatalogSearch)
  expect(sync(parseArgv(["catalog", "get", "telegram.send-photo"]))).toEqual({
    _tag: "CatalogGet",
    id: "telegram.send-photo",
  } satisfies CatalogGet)
})
