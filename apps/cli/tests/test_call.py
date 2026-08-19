from caspian_cli.desugar import parse_argv
from caspian_cli.run import run_intent


class RecordingGateway:
    def __init__(self):
        self.calls = []

    def request(self, method, path, json=None):
        self.calls.append((method, path, json))
        return {"ok": True, "id": "msg_1"}


def test_call_post_uses_conversation_messages():
    gw = RecordingGateway()
    run_intent(
        parse_argv(["call", "post", "--thread", "telegram:123:456", "--text", "shipping now"]),
        gateway=gw,
    )
    assert gw.calls == [
        ("POST", "/v1/conversations/123:456/messages", {"text": "shipping now"}),
    ]


def test_call_post_on_slack_is_the_same_command():
    gw = RecordingGateway()
    run_intent(
        parse_argv(["call", "post", "--thread", "slack:C123:ts", "--text", "shipped"]),
        gateway=gw,
    )
    assert gw.calls[0][1] == "/v1/conversations/C123:ts/messages"
    body = gw.calls[0][2]
    assert "chat_id" not in body
    assert "thread_id" not in body
