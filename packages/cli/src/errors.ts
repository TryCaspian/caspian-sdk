/**
 * Closed CLI error ADT. Failure is data — never a throw across parse/plan/run.
 */
import * as Schema from "effect/Schema"

export class UsageError extends Schema.TaggedError<UsageError>()("UsageError", {
  reason: Schema.String,
}) {}

/** Dashboard for credit after sign-in. */
export const DASHBOARD_URL = "https://dashboard.trycaspianai.com"

export const hostedNeeded = (): UsageError =>
  new UsageError({
    reason: [
      "This command needs the hosted Caspian gateway.",
      "Pass --api-key KEY and optionally --gateway URL,",
      "or set CASPIAN_API_KEY and CASPIAN_BASE_URL,",
      "or run caspian init / caspian login",
      "(browser sign-in; the CLI secret is ~/.caspian/.env, not this repo's .env),",
      `or sign up at ${DASHBOARD_URL}`,
    ].join("\n"),
  })
