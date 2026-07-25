"""Per-channel behaviour guides for the agent's brain.

These are opt-in: a developer injects them into their agent's system prompt via
``client.behavior_prompt()`` (or fetches one with ``client.channel_guide(...)``),
tweaks them, or ignores them entirely. They tell the LLM how to *behave* on each
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
}


def guide_for(channel: str) -> str | None:
    """The behaviour guide for one channel, or None if there isn't one."""
    return _GUIDES.get(channel)


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
