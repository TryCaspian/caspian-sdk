"""Minimal Linear auto-reply agent.

Set environment variables:

    CASPIAN_BASE_URL=http://127.0.0.1:8000
    CASPIAN_API_KEY=comm_test_replace_me
    LINEAR_API_KEY=lin_api_...          (Linear Personal API key)
    LINEAR_ORGANIZATION_ID=org_...      (Linear Workspace Organization ID)
    LINEAR_WEBHOOK_SECRET=sec_...       (Linear Webhook Secret)

Or reuse an existing connection ID:

    LINEAR_CONNECTION_ID=conn_...
"""

import os

from caspian_sdk import CommClient

client = CommClient()

connection_id = os.getenv("LINEAR_CONNECTION_ID")

if not connection_id:
    # Auto-discover existing active Linear connection to avoid duplicate provisioning
    existing = [
        c
        for c in client.list_connections(channel="linear")
        if c.get("status") in ("active", "provisioning")
    ]
    if existing:
        connection_id = existing[0]["id"]

if connection_id:
    connection = client.get_connection(connection_id)
    print(f"Reusing Linear connection: {connection['id']}")
else:
    customer = client.create_customer("Linear Demo")
    agent = client.create_agent("Linear Auto Reply")

    connection = client.connect_linear(
        api_key=os.environ["LINEAR_API_KEY"],
        organization_id=os.environ["LINEAR_ORGANIZATION_ID"],
        webhook_secret=os.environ["LINEAR_WEBHOOK_SECRET"],
        customer_id=customer["id"],
        agent_id=agent["id"],
    )

    connection_id = connection["id"]
    print(f"Created Linear connection: {connection['id']}")
    print(f'Reuse it next time with: export LINEAR_CONNECTION_ID="{connection_id}"')


@client.on_message
def handle(message):
    sender = (message.sender or {}).get("name", "User")
    print(f"<- Linear Issue Comment from {sender}: {message.text!r}")
    reply_text = "🤖 Caspian Agent ACK: Received your comment on Linear issue. Processing..."
    message.reply(reply_text)
    print("-> Replied to Linear issue comment successfully!")


print("Listening for Linear comments (Ctrl+C to stop)...")
client.listen()
