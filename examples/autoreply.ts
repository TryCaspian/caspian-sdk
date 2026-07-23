/**
 * Minimal TypeScript auto-reply agent.
 *
 * From the repository root, run:
 *
 *     npx --yes tsx examples/autoreply.ts
 *
 * Set COMM_API_KEY and, when using a non-default gateway, COMM_BASE_URL first.
 * The TypeScript SDK also accepts the branded CASPIAN_* names as fallbacks.
 */

import { CommClient } from "../sdks/typescript/src/index.js";

async function main() {
  const client = new CommClient({
    apiKey: process.env.COMM_API_KEY,
    baseUrl: process.env.COMM_BASE_URL,
  });

  const customer = await client.createCustomer("Acme");
  const agent = await client.createAgent("Support Agent");
  const connection = await client.connectEmail({
    customerId: customer.id,
    agentId: agent.id,
    displayName: "Acme Support",
  });

  console.log(`Email connection active: ${connection.address}`);

  client.onMessage(async (message) => {
    console.log(`Inbound from ${message.sender?.address ?? "unknown sender"}: ${message.text ?? ""}`);
    await message.reply(`Thanks for reaching out. You said: ${message.text ?? ""}`);
  });

  console.log("Listening for inbound messages (Ctrl+C to stop)");
  await client.listen();
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
