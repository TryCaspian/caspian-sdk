/**
 * Register caspian-opencode-plugin in opencode.json / opencode.jsonc.
 *
 * JSONC-aware: comments, trailing commas, nested plugin entries. Never
 * rewrite a file just because JSON.parse failed.
 */

import { join } from "node:path";

export const PLUGIN_NAME = "caspian-opencode-plugin";

export function registerPluginInConfig(raw, pkgName = PLUGIN_NAME) {
  const text = raw ?? "";
  const array = findLivePluginArray(text);
  if (array) {
    if (arrayContainsString(text.slice(array.open + 1, array.close), pkgName)) {
      return { text, changed: false, action: "skip" };
    }
    return {
      text: insertIntoPluginArray(text, array, pkgName),
      changed: true,
      action: "insert",
    };
  }
  return {
    text: insertPluginKey(text, pkgName),
    changed: true,
    action: "create-key",
  };
}

export async function resolveConfigPaths(opts) {
  const { projectLocal, cwd, home, exists } = opts;
  if (projectLocal) {
    const paths = [
      join(cwd, "opencode.jsonc"),
      join(cwd, "opencode.json"),
      join(cwd, ".opencode", "opencode.jsonc"),
      join(cwd, ".opencode", "opencode.json"),
    ];
    const found = [];
    for (const p of paths) {
      if (await exists(p)) found.push(p);
    }
    return found.length ? found : [join(cwd, "opencode.json")];
  }
  const dir = join(home, ".config", "opencode");
  const paths = [join(dir, "opencode.jsonc"), join(dir, "opencode.json")];
  const found = [];
  for (const p of paths) {
    if (await exists(p)) found.push(p);
  }
  return found.length ? found : [join(dir, "opencode.json")];
}

function findLivePluginArray(text) {
  const walker = createWalker(text);
  for (let i = 0; i < text.length; i++) {
    const atCode = walker.isCode();
    walker.step(i);
    if (!atCode || !text.startsWith('"plugin"', i)) continue;
    let j = i + '"plugin"'.length;
    j = skipWsAndComments(text, j);
    if (text[j] !== ":") continue;
    j = skipWsAndComments(text, j + 1);
    if (text[j] !== "[") continue;
    const close = findMatchingBracket(text, j);
    if (close < 0) return null;
    return { open: j, close };
  }
  return null;
}

function findMatchingBracket(text, open) {
  const walker = createWalker(text);
  for (let i = 0; i < open; i++) walker.step(i);
  let depth = 0;
  for (let i = open; i < text.length; i++) {
    const kind = walker.step(i);
    if (kind !== "code") continue;
    if (text[i] === "[") depth++;
    else if (text[i] === "]") {
      depth--;
      if (depth === 0) return i;
    }
  }
  return -1;
}

function arrayContainsString(body, pkgName) {
  const needles = [`"${pkgName}"`, `'${pkgName}'`];
  const walker = createWalker(body);
  for (let i = 0; i < body.length; i++) {
    const atCode = walker.isCode();
    walker.step(i);
    if (!atCode) continue;
    for (const needle of needles) {
      if (body.startsWith(needle, i)) return true;
    }
  }
  return false;
}

function insertIntoPluginArray(text, array, pkgName) {
  const body = text.slice(array.open + 1, array.close);
  const last = lastSignificantIndex(body);
  if (last < 0) {
    const inserted = `\n    "${pkgName}"\n  `;
    return text.slice(0, array.open + 1) + inserted + text.slice(array.close);
  }

  const lastChar = body[last];
  let nextBody;
  if (lastChar === ",") {
    nextBody = `${body.slice(0, last + 1)}\n    "${pkgName}"\n  `;
  } else {
    nextBody = `${body.slice(0, last + 1)},${body.slice(last + 1).replace(/\s*$/, "")}\n    "${pkgName}"\n  `;
  }
  return text.slice(0, array.open + 1) + nextBody + text.slice(array.close);
}

function insertPluginKey(text, pkgName) {
  const open = firstCodeIndexOf(text, "{");
  if (open < 0) {
    return `{\n  "plugin": ["${pkgName}"]\n}\n`;
  }
  const after = text.slice(open + 1);
  const hasOtherKeys = hasCodeContent(after);
  const entry = hasOtherKeys
    ? `\n  "plugin": ["${pkgName}"],`
    : `\n  "plugin": ["${pkgName}"]\n`;
  return text.slice(0, open + 1) + entry + after;
}

function hasCodeContent(text) {
  const walker = createWalker(text);
  for (let i = 0; i < text.length; i++) {
    const kind = walker.step(i);
    if (kind !== "code") continue;
    const c = text[i];
    if (c === "}") return false;
    if (!isWs(c)) return true;
  }
  return false;
}

function firstCodeIndexOf(text, ch) {
  const walker = createWalker(text);
  for (let i = 0; i < text.length; i++) {
    const kind = walker.step(i);
    if (kind === "code" && text[i] === ch) return i;
  }
  return -1;
}

function lastSignificantIndex(text) {
  const walker = createWalker(text);
  let last = -1;
  for (let i = 0; i < text.length; i++) {
    const kind = walker.step(i);
    if (kind === "comment") continue;
    if (isWs(text[i])) continue;
    last = i;
  }
  return last;
}

function skipWsAndComments(text, start) {
  const walker = createWalker(text);
  for (let i = 0; i < start; i++) walker.step(i);
  let i = start;
  while (i < text.length) {
    const kind = walker.step(i);
    if (kind === "comment" || (kind === "code" && isWs(text[i]))) {
      i++;
      continue;
    }
    return i;
  }
  return i;
}

function isWs(c) {
  return c === " " || c === "\t" || c === "\n" || c === "\r";
}

/**
 * Advance one index. Returns the kind of that index AFTER applying the
 * transition that starts at this character.
 */
function createWalker(text) {
  let inString = false;
  let inLineComment = false;
  let inBlockComment = false;
  let escape = false;
  let skipNext = false;

  return {
    isCode() {
      return !inString && !inLineComment && !inBlockComment && !skipNext;
    },
    step(i) {
      if (skipNext) {
        skipNext = false;
        return inBlockComment || inLineComment ? "comment" : "code";
      }

      const c = text[i];
      const n = text[i + 1];

      if (inLineComment) {
        if (c === "\n") inLineComment = false;
        return "comment";
      }

      if (inBlockComment) {
        if (c === "*" && n === "/") {
          skipNext = true;
          inBlockComment = false;
        }
        return "comment";
      }

      if (inString) {
        if (escape) {
          escape = false;
          return "string";
        }
        if (c === "\\") {
          escape = true;
          return "string";
        }
        if (c === '"') {
          inString = false;
          return "string";
        }
        return "string";
      }

      if (c === "/" && n === "/") {
        inLineComment = true;
        return "comment";
      }
      if (c === "/" && n === "*") {
        inBlockComment = true;
        return "comment";
      }
      if (c === '"') {
        inString = true;
        return "string";
      }
      return "code";
    },
  };
}
