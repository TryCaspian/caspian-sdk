"""Telegram reminder agent — the bot speaks FIRST.

Most channel examples only reply. The useful half is the agent initiating:
"remind me in 20 stretch" -> twenty minutes later the bot messages you,
unprompted, in the same chat.

Setup: get a bot token from @BotFather on Telegram, then:

    CASPIAN_API_KEY=... TELEGRAM_BOT_TOKEN=7123:AAE... \
        uv run python examples/telegram_reminders.py
"""

import os
import re
import threading

from caspian_sdk import CommClient

client = CommClient()  # reads CASPIAN_API_KEY / CASPIAN_BASE_URL from env or ./.env

client.connect_telegram(bot_token=os.environ["TELEGRAM_BOT_TOKEN"])
print('Telegram connection active. Message your bot: "remind me in 1 stretch"')


def schedule(conversation_id: str, minutes: float, text: str) -> None:
    def fire():
        # Agent-initiated send: no inbound message triggered this.
        client.send_message(conversation_id, text=f"Reminder: {text}")

    threading.Timer(minutes * 60, fire).start()


@client.on_message
def handle(message):
    match = re.match(r"remind me in (\d+)\s*(?:min\w*)?\s*(.*)", message.text or "", re.I)
    if not match:
        message.reply('Try: "remind me in 20 stand up and stretch"')
        return
    minutes, text = int(match.group(1)), match.group(2) or "you asked for a nudge"
    schedule(message.conversation_id, minutes, text)
    message.reply(f"Set. I'll message you in {minutes} min.")


client.listen()
