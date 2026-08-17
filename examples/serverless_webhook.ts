/**
 * Serverless webhook handler example (TypeScript / Vercel / Cloudflare Workers).
 *
 * Instead of running client.listen() in an infinite poll loop, serverless functions
 * process pushed gateway deliveries one-by-one with client.handleWebhook().
 *
 * Configure your webhook URL and secret in Caspian first:
 *   await client.setWebhook("https://your-domain.vercel.app/api/webhook", "whsec_123");
 */
import { CommClient, WebhookVerificationError } from "../sdks/typescript/src/index.js";

const client = new CommClient();
const WEBHOOK_SECRET = process.env.CASPIAN_WEBHOOK_SECRET ?? "whsec_123";

client.onMessage(async (message) => {
  console.log(`Serverless received: ${message.text}`);
  await message.reply(`Serverless auto-reply: ${message.text}`);
});

/** Web Standard Request handler (Vercel Edge / Cloudflare Workers / Next.js API route). */
export async function POST(req: Request): Promise<Response> {
  const body = await req.text();
  const headers: Record<string, string> = {};
  req.headers.forEach((value, key) => {
    headers[key] = value;
  });

  try {
    const result = await client.handleWebhook({
      body,
      headers,
      secret: WEBHOOK_SECRET,
    });

    return new Response(JSON.stringify(result), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    if (err instanceof WebhookVerificationError) {
      return new Response(JSON.stringify({ error: err.detail }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(JSON.stringify({ error: "Internal error" }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }
}
