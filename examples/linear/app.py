from caspian import Caspian, HandlerContext, Message, Thread

HELP = "Commands: help — this menu"


def register(cx: Caspian) -> None:
    @cx.on_message({"channel": "linear", "command": "help"})
    def on_help(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.post(HELP)
