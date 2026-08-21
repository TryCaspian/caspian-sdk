"""Failing-first tests for the A/B skill gates that the rewrite still missed.

These encode the skills, not the old CommClient: overlap drain, Host as a port,
tools derived from Commands, inbound ownership, one inbound pipeline.
"""

from __future__ import annotations

import json

from caspian.core.commands import Command, Initiate, Post
from caspian.core.overlap import OverlapDecision, OverlapState, SlotStatus, overlap_transition
from caspian.core.ports import Connection, RawInbound, Result
from caspian.core.predicates import And, MatchChannel, MatchKind
from caspian.core.step import StepState, step
from caspian.core.types import (
    Action,
    App,
    Event,
    Message,
    Overlap,
    OverlapPolicy,
    Rule,
    ThreadId,
)
from caspian.facade.caspian import Caspian
from caspian.facade.host import FacadeHost
from caspian.hosted.client import FakeGatewayClient
from caspian.interpreters.transport import ChaosTransport, RecordingTransport
from caspian.provision import Via
from caspian.tools import ToolSet


def _msg(text: str, thread: str = "telegram:1") -> Message:
    return Message(thread_id=ThreadId(thread), text=text, chat_kind="dm")


def _app(*rules: Rule) -> App:
    return App(rules=rules)


class TestOverlapLaws:
    def test_stream_always_executes(self) -> None:
        busy = OverlapState(status=SlotStatus.BUSY, queued=4)
        result = overlap_transition(busy, OverlapPolicy.STREAM, bound=16)
        assert result.decision == OverlapDecision.EXECUTE

    def test_drop_while_busy_never_enqueues(self) -> None:
        """drop is absorbing: a busy drop slot cannot enqueue, at any queue depth."""
        busy = OverlapState(status=SlotStatus.BUSY, queued=5)
        dropped = overlap_transition(busy, OverlapPolicy.DROP, bound=16)
        assert dropped.decision == OverlapDecision.DROP
        assert dropped.new_state.queued == 5

    def test_action_does_not_share_text_queue(self) -> None:
        rule_msg = Rule(
            predicate=MatchKind(kind="message"),
            overlap=Overlap(policy=OverlapPolicy.QUEUE, bound=16),
            handler_id="h_msg",
        )
        rule_act = Rule(
            predicate=MatchKind(kind="action"),
            overlap=Overlap(policy=OverlapPolicy.QUEUE, bound=16),
            handler_id="h_act",
        )
        app = _app(rule_msg, rule_act)
        state = StepState()
        first = step(state, _msg("hi"), app, overlap_key="tg:1")
        assert first.commands  # text occupies the slot
        action = Action(thread_id=ThreadId("telegram:1"), data="ok")
        second = step(state, action, app, overlap_key="tg:1")
        assert second.commands, "buttons must not wait behind the text queue"
        assert second.matched_rule is not None
        assert second.matched_rule.handler_id == "h_act"


class TestQueueDrainsLatest:
    def test_batch_drains_latest_and_reports_skipped(self) -> None:
        seen: list[tuple[str, int]] = []

        class BatchAdapter:
            name = "telegram"

            def parse(self, raw: RawInbound) -> Result:
                payload = json.loads(raw.body)
                events: list[Event] = [
                    _msg(str(item["text"])) for item in payload["events"]
                ]
                return Result.ok(events)

            def execute(self, cmd: object, conn: Connection) -> Result:
                from caspian.core.ports import Sent

                return Result.ok(Sent(raw={"transport": "noop", "native": getattr(cmd, "tag", "")}))

            def verify(self, raw: RawInbound, conn: Connection) -> bool:
                return True

            def overlap_key(self, event: Event) -> str:
                return str(event.thread_id)

            def capabilities(self) -> frozenset[str]:
                return frozenset({"receive", "send"})

            def format(self, text: str) -> str:
                return text

            def encode_thread(self, *parts: str) -> ThreadId:
                return ThreadId(":".join(parts))

            def decode_thread(self, thread_id: ThreadId) -> str:
                return str(thread_id)

        from caspian.core.types import ConnectionId
        from caspian.interpreters.process import ProcessInterpreter

        def handler(thread: object, msg: Message, ctx: object) -> None:
            seen.append((msg.text, getattr(ctx, "skipped", 0)))
            thread.post(msg.text)  # type: ignore[attr-defined]

        host = FacadeHost({"h": handler})
        pred = And(left=MatchKind(kind="message"), right=MatchChannel(channels=("telegram",)))
        overlap = Overlap(policy=OverlapPolicy.QUEUE, bound=16)
        app = _app(Rule(predicate=pred, overlap=overlap, handler_id="h"))
        interp = ProcessInterpreter(
            app,
            BatchAdapter(),  # type: ignore[arg-type]
            Connection(id=ConnectionId("c1"), channel="telegram", config={}),
            host=host,
            transport=RecordingTransport(),
        )
        body = json.dumps(
            {"events": [{"text": "one"}, {"text": "two"}, {"text": "three"}]}
        ).encode()
        interp.handle_webhook(RawInbound(body=body))

        texts = [t for t, _ in seen]
        assert texts[0] == "one"
        assert texts[-1] == "three"
        assert "two" not in texts
        assert seen[-1][1] >= 1


