"""Microsoft Teams auto-reply agent — end-to-end demo.

Two modes:

  FAKE (default, no Azure needed):
    uv run python examples/teams_autoreply.py

  REAL (Azure Bot registration required):
    TEAMS_APP_ID=<app-id> TEAMS_APP_PASSWORD=<app-password> \\
    uv run python examples/teams_autoreply.py --real

For the real mode you also need:
  - CASPIAN_BASE_URL / CASPIAN_API_KEY pointing at a running gateway
  - The gateway's /webhooks/teams endpoint exposed via ngrok or similar
  - That URL registered as the Bot Framework messaging endpoint in Azure
"""

import argparse
import json
import os

from caspian_sdk import CommClient

parser = argparse.ArgumentParser()
parser.add_argument("--real", action="store_true", help="Use live Azure Bot credentials")
args = parser.parse_args()

client = CommClient()

customer = client.create_customer("Demo Corp")
agent = client.create_agent("Teams Bot")

if args.real:
    import os
    connection = client._connect(
        "teams",
        customer["id"],
        agent["id"],
        app_id=os.environ["TEAMS_APP_ID"],
        app_password=os.environ["TEAMS_APP_PASSWORD"],
    )
else:
    # channel is always "teams"; the gateway picks the fake-teams provider when
    # it is the one configured for the teams channel (COMM_PROVIDER=fake-teams).
    # A unique app_id keeps each demo run's resource distinct (single-tenant guard).
    import secrets as _secrets
    connection = client._connect(
        "teams", customer["id"], agent["id"],
        app_id=f"demo-{_secrets.token_hex(4)}",
    )

print(f"Teams connection active: {connection['id']}  status={connection['status']}")
print(f"Provider address: {connection.get('address')}")

if not args.real:
    # Simulate an inbound Teams message via the fake provider's webhook shape
    import httpx, os
    base = os.environ.get("CASPIAN_BASE_URL", "http://localhost:8000")
    payload = {
        "type": "message",
        "id": "msg-001",
        "channelId": "msteams",
        "from": {"id": "user-aad-id", "name": "Alice"},
        "conversation": {"id": "19:demo@thread.tacv2"},
        "text": "Hello from Teams!",
        "serviceUrl": "https://smba.trafficmanager.net/teams/",
        "recipient": {"id": connection.get("address", "").removeprefix("teams:")},
    }
    resp = httpx.post(
        f"{base}/internal/providers/fake-teams/webhooks",
        content=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    print(f"Simulated webhook -> HTTP {resp.status_code}")


@client.on_message
def handle(message):
    sender = (message.sender or {}).get("address", "unknown")
    print(f"Inbound from {sender}: {message.text!r}")
    message.reply(f"Teams bot received: {message.text}")


print("Listening (Ctrl+C to stop)")
client.listen()
