#!/usr/bin/env node

/**
 * Register the Caspian OpenCode plugin and install slash commands + skills.
 *
 * OpenCode loads npm plugins from the `plugin` array, but skills and slash
 * commands are only discovered from:
 *   - ~/.config/opencode/{skills,commands}/  (global)
 *   - .opencode/{skills,commands}/           (project)
 * They are NOT auto-loaded from inside the npm package — this script copies them.
 *
 * Important: if both opencode.json and opencode.jsonc exist, OpenCode may use
 * either (or both). Setup registers the plugin in every config file it finds.
 *
 * Usage:
 *   bunx caspian-opencode-plugin setup           # global (~/.config/opencode)
 *   bunx caspian-opencode-plugin setup --project # project-local
 */

import { cp, readdir, readFile, writeFile, mkdir, access } from "node:fs/promises";
import { dirname, resolve, join } from "node:path";
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

async function exists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

/** Strip // and /* *\/ comments for JSONC parse (best-effort). */
function parseJsonc(text) {
  const stripped = text
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
  return JSON.parse(stripped);
}

async function copyMarkdownDir(source, target, label) {
  let entries;
  try {
    entries = (await readdir(source)).filter((f) => f.endsWith(".md"));
  } catch {
    return { copied: 0, skipped: 0 };
  }

  let copied = 0;
  let skipped = 0;
  if (entries.length === 0) return { copied, skipped };

  await mkdir(target, { recursive: true });
  for (const file of entries) {
    const srcPath = resolve(source, file);
    const destPath = resolve(target, file);
    const srcContent = await readFile(srcPath, "utf-8");
    try {
      const destContent = await readFile(destPath, "utf-8");
      if (destContent === srcContent) {
        console.log(`  [skip] ${label}/${file} (up to date)`);
        skipped++;
        continue;
      }
      console.log(`  [update] ${label}/${file}`);
    } catch {
      console.log(`  [copy] ${label}/${file}`);
    }
    await writeFile(destPath, srcContent);
    copied++;
  }
  return { copied, skipped };
}

async function copySkillTrees(source, target) {
  let dirs;
  try {
    dirs = (await readdir(source, { withFileTypes: true })).filter((d) =>
      d.isDirectory(),
    );
  } catch {
    return { copied: 0, skipped: 0 };
  }

  let copied = 0;
  let skipped = 0;
  await mkdir(target, { recursive: true });

  for (const d of dirs) {
    const from = resolve(source, d.name);
    const to = resolve(target, d.name);
    const skillMd = resolve(from, "SKILL.md");
    let srcContent;
    try {
      srcContent = await readFile(skillMd, "utf-8");
    } catch {
      console.warn(`  [warn] skip skills/${d.name} (no SKILL.md)`);
      continue;
    }

    const destSkill = resolve(to, "SKILL.md");
    try {
      const destContent = await readFile(destSkill, "utf-8");
      if (destContent === srcContent) {
        console.log(`  [skip] skills/${d.name} (up to date)`);
        skipped++;
        continue;
      }
      console.log(`  [update] skills/${d.name}`);
    } catch {
      console.log(`  [copy] skills/${d.name}`);
    }

    await cp(from, to, { recursive: true, force: true });
    copied++;
  }

  return { copied, skipped };
}

/**
 * Ensure plugin is listed. For .jsonc with comments, do a surgical string edit
 * when possible so we don't wipe comments.
 */
