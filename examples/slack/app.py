from caspian import Action, Button, Caspian, HandlerContext, Message, Thread

HELP = "/help menu\n/blocks layout + button\n/send standalone\nanything else is echoed."
MENU = (Button(label="ok", data="ok"),)


def register(cx: Caspian) -> None:
    @cx.on_message({"channel": "slack", "command": ["help", "start"]})
    def on_help(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.post(HELP, actions=MENU)

    @cx.on_message({"channel": "slack", "command": "blocks"})
    def on_blocks(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.send_blocks((), text="blocks", actions=(Button(label="ok", data="ok"),))

    @cx.on_message({"channel": "slack", "command": "send"})
    def on_send(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.send("standalone")

    @cx.on_action({"channel": "slack", "data": "ok"})
    def on_ok(thread: Thread, action: Action, ctx: HandlerContext) -> None:
        thread.post("ok")

    @cx.on_message({"channel": "slack"})
    def on_echo(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        if msg.text.strip():
            thread.post(msg.text)
