/**
 * Which Caspian email identity to listen on / send from.
 *
 * Caspian allows multiple email connections per API key. Default: use any
 * active email connection (typically the auto-connected one). Narrow with
 * connectionId and/or address allowlists.
 */

export interface EmailIdentityConfig {
  /**
   * Prefer this connection for outbound sends and (if listenConnectionIds
   * empty) as the sole listen filter when set alone.
   */
  connectionId?: string;
  /**
   * Prefer this inbox address for outbound (matched against connection.address).
   * Also used as a listen filter when listenAddresses is empty.
   */
  address?: string;
  /**
   * Only handle inbound for these connection ids. Empty = no id filter
   * (unless connectionId is set — then that id alone is used).
   */
  listenConnectionIds: string[];
  /**
   * Only handle inbound for these inbox addresses. Empty = no address filter
   * (unless address is set — then that address alone is used).
   */
  listenAddresses: string[];
}

export const DEFAULT_EMAIL_IDENTITY: EmailIdentityConfig = {
  listenConnectionIds: [],
  listenAddresses: [],
};

export interface EmailConnection {
  id: string;
  channel: string;
  status: string;
  address?: string;
  capabilities?: string[];
  /** OAuth install links (Discord/Slack/X) — hand to the user. */
  authorize_url?: string;
  display_name?: string | null;
}

/** Effective listen filters (connection ids / addresses). Empty = admit all email. */
export function listenFilters(identity: EmailIdentityConfig): {
  connectionIds: string[];
  addresses: string[];
} {
  const connectionIds =
    identity.listenConnectionIds.length > 0
      ? identity.listenConnectionIds
      : identity.connectionId
        ? [identity.connectionId]
        : [];
  const addresses =
    identity.listenAddresses.length > 0
      ? identity.listenAddresses.map((a) => a.toLowerCase())
      : identity.address
        ? [identity.address.toLowerCase()]
        : [];
  return { connectionIds, addresses };
}

export function admitsIdentity(
  identity: EmailIdentityConfig,
  envelope: { connectionId?: string; inboxAddress?: string },
): boolean {
  const { connectionIds, addresses } = listenFilters(identity);
  if (connectionIds.length) {
    if (!envelope.connectionId || !connectionIds.includes(envelope.connectionId)) {
      return false;
    }
  }
  if (addresses.length) {
    const inbox = (envelope.inboxAddress ?? "").toLowerCase();
    if (!inbox || !addresses.includes(inbox)) return false;
  }
  return true;
}

/** Pick which connection to send from. */
export function resolveSendConnection(
  identity: EmailIdentityConfig,
  connections: EmailConnection[],
): EmailConnection | null {
  const email = connections.filter(
    (c) => c.channel === "email" && c.status === "active",
  );
  if (!email.length) return null;

  if (identity.connectionId) {
    const hit = email.find((c) => c.id === identity.connectionId);
    if (hit) return hit;
  }
  if (identity.address) {
    const want = identity.address.toLowerCase();
    const hit = email.find((c) => (c.address ?? "").toLowerCase() === want);
    if (hit) return hit;
  }
  return email[0] ?? null;
}
