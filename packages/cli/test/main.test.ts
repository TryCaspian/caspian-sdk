import { expect, test } from "bun:test"
import { join } from "node:path"

test("binary --help lists namespaces not connect", async () => {
  const proc = Bun.spawn(["bun", "src/main.ts", "--help"], {
    cwd: join(import.meta.dir, ".."),
    stdout: "pipe",
    stderr: "pipe",
  })
  const text = await new Response(proc.stdout).text()
  const code = await proc.exited
  expect(code).toBe(0)
  for (const name of ["channels", "call", "catalog", "threads", "login"]) {
    expect(text).toContain(name)
  }
  expect(text.includes("connect")).toBe(false)
  expect(text.includes("init")).toBe(false)
})

test("binary init points at login, not sandbox", async () => {
  const proc = Bun.spawn(["bun", "src/main.ts", "init"], {
    cwd: join(import.meta.dir, ".."),
    stdout: "pipe",
    stderr: "pipe",
  })
  const err = await new Response(proc.stderr).text()
  expect(await proc.exited).toBe(1)
  expect(err).toContain("caspian login")
  expect(err).not.toContain("sandbox")
})
