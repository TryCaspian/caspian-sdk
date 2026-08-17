import { describe, expect, test } from "bun:test";
import {
  PLUGIN_NAME,
  registerPluginInConfig,
  resolveConfigPaths,
} from "../bin/setup-register.js";

const PKG = PLUGIN_NAME;

function parseLikeOpenCode(text: string): unknown {
  const stripped = text
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/.*$/gm, "$1")
    .replace(/,(\s*[}\]])/g, "$1");
  return JSON.parse(stripped);
}

describe("registerPluginInConfig", () => {
  test("appends to a typical oh-my-opencode plugin array and keeps comments", () => {
    const raw = `{
  "$schema": "https://opencode.ai/config.json",
  // oh-my-opencode + custom providers
  "plugin": [
    "oh-my-opencode",
    "opencode-antigravity-auth@latest"
  ],
  "provider": {
    "openai": { "options": { "apiKey": "{env:OPENAI_API_KEY}" } }
  }
}
`;
    const { text, changed } = registerPluginInConfig(raw);
    expect(changed).toBe(true);
    expect(text).toContain("// oh-my-opencode + custom providers");
    expect(text).toContain(`"oh-my-opencode"`);
    expect(text).toContain(`"${PKG}"`);
    const parsed = parseLikeOpenCode(text) as { plugin: string[] };
    expect(parsed.plugin).toContain(PKG);
    expect(parsed.plugin).toContain("oh-my-opencode");
  });

  test("does not wipe trailing-comma jsonc that has no plugin key yet", () => {
    const raw = `{
  "$schema": "https://opencode.ai/config.json",
  "model": "anthropic/claude-sonnet-4-5",
  "autoupdate": true,
  "server": {
    "port": 4096,
  },
}
`;
    const { text, changed } = registerPluginInConfig(raw);
    expect(changed).toBe(true);
    expect(text).toContain(`"model": "anthropic/claude-sonnet-4-5"`);
    expect(text).toContain(`"port": 4096`);
    const parsed = parseLikeOpenCode(text) as {
      plugin: string[];
      model: string;
    };
    expect(parsed.plugin).toEqual([PKG]);
    expect(parsed.model).toBe("anthropic/claude-sonnet-4-5");
  });

  test("inserts a comma before an inline comment on the last plugin", () => {
    const raw = `{
  "plugin": [
    "oh-my-opencode" // recommended
  ]
}
`;
    const { text } = registerPluginInConfig(raw);
    expect(text).toContain(`"oh-my-opencode", // recommended`);
    expect(text).toContain(`"${PKG}"`);
    const parsed = parseLikeOpenCode(text) as { plugin: string[] };
    expect(parsed.plugin).toEqual(["oh-my-opencode", PKG]);
  });

  test("does not close the array at a nested plugin entry", () => {
    const raw = `{
  "plugin": [
    "oh-my-opencode",
    ["./plugins/demo.ts", { "label": "demo" }]
  ]
}
`;
    const { text } = registerPluginInConfig(raw);
    const parsed = parseLikeOpenCode(text) as { plugin: unknown[] };
    expect(parsed.plugin.at(-1)).toBe(PKG);
    expect(parsed.plugin[1]).toEqual(["./plugins/demo.ts", { label: "demo" }]);
  });

  test("ignores a commented-out plugin example and adds a real plugin key", () => {
    const raw = `{
  "$schema": "https://opencode.ai/config.json",
  // "plugin": ["example-plugin"]
  "model": "anthropic/claude-sonnet-4-5"
}
`;
    const { text } = registerPluginInConfig(raw);
    expect(text).toContain(`// "plugin": ["example-plugin"]`);
    expect(text).toContain(`"model": "anthropic/claude-sonnet-4-5"`);
    const parsed = parseLikeOpenCode(text) as {
      plugin: string[];
      model: string;
    };
    expect(parsed.plugin).toEqual([PKG]);
    expect(parsed.model).toBe("anthropic/claude-sonnet-4-5");
  });

  test("ignores plugin arrays inside block comments", () => {
    const raw = `{
  "$schema": "https://opencode.ai/config.json",
  /* example:
     "plugin": ["old-plugin"]
  */
  "plugin": [
    "oh-my-opencode"
  ]
}
`;
    const { text } = registerPluginInConfig(raw);
    expect(text).toContain(`"plugin": ["old-plugin"]`);
    const parsed = parseLikeOpenCode(text) as { plugin: string[] };
    expect(parsed.plugin).toEqual(["oh-my-opencode", PKG]);
  });

  test("skips when the live plugin array already lists the package", () => {
    const raw = `{
  "plugin": [
    "oh-my-openagent@latet",
    "${PKG}"
  ]
}
`;
    const { text, changed } = registerPluginInConfig(raw);
    expect(changed).toBe(false);
    expect(text).toBe(raw);
  });

  test("does not skip just because the package name appears in a comment", () => {
    const raw = `{
  // later: "${PKG}"
  "plugin": [
    "oh-my-opencode"
  ]
}
`;
    const { text, changed } = registerPluginInConfig(raw);
    expect(changed).toBe(true);
    const parsed = parseLikeOpenCode(text) as { plugin: string[] };
    expect(parsed.plugin).toEqual(["oh-my-opencode", PKG]);
  });

  test("registers in the user's real opencode.jsonc shape", () => {
    const raw = `{
  "$schema": "https://opencode.ai/config.json",
  "disabled_providers": [],
  "plugin": [
    "oh-my-openagent@latet",
    "@knikolov/opencode-plugin-simple-memory",
    "opencode-websearch-cited@1.1.1"
  ],
  "provider": {
    "juspay-grid": {
      "name": "Juspay Grid",
      "npm": "@ai-sdk/openai-compatible",
      "options": { "baseURL": "https://grid.ai.juspay.net/v1" }
    }
  },
  "mcp": {
    "agentcash": {
      "type": "local",
      "command": ["npx", "-y", "agentcash@latest"],
      "enabled": true
    }
  }
}
`;
    const { text, changed } = registerPluginInConfig(raw);
    expect(changed).toBe(true);
    expect(text).toContain(`"juspay-grid"`);
    expect(text).toContain(`"agentcash"`);
    const parsed = parseLikeOpenCode(text) as { plugin: string[] };
    expect(parsed.plugin).toEqual([
      "oh-my-openagent@latet",
      "@knikolov/opencode-plugin-simple-memory",
      "opencode-websearch-cited@1.1.1",
      PKG,
    ]);
  });
});

