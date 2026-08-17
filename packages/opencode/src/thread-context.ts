/**
 * Per-OpenCode-session Caspian conversation context (email, telegram, …).
 * Lets tools reply on-thread instead of initiate() a new outbound.
 */

export interface EmailThreadContext {
  openCodeSessionId: string;
  messageId: string;
  conversationId: string;
  channel?: string;
  from?: string;
  subject?: string | null;
  inboxAddress?: string;
  updatedAt: number;
}

// Bounds bySession/byConversation the same way dedupe.ts bounds its MEMORY
// set: this process runs for the plugin's whole lifetime, so without a cap
// these Maps grow by one entry per inbound message forever.
const MAX_THREADS = 2000;

function evictOldest<K, V>(map: Map<K, V>): void {
  if (map.size <= MAX_THREADS) return;
  const drop = map.size - MAX_THREADS;
  let i = 0;
  for (const key of map.keys()) {
    map.delete(key);
    if (++i >= drop) break;
  }
}

export class ThreadContextStore {
  private readonly bySession = new Map<string, EmailThreadContext>();
  private readonly byConversation = new Map<string, EmailThreadContext>();

  remember(ctx: Omit<EmailThreadContext, "updatedAt">): EmailThreadContext {
    const next: EmailThreadContext = { ...ctx, updatedAt: Date.now() };
    this.bySession.set(ctx.openCodeSessionId, next);
    evictOldest(this.bySession);
    if (ctx.conversationId) {
      this.byConversation.set(ctx.conversationId, next);
      evictOldest(this.byConversation);
    }
    return next;
  }

  getBySession(sessionId: string): EmailThreadContext | undefined {
    return this.bySession.get(sessionId);
  }

  getByConversation(conversationId: string): EmailThreadContext | undefined {
    return this.byConversation.get(conversationId);
  }
}
