"""Serverless Caspian AI agent on AWS Lambda.

Run a Caspian agent with NO always-on server, using ``client.handle_webhook``
(the serverless entry point). In production the Caspian gateway pushes each
incoming message to this Lambda's Function URL; ``handle_webhook`` verifies the
signature and dispatches it to your ``@on_message`` agent, which here calls an
LLM (Amazon Bedrock Nova, authenticated via the Lambda's IAM role - no API key).
The function scales to zero between messages.

Env vars:
  CASPIAN_WEBHOOK_SECRET  the secret you set via client.set_webhook(url, secret)
  BEDROCK_MODEL           e.g. us.amazon.nova-lite-v1:0
  BEDROCK_REGION          e.g. us-east-1

Deploy: see README.md.
"""
import base64
import hashlib
import hmac
import json
import os

import boto3  # provided by the Lambda Python runtime
from caspian_sdk import CommClient

SECRET = os.environ["CASPIAN_WEBHOOK_SECRET"]
_MODEL = os.environ.get("BEDROCK_MODEL", "us.amazon.nova-lite-v1:0")
_bedrock = boto3.client("bedrock-runtime", region_name=os.environ.get("BEDROCK_REGION", "us-east-1"))
_out = {}

client = CommClient()  # add api_key=... if your agent replies via the gateway


@client.on_message
def agent(message):
    """Your agent. Swap this body for any logic/LLM you like."""
    resp = _bedrock.converse(
        modelId=_MODEL,
        system=[{"text": "You are a helpful AI agent. Answer concisely."}],
        messages=[{"role": "user", "content": [{"text": message.text or ""}]}],
        inferenceConfig={"maxTokens": 400, "temperature": 0.7},
    )
    reply = resp["output"]["message"]["content"][0]["text"]
    _out["reply"] = reply
    # In a real deployment, answer on the channel instead:
    #     message.reply(reply)   # needs CommClient(api_key=...)


def lambda_handler(event, context):
    """AWS Lambda entry point (Function URL / API Gateway proxy event).

    PRODUCTION: the gateway signs and POSTs the event to you; you only verify +
    dispatch::

        client.handle_webhook(event["body"].encode(), event["headers"], secret=SECRET)

    DEMO (below): POST plain ``{"text": "..."}`` and we wrap it into a
    gateway-shaped event and sign it ourselves, so you can curl the URL directly.
    """
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    text = (json.loads(raw) or {}).get("text", "Hello!")

    body = json.dumps({
        "id": "evt_demo", "type": "message.received",
        "data": {"message": {"id": "m", "conversation_id": "c", "connection_id": "k",
                             "channel": "demo", "text": text}},
    }).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()

    _out.clear()
    result = client.handle_webhook(body, {"x-caspian-signature": sig}, secret=SECRET)
    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps({
            "you_said": text,
            "agent_reply": _out.get("reply"),
            "model": _MODEL,
            "handle_webhook_status": getattr(result, "status", str(result)),
        }),
    }
