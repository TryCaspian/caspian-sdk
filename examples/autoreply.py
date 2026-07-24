"""Minimal auto-reply agent.

Point CASPIAN_BASE_URL and CASPIAN_API_KEY (or the legacy COMM_BASE_URL /
COMM_API_KEY names) at a Caspian gateway (hosted, or any deployment of the
Caspian gateway) — `caspian init` writes them to ./.env — then:

    uv run python examples/autoreply.py
"""

from caspian_sdk import CommClient

# Reads CASPIAN_API_KEY / CASPIAN_BASE_URL (legacy COMM_API_KEY / COMM_BASE_URL
# also work) from the environment or ./.env, e.g. as written by `caspian init`
# (base_url defaults to the hosted gateway at https://api.trycaspianai.com).
client = CommClient()

customer = client.create_customer("Acme")
agent = client.create_agent("Support Agent")
connection = client.connect_email(customer["id"], agent["id"], username="support")
print(f"Email connection active: {connection['address']}")


@client.on_message
def handle(message):
    print(f"Inbound from {message.sender['address']}: {message.text!r}")
    message.reply(f"Thanks for reaching out. You said: {message.text}")


print("Listening for inbound messages (Ctrl+C to stop)")
client.listen()
