from caspian import Action, Button, Caspian, HandlerContext, Message, Thread

HELP = "/help menu\n/send standalone\nanything else is echoed."
MENU = (Button(label="ok", data="ok"),)


def register(cx: Caspian) -> None:
    @cx.on_message({"channel": "discord", "command": ["help", "start"]})
    def on_help(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.post(HELP, actions=MENU)

    @cx.on_message({"channel": "discord", "command": "send"})
    def on_send(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.send("standalone")

    @cx.on_message({"channel": "discord", "command": "typing", "overlap": "drop"})
    def on_typing(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.typing()
        thread.post("done thinking.")

    @cx.on_message({"channel": "discord", "command": "pin", "kind": "channel"})
    def on_pin(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.pin(msg.message_id)

    @cx.on_action({"channel": "discord", "data": "ok"})
    def on_ok(thread: Thread, action: Action, ctx: HandlerContext) -> None:
        thread.post("ok")

    @cx.on_message({"channel": "discord"})
    def on_echo(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        if msg.text.strip():
            thread.post(msg.text)
