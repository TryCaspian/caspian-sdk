from caspian import Caspian, HandlerContext, Message, Thread

HELP = "/help menu\nanything else is echoed."


def register(cx: Caspian) -> None:
    @cx.on_message({"channel": "x", "command": "help"})
    def on_help(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.post(HELP)

    @cx.on_message({"channel": "x"})
    def on_echo(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        if msg.text.strip():
            thread.post(msg.text)
