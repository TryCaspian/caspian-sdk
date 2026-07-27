/**
 * Exactly-once inbound handling across plugin double-loads and multiple
 * OpenCode processes sharing one Caspian API key.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

const MEMORY = new Set<string>();
const MAX_MEMORY = 2000;
const MAX_FILE = 500;

function dedupePath(configDir?: string): string {
  const dir = configDir ?? join(homedir(), ".config", "opencode");
  return join(dir, "caspian-processed-messages.json");
}

function readFileIds(path: string): string[] {
  if (!existsSync(path)) return [];
  try {
    const raw = JSON.parse(readFileSync(path, "utf-8")) as { ids?: string[] };
    return Array.isArray(raw.ids) ? raw.ids : [];
  } catch {
    return [];
  }
}

function writeFileIds(path: string, ids: string[]): void {
  const dir = dirname(path);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  writeFileSync(
    path,
    JSON.stringify({ ids: ids.slice(-MAX_FILE), updatedAt: Date.now() }, null, 2) +
      "\n",
    "utf-8",
  );
}

/**
 * Returns true if this messageId was already claimed (skip handling).
 * Returns false if we claimed it (caller should handle).
 */
export function claimMessage(
  messageId: string,
  configDir?: string,
): { duplicate: boolean } {
  if (!messageId) return { duplicate: false };

  if (MEMORY.has(messageId)) return { duplicate: true };

  const path = dedupePath(configDir);
  const ids = readFileIds(path);
  if (ids.includes(messageId)) {
    MEMORY.add(messageId);
    return { duplicate: true };
  }

  MEMORY.add(messageId);
  if (MEMORY.size > MAX_MEMORY) {
    const drop = MEMORY.size - MAX_MEMORY;
    let i = 0;
    for (const id of MEMORY) {
      MEMORY.delete(id);
      if (++i >= drop) break;
    }
  }

  ids.push(messageId);
  writeFileIds(path, ids);
  return { duplicate: false };
}

/** Test helper. */
export function _resetDedupeMemory(): void {
  MEMORY.clear();
}