async function ensurePluginRegistered(configPath) {
  const label = configPath.endsWith(".jsonc") ? "opencode.jsonc" : "opencode.json";
  let raw;
  try {
    raw = await readFile(configPath, "utf-8");
  } catch {
    raw = "{\n}\n";
  }

  if (raw.includes(`"${pkgName}"`) || raw.includes(`'${pkgName}'`)) {
    console.log(`  [skip] ${pkgName} already in ${label} plugin array`);
    return;
  }

  // Surgical insert into an existing "plugin": [ ... ] array
  const pluginArray = /("plugin"\s*:\s*\[)([\s\S]*?)(\])/m;
  if (pluginArray.test(raw)) {
    const updated = raw.replace(pluginArray, (full, open, body, close) => {
      const trimmed = body.trim();
      if (!trimmed) {
        return `${open}\n    "${pkgName}"\n  ${close}`;
      }
      const needsComma = /["\w]\s*$/.test(trimmed);
      const insertion = needsComma
        ? `${body.replace(/\s*$/, "")},\n    "${pkgName}"\n  `
        : `${body.replace(/\s*$/, "")}\n    "${pkgName}"\n  `;
      return `${open}${insertion}${close}`;
    });
    await writeFile(configPath, updated);
    console.log(`  [add] ${pkgName} to ${label} plugin array`);
    return;
  }

  // No plugin array — parse (json/jsonc) and rewrite
  let config;
  try {
    config = configPath.endsWith(".jsonc") ? parseJsonc(raw) : JSON.parse(raw);
  } catch {
    config = {};
  }
  if (!config.$schema) config.$schema = "https://opencode.ai/config.json";
  if (!Array.isArray(config.plugin)) config.plugin = [];
  config.plugin.push(pkgName);
  await mkdir(dirname(configPath), { recursive: true });
  await writeFile(configPath, JSON.stringify(config, null, 2) + "\n");
  console.log(`  [add] ${pkgName} to ${label} plugin array (created/rewrote)`);
}

async function mergeCommandsIntoJson(configPath) {
  if (configPath.endsWith(".jsonc")) {
    // Keep command templates in .json / markdown only — avoid bloating jsonc.
    return;
  }

  const packagedCommandsPath = resolve(packageRoot, "opencode.commands.json");
  let config;
  try {
    config = JSON.parse(await readFile(configPath, "utf-8"));
  } catch {
    config = { $schema: "https://opencode.ai/config.json" };
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
        existing && JSON.stringify(existing) === JSON.stringify(def);
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
}

async function resolveConfigPaths(projectLocal) {
  if (projectLocal) {
    const root = process.cwd();
    const paths = [
      join(root, "opencode.jsonc"),
      join(root, "opencode.json"),
      join(root, ".opencode", "opencode.jsonc"),
      join(root, ".opencode", "opencode.json"),
    ];
    const found = [];
    for (const p of paths) {
      if (await exists(p)) found.push(p);
    }
    return found.length ? found : [join(root, "opencode.json")];
  }

  const dir = join(homedir(), ".config", "opencode");
  const paths = [join(dir, "opencode.jsonc"), join(dir, "opencode.json")];
  const found = [];
  for (const p of paths) {
    if (await exists(p)) found.push(p);
  }
  return found.length ? found : [join(dir, "opencode.json")];
}

async function setup(projectLocal) {
  const configPaths = await resolveConfigPaths(projectLocal);

  const commandsSource = resolve(packageRoot, "src", "commands");
  const commandsTarget = projectLocal
    ? resolve(process.cwd(), ".opencode", "commands")
    : resolve(homedir(), ".config", "opencode", "commands");

  const skillsSource = resolve(packageRoot, "src", "skills");
  const skillsTarget = projectLocal
    ? resolve(process.cwd(), ".opencode", "skills")
    : resolve(homedir(), ".config", "opencode", "skills");

  console.log(`\n${pkgName} setup v${ourVersion}`);
  console.log(
    "  target: " +
      (projectLocal ? "project" : "global (~/.config/opencode/)"),
  );
  console.log(`  configs: ${configPaths.join(", ")}\n`);

  for (const configPath of configPaths) {
    await ensurePluginRegistered(configPath);
    await mergeCommandsIntoJson(configPath);
  }

  const cmds = await copyMarkdownDir(commandsSource, commandsTarget, "commands");
  const skills = await copySkillTrees(skillsSource, skillsTarget);

  console.log(
    `\nDone: plugin registered` +
      `, ${cmds.copied} commands written (${cmds.skipped} unchanged)` +
      `, ${skills.copied} skills written (${skills.skipped} unchanged)`,
  );
  console.log("\nRestart OpenCode to activate.");
  console.log(
    "Tip: if tools are still missing, confirm caspian-opencode-plugin is in the",
  );
  console.log(
    "     same config file OpenCode actually loads (opencode.jsonc vs opencode.json).",
  );
}
