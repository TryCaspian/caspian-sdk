"""Serverless webhook mode: one pushed event per invocation (AWS Lambda shown).

No poll loop — point the gateway at this function's URL once:

    client.set_webhook("https://<your-function-url>", secret="<random secret>")

and it POSTs each event delivery here. The SDK verifies the
x-caspian-signature HMAC and dispatches to the same handlers listen() uses.

Set CASPIAN_API_KEY / CASPIAN_BASE_URL (legacy COMM_* names also work) and
CASPIAN_WEBHOOK_SECRET in the function's environment.
"""

import json

from caspian_sdk import CommClient, WebhookVerificationError

client = CommClient()


@client.on_message
def handle(message):
    message.reply(f"Thanks for reaching out. You said: {message.text}")


def lambda_handler(event, context):
    # API Gateway / Function URL proxy event: body is the raw signed payload.
    try:
        result = client.handle_webhook(event["body"] or "", event["headers"])
    except WebhookVerificationError as exc:
        return {"statusCode": exc.status_code, "body": exc.detail}
    return {"statusCode": 200, "body": json.dumps(result)}
