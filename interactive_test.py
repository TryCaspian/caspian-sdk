"""Live test for interactions + reactions + rich blocks over Telegram.

    export CASPIAN_API_KEY=...            # from the dashboard (any of your projects)
    export TELEGRAM_BOT_TOKEN=...         # @BotFather -> /newbot
    uv run python interactive_test.py

Then, from your phone:
  1. Message the bot -> it replies with a card + two buttons.
  2. Tap "Say hi" (the callback button) -> terminal prints [INTERACTION] and the
     bot replies in-thread. ("Open docs" is a link button, no round-trip.)
  3. Add an emoji reaction to one of the bot's messages -> terminal prints [REACTION].
"""

import os

from caspian_sdk import CommClient
from caspian_sdk import blocks as b

client = CommClient()  # CASPIAN_API_KEY / CASPIAN_BASE_URL from env or ./.env

client.create_customer("Interactive Test")
client.create_agent("Tester")
conn = client.connect_telegram(bot_token=os.environ["TELEGRAM_BOT_TOKEN"])
print(f"Bot connected: {conn['address']} - message it from your phone now.\n")


@client.on_message
def on_message(msg):
    print(f"[MESSAGE] {msg.sender.get('address')}: {msg.text!r}")
    msg.reply(
        text="Tap a button (this line is the plain-text fallback).",
        blocks=[
            b.heading("Interactive test"),
            b.text("Tap a button below - the callback should come back as an event."),
            b.buttons([
                {"label": "Open docs", "url": "https://docs.trycaspianai.com"},
                {"label": "Say hi", "value": "sayhi"},
            ]),
        ],
    )


@client.on_interaction
def on_interaction(i):
    src = i.source_message.get("id") if i.source_message else None
    print(f"[INTERACTION] value={i.value!r}  on_message={src}")
    i.reply(f"Got your tap: {i.value}")


@client.on_reaction
def on_reaction(r):
    print(f"[REACTION] {r.action} {r.emoji!r}")


print("Listening - message the bot, tap 'Say hi', add a reaction. Ctrl+C to stop.")
client.listen()
