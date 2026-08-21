from caspian import Attachment, Caspian, HandlerContext, Message, Thread

HELP = "/help menu\n/media sends a sample image\nanything else is echoed."


def register(cx: Caspian) -> None:
    @cx.on_message({"channel": "sms", "command": "help"})
    def on_help(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.post(HELP)

    @cx.on_message({"channel": "sms", "command": "media"})
    def on_media(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.send_media(
            Attachment(type="file", url="https://example.com/img.jpg"),
            caption="sample",
        )

    @cx.on_message({"channel": "sms"})
    def on_echo(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        if msg.text.strip():
            thread.post(msg.text)
