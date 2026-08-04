"""Serverless webhook handler example (Python / AWS Lambda).

Instead of running client.listen() in an infinite poll loop, serverless functions
process pushed gateway deliveries one-by-one with client.handle_webhook().

Configure your webhook URL and secret in Caspian first:
    client.set_webhook("https://your-api-gateway.com/webhook", secret="whsec_123")
"""

import json
import os
from caspian_sdk import CommClient, WebhookVerificationError

client = CommClient()
WEBHOOK_SECRET = os.environ.get("CASPIAN_WEBHOOK_SECRET", "whsec_123")


@client.on_message
def handle_message(message):
    print(f"Serverless received: {message.text}")
    message.reply(f"Serverless auto-reply: {message.text}")


def lambda_handler(event, context):
    """AWS Lambda entrypoint."""
    body = event.get("body", "")
    headers = event.get("headers", {})

    try:
        result = client.handle_webhook(body, headers, WEBHOOK_SECRET)
        return {
            "statusCode": 200,
            "body": json.dumps({"status": result.status, "event_id": result.event_id}),
        }
    except WebhookVerificationError as exc:
        return {
            "statusCode": 401,
            "body": json.dumps({"error": str(exc)}),
        }
