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

export class ThreadContextStore {
  private readonly bySession = new Map<string, EmailThreadContext>();
  private readonly byConversation = new Map<string, EmailThreadContext>();

  remember(ctx: Omit<EmailThreadContext, "updatedAt">): EmailThreadContext {
    const next: EmailThreadContext = { ...ctx, updatedAt: Date.now() };
    this.bySession.set(ctx.openCodeSessionId, next);
    if (ctx.conversationId) {
      this.byConversation.set(ctx.conversationId, next);
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
