import { expect, test } from "bun:test"

test("core dependency graph forbids adapters, provision, and node builtins", () => {
  const result = Bun.spawnSync({
    cmd: [
      "bunx",
      "depcruise",
      "src/core",
      "--config",
      ".dependency-cruiser.cjs",
      "--output-type",
      "err",
    ],
    cwd: `${import.meta.dir}/..`,
    stdout: "pipe",
    stderr: "pipe",
  })

  const stderr = result.stderr.toString()
  const stdout = result.stdout.toString()
  expect(result.exitCode, `${stdout}\n${stderr}`).toBe(0)
})
