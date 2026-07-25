"""Email triage agent: classify inbound mail and acknowledge it.

Connects an email inbox (support@<your-domain>), then on every inbound message
does a trivial keyword classification - billing / bug / other - and replies with
a templated acknowledgement for that category.

The classifier is deliberately dumb (plain string checks). The point is the
inbound loop and the templated reply, not the NLP - swap in a real model wherever
`classify` is if you want.

Run:

    export CASPIAN_API_KEY=...          # from the dashboard
    uv run python examples/email_triage.py

Mail sent to the printed address is delivered to the handler below.
"""

from caspian_sdk import CommClient

# Reads CASPIAN_API_KEY / CASPIAN_BASE_URL from the environment or ./.env
# (base_url defaults to the hosted gateway at https://api.trycaspianai.com).
client = CommClient()

customer = client.create_customer("Acme")
agent = client.create_agent("Triage Agent")

# username= picks the mailbox local part (email ignores display_name).
connection = client.connect_email(customer["id"], agent["id"], username="support")
print(f"Email connection active: {connection['address']}")

TEMPLATES = {
    "billing": (
        "Thanks - this looks like a billing question. Our billing team will "
        "review your account and reply within one business day."
    ),
    "bug": (
        "Thanks for the report - we've logged this as a possible bug. An engineer "
        "will take a look and follow up with next steps."
    ),
    "other": (
        "Thanks for reaching out. We've received your message and someone will "
        "get back to you shortly."
    ),
}


def classify(text: str) -> str:
    """Trivial keyword routing. Replace with a real model if you like."""
    lowered = (text or "").lower()
    if any(word in lowered for word in ("invoice", "refund", "charge", "billing", "payment")):
        return "billing"
    if any(word in lowered for word in ("bug", "error", "broken", "crash", "not working")):
        return "bug"
    return "other"


@client.on_message
def handle(message):
    category = classify(message.text)
    print(f"Inbound from {message.sender['address']} -> {category}")
    message.reply(TEMPLATES[category])


print("Listening for inbound messages (Ctrl+C to stop)")
client.listen()
