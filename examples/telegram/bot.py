"""Telegram bot that exercises every Caspian command Telegram actually supports.

Self-host webhook: Telegram POSTs updates to a public HTTPS URL, this process
calls ``cx.handle``. Poll is the no-public-URL alternative (commented at the
bottom). Hosted is a third path: Caspian's gateway owns the Telegram webhook
and you still pass a BotFather token — hosted does not mint a bot.

Register specific ``command`` / ``data`` rules first; the catch-all echo is last
because ``step()`` takes the first matching rule.
"""

from __future__ import annotations

import os
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from caspian import (
    Action,
    Attachment,
    Button,
    Caspian,
    HandlerContext,
    Message,
    Thread,
)

token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
webhook_url = os.environ.get("TELEGRAM_WEBHOOK_URL", "").strip()
webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip() or secrets.token_urlsafe(24)
if not token:
    raise SystemExit("Set TELEGRAM_BOT_TOKEN (BotFather → /newbot), then rerun.")
if not webhook_url:
    raise SystemExit(
        "Set TELEGRAM_WEBHOOK_URL to a public HTTPS URL (ngrok / cloudflared), then rerun."
    )

cx = Caspian()

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


@cx.on_message(
    {
        "channel": "telegram",
        "command": "story",
        
    }
)
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


@cx.on_action(
    {
        "channel": "telegram",
        "data": "story",
        "overlap": "stream"
    }
)
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


class _Webhook(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        results = cx.handle("telegram", body, {k: v for k, v in self.headers.items()})
        for result in results:
            if not result.is_ok:
                print(result.error)
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        print(format % args)


if __name__ == "__main__":
    parsed = urlparse(webhook_url)
    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("127.0.0.1", port), _Webhook)
    cx.channels.add(
        "telegram",
        via="self-host",
        bot_token=token,
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
    )
    print(f"webhook {webhook_url}  local :{port}{parsed.path or '/'}")
    # cx.poll("telegram")  # no public URL: long-poll getUpdates instead
    #
    # Hosted (gateway owns Telegram's webhook; you still pass the BotFather token):
    #   cx = Caspian(api_key=os.environ["CASPIAN_API_KEY"])
    #   cx.channels.add("telegram", bot_token=token)
    #   cx.run()
    server.serve_forever()
