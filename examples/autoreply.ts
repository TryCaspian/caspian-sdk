/**
 * Minimal auto-reply agent (TypeScript).
 *
 * TS equivalent of autoreply.py in this folder: connect an email inbox,
 * register a message handler, reply, and listen.
 *
 * Setup (no build/publish step required — runs straight off SDK source):
 *   export COMM_API_KEY=your_key_here     # or CASPIAN_API_KEY
 *   npx tsx examples/autoreply.ts
 *
 * COMM_BASE_URL (or CASPIAN_BASE_URL) is optional; it defaults to the
 * hosted gateway at https://api.trycaspianai.com.
 */
import { CommClient } from "../sdks/typescript/src/index.js";

// Reads COMM_API_KEY / COMM_BASE_URL (or the CASPIAN_* equivalents) from the
// environment or a local .env file - no arguments required.
const client = new CommClient();

async function main() {
  const inbox = await client.connectEmail();
  console.log(`Email connection active: ${inbox.address}`);

  client.onMessage(async (message) => {
    const from = String(message.sender?.["address"] ?? "unknown sender");
    console.log(`Inbound from ${from}: ${message.text}`);
    await message.reply(`Thanks for reaching out. You said: ${message.text}`);
  });

  console.log("Listening for inbound messages (Ctrl+C to stop)");
  await client.listen();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});