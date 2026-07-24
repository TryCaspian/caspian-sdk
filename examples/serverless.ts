/**
 * Example of running the Caspian SDK in a serverless function (Next.js App Router).
 */

import { CommClient, WebhookVerificationError } from "caspian-sdk";

// 1. Initialize the client outside the handler to reuse connection pools
const client = new CommClient({ apiKey: process.env.CASPIAN_API_KEY });
const WEBHOOK_SECRET = process.env.CASPIAN_WEBHOOK_SECRET!;

// 2. Register your agent logic normally
client.onMessage(async (msg) => {
  const stream = await msg.stream();
  await stream.append(`Received via serverless webhook! You said: ${msg.text}`);
  await stream.finalize();
});

// 3. Route inbound HTTP requests into the SDK's webhook handler
export async function POST(req: Request) {
  const signature = req.headers.get("x-caspian-signature");
  if (!signature) {
    return new Response("Missing signature", { status: 401 });
  }

  // Next.js Request body as an ArrayBuffer
  const bodyBuffer = Buffer.from(await req.arrayBuffer());

  try {
    // Verifies the signature, deduplicates the event, and routes to handlers
    await client.handleWebhook(bodyBuffer, signature, WEBHOOK_SECRET);
  } catch (err) {
    if (err instanceof WebhookVerificationError) {
      return new Response("Invalid signature", { status: 401 });
    }
    console.error(err);
    return new Response("Internal Error", { status: 500 });
  }

  return new Response(JSON.stringify({ ok: true }), {
    headers: { "Content-Type": "application/json" }
  });
}