describe("resolveConfigPaths", () => {
  test("global setup prefers existing jsonc and also returns json", async () => {
    const paths = await resolveConfigPaths({
      projectLocal: false,
      cwd: "/proj",
      home: "/home/u",
      exists: (p) =>
        p === "/home/u/.config/opencode/opencode.jsonc" ||
        p === "/home/u/.config/opencode/opencode.json",
    });
    expect(paths).toEqual([
      "/home/u/.config/opencode/opencode.jsonc",
      "/home/u/.config/opencode/opencode.json",
    ]);
  });

  test("global setup still targets jsonc when only jsonc exists", async () => {
    const paths = await resolveConfigPaths({
      projectLocal: false,
      cwd: "/proj",
      home: "/home/u",
      exists: (p) => p === "/home/u/.config/opencode/opencode.jsonc",
    });
    expect(paths).toEqual(["/home/u/.config/opencode/opencode.jsonc"]);
  });

  test("project setup finds .opencode/opencode.jsonc", async () => {
    const paths = await resolveConfigPaths({
      projectLocal: true,
      cwd: "/proj",
      home: "/home/u",
      exists: (p) => p === "/proj/.opencode/opencode.jsonc",
    });
    expect(paths).toEqual(["/proj/.opencode/opencode.jsonc"]);
  });
});
