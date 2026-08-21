import { expect, test } from "bun:test"
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import {
  caspianHome,
  cliSecretPath,
  mergeEnv,
  parseDotenv,
  resolveCredentials,
  writeEnvFile,
} from "../src/credentials.ts"

test("CASPIAN_HOME is the CLI secret directory", () => {
  expect(caspianHome({ CASPIAN_HOME: "/tmp/caspian-secret" })).toBe(
    "/tmp/caspian-secret",
  )
  expect(cliSecretPath({ CASPIAN_HOME: "/tmp/caspian-secret" })).toBe(
    "/tmp/caspian-secret/.env",
  )
})

test("flag beats env beats CLI secret beats project .env", () => {
  const creds = resolveCredentials({
    flagKey: "from-flag",
    env: { CASPIAN_API_KEY: "from-env" },
    cliEnvText: "CASPIAN_API_KEY=from-cli\n",
    projectEnvText: "CASPIAN_API_KEY=from-project\n",
    defaultBaseUrl: "https://api.trycaspianai.com",
  })
  expect(creds.apiKey).toBe("from-flag")
})

test("CLI secret beats project .env when no flag or env", () => {
  const creds = resolveCredentials({
    env: {},
    cliEnvText: "CASPIAN_API_KEY=from-cli\nCASPIAN_BASE_URL=https://cli.example\n",
    projectEnvText: "CASPIAN_API_KEY=from-project\nCASPIAN_BASE_URL=https://proj.example\n",
  })
  expect(creds.apiKey).toBe("from-cli")
  expect(creds.baseUrl).toBe("https://cli.example")
})

test("project .env is last-resort fallback", () => {
  const creds = resolveCredentials({
    env: {},
    cliEnvText: "",
    projectEnvText: "CASPIAN_API_KEY=from-project\n",
    defaultBaseUrl: "https://api.trycaspianai.com",
  })
  expect(creds.apiKey).toBe("from-project")
})

test("writeEnvFile merges and does not clobber other keys", () => {
  const dir = mkdtempSync(join(tmpdir(), "caspian-cred-"))
  const path = join(dir, ".env")
  writeFileSync(path, "OTHER=keep\nCASPIAN_API_KEY=old\n")
  writeEnvFile(path, {
    CASPIAN_API_KEY: "new",
    CASPIAN_BASE_URL: "https://gw.example",
  })
  const text = readFileSync(path, "utf8")
  const parsed = parseDotenv(text)
  expect(parsed["OTHER"]).toBe("keep")
  expect(parsed["CASPIAN_API_KEY"]).toBe("new")
  expect(parsed["CASPIAN_BASE_URL"]).toBe("https://gw.example")
  expect(mergeEnv("A=1\n", { B: "2" })).toContain("A=1")
})
