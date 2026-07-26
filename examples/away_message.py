"""Away-message agent: reply to each sender at most once per session.

A plain autoreply (see examples/autoreply.py) answers every inbound message,
which can spam a sender who writes multiple times while you're away. This
example adds a simple in-memory guard so each sender only gets one
out-of-office notice, no matter how many messages they send.

Run:

    export CASPIAN_API_KEY=...          # from the dashboard
    uv run python examples/away_message.py

Swap the in-memory `already_replied` set for a database or cache if you need
this to survive a restart.
"""

from caspian_sdk import CommClient

# Reads CASPIAN_API_KEY / CASPIAN_BASE_URL from the environment or ./.env
# (base_url defaults to the hosted gateway at https://api.trycaspianai.com).
client = CommClient()

customer = client.create_customer("Acme")
agent = client.create_agent("Away Agent")
connection = client.connect_email(customer["id"], agent["id"], username="support")
print(f"Email connection active: {connection['address']}")

AWAY_MESSAGE = (
    "Thanks for reaching out. I'm away right now and will reply as soon as "
    "I'm back. This is the only automated reply you'll get for this thread."
)

already_replied: set[str] = set()


@client.on_message
def handle(message):
    sender = message.sender["address"]
    if sender in already_replied:
        print(f"Skipping duplicate reply to {sender}")
        return

    already_replied.add(sender)
    print(f"Sending away-message to {sender}")
    message.reply(AWAY_MESSAGE)


print("Listening for inbound messages (Ctrl+C to stop)")
client.listen()