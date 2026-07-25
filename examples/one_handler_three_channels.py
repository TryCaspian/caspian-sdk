"""One handler, three channels: Discord + Telegram + email.

The point of Caspian in one file — the handler never branches on channel.
Discord gateway intents, Telegram privacy modes, email threading: all behind
the connect calls. Adding a fourth channel is one more line, not new code.

Setup: a Discord bot token (discord.com/developers, Message Content intent
on) and a Telegram token from @BotFather. Email needs nothing.

    CASPIAN_API_KEY=... DISCORD_BOT_TOKEN=... TELEGRAM_BOT_TOKEN=... \
        uv run python examples/one_handler_three_channels.py
"""

import os

from caspian_sdk import CommClient

client = CommClient()

client.connect_discord(bot_token=os.environ["DISCORD_BOT_TOKEN"])
client.connect_telegram(bot_token=os.environ["TELEGRAM_BOT_TOKEN"])
inbox = client.connect_email(username="omnipresent-agent")
print(f"Live on Discord, Telegram, and {inbox['address']}")


@client.on_message
def handle(message):
    # Same code path for a Discord mention, a Telegram DM, and an email.
    message.reply(f"({message.channel}) heard you: {message.text}")


client.listen()
