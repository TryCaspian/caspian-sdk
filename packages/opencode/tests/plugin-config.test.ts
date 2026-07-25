import { describe, expect, test } from "bun:test";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  caspianJsonPath,
  configuredChannels,
  enableChannel,
  readCaspianJson,
  writeCaspianJson,
} from "../src/plugin-config.js";

describe("plugin-config", () => {
  test("enableChannel merges telegram into channels", () => {
    const dir = mkdtempSync(join(tmpdir(), "caspian-cfg-"));
    const path = caspianJsonPath(dir);
    try {
      writeCaspianJson({ channels: ["email"] }, path);
      const { channels } = enableChannel(
        "telegram",
        { telegram: { connectionId: "conn_tg" } },
        path,
      );
      expect(channels).toEqual(["email", "telegram"]);
      const raw = JSON.parse(readFileSync(path, "utf-8")) as {
        channels: string[];
        telegram: { connectionId: string };
      };
      expect(raw.channels).toEqual(["email", "telegram"]);
      expect(raw.telegram.connectionId).toBe("conn_tg");
      expect(configuredChannels(path)).toEqual(["email", "telegram"]);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  test("enableChannel is idempotent for existing channel", () => {
    const dir = mkdtempSync(join(tmpdir(), "caspian-cfg-"));
    const path = caspianJsonPath(dir);
    try {
      writeCaspianJson({ channels: ["email", "telegram"] }, path);
      const { channels } = enableChannel("telegram", undefined, path);
      expect(channels).toEqual(["email", "telegram"]);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  test("readCaspianJson returns {} for missing file", () => {
    const dir = mkdtempSync(join(tmpdir(), "caspian-cfg-"));
    try {
      expect(readCaspianJson(join(dir, "missing.json"))).toEqual({});
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
