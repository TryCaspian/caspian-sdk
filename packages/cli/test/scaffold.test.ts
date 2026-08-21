import { expect, test } from "bun:test"
import {
  filesFor,
  INIT_STACKS,
  parseStackChoice,
  type InitStack,
} from "../src/scaffold.ts"

test("parseStackChoice accepts numbers and names", () => {
  expect(parseStackChoice("1")).toBe("openai-python")
  expect(parseStackChoice(" openai-ts ")).toBe("openai-ts")
  expect(parseStackChoice("3")).toBe("mastra")
  expect(parseStackChoice("ai-sdk")).toBe("ai-sdk")
  expect(parseStackChoice("langchain")).toBeUndefined()
})

test("every stack writes a hosted-email agent that uses cx.tools", () => {
  for (const stack of INIT_STACKS) {
    const files = filesFor(stack)
    const paths = files.map((file) => file.path)
    expect(paths).toContain(".gitignore")
    expect(paths).toContain("README.md")
    const code =
      files.find((file) => file.path === "main.py" || file.path === "index.ts")
        ?.contents ?? ""
    expect(code).toContain("email")
    expect(code).toContain("cx.tools")
    expect(code).toContain("post_message")
    expect(code).toContain("OPENAI_API_KEY")
  }
})

test("openai-python is a uv project; typescript stacks are bun", () => {
  expect(filesFor("openai-python").map((file) => file.path)).toContain(
    "pyproject.toml",
  )
  expect(filesFor("openai-python").map((file) => file.path)).not.toContain(
    "package.json",
  )
  const ts: ReadonlyArray<InitStack> = ["openai-ts", "mastra", "ai-sdk"]
  for (const stack of ts) {
    expect(filesFor(stack).map((file) => file.path)).toContain("package.json")
    expect(filesFor(stack).map((file) => file.path)).toContain("index.ts")
  }
})