class TestToolsDerivedFromCommands:
    def test_post_tool_schema_comes_from_post_model(self) -> None:
        props = Post.model_json_schema()["properties"]
        tool = next(d for d in ToolSet(preset="messenger").definitions if d.name == "post_message")
        assert "text" in tool.parameters["properties"]
        assert "text" in props

    def test_outbound_send_dm_is_initiate(self) -> None:
        names = [d.name for d in ToolSet(preset="outbound").definitions]
        assert "send_dm" in names
        commands = ToolSet().execute("send_dm", {"text": "hi", "thread_id": "tg:1"})
        assert commands[0].tag == "Initiate"  # type: ignore[union-attr]
        assert isinstance(commands[0], Initiate)

    def test_every_default_tool_maps_to_a_command_tag(self) -> None:
        tags = {getattr(t, "__name__", "") for t in Command.__args__}  # type: ignore[attr-defined]
        # Command is an Annotated union — unwrap if needed
        origin = getattr(Command, "__origin__", None) or Command
        args = getattr(origin, "__args__", ())
        tags = {a.__name__ for a in args if hasattr(a, "__name__")}
        mapped = {"Post", "Edit", "React", "Typing", "Initiate"}
        assert mapped <= tags


class TestProvisionSkillGates:
    def test_telegram_hosted_requires_bot_token(self) -> None:
        from caspian.provision import Channels

        channels = Channels()
        got = channels.add("telegram")
        assert isinstance(got, str)
        assert "bot_token" in got

    def test_email_hosted_needs_no_token(self) -> None:
        from caspian.provision import Channels

        conn = Channels().add("email")
        assert isinstance(conn, Connection)
        assert conn.via == Via.HOSTED
        assert conn.inbound_owner == "gateway"

    def test_self_host_inbound_owner_is_local(self) -> None:
        from caspian.provision import Channels

        conn = Channels().add("telegram", via="self-host", bot_token="123:ABC")
        assert isinstance(conn, Connection)
        assert conn.inbound_owner == "local"


class TestInboundOwnerAndPipeline:
    def test_handle_hosted_channel_is_rejected(self) -> None:
        cx = Caspian(dispatch=False, gateway_client=FakeGatewayClient())
        cx.channels.add("telegram", bot_token="123:ABC")
        results = cx.handle("telegram", b"{}")
        assert len(results) == 1 and not results[0].is_ok
        assert results[0].error is not None
        assert results[0].error.tag == "ProvisionError"

    def test_handle_gateway_is_the_same_pipeline(self) -> None:
        client = FakeGatewayClient()
        transport = RecordingTransport()
        cx = Caspian(gateway_client=client, transport=transport)
        cx.channels.add("telegram", bot_token="123:ABC")
        seen: list[str] = []

        @cx.on_message({"channel": "telegram"})
        def reply(thread, msg, ctx):  # noqa: ANN001
            seen.append(msg.text)
            thread.post(f"echo:{msg.text}")

        body = json.dumps(
            {
                "type": "message",
                "channel": "telegram",
                "conversation_id": "42",
                "message": {"text": "ping", "chat_kind": "dm"},
            }
        ).encode()
        results = cx.handle("gateway", body)
        assert seen == ["ping"]
        assert all(r.is_ok for r in results)

    def test_poll_uses_handle_webhook(self) -> None:
        transport = RecordingTransport()
        cx = Caspian(transport=transport)
        cx.channels.add("telegram", via="self-host", bot_token="123:ABC")
        seen: list[str] = []

        @cx.on_message({"channel": "telegram"})
        def reply(thread, msg, ctx):  # noqa: ANN001
            seen.append(msg.text)
            thread.post("ok")

        class _PollTransport:
            def dispatch(self, sent):  # noqa: ANN001
                body = {
                    "ok": True,
                    "result": [
                        {
                            "update_id": 7,
                            "message": {
                                "message_id": 1,
                                "from": {"id": 9},
                                "chat": {"id": 555, "type": "private"},
                                "text": "polled",
                            },
                        }
                    ],
                }
                from caspian.core.ports import Sent

                return Result.ok(Sent(raw={"body": json.dumps(body).encode()}))

        results = cx.poll("telegram", transport=_PollTransport(), max_iterations=1)
        assert seen == ["polled"]
        assert any(r.is_ok for r in results)

    def test_run_polls_gateway_through_handle(self) -> None:
        client = FakeGatewayClient()
        client.queue_ok({})  # GET /v1/connections: none exist yet
        client.queue_ok({"id": "c1", "channel": "telegram", "status": "active"})
        # run() seeks past existing history first, so a fresh bot does not
        # re-answer old messages; that costs one extra request at startup.
        client.queue_ok({})
        client.queue_ok(
            {
                "cursor": "c2",
                "events": [
                    {
                        "type": "message",
                        "channel": "telegram",
                        "conversation_id": "9",
                        "message": {"text": "from-gw", "chat_kind": "dm"},
                    }
                ],
            }
        )
        transport = RecordingTransport()
        cx = Caspian(gateway_client=client, transport=transport)
        cx.channels.add("telegram", bot_token="123:ABC")
        seen: list[str] = []

        @cx.on_message({"channel": "telegram"})
        def reply(thread, msg, ctx):  # noqa: ANN001
            seen.append(msg.text)
            thread.post("ok")

        results = cx.run(max_iterations=1)
        assert seen == ["from-gw"]
        assert any(r.is_ok for r in results)


