import { describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  resolveTelegramBotToken,
  telegramTokenSetupMessage,
} from "../src/secrets.js";

describe("secrets", () => {
  test("prefers process env", () => {
    const got = resolveTelegramBotToken({
      cwd: "/tmp/nope",
      env: { TELEGRAM_BOT_TOKEN: " from-env " } as NodeJS.ProcessEnv,
      opencodeConfigDir: "/tmp/nope-oc",
    });
    expect(got.value).toBe("from-env");
    expect(got.source).toBe("env");
  });

  test("reads project .env", () => {
    const dir = mkdtempSync(join(tmpdir(), "caspian-sec-"));
    try {
      writeFileSync(
        join(dir, ".env"),
        "TELEGRAM_BOT_TOKEN=project-token\n",
        "utf-8",
      );
      const got = resolveTelegramBotToken({
        cwd: dir,
        env: {} as NodeJS.ProcessEnv,
        opencodeConfigDir: join(dir, "oc"),
      });
      expect(got.value).toBe("project-token");
      expect(got.source).toBe("project-dotenv");
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  test("reads opencode caspian.env", () => {
    const dir = mkdtempSync(join(tmpdir(), "caspian-sec-"));
    const oc = join(dir, "opencode");
    try {
      mkdirSync(oc, { recursive: true });
      writeFileSync(
        join(oc, "caspian.env"),
        "TELEGRAM_BOT_TOKEN=oc-token\n",
        "utf-8",
      );
      const got = resolveTelegramBotToken({
        cwd: join(dir, "empty"),
        env: {} as NodeJS.ProcessEnv,
        opencodeConfigDir: oc,
      });
      expect(got.value).toBe("oc-token");
      expect(got.source).toBe("opencode-env");
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  test("setup message lists hint paths", () => {
    const msg = telegramTokenSetupMessage(["/a/.env", "/b/caspian.env"]);
    expect(msg).toContain("TELEGRAM_BOT_TOKEN");
    expect(msg).toContain("/a/.env");
    expect(msg).toContain("@BotFather");
  });
});
