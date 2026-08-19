import { expect, test } from "bun:test"
import { helpText } from "../src/desugar.ts"

test("help lists namespaces not connect", () => {
  const text = helpText()
  for (const name of ["channels", "call", "catalog", "threads", "login", "init"]) {
    expect(text).toContain(name)
  }
  expect(text).toContain("--api-key")
  expect(text).toContain("https://dashboard.trycaspianai.com")
  expect(text).toContain("~/.caspian/.env")
  expect(text.includes("connect")).toBe(false)
  expect(text.includes("sandbox")).toBe(false)
})
