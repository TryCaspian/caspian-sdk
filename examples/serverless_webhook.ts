/**
 * Serverless webhook mode: one pushed event per invocation (fetch-style
 * handler — works on Vercel functions, Cloudflare Workers, Deno, Bun).
 *
 * No poll loop — point the gateway at this function's URL once:
 *
 *     await client.setWebhook("https://<your-function-url>", "<random secret>");
 *
 * and it POSTs each event delivery here. The SDK verifies the
 * x-caspian-signature HMAC and dispatches to the same handlers listen() uses.
 *
 * Set CASPIAN_API_KEY / CASPIAN_BASE_URL (legacy COMM_* names also work) and
 * CASPIAN_WEBHOOK_SECRET in the function's environment.
 */
import { CommClient, WebhookVerificationError } from "caspian-sdk";

const client = new CommClient();

client.onMessage(async (message) => {
  await message.reply(`Thanks for reaching out. You said: ${message.text}`);
});

export async function POST(request: Request): Promise<Response> {
  try {
    const result = await client.handleWebhook(request);
    return Response.json(result);
  } catch (err) {
    if (err instanceof WebhookVerificationError) {
      return Response.json({ error: err.detail }, { status: err.statusCode });
    }
    throw err;
  }
}
