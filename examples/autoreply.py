"""Minimal auto-reply agent.

Point COMM_BASE_URL and COMM_API_KEY at a Caspian gateway (hosted, or any
deployment of the Caspian gateway), then:

    uv run python examples/autoreply.py
"""

import os

from caspian_sdk import CommClient

client = CommClient(
    api_key=os.environ.get("COMM_BOOTSTRAP_API_KEY", "comm_test_replace_me"),
    base_url=os.environ.get("COMM_BASE_URL", "https://api.trycaspianai.com"),
)

customer = client.create_customer("Acme")
agent = client.create_agent("Support Agent")
connection = client.connect_email(customer["id"], agent["id"], display_name="Acme Support")
print(f"Email connection active: {connection['address']}")


@client.on_message
def handle(message):
    print(f"Inbound from {message.sender['address']}: {message.text!r}")
    message.reply(f"Thanks for reaching out. You said: {message.text}")


print("Listening for inbound messages (Ctrl+C to stop)")
client.listen()
