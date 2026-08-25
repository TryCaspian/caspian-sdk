"""Per-channel behaviour guides for the agent's brain.

These are opt-in: a developer fetches them from ``GET /v1/channels`` (or one at a
time from ``GET /v1/channels/{channel}/guide``), injects them into their agent's
system prompt, tweaks them, or ignores them entirely. They tell the LLM how to
*behave* on each
platform (threading, formatting, length, etiquette) — separate from SKILL.md,
which tells the coding agent how to *build* the integration.

Keyed by logical channel (not provider), since behaviour is channel-level.
"""

_GUIDES: dict[str, str] = {
    "slack": """## Slack
- Replies auto-thread: `reply()` posts under the user's message. Stay in that thread; don't start a new top-level message.
- Respond only when @-mentioned in a channel, or to any direct message. Ignore unrelated channel chatter.
- Formatting is Slack mrkdwn: *bold* (single asterisk), _italic_, `code`, ```code blocks```, > quote. Standard **markdown** bold does NOT render.
- Keep it short and conversational — Slack is chat, not email.
- Mention a person as <@USERID> when you have their id.""",

    "discord": """## Discord
- Replies post in the same channel or thread as the incoming message.
- Respond when @-mentioned or in a DM — not to every message in a channel.
- Markdown works: **bold**, *italic*, `code`, ```blocks```, > quote.
- Hard limit ~2000 characters per message; split longer answers.
- Mention a user as <@USERID>.""",

    "whatsapp": """## WhatsApp
- Plain, human text; minimal formatting. Keep it brief and personal (1:1 chat).
- You can reply freely within 24h of the user's last message (the customer-service window). Outside it only pre-approved templates send — so answer promptly.
- No @-mentions or channels; it's a direct conversation.""",

    "x": """## X (Twitter)
- Direct messages allow long text (~10,000 chars), plain, no markdown.
- Public replies are capped at 280 characters — be terse; links count toward the limit.
- Conversational. Don't add hashtags or @-handles unless the user asks.""",

    "email": """## Email
- Longer-form is fine and expected. Use a clear subject and a structured body.
- Replies quote and thread into the existing conversation automatically — don't restate the whole thread.
- Assume plain rendering (light markdown at most). Sign off naturally.""",

    "telegram": """## Telegram
- Supports Markdown: *bold*, _italic_, `code`. Keep messages fairly short.
- Reply directly; the SDK threads to the user's message. Fast, personal, chat tone.""",

    "imessage": """## iMessage
- Plain text ONLY — no markdown, headers, or code blocks (they render as literal characters).
- Short, texting-style messages; split a long answer into a couple of texts.
- Personal 1:1 tone.""",

    "phone": """## SMS
- Plain text, no formatting. Keep it very short — long messages split into multiple segments (each costs money).
- Avoid links unless necessary. Texting tone.""",

    "rcs": """## RCS
- Rich chat surface: concise text, texting-style. Keep messages short; the platform handles any media/buttons.""",

    "instagram": """## Instagram DM
- Plain text, casual DM tone, no markdown. Keep replies short — it's a mobile chat surface.""",

    "facebook": """## Facebook Messenger
- Plain text, casual DM tone, no markdown. Keep replies short.""",

    "voice": """## Voice
- This is SPOKEN aloud. Write for the ear: short sentences, no markdown, no URLs, no code. Spell out anything ambiguous.""",

    "bluesky": """## Bluesky
- Short-form, plain text — no markdown rendering. Keep replies concise (a post is capped at 300 characters).
- Reply directly to the user's message; the SDK threads it. Conversational, friendly tone.
- Don't add hashtags or @-handles unless the user asks.""",

    "linear": """## Linear
- Issue comments and updates thread to the Linear issue (identifier like ENG-123).
- Markdown is supported for code snippets, lists, and quotes.
- Keep comments constructive, concise, and focused on issue resolution.""",
}



# Setup guides: how a DEVELOPER connects a channel (credentials + where to get
# them), distinct from the behaviour guides above (how the agent should reply).
# Surfaced on GET /v1/channels and GET /v1/channels/{channel}/guide so onboarding
# is self-serve.
_SETUP: dict[str, str] = {
    "bluesky": """### Connect Bluesky (via the Caspian SDK)
Install:  pip install caspian-sdk   (or: uv add caspian-sdk)

Point the SDK at THIS gateway (the base URL is the host serving this endpoint).
The channel list/guide are public, but creating a connection is authenticated —
you need a project API key (ask the human, or read CASPIAN_API_KEY yourself —
the SDK does not read the environment for you):
    import os
    from caspian import Caspian

    cx = Caspian(api_key=os.environ["CASPIAN_API_KEY"],
                 base_url=os.environ.get("CASPIAN_BASE_URL", "https://api.trycaspianai.com"))

Per-connection credentials (ask the human for these two):
- `identifier`   — the account's Bluesky handle (e.g. `myagent.bsky.social`) or email.
- `app_password` — a Bluesky App Password (NOT the account login password).

Create the app password: log in at https://bsky.app -> Settings -> Privacy and
Security -> App Passwords -> Add App Password -> copy it (`xxxx-xxxx-xxxx-xxxx`).

Connect, then receive & reply:
    cx.channels.add("bluesky", identifier="myagent.bsky.social",
                    app_password="xxxx-xxxx-xxxx-xxxx")

    @cx.on_message()
    def handle(thread, msg, ctx):
        thread.post("...")   # your answer to msg.text

    cx.run()   # polls the gateway for inbound (no webhook to configure)

Capabilities: receive, reply, send.""",
}


def guide_for(channel: str) -> str | None:
    """The behaviour guide for one channel, or None if there isn't one."""
    return _GUIDES.get(channel)


def setup_for(channel: str) -> str | None:
    """The connect/setup guide for one channel, or None if there isn't one."""
    return _SETUP.get(channel)


def combined_guide(channels: list[str]) -> str:
    """One system-prompt block covering the given channels (deduped, ordered).

    Empty string when none of the channels have a guide, so callers can append
    it unconditionally.
    """
    seen: list[str] = []
    for ch in channels:
        if ch not in seen and ch in _GUIDES:
            seen.append(ch)
    if not seen:
        return ""
    header = (
        "# How to reply on each channel\n\n"
        "You reach humans across several channels behind one handler. Each channel "
        "has its own conventions — follow the one that matches the incoming "
        "message's channel:\n"
    )
    return header + "\n\n".join(_GUIDES[ch] for ch in seen)
