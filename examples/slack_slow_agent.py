"""Slack agent that thinks longer than Slack's patience.

Slack expects webhook responses fast, so a slow agent (LLM calls, tool use)
normally needs an ack-now-reply-later queue. `listen(ack=...)` does that for
you: the sender sees an instant acknowledgement, your handler takes as long
as it needs, and the real reply lands in the same thread.

Setup: create a Slack app at api.slack.com/apps (or run `caspian connect
slack` for the guided version), then:

    CASPIAN_API_KEY=... SLACK_CLIENT_ID=... SLACK_CLIENT_SECRET=... \
    SLACK_SIGNING_SECRET=... uv run python examples/slack_slow_agent.py
"""

import os
import time

from caspian_sdk import CommClient

client = CommClient()

connection = client.connect_slack(
    slack_client_id=os.environ["SLACK_CLIENT_ID"],
    slack_client_secret=os.environ["SLACK_CLIENT_SECRET"],
    slack_signing_secret=os.environ["SLACK_SIGNING_SECRET"],
)
print("Approve the install, then mention the bot in a channel it's invited to:")
print(connection["authorize_url"])


def expensive_agent_call(question: str) -> str:
    time.sleep(15)  # stand-in for your LLM + tool loop
    return f"Here's what I found on {question!r}: ..."


@client.on_message
def handle(message):
    message.reply(expensive_agent_call(message.text))


# Instant ack, then the real reply whenever it's ready — threading handled.
client.listen(ack="On it, one moment…")
