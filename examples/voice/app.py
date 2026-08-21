from caspian import Caspian, HandlerContext, Message, Thread


def register(cx: Caspian) -> None:
    @cx.on_message({"channel": "voice"})
    def on_echo(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        if msg.text.strip():
            thread.post(msg.text)
