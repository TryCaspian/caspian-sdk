#!/usr/bin/env node

/**
 * Install Caspian OpenCode slash commands + register the plugin.
 *
 * Skills are auto-discovered from the npm package. Commands are not —
 * they must live under `.opencode/commands/` or `~/.config/opencode/commands/`.
 *
 * Usage:
 *   bunx caspian-opencode-plugin setup           # global (~/.config/opencode)
 *   bunx caspian-opencode-plugin setup --project # project-local
 */

import { readdir, readFile, writeFile, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { homedir } from "node:os";

const __dirname = dirname(fileURLToPath(import.meta.url));
const packageRoot = resolve(__dirname, "..");
const pkgName = "caspian-opencode-plugin";

const ourPkg = JSON.parse(
  await readFile(resolve(packageRoot, "package.json"), "utf-8"),
);
const ourVersion = ourPkg.version;

const args = process.argv.slice(2);
const command = args[0];

if (command !== "setup") {
  console.log(`Usage: ${pkgName} setup [--project]`);
  process.exit(command ? 1 : 0);
}

const isProject = args.includes("--project");
await setup(isProject);

async function setup(projectLocal) {
  const configPath = projectLocal
    ? resolve(process.cwd(), "opencode.json")
    : resolve(homedir(), ".config", "opencode", "opencode.json");

  const commandsSource = resolve(packageRoot, "src", "commands");
  const commandsTarget = projectLocal
    ? resolve(process.cwd(), ".opencode", "commands")
    : resolve(homedir(), ".config", "opencode", "commands");

  const packagedCommandsPath = resolve(packageRoot, "opencode.commands.json");

  console.log(`\n${pkgName} setup v${ourVersion}`);
  console.log(
    `  target: ${projectLocal ? "project" : "global (~/.config/opencode/)"}\n`,
  );

  let config;
  try {
    config = JSON.parse(await readFile(configPath, "utf-8"));
  } catch {
    config = {};
  }

  if (!config.$schema) config.$schema = "https://opencode.ai/config.json";
  if (!Array.isArray(config.plugin)) config.plugin = [];

  if (config.plugin.includes(pkgName)) {
    console.log(`  [skip] ${pkgName} already in opencode.json plugin array`);
  } else {
    config.plugin.push(pkgName);
    console.log(`  [add] ${pkgName} to opencode.json plugin array`);
  }

  try {
    const packaged = JSON.parse(await readFile(packagedCommandsPath, "utf-8"));
    if (!config.command || typeof config.command !== "object") {
      config.command = {};
    }
    let merged = 0;
    for (const [name, def] of Object.entries(packaged)) {
      const existing = config.command[name];
      const same =
        existing &&
        JSON.stringify(existing) === JSON.stringify(def);
      if (same) continue;
      config.command[name] = def;
      merged++;
      console.log(
        existing ? `  [update] command ${name}` : `  [add] command ${name}`,
      );
    }
    if (merged === 0) {
      console.log(`  [skip] opencode.json commands already up to date`);
    }
  } catch (err) {
    console.warn(
      `  [warn] could not merge opencode.commands.json: ${err?.message || err}`,
    );
  }

  await mkdir(dirname(configPath), { recursive: true });
  await writeFile(configPath, JSON.stringify(config, null, 2) + "\n");

  let entries;
  try {
    entries = (await readdir(commandsSource)).filter((f) => f.endsWith(".md"));
  } catch {
    console.log("\nDone (plugin registered; no command markdown to copy).");
    console.log("\nRestart OpenCode to activate.");
    return;
  }

  let copied = 0;
  let skipped = 0;
  if (entries.length > 0) {
    await mkdir(commandsTarget, { recursive: true });
    for (const file of entries) {
      const srcPath = resolve(commandsSource, file);
      const destPath = resolve(commandsTarget, file);
      const srcContent = await readFile(srcPath, "utf-8");
      try {
        const destContent = await readFile(destPath, "utf-8");
        if (destContent === srcContent) {
          console.log(`  [skip] ${file} (up to date)`);
          skipped++;
          continue;
        }
        console.log(`  [update] ${file}`);
      } catch {
        console.log(`  [copy] ${file}`);
      }
      await writeFile(destPath, srcContent);
      copied++;
    }
  }

  console.log(
    `\nDone: plugin registered, ${copied} command files written, ${skipped} unchanged`,
  );
  console.log("\nRestart OpenCode to activate.");
}
