"""Tools derived from Command types.

Which tools exist depends on whether a thread is bound, not only on the preset.
The rule is that a model is never handed a tool that cannot work, and never
asked for a value that would be ignored. These semantics match the TypeScript
SDK exactly; docs are written once for both.
"""

from __future__ import annotations

from caspian.core.types import ThreadId
from caspian.facade.thread import Thread
from caspian.tools import ToolSet


class TestBoundToAThread:
    """A conversation is in hand, so tools are scoped to it."""

    def _tools(self) -> ToolSet:
        return ToolSet(thread=Thread(thread_id=ThreadId("tg:1")), preset="messenger")

    def test_offers_the_conversation_tools(self) -> None:
        names = {d.name for d in self._tools().definitions}
        assert names == {
            "post_message",
            "edit_message",
            "add_reaction",
            "start_typing",
            "send_dm",
        }

    def test_thread_id_is_not_asked_for(self) -> None:
        """The thread is known, and execute() overrides whatever is sent.

        Leaving thread_id in the schema asks the model for a value that is then
        discarded: wasted tokens, and an invitation to invent ids.
        """
        edit = next(d for d in self._tools().definitions if d.name == "edit_message")
        assert "thread_id" not in edit.parameters["properties"]
        assert "thread_id" not in edit.parameters["required"]

    def test_send_dm_keeps_its_thread_id(self) -> None:
        """Sending a DM names a different conversation by definition."""
        dm = next(d for d in self._tools().definitions if d.name == "send_dm")
        assert "thread_id" in dm.parameters["properties"]


class TestNoThread:
    """No conversation in hand, so only tools that name their own target."""

    def test_only_offers_tools_that_can_work(self) -> None:
        names = {d.name for d in ToolSet().definitions}
        assert names == {"post_message", "send_dm"}

    def test_editing_is_withheld_rather_than_offered_broken(self) -> None:
        """edit/react/typing need a thread. Emitting them would build commands
        with an empty thread_id that fail later, at the adapter."""
        names = {d.name for d in ToolSet().definitions}
        assert "edit_message" not in names
        assert "add_reaction" not in names
        assert "start_typing" not in names

    def test_messenger_collapses_to_outbound_without_a_thread(self) -> None:
        assert (
            {d.name for d in ToolSet(preset="messenger").definitions}
            == {d.name for d in ToolSet(preset="outbound").definitions}
        )

    def test_thread_id_must_be_supplied(self) -> None:
        post = next(d for d in ToolSet().definitions if d.name == "post_message")
        assert "thread_id" in post.parameters["properties"]


class TestExecute:
    def test_bound_thread_is_used(self) -> None:
        tools = ToolSet(thread=Thread(thread_id=ThreadId("tg:1")))
        commands = tools.execute("post_message", {"text": "hello"})
        assert len(commands) == 1
        assert commands[0].tag == "Post"  # type: ignore[union-attr]
        assert commands[0].text == "hello"  # type: ignore[union-attr]
        assert commands[0].thread_id == "tg:1"  # type: ignore[union-attr]

    def test_bound_execute_enqueues_on_the_handler_thread(self) -> None:
        """Parity with TypeScript: execute() must land on the handler thread.

        Hosted turns collect Commands from the handler's Thread. A throwaway
        Thread that is only returned from execute() is never sent.
        """
        handler = Thread(thread_id=ThreadId("tg:1"))
        tools = ToolSet(thread=handler)
        returned = tools.execute("post_message", {"text": "hello"})
        assert handler.commands == returned
        assert handler.commands[0].text == "hello"  # type: ignore[union-attr]

    def test_send_dm_enqueues_on_the_handler_thread_with_the_named_id(self) -> None:
        handler = Thread(thread_id=ThreadId("email:1"))
        tools = ToolSet(thread=handler)
        tools.execute("send_dm", {"thread_id": "email:other", "text": "secret"})
        assert len(handler.commands) == 1
        assert handler.commands[0].tag == "Initiate"  # type: ignore[union-attr]
        assert handler.commands[0].thread_id == "email:other"  # type: ignore[union-attr]
        assert handler.commands[0].text == "secret"  # type: ignore[union-attr]

    def test_unbound_takes_the_thread_id_from_the_model(self) -> None:
        tools = ToolSet()
        commands = tools.execute("post_message", {"text": "hi", "thread_id": "slack:C1"})
        assert len(commands) == 1
        assert commands[0].thread_id == "slack:C1"  # type: ignore[union-attr]


def test_facade_exposes_tools_like_typescript() -> None:
    """cx.tools() must exist in both languages, or docs cannot be written once."""
    from caspian import Caspian

    cx = Caspian()
    assert {t.name for t in cx.tools().definitions} == {"post_message", "send_dm"}
    bound = cx.tools(Thread(thread_id=ThreadId("tg:1")))
    assert len(bound.definitions) == 5
    assert bound.bound is True
