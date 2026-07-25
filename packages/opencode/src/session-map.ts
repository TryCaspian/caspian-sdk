/**
 * CP: map key ↔ OpenCode sessionId.
 *
 * With threading.enabled (default), key = Caspian conversationId
 * (one email thread → one OpenCode session).
 * With threading.enabled=false, key = sharedSessionKey for all mail.
 */

export interface SessionStore {
  get(key: string): string | undefined;
  set(key: string, sessionId: string): void;
  has(key: string): boolean;
}

export class MemorySessionMap implements SessionStore {
  private readonly map = new Map<string, string>();

  get(key: string): string | undefined {
    return this.map.get(key);
  }

  set(key: string, sessionId: string): void {
    this.map.set(key, sessionId);
  }

  has(key: string): boolean {
    return this.map.has(key);
  }

  size(): number {
    return this.map.size;
  }
}
