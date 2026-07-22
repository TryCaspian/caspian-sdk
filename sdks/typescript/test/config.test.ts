import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { config } from "../src/config.js";

const originalCwd = process.cwd();
const originalEnv = { ...process.env };
let cwd: string;

beforeEach(() => {
  cwd = mkdtempSync(join(tmpdir(), "caspian-config-"));
  process.chdir(cwd);
  process.env = { ...originalEnv };
  delete process.env.CASPIAN_API_KEY;
  delete process.env.COMM_API_KEY;
});

afterEach(() => {
  process.chdir(originalCwd);
  process.env = { ...originalEnv };
  rmSync(cwd, { recursive: true, force: true });
});

describe("config", () => {
  it("uses explicit values before environment and dotenv values", () => {
    process.env.CASPIAN_API_KEY = "from-env";
    writeFileSync(".env", "CASPIAN_API_KEY=from-dotenv\n");

    expect(config("explicit", "CASPIAN_API_KEY", "fallback")).toBe("explicit");
  });

  it("uses process.env before dotenv", () => {
    process.env.CASPIAN_API_KEY = "from-env";
    writeFileSync(".env", "CASPIAN_API_KEY=from-dotenv\n");

    expect(config(undefined, "CASPIAN_API_KEY", "fallback")).toBe("from-env");
  });

  it("uses dotenv before the fallback", () => {
    writeFileSync(".env", "CASPIAN_API_KEY=from-dotenv\n");

    expect(config(undefined, "CASPIAN_API_KEY", "fallback")).toBe("from-dotenv");
  });

  it("returns the fallback when no source has a value", () => {
    expect(config(undefined, "CASPIAN_API_KEY", "fallback")).toBe("fallback");
  });

  it("ignores comments and blank lines and unwraps matching quotes", () => {
    writeFileSync(
      ".env",
      "\n# local credentials\nCASPIAN_API_KEY='single-quoted'\nCASPIAN_BASE_URL=\"https://example.test\"\n",
    );

    expect(config(undefined, "CASPIAN_API_KEY")).toBe("single-quoted");
    expect(config(undefined, "CASPIAN_BASE_URL")).toBe("https://example.test");
  });

  it("falls back to the legacy COMM name within each source", () => {
    process.env.COMM_API_KEY = "legacy-env";
    writeFileSync(".env", "CASPIAN_API_KEY=branded-dotenv\n");

    expect(config(undefined, "CASPIAN_API_KEY")).toBe("legacy-env");
  });
});
