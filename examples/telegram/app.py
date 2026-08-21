"""Shared Telegram handlers. Self-host and hosted register the same rules."""

from __future__ import annotations

from caspian import (
    Action,
    Attachment,
    Button,
    Caspian,
    HandlerContext,
    Message,
    Thread,
)

PHOTO = "https://www.python.org/static/community_logos/python-logo.png"
STORY = ("Once ", "upon ", "a time, ", "a bot ", "learned ", "to stream.")
HELP = """\
/help     this menu + buttons
/reply    quote the message you sent
/send     a standalone post (not a reply)
/buttons  callback + url keyboard
/blocks   rich layout (Telegram renders as text)
/media    send a photo by URL
/typing   typing indicator, then a line
/story    stream a reply in place
/react    thumbs-up the message you sent
/pin      pin that message (groups)
/unpin    unpin it
/delete   delete that message
/forward  forward it to this same chat
/initiate cold-DM shape (same send on Telegram)

Anything else is echoed. Photos and files come back as media.
"""

MENU = (
    Button(label="buttons", data="buttons"),
    Button(label="story", data="story"),
    Button(label="media", data="media"),
    Button(label="docs", url="https://core.telegram.org/bots/api"),
)


def _help(thread: Thread) -> None:
    thread.post(HELP, actions=MENU)


def _buttons(thread: Thread) -> None:
    thread.post("callback or url:", actions=MENU)


def _story(thread: Thread) -> None:
    with thread.stream(min_chars=1, throttle=0.25) as out:
        for chunk in STORY:
            out.append(chunk)


def _media(thread: Thread) -> None:
    thread.send_media(Attachment(type="photo", url=PHOTO), caption="by url")


def register(cx: Caspian) -> None:
    """Attach the Telegram kitchen-sink rules. Specific commands first; echo last."""

    @cx.on_message({"channel": "telegram", "command": ["start", "help"]})
    def on_help(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        _help(thread)

    @cx.on_message({"channel": "telegram", "command": "reply"})
    def on_reply(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.reply(msg.message_id, "quoted.")

    @cx.on_message({"channel": "telegram", "command": "send"})
    def on_send(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.send("standalone — not a reply to your message.")

    @cx.on_message({"channel": "telegram", "command": "buttons"})
    def on_buttons(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        _buttons(thread)

    @cx.on_message({"channel": "telegram", "command": "blocks"})
    def on_blocks(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.send_blocks(
            (),
            text="SendBlocks — Telegram has no native blocks, so this is text + keyboard.",
            actions=(Button(label="ok", data="ok"),),
        )

    @cx.on_message({"channel": "telegram", "command": "media"})
    def on_media(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        _media(thread)

    @cx.on_message({"channel": "telegram", "command": "typing", "overlap": "drop"})
    def on_typing(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.typing()
        thread.post("done thinking.")

    @cx.on_message({"channel": "telegram", "command": "story"})
    def on_story(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        _story(thread)

    @cx.on_message({"channel": "telegram", "command": "react"})
    def on_react(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.react(msg.message_id, "👍")

    @cx.on_message({"channel": "telegram", "command": "pin", "kind": "group"})
    @cx.on_message({"channel": "telegram", "command": "pin", "kind": "channel"})
    def on_pin_group(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.pin(msg.message_id)

    @cx.on_message({"channel": "telegram", "command": "pin", "kind": "dm"})
    def on_pin_dm(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.post("pin needs a group.")

    @cx.on_message({"channel": "telegram", "command": "unpin"})
    def on_unpin(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.unpin(msg.message_id)

    @cx.on_message({"channel": "telegram", "command": "delete"})
    def on_delete(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.delete(msg.message_id)

    @cx.on_message({"channel": "telegram", "command": "forward"})
    def on_forward(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.forward(thread.thread_id, msg.message_id)

    @cx.on_message({"channel": "telegram", "command": "initiate"})
    def on_initiate(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.initiate("hello — Initiate maps to sendMessage on Telegram.")

    @cx.on_action({"channel": "telegram", "data": "buttons"})
    def on_action_buttons(thread: Thread, action: Action, ctx: HandlerContext) -> None:
        _buttons(thread)

    @cx.on_action({"channel": "telegram", "data": "story", "overlap": "stream"})
    def on_action_story(thread: Thread, action: Action, ctx: HandlerContext) -> None:
        _story(thread)

    @cx.on_action({"channel": "telegram", "data": "media"})
    def on_action_media(thread: Thread, action: Action, ctx: HandlerContext) -> None:
        _media(thread)

    @cx.on_action({"channel": "telegram"})
    def on_action_other(thread: Thread, action: Action, ctx: HandlerContext) -> None:
        thread.post(f"you picked {action.data}")

    @cx.on_message({"channel": "telegram"})
    def on_echo(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        if msg.attachments:
            att = msg.attachments[0]
            thread.send_media(att, caption=f"got {att.type}")
            return
        text = msg.text.strip()
        if not text:
            return
        extra = f" (reply to {msg.reply_to})" if msg.reply_to else ""
        thread.post(f"{text}{extra}")
