import { expect, test } from "bun:test"
import { existsSync, mkdtempSync, readFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { parseDotenv } from "../src/credentials.ts"

const bareEnv = (): Record<string, string> => {
  const env: Record<string, string> = {}
  for (const [key, value] of Object.entries(process.env)) {
    if (
      value !== undefined &&
      key !== "CASPIAN_API_KEY" &&
      key !== "COMM_API_KEY" &&
      key !== "CASPIAN_HOME" &&
      key !== "CASPIAN_BASE_URL" &&
      key !== "COMM_BASE_URL"
    ) {
      env[key] = value
    }
  }
  return env
}

test("binary --help lists namespaces not connect", async () => {
  const proc = Bun.spawn(["bun", "src/main.ts", "--help"], {
    cwd: join(import.meta.dir, ".."),
    stdout: "pipe",
    stderr: "pipe",
  })
  const text = await new Response(proc.stdout).text()
  const code = await proc.exited
  expect(code).toBe(0)
  for (const name of ["channels", "call", "catalog", "threads", "login", "init"]) {
    expect(text).toContain(name)
  }
  expect(text.includes("connect")).toBe(false)
  expect(text.includes("sandbox")).toBe(false)
})

test("binary init cli stores the key in CASPIAN_HOME, not cwd .env", async () => {
  const secret = mkdtempSync(join(tmpdir(), "caspian-secret-"))
  const cwd = mkdtempSync(join(tmpdir(), "caspian-proj-"))
  const proc = Bun.spawn(["bun", join(import.meta.dir, "../src/main.ts"), "init", "cli"], {
    cwd,
    env: {
      ...bareEnv(),
      CASPIAN_HOME: secret,
      CASPIAN_API_KEY: "ck_test",
      CASPIAN_BASE_URL: "https://gw.example",
    },
    stdout: "pipe",
    stderr: "pipe",
  })
  const out = await new Response(proc.stdout).text()
  const err = await new Response(proc.stderr).text()
  expect(await proc.exited).toBe(0)
  expect(err).toBe("")
  expect(out).toContain("Setting up Caspian")
  expect(out).toContain("caspian init project")
  expect(out).toContain("caspian init agent")
  expect(out).not.toContain("sandbox")
  const stored = parseDotenv(readFileSync(join(secret, ".env"), "utf8"))
  expect(stored["CASPIAN_API_KEY"]).toBe("ck_test")
  expect(existsSync(join(cwd, ".env"))).toBe(false)
})

test("binary init project writes ./.env for the SDK and the CLI secret", async () => {
  const secret = mkdtempSync(join(tmpdir(), "caspian-secret-"))
  const cwd = mkdtempSync(join(tmpdir(), "caspian-proj-"))
  const proc = Bun.spawn(
    ["bun", join(import.meta.dir, "../src/main.ts"), "init", "project"],
    {
      cwd,
      env: {
        ...bareEnv(),
        CASPIAN_HOME: secret,
        CASPIAN_API_KEY: "ck_test",
        CASPIAN_BASE_URL: "https://gw.example",
      },
      stdout: "pipe",
      stderr: "pipe",
    },
  )
  expect(await proc.exited).toBe(0)
  expect(parseDotenv(readFileSync(join(secret, ".env"), "utf8"))["CASPIAN_API_KEY"]).toBe(
    "ck_test",
  )
  expect(parseDotenv(readFileSync(join(cwd, ".env"), "utf8"))["CASPIAN_API_KEY"]).toBe(
    "ck_test",
  )
})
