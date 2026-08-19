import { expect, test } from "bun:test"
import { readdir, readFile } from "node:fs/promises"
import { join } from "node:path"

const SRC = join(import.meta.dir, "../src")

const walk = async (dir: string): Promise<string[]> => {
  const entries = await readdir(dir, { withFileTypes: true })
  const files: string[] = []
  for (const entry of entries) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) files.push(...(await walk(path)))
    else if (entry.name.endsWith(".ts")) files.push(path)
  }
  return files
}

test("CLI source does not import adapters", async () => {
  const files = await walk(SRC)
  expect(files.length).toBeGreaterThan(0)
  for (const file of files) {
    const text = await readFile(file, "utf8")
    expect(text).not.toContain("caspian/telegram")
    expect(text).not.toContain("caspian/discord")
    expect(text).not.toContain("caspian/slack")
    expect(text).not.toContain("/adapters/")
    expect(text).not.toMatch(/if\s*\(\s*(channel|intent\.channel)\s*===?\s*["']telegram["']/)
  }
})
