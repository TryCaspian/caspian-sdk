"""Tests for the B facade — verifying desugar to A rules."""

from __future__ import annotations

from caspian.core.types import Message, ThreadId
from caspian.facade.caspian import Caspian


class TestFacadeDesugar:
    """on_message / on_action must produce Rules with correct predicates and overlap."""

    def test_on_message_creates_rule(self) -> None:
        cx = Caspian()

        def handler(thread, msg, ctx):
            thread.post("hi")

        cx.on_message({"channel": "telegram", "overlap": "queue"}, handler)

        app = cx.app
        assert len(app.rules) == 1
        rule = app.rules[0]
        assert rule.overlap.policy.value == "queue"
        assert rule.handler_id != ""

    def test_on_action_creates_rule_with_drop(self) -> None:
        cx = Caspian()

        def handler(thread, act, ctx):
            pass

        cx.on_action({"overlap": "drop"}, handler)

        app = cx.app
        assert len(app.rules) == 1
        rule = app.rules[0]
        assert rule.overlap.policy.value == "drop"

    def test_multiple_handlers_produce_multiple_rules(self) -> None:
        cx = Caspian()
        cx.on_message({"channel": "telegram"}, lambda t, m, c: None)
        cx.on_message({"channel": "discord"}, lambda t, m, c: None)
        cx.on_action({}, lambda t, a, c: None)

        assert len(cx.app.rules) == 3

    def test_memory_interpreter_runs_step(self) -> None:
        cx = Caspian()
        cx.on_message({"channel": "telegram"}, lambda t, m, c: t.post("reply"))

        interp = cx.interpret()
        event = Message(
            thread_id=ThreadId("telegram:123"),
            text="hello",
            chat_kind="dm",
        )
        result = interp.run(cx.app, event, channel_name="telegram")

        assert len(result.commands) == 2
        assert result.commands[0].tag == "Typing"  # type: ignore[union-attr]
        assert result.commands[1].tag == "Host"  # type: ignore[union-attr]

    def test_decorator_syntax(self) -> None:
        cx = Caspian()

        @cx.on_message({"channel": ["telegram", "discord"]})
        def handle(thread, msg, ctx):
            thread.post("hello")

        assert len(cx.app.rules) == 1


class TestThread:
    """Thread.post/typing/edit enqueue Commands, not HTTP calls."""

    def test_post_enqueues_command(self) -> None:
        from caspian.facade.thread import Thread

        t = Thread(thread_id=ThreadId("tg:1"))
        t.post("hello")
        assert len(t.commands) == 1
        assert t.commands[0].tag == "Post"  # type: ignore[union-attr]
        assert t.commands[0].text == "hello"  # type: ignore[union-attr]

    def test_typing_enqueues_command(self) -> None:
        from caspian.facade.thread import Thread

        t = Thread(thread_id=ThreadId("tg:1"))
        t.typing()
        assert len(t.commands) == 1
        assert t.commands[0].tag == "Typing"  # type: ignore[union-attr]

    def test_multiple_commands_accumulate(self) -> None:
        from caspian.facade.thread import Thread

        t = Thread(thread_id=ThreadId("tg:1"))
        t.typing()
        t.post("hi")
        t.react("msg1", "👍")
        assert len(t.commands) == 3
