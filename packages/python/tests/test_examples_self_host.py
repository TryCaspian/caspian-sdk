import sys
from pathlib import Path

from caspian import Caspian
from caspian.core.types import Message, ThreadId

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def test_discord_help_posts_menu() -> None:
    from examples.discord.app import register

    cx = Caspian(dispatch=False)
    register(cx)
    event = Message(
        thread_id=ThreadId("discord:1"),
        text="/help",
        chat_kind="channel",
    )
    result = cx.interpret().run(cx.app, event, channel_name="discord")
    assert any(getattr(c, "tag", "") == "Host" for c in result.commands)
