/**
 * Chaos interpreter of GatewayClient.
 *
 * sdk-reliability: the same Plan runs against a client that always fails;
 * the failure is AdapterError data, which runPlan folds into UsageError.
 */
import { AdapterError, type GatewayClient } from "caspian-sdk"
import * as Effect from "effect/Effect"

export const chaosGatewayClient = (reason = "chaos"): GatewayClient => ({
  send: () =>
    Effect.fail(new AdapterError({ reason, commandTag: "gateway" })),
})
