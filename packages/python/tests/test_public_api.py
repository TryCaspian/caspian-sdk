"""Public SDK surface — what a developer or agent imports from `caspian`."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

from caspian.facade.caspian import Caspian
from caspian.facade.channels import ChannelManager


def test_barrel_exports_handler_types() -> None:
    import caspian as pkg

    for name in (
        "Caspian",
        "Thread",
        "Stream",
        "Message",
        "Action",
        "Button",
        "Attachment",
        "HandlerContext",
        "MessageHandler",
        "ActionHandler",
        "OnMessageOptions",
        "OnActionOptions",
        "Result",
        "Sent",
        "Connection",
        "Via",
        "ChannelName",
        "CaspianError",
        "ProvisionError",
        "ToolSet",
    ):
        assert hasattr(pkg, name), f"caspian.{name} missing from the public barrel"
        assert name in pkg.__all__


def test_package_declares_itself_typed() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "caspian" / "py.typed"
    assert root.is_file()
    spec = importlib.util.find_spec("caspian")
    assert spec is not None and spec.origin is not None
    assert (Path(spec.origin).parent / "py.typed").is_file()


def test_add_signature_names_the_secrets_agents_guess_wrong() -> None:
    params = inspect.signature(ChannelManager.add).parameters
    for name in (
        "channel",
        "via",
        "bot_token",
        "webhook_url",
        "webhook_secret",
        "signing_secret",
        "app_secret",
    ):
        assert name in params, f"channels.add is missing {name}= — IntelliSense cannot offer it"


def test_add_forwards_webhook_secret_into_connection_config() -> None:
    cx = Caspian(dispatch=False)
    conn = cx.channels.add(
        "telegram",
        via="self-host",
        bot_token="123:ABC",
        webhook_secret="s3cr3t",
    )
    assert conn.config["webhook_secret"] == "s3cr3t"
    assert conn.config["bot_token"] == "123:ABC"


def test_add_channel_is_a_catalog_literal() -> None:
    """`channel: str` collapses in the editor; ChannelName is the completion list."""
    from typing import Literal, get_args, get_origin, get_type_hints

    from caspian.catalog import ChannelName

    hints = get_type_hints(ChannelManager.add)
    assert hints["channel"] is ChannelName
    assert get_origin(hints["via"]) is Literal
    assert set(get_args(hints["via"])) == {"hosted", "self-host"}


def test_on_message_channel_is_a_catalog_literal() -> None:
    from typing import get_args, get_type_hints

    from caspian import OnMessageOptions
    from caspian.catalog import ChannelName

    keys = OnMessageOptions.__optional_keys__ | OnMessageOptions.__required_keys__
    assert keys == {"channel", "kind", "overlap", "bound", "ack", "command"}
    hints = get_type_hints(OnMessageOptions)
    assert ChannelName in get_args(hints["channel"])


def test_on_action_options_include_data() -> None:
    from caspian import OnActionOptions

    keys = OnActionOptions.__optional_keys__ | OnActionOptions.__required_keys__
    assert keys == {"channel", "overlap", "bound", "ack", "data"}


def test_handler_protocol_allows_msg_not_message() -> None:
    """Protocol params are positional-only; `msg` vs `message` must not error."""
    from caspian.facade.host import MessageHandler

    kinds = [
        p.kind
        for name, p in inspect.signature(MessageHandler.__call__).parameters.items()
        if name != "self"
    ]
    assert kinds
    assert all(k == inspect.Parameter.POSITIONAL_ONLY for k in kinds)


def test_tools_returns_toolset() -> None:
    from caspian import ToolSet

    cx = Caspian(dispatch=False)
    tools = cx.tools()
    assert isinstance(tools, ToolSet)
    assert tools.definitions
