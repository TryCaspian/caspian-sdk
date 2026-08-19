from caspian_cli.desugar import parse_argv
from caspian_cli.run import run_intent


class RecordingGateway:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def request(self, method, path, json=None):
        self.calls.append((method, path, json))
        if self.responses:
            return self.responses.pop(0)
        return []


def test_threads_ls_lists_conversations():
    gw = RecordingGateway(
        [[{"id": "telegram:123:456", "channel": "telegram"}]]
    )
    out = run_intent(parse_argv(["threads", "ls", "--channel", "telegram"]), gateway=gw)
    assert gw.calls == [("GET", "/v1/conversations", None)]
    assert out[0]["id"] == "telegram:123:456"


def test_threads_tail_gets_events():
    gw = RecordingGateway([[{"seq": 1, "type": "message.received"}]])
    out = run_intent(parse_argv(["threads", "tail", "telegram:123:456"]), gateway=gw)
    assert gw.calls == [("GET", "/v1/events", None)]
    assert out[0]["seq"] == 1
