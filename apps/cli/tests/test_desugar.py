import pytest
from caspian_cli.desugar import parse_argv
from caspian_cli.intent import Call, ChannelsAdd


def test_channels_add_telegram_omitting_via_is_hosted():
    intent = parse_argv(["channels", "add", "telegram"])
    assert intent == ChannelsAdd(channel="telegram", via="hosted")


def test_channels_add_self_host():
    intent = parse_argv([
        "channels", "add", "telegram",
        "--via", "self-host",
        "--bot-token", "123:abc",
        "--webhook-url", "https://example.com/hook",
    ])
    assert intent == ChannelsAdd(
        channel="telegram",
        via="self-host",
        bot_token="123:abc",
        webhook_url="https://example.com/hook",
    )


def test_call_post_is_the_send_path():
    intent = parse_argv([
        "call", "post",
        "--thread", "telegram:123:456",
        "--text", "shipping now",
    ])
    assert intent == Call(
        id="post",
        args={"thread_id": "telegram:123:456", "text": "shipping now"},
    )


def test_call_native_id_is_still_call():
    intent = parse_argv([
        "call", "telegram.send-photo",
        "--thread", "telegram:123:456",
        "--file", "./graph.png",
    ])
    assert intent == Call(
        id="telegram.send-photo",
        args={"thread_id": "telegram:123:456", "file": "./graph.png"},
    )


def test_connect_is_error():
    with pytest.raises(SystemExit):
        parse_argv(["connect", "telegram"])


def test_channel_verb_is_error_use_call():
    with pytest.raises(SystemExit, match="caspian call"):
        parse_argv(["telegram", "send-photo", "--thread", "telegram:1", "--file", "x.png"])


def test_threads_reply_is_error_use_call_post():
    with pytest.raises(SystemExit, match="caspian call post"):
        parse_argv(["threads", "reply", "telegram:123:456", "--text", "on my way"])


def test_channels_watch_is_error_use_threads_tail():
    with pytest.raises(SystemExit, match="caspian threads tail"):
        parse_argv(["channels", "watch"])
