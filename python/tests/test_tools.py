"""Tests for tools — derived from Command types."""

from __future__ import annotations

from caspian.core.types import ThreadId
from caspian.facade.thread import Thread
from caspian.tools import ToolSet


class TestToolSet:
    def test_definitions_messenger_preset(self) -> None:
        tools = ToolSet(preset="messenger")
        defs = tools.definitions
        names = [d.name for d in defs]
        assert "post_message" in names
        assert "edit_message" in names
        assert "add_reaction" in names
        assert "start_typing" in names
        assert "send_dm" not in names

    def test_definitions_outbound_preset(self) -> None:
        tools = ToolSet(preset="outbound")
        defs = tools.definitions
        names = [d.name for d in defs]
        assert "send_dm" in names

    def test_execute_post_message(self) -> None:
        thread = Thread(thread_id=ThreadId("tg:1"))
        tools = ToolSet(thread=thread)
        commands = tools.execute("post_message", {"text": "hello"})
        assert len(commands) == 1
        assert commands[0].tag == "Post"  # type: ignore[union-attr]
        assert commands[0].text == "hello"  # type: ignore[union-attr]

    def test_execute_uses_thread_id(self) -> None:
        tools = ToolSet()
        commands = tools.execute("post_message", {"text": "hi", "thread_id": "slack:C1"})
        assert len(commands) == 1
        assert commands[0].thread_id == "slack:C1"  # type: ignore[union-attr]


def test_facade_exposes_tools_like_typescript() -> None:
    """cx.tools() must exist in both languages, or docs cannot be written once."""
    from caspian import Caspian

    cx = Caspian()
    names = {t.name for t in cx.tools().definitions}
    assert "post_message" in names
    assert cx.tools(preset="outbound").definitions != cx.tools().definitions
