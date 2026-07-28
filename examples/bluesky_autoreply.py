"""Minimal Bluesky auto-reply agent.

Set:

    CASPIAN_BASE_URL=http://127.0.0.1:8000
    CASPIAN_API_KEY=comm_test_replace_me
    BLUESKY_CONNECTION_ID=<existing-connection-id>

For first-time provisioning, also set:

    BLUESKY_IDENTIFIER=your-handle.bsky.social
    BLUESKY_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
"""

import os
import time
from caspian_sdk import CommClient

client = CommClient()

connection_id = os.getenv("BLUESKY_CONNECTION_ID")

# Reuse an existing Bluesky connection if BLUESKY_CONNECTION_ID is provided.
# This avoids reconnecting the same Bluesky identity on every run.
if connection_id:
    connection = client.get_connection(connection_id)
    print(f"Reusing Bluesky connection: {connection}")
else:
    customer = client.create_customer("Bluesky Demo")
    agent = client.create_agent("Bluesky Auto Reply")

    connection = client.connect_bluesky(
        identifier=os.environ["BLUESKY_IDENTIFIER"],
        app_password=os.environ["BLUESKY_APP_PASSWORD"],
        customer_id=customer["id"],
        agent_id=agent["id"],
    )

    connection_id = connection["id"]

    print(f"Created Bluesky connection: {connection}")
    print()
    print("Reuse it next time with:")
    print(f'export BLUESKY_CONNECTION_ID="{connection_id}"')


@client.on_message
def handle(message):
    print(f"Inbound from {message.sender['address']}: {message.text!r}")
    time.sleep(10)
    message.reply(f"Thanks for reaching out. You said: {message.text}")


print("Listening for inbound messages (Ctrl+C to stop)")
client.listen(
    ack="Please wait while I process your request. I'm working on it now."
)