import { describe, expect, test } from "bun:test";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  ensureCredentials,
  initViaCli,
  mintSandboxKey,
  parseDotEnv,
  writeDotEnv,
} from "../src/onboard.ts";

describe("parseDotEnv / writeDotEnv", () => {
  test("round-trips keys", () => {
    const dir = mkdtempSync(join(tmpdir(), "caspian-oc-"));
    const path = join(dir, ".env");
    writeDotEnv(path, {
      CASPIAN_API_KEY: "comm_test",
      CASPIAN_BASE_URL: "https://api.trycaspianai.com",
    });
    writeDotEnv(path, { CASPIAN_API_KEY: "comm_replaced" });
    const parsed = parseDotEnv(readFileSync(path, "utf-8"));
    expect(parsed.CASPIAN_API_KEY).toBe("comm_replaced");
    expect(parsed.CASPIAN_BASE_URL).toBe("https://api.trycaspianai.com");
    rmSync(dir, { recursive: true, force: true });
  });
});

describe("mintSandboxKey", () => {
  test("posts to /v1/projects/sandbox", async () => {
    const calls: string[] = [];
    const fetchImpl = async (input: string | URL | Request, init?: RequestInit) => {
      calls.push(String(input));
      expect(init?.method).toBe("POST");
      return new Response(
        JSON.stringify({ project_id: "proj_1", api_key: "comm_sandbox_x" }),
        { status: 200 },
      );
    };
    const minted = await mintSandboxKey({
      gateway: "https://api.example.com",
      projectName: "opencode",
      fetchImpl,
    });
    expect(minted.apiKey).toBe("comm_sandbox_x");
    expect(calls[0]).toBe("https://api.example.com/v1/projects/sandbox");
  });
});

describe("initViaCli", () => {
  test("skips when caspian is not on PATH (default cold-start)", async () => {
    const dir = mkdtempSync(join(tmpdir(), "caspian-oc-cli-"));
    const tried: string[] = [];
    const creds = await initViaCli({
      cwd: dir,
      gateway: "https://api.trycaspianai.com",
      projectName: "opencode",
      which: () => null,
      shell: async (cmd) => {
        tried.push(cmd[0] ?? "");
        return { exitCode: 0, stdout: "", stderr: "" };
      },
    });
    expect(creds).toBeNull();
    expect(tried).toEqual([]);
    rmSync(dir, { recursive: true, force: true });
  });

  test("tries caspian then uvx when includeUvx", async () => {
    const dir = mkdtempSync(join(tmpdir(), "caspian-oc-cli-"));
    const tried: string[] = [];
    const creds = await initViaCli({
      cwd: dir,
      gateway: "https://api.trycaspianai.com",
      projectName: "opencode",
      includeUvx: true,
      requireCaspianOnPath: false,
      which: () => null,
      shell: async (cmd) => {
        tried.push(cmd[0] ?? "");
        if (cmd[0] === "caspian") {
          return { exitCode: 127, stdout: "", stderr: "not found" };
        }
        if (cmd[0] === "uvx" && cmd.includes("caspian")) {
          writeFileSync(
            join(dir, ".env"),
            "CASPIAN_API_KEY=comm_from_cli\nCASPIAN_BASE_URL=https://api.trycaspianai.com\n",
          );
          return { exitCode: 0, stdout: "ok", stderr: "" };
        }
        return { exitCode: 127, stdout: "", stderr: "" };
      },
    });
    expect(tried).toContain("caspian");
    expect(tried).toContain("uvx");
    expect(creds?.apiKey).toBe("comm_from_cli");
    rmSync(dir, { recursive: true, force: true });
  });
});

describe("ensureCredentials", () => {
  test("returns existing env without creating", async () => {
    const result = await ensureCredentials({
      env: {
        CASPIAN_API_KEY: "comm_existing",
        CASPIAN_BASE_URL: "https://gw.example",
      } as NodeJS.ProcessEnv,
      autoCreate: true,
      shell: async () => {
        throw new Error("should not shell");
      },
      fetchImpl: async () => {
        throw new Error("should not fetch");
      },
    });
    expect(result?.apiKey).toBe("comm_existing");
    expect(result?.created).toBe(false);
    expect(result?.source).toBe("env");
  });

  test("CLI path creates and mirrors to opencode env", async () => {
    const projectDir = mkdtempSync(join(tmpdir(), "caspian-oc-proj-"));
    const configDir = mkdtempSync(join(tmpdir(), "caspian-oc-cfg-"));
    const env = {} as NodeJS.ProcessEnv;

    const result = await ensureCredentials({
      env,
      cwd: projectDir,
      opencodeConfigDir: configDir,
      autoCreate: true,
      preferHttpMint: false,
      which: () => "/usr/bin/caspian",
      shell: async () => {
        writeFileSync(
          join(projectDir, ".env"),
          "CASPIAN_API_KEY=comm_cli\nCASPIAN_BASE_URL=https://api.trycaspianai.com\n",
        );
        return { exitCode: 0, stdout: "", stderr: "" };
      },
      fetchImpl: async () => {
        throw new Error("should not mint when CLI works");
      },
    });

    expect(result?.source).toBe("cli");
    expect(result?.created).toBe(true);
    expect(env.CASPIAN_API_KEY).toBe("comm_cli");
    expect(
      parseDotEnv(readFileSync(join(configDir, "caspian.env"), "utf-8"))
        .CASPIAN_API_KEY,
    ).toBe("comm_cli");

    rmSync(projectDir, { recursive: true, force: true });
    rmSync(configDir, { recursive: true, force: true });
  });

  test("HTTP mint when no CLI / no .env (zero-setup cold start)", async () => {
    const projectDir = mkdtempSync(join(tmpdir(), "caspian-oc-http-"));
    const configDir = mkdtempSync(join(tmpdir(), "caspian-oc-cfg2-"));
    const env = {} as NodeJS.ProcessEnv;
    let shelled = false;

    const result = await ensureCredentials({
      env,
      cwd: projectDir,
      opencodeConfigDir: configDir,
      autoCreate: true,
      gateway: "https://api.trycaspianai.com",
      which: () => null, // no caspian on PATH
      shell: async () => {
        shelled = true;
        return { exitCode: 127, stdout: "", stderr: "missing" };
      },
      fetchImpl: async () =>
        new Response(
          JSON.stringify({ project_id: "p", api_key: "comm_minted" }),
          { status: 200 },
        ),
    });

    expect(shelled).toBe(false); // skipped CLI entirely
    expect(result?.source).toBe("sandbox-api");
    expect(result?.apiKey).toBe("comm_minted");
    expect(result?.created).toBe(true);
    expect(
      parseDotEnv(readFileSync(join(projectDir, ".env"), "utf-8")).CASPIAN_API_KEY,
    ).toBe("comm_minted");
    expect(
      parseDotEnv(readFileSync(join(configDir, "caspian.env"), "utf-8"))
        .CASPIAN_API_KEY,
    ).toBe("comm_minted");

    rmSync(projectDir, { recursive: true, force: true });
    rmSync(configDir, { recursive: true, force: true });
  });
});