class TestReliabilityGates:
    def test_every_core_basemodel_is_frozen(self) -> None:
        from pydantic import BaseModel

        import caspian.core.commands as commands
        import caspian.core.overlap as overlap
        import caspian.core.predicates as predicates
        import caspian.core.types as types

        for mod in (types, commands, overlap, predicates):
            for name in dir(mod):
                obj = getattr(mod, name)
                if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
                    frozen = obj.model_config.get("frozen")
                    assert frozen is True, f"{mod.__name__}.{name} is not frozen"

    def test_every_caspian_error_is_an_exception(self) -> None:
        import caspian.core.errors as errors
        from caspian.core.errors import CaspianError

        for name in dir(errors):
            obj = getattr(errors, name)
            if isinstance(obj, type) and issubclass(obj, CaspianError):
                assert issubclass(obj, Exception), f"{name} is not an Exception"

    def test_one_transport_port(self) -> None:
        import caspian.facade.caspian as facade
        import caspian.interpreters.polling as polling
        import caspian.interpreters.process as process
        from caspian.core.ports import TransportPort

        assert hasattr(TransportPort, "dispatch")
        assert not hasattr(TransportPort, "send")
        assert not hasattr(process, "Transport")
        assert not hasattr(polling, "_Transport")
        assert not hasattr(facade, "Transport")

    def test_chaos_transport_returns_error_value(self) -> None:
        from caspian.core.ports import Sent

        result = ChaosTransport().dispatch(Sent(raw={"transport": "http_json"}))
        assert not result.is_ok
        assert result.error is not None
        assert result.error.tag == "AdapterError"

    def test_process_interpreter_does_not_import_facade(self) -> None:
        import caspian.interpreters.process as process

        assert "caspian.facade" not in process.__dict__.get("__file__", "")
        with open(process.__file__) as fh:
            source = fh.read()
        assert "caspian.facade" not in source

    def test_listen_does_not_name_discord_or_slack_runners(self) -> None:
        """Inbound mode is a catalog dim. Facade starts SocketSession, not a vendor class."""
        import caspian.facade.caspian as facade
        import caspian.interpreters as interpreters

        with open(facade.__file__) as fh:
            source = fh.read()
        assert "discord_gateway" not in source
        assert "slack_socket" not in source
        assert "DiscordGatewayRunner" not in source
        assert "SlackSocketRunner" not in source
        assert not hasattr(interpreters, "DiscordGatewayRunner")
        assert not hasattr(interpreters, "SlackSocketRunner")


class TestHostedOnlyChannels:
    """A channel the gateway supports must be usable even with no local adapter.

    Regression: channels.add() used to require a REGISTRY entry for every
    channel, which made bluesky/zulip/gmeet/rcs unreachable in hosted mode even
    though the gateway speaks them and no local adapter is ever used there.
    """

    def test_hosted_channel_without_adapter_is_allowed(self) -> None:
        from caspian.facade.channels import ChannelManager

        cm = ChannelManager()
        for channel in ("bluesky", "zulip", "gmeet", "rcs", "instagram"):
            record = cm.add(channel)  # hosted is the default
            assert record.channel == channel
            assert record.inbound_owner == "gateway"
        assert cm.self_hostable("bluesky") is False
        assert cm.self_hostable("telegram") is True

    def test_self_host_without_adapter_fails_loudly(self) -> None:
        from caspian.core.errors import ProvisionError
        from caspian.facade.channels import ChannelManager

        cm = ChannelManager()
        try:
            cm.add("bluesky", via="self-host", bot_token="t")
        except ProvisionError as exc:
            assert exc.tag == "ProvisionError"
            assert "cannot be self-hosted" in exc.reason
        else:
            raise AssertionError("self-host without adapter must throw")

    def test_hosted_only_channel_points_at_the_gateway_pipeline(self) -> None:
        import pytest

        from caspian.facade.channels import ChannelManager

        cm = ChannelManager()
        cm.add("bluesky")
        with pytest.raises(KeyError) as exc:
            cm.adapter_for("bluesky")
        assert "hosted-only" in str(exc.value)
