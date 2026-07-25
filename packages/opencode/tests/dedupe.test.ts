import { describe, expect, test } from "bun:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { _resetDedupeMemory, claimMessage } from "../src/dedupe.ts";

describe("claimMessage", () => {
  test("second claim is duplicate", () => {
    _resetDedupeMemory();
    const dir = mkdtempSync(join(tmpdir(), "caspian-dedupe-"));
    // Point file path via configDir
    expect(claimMessage("msg_unique_1", dir).duplicate).toBe(false);
    expect(claimMessage("msg_unique_1", dir).duplicate).toBe(true);
    _resetDedupeMemory();
    // Survives memory reset via file
    expect(claimMessage("msg_unique_1", dir).duplicate).toBe(true);
    rmSync(dir, { recursive: true, force: true });
  });
});
