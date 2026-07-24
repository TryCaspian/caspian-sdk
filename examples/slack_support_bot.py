"""Slack support bot that answers with rich message blocks.

Installs the shared Slack app onto a workspace (one-click OAuth), then replies to
every inbound message with a rich card: a heading, a short body, and two buttons
- a "Read the docs" link and a "Talk to a human" callback. This doubles as a
showcase for rich messages.

Rich blocks render natively on Slack (and Discord, Telegram, email); on
text-only channels (SMS, etc.) they degrade automatically to clean text, so
buttons appear as labelled links / "reply ..." hints. You never lose the message.

Run:

    export CASPIAN_API_KEY=...          # from the dashboard
    uv run python examples/slack_support_bot.py

The script prints an "Add to Slack" URL once. Open it, pick a workspace, approve,
and messages in that workspace start flowing to this agent.
"""

from caspian_sdk import CommClient
from caspian_sdk import blocks as b

# Reads CASPIAN_API_KEY / CASPIAN_BASE_URL from the environment or ./.env
# (base_url defaults to the hosted gateway at https://api.trycaspianai.com).
client = CommClient()

customer = client.create_customer("Acme")
agent = client.create_agent("Support Bot")

# One-click install of the shared Slack app - no Slack app to create.
# Pass display_name to post under your own brand instead of the shared name.
connection = client.install_slack(customer["id"], agent["id"], display_name="Acme Support")
print("Open this once to add the bot to your Slack workspace:")
print(f"    {connection['authorize_url']}")
print("Waiting for the workspace owner to approve, then listening...\n")


@client.on_message
def handle(message):
    print(f"Inbound from {message.sender['address']}: {message.text!r}")
    message.reply(
        blocks=[
            b.heading("Thanks for reaching out"),
            b.text(
                "We got your message and a teammate will follow up shortly. "
                "In the meantime, the docs cover most common questions."
            ),
            b.buttons(
                [
                    {"label": "Read the docs", "url": "https://docs.trycaspianai.com"},
                    {"label": "Talk to a human", "value": "escalate:human"},
                ]
            ),
        ],
        # Plain-text fallback for channels that can't render blocks.
        text="Thanks for reaching out - a teammate will follow up shortly.",
    )


print("Listening for inbound messages (Ctrl+C to stop)")
client.listen()
