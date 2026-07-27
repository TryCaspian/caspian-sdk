/**
 * Email subject helpers — avoid bare "Re:" / "(no subject)" on outbound.
 */

const EMPTYISH =
  /^(?:\(no subject\)|no subject|re\s*:?\s*|fwd?\s*:?\s*|fw\s*:?\s*)$/i;

/** True when subject is missing or useless (e.g. bare "Re:"). */
export function isBlankSubject(subject: string | null | undefined): boolean {
  if (subject == null) return true;
  const s = subject.trim();
  if (!s) return true;
  return EMPTYISH.test(s);
}

/** Strip leading Re:/Fwd: wrappers to get a base topic. */
export function stripReplyPrefixes(subject: string): string {
  let s = subject.trim();
  // Peel nested Re:/Fwd: a few times.
  for (let i = 0; i < 5; i++) {
    const next = s.replace(/^(?:re|fwd?|fw)\s*:\s*/i, "").trim();
    if (next === s) break;
    s = next;
  }
  return s;
}

/**
 * Pull a better subject from quoted email body when the header is bare Re:.
 * Looks for lines like `Subject: The Zen of Python`.
 */
export function inferSubjectFromBody(body: string | null | undefined): string | undefined {
  if (!body) return undefined;
  const matches = [
    ...body.matchAll(/^\s*>?\s*Subject:\s*(.+?)\s*$/gim),
  ];
  for (const m of matches) {
    const raw = (m[1] ?? "").trim();
    if (!isBlankSubject(raw)) {
      return stripReplyPrefixes(raw) || raw;
    }
  }
  return undefined;
}

/** Normalize inbound subject for prompts/titles. */
export function resolveInboundSubject(
  subject: string | null | undefined,
  body?: string | null,
): string {
  if (!isBlankSubject(subject)) {
    return subject!.trim();
  }
  const inferred = inferSubjectFromBody(body);
  if (inferred) return inferred;
  return "(no subject)";
}

/** Build a proper reply subject from a base topic. */
export function replySubject(base: string | null | undefined): string | undefined {
  if (isBlankSubject(base)) return undefined;
  const core = stripReplyPrefixes(base!);
  if (!core) return undefined;
  return `Re: ${core}`;
}

/**
 * Normalize outbound subject for initiate().
 * Rejects bare Re:/empty; optionally fills from threadBase.
 */
export function normalizeOutboundSubject(
  subject: string | null | undefined,
  opts?: { threadBase?: string | null; asReply?: boolean },
): string | undefined {
  const threadBase = opts?.threadBase;
  if (!isBlankSubject(subject)) {
    const s = subject!.trim();
    // "Re:" alone already rejected; "Re: Topic" kept as-is.
    if (opts?.asReply && !/^re\s*:/i.test(s)) {
      return `Re: ${stripReplyPrefixes(s)}`;
    }
    return s;
  }
  if (opts?.asReply) return replySubject(threadBase);
  if (!isBlankSubject(threadBase)) return stripReplyPrefixes(threadBase!);
  return undefined;
}
