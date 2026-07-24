"""Proactive reminder: cold-start a conversation with an outbound message.

Connects an SMS/voice phone line, then uses `initiate()` to send a message to a
recipient who hasn't written first. This is the outbound (proactive) direction -
no inbound listener needed.

`initiate()` works on any channel with the INITIATE capability. Today that's SMS;
other initiate-capable channels light up the same call as they ship. It does not
imply any particular channel is available on your deployment - check `channels()`.

Bring your own carrier creds (Twilio or Telnyx) via keyword args to connect_phone,
per your deployment's configuration.

Run:

    export CASPIAN_API_KEY=...          # from the dashboard
    uv run python examples/reminder.py
"""

from caspian_sdk import CommClient

# Reads CASPIAN_API_KEY / CASPIAN_BASE_URL from the environment or ./.env
# (base_url defaults to the hosted gateway at https://api.trycaspianai.com).
client = CommClient()

customer = client.create_customer("Acme")
agent = client.create_agent("Reminder Agent")

# Bring-your-own carrier creds go here as kwargs (e.g. Twilio or Telnyx),
# depending on how your deployment is configured.
connection = client.connect_phone(customer["id"], agent["id"])
print(f"Phone connection active: {connection['id']}")

# Placeholder recipient - swap in a real E.164 number you're allowed to message.
recipient = "+15555550123"

result = client.initiate(
    connection["id"],
    recipient=recipient,
    text="Reminder: your appointment is tomorrow at 10am. Reply STOP to opt out.",
)
print(f"Sent reminder to {recipient}: {result}")
