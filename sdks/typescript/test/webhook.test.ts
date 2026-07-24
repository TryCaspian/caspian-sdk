import { describe, expect, it, vi } from "vitest";
import { CommClient, WebhookVerificationError } from "../src/index.js";
import crypto from "node:crypto";

describe("Webhook Handler", () => {
  it("verifies valid signatures and dispatches events", async () => {
    const client = new CommClient({ apiKey: "test" });
    const seen: string[] = [];
    client.onMessage(async (msg) => {
      seen.push(msg.id);
    });

    const secret = "my_secret";
    const payload = JSON.stringify({
      type: "message.received",
      seq: 1,
      id: "event_1",
      data: { message: { id: "msg_1", connection_id: "c1", conversation_id: "c2" } }
    });
    
    const signature = crypto.createHmac("sha256", secret).update(payload).digest("hex");

    // First call
    await client.handleWebhook(payload, signature, secret);
    expect(seen).toEqual(["msg_1"]);

    // Deduplication test
    await client.handleWebhook(payload, signature, secret);
    expect(seen).toEqual(["msg_1"]); // still 1
  });

  it("rejects invalid signatures", async () => {
    const client = new CommClient({ apiKey: "test" });
    const secret = "my_secret";
    const payload = JSON.stringify({ id: "event_1" });
    
    await expect(client.handleWebhook(payload, "bad_sig", secret))
      .rejects.toThrow(WebhookVerificationError);
  });
});
