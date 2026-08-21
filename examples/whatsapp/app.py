from caspian import Attachment, Button, Caspian, HandlerContext, Message, Thread

HELP = "/help menu\n/reply quote\n/media sample image\n/react thumbs-up\nanything else is echoed."
MENU = (Button(label="ok", data="ok"),)


def register(cx: Caspian) -> None:
    @cx.on_message({"channel": "whatsapp", "command": ["help", "start"]})
    def on_help(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.post(HELP, actions=MENU)

    @cx.on_message({"channel": "whatsapp", "command": "reply"})
    def on_reply(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.reply(msg.message_id, "quoted.")

    @cx.on_message({"channel": "whatsapp", "command": "media"})
    def on_media(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.send_media(
            Attachment(type="photo", url="https://example.com/img.jpg"),
            caption="sample",
        )

    @cx.on_message({"channel": "whatsapp", "command": "react"})
    def on_react(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.react(msg.message_id, "👍")

    @cx.on_message({"channel": "whatsapp"})
    def on_echo(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        if msg.text.strip():
            thread.post(msg.text)
