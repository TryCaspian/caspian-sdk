"""Example of running the Caspian SDK in a serverless function (FastAPI/AWS Lambda)."""

import os
from fastapi import FastAPI, Request, HTTPException
from caspian_sdk import CommClient, WebhookVerificationError

app = FastAPI()

# 1. Initialize the client outside the handler to reuse connection pools
client = CommClient(api_key=os.environ.get("CASPIAN_API_KEY"))
WEBHOOK_SECRET = os.environ.get("CASPIAN_WEBHOOK_SECRET")

# 2. Register your agent logic normally
@client.on_message
def handle_message(msg):
    with msg.stream() as s:
        s.append(f"Received via serverless webhook! You said: {msg.text}")

# 3. Route inbound HTTP requests into the SDK's webhook handler
@app.post("/api/caspian-webhook")
async def caspian_webhook(request: Request):
    signature = request.headers.get("x-caspian-signature")
    if not signature:
        raise HTTPException(status_code=401, detail="Missing signature")
    
    body = await request.body()
    try:
        # Verifies the signature, deduplicates the event, and routes to handlers
        client.handle_webhook(body, signature, WEBHOOK_SECRET)
    except WebhookVerificationError:
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    return {"ok": True}
