"""Slack agent driven by slash commands and emoji reactions, not just prose.

Three inbound event types, three handlers. `on_message` reads what a human
typed; `on_command` catches an explicit `/deploy prod` with no intent-guessing;
`on_reaction` treats a :white_check_mark: as approval — a one-tap confirmation
that beats asking "are you sure?" and waiting for someone to type "yes".

Commands and reactions are separate events for a reason: an agent that pattern-
matches "/deploy" out of message text will also fire on someone *quoting* the
command in a sentence. An explicit event has no such ambiguity.

Your Slack app needs the `commands`, `reactions:read`, and `reactions:write`
scopes; the SDK's default scope string already requests them, and Slack sends
nothing at all if they're missing.

Setup: create a Slack app at api.slack.com/apps (or run `caspian connect
slack`), add a slash command pointing at your webhook, then:

    CASPIAN_API_KEY=... SLACK_CLIENT_ID=... SLACK_CLIENT_SECRET=... \
    SLACK_SIGNING_SECRET=... uv run python examples/slack_commands_and_reactions.py
"""

import os

from caspian_sdk import CommClient

client = CommClient()

connection = client.connect_slack(
    slack_client_id=os.environ["SLACK_CLIENT_ID"],
    slack_client_secret=os.environ["SLACK_CLIENT_SECRET"],
    slack_signing_secret=os.environ["SLACK_SIGNING_SECRET"],
)
print("Approve the install, then try /deploy in a channel the bot is in:")
print(connection["authorize_url"])

# Commands the agent knows about. Anything else gets a usage hint rather than
# silence, so a typo doesn't look like the bot is down.
KNOWN = {"/deploy", "/status"}


@client.on_command
def handle_command(command):
    if command.name not in KNOWN:
        command.reply(f"Unknown command. Try: {', '.join(sorted(KNOWN))}")
        return
    if command.name == "/status":
        command.reply("All systems nominal.")
        return
    target = command.args[0] if command.args else "staging"
    command.reply(f"Deploying to {target}. React with :white_check_mark: to confirm.")


@client.on_reaction
def handle_reaction(reaction):
    # Removing the checkmark is a withdrawal of approval, not a second approval,
    # so the action has to be checked and not just the emoji.
    if reaction.emoji == "white_check_mark" and reaction.action == "added":
        print(f"Approved by {reaction.sender} on {reaction.source_message}")


@client.on_message
def handle_message(message):
    message.reply("Try /status or /deploy — I take commands, not hints.")


client.listen()
