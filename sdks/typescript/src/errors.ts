import type { CommClient } from "./client.js";
import type { LoginOptions } from "./types.js";

/** Raised when the gateway returns a non-2xx response (or a transport error). */
export class CommError extends Error {
  readonly statusCode: number;
  readonly detail: string;

  constructor(statusCode: number, detail: string) {
    super(`${statusCode}: ${detail}`);
    this.name = "CommError";
    this.statusCode = statusCode;
    this.detail = detail;
    // Restore the prototype chain (transpilation to ES5 loses it).
    Object.setPrototypeOf(this, CommError.prototype);
  }
}

/**
 * Raised when a paid channel needs a one-time developer sign-in first (HTTP
 * 401). Paid channels are tied to a real Caspian account (identity) before any
 * spend; free channels never raise this. Call `login()` to run the sign-in, or
 * read `loginOptions` for the raw device-flow endpoints. The human-readable
 * message is on `.detail` (and the standard Error `.message`).
 */
export class AccountRequiredError extends CommError {
  readonly reason: string;
  readonly loginOptions: Array<Record<string, unknown>>;
  private readonly client: CommClient;

  constructor(statusCode: number, payload: Record<string, any>, client: CommClient) {
    super(statusCode, payload?.message ?? "Sign in to Caspian to use paid channels.");
    this.name = "AccountRequiredError";
    this.reason = payload?.reason ?? "account_required";
    this.loginOptions = payload?.login_options ?? [];
    this.client = client;
    Object.setPrototypeOf(this, AccountRequiredError.prototype);
  }

  /** Run the one-time developer sign-in (prints a URL, waits for approval). */
  login(opts?: LoginOptions): Promise<Record<string, unknown>> {
    return this.client.login(opts);
  }
}

/**
 * Raised when a paid channel is blocked because the project is out of credit
 * (HTTP 402) or has hit a spend cap (HTTP 429).
 *
 * Carries the machine-actionable fields the gateway returns so you can react in
 * code: `balanceCents` and `paymentOptions` (each option describes the request
 * that mints a Caspian-hosted checkout URL). `topUp(amountCents)` is a shortcut
 * that mints that link for you.
 */
export class InsufficientCreditError extends CommError {
  readonly reason: string;
  readonly balanceCents: number | null;
  readonly paymentOptions: Array<Record<string, unknown>>;
  private readonly client: CommClient;

  constructor(statusCode: number, payload: Record<string, any>, client: CommClient) {
    super(statusCode, payload?.message ?? "Out of Caspian credit.");
    this.name = "InsufficientCreditError";
    this.reason = payload?.reason ?? "insufficient_credit";
    this.balanceCents = payload?.balance_cents ?? null;
    this.paymentOptions = payload?.payment_options ?? [];
    this.client = client;
    Object.setPrototypeOf(this, InsufficientCreditError.prototype);
  }

  /**
   * Mint a hosted checkout link to refill credit. Defaults to the amount the
   * gateway suggested in the 402. Returns `{ checkout_url, ... }`; open it (or
   * hand it to whoever holds the card).
   */
  topUp(amountCents?: number): Promise<Record<string, unknown>> {
    if (amountCents === undefined) {
      for (const option of this.paymentOptions) {
        const create = (option as Record<string, any>)?.create;
        const suggested = create?.body?.amount_cents;
        if (typeof suggested === "number") {
          amountCents = suggested;
          break;
        }
      }
    }
    return this.client.topUp(amountCents ?? 2000);
  }
}
export class WebhookVerificationError extends CommError { constructor() { super(400, "Invalid webhook signature"); this.name = "WebhookVerificationError"; Object.setPrototypeOf(this, WebhookVerificationError.prototype); } }
