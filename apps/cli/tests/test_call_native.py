import pytest
from caspian_cli.desugar import parse_argv
from caspian_cli.run import run_intent


class RecordingGateway:
    def __init__(self):
        self.calls = []

    def request(self, method, path, json=None):
        self.calls.append((method, path, json))
        return {"ok": True}


def test_call_telegram_send_photo_fails_loudly_when_hosted_has_no_endpoint():
    gw = RecordingGateway()
    with pytest.raises(SystemExit, match="SendMedia is not available in hosted mode"):
        run_intent(
            parse_argv([
                "call", "telegram.send-photo",
                "--thread", "telegram:123:456",
                "--file", "./graph.png",
            ]),
            gateway=gw,
        )
    assert gw.calls == []
