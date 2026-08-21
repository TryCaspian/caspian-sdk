from caspian import Button, Caspian, HandlerContext, Message, Thread

HELP = "/help menu\n/typing indicator\nanything else is echoed."
MENU = (Button(label="ok", data="ok"),)


def register(cx: Caspian) -> None:
    @cx.on_message({"channel": "messenger", "command": ["help", "start"]})
    def on_help(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.post(HELP, actions=MENU)

    @cx.on_message({"channel": "messenger", "command": "typing", "overlap": "drop"})
    def on_typing(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.typing()
        thread.post("done thinking.")

    @cx.on_message({"channel": "messenger"})
    def on_echo(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        if msg.text.strip():
            thread.post(msg.text)
