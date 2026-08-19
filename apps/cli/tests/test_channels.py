from caspian_cli.desugar import parse_argv
from caspian_cli.run import run_intent


class RecordingGateway:
    def __init__(self):
        self.calls = []
        self.responses = [{"id": "conn_1", "channel": "telegram", "status": "active"}]

    def request(self, method, path, json=None):
        self.calls.append((method, path, json))
        return self.responses.pop(0)


def test_hosted_channels_add_posts_connection():
    gw = RecordingGateway()
    out = run_intent(parse_argv(["channels", "add", "telegram"]), gateway=gw)
    assert gw.calls == [("POST", "/v1/connections/telegram", {"wait": True})]
    assert out["id"] == "conn_1"


def test_self_host_does_not_call_gateway():
    gw = RecordingGateway()
    out = run_intent(
        parse_argv([
            "channels", "add", "telegram",
            "--via", "self-host",
            "--bot-token", "123:abc",
            "--webhook-url", "https://example.com/hook",
        ]),
        gateway=gw,
    )
    assert gw.calls == []
    assert out["via"] == "self-host"
    assert out["channel"] == "telegram"


def test_channels_ls_gets_connections():
    gw = RecordingGateway()
    gw.responses = [[{"id": "conn_1", "channel": "telegram"}]]
    out = run_intent(parse_argv(["channels", "ls"]), gateway=gw)
    assert gw.calls == [("GET", "/v1/connections", None)]
    assert out[0]["id"] == "conn_1"
