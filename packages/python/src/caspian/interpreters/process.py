"""Process interpreter — the ONE inbound pipeline.

verify → parse → step → HostPort → execute → transport.

Webhook, poll, and hosted gateway all call handle_webhook. This module must
not import the B-surface package (import-linter + skill layering).
"""

from __future__ import annotations

from caspian.catalog import Capability
from caspian.core.commands import Command, Host, Typing
from caspian.core.errors import DecodeError
from caspian.core.overlap import OverlapDecision, drain_transition
from caspian.core.ports import (
    AdapterPort,
    Connection,
    HostPort,
    RawInbound,
    Result,
    Sent,
    TransportPort,
)
from caspian.core.predicates import evaluate
from caspian.core.step import StepResult, StepState, step
from caspian.core.types import App, Event, Rule


class ProcessInterpreter:
    """Runner for one connection. Overlap state and the latest queued event live here."""

    def __init__(
        self,
        app: App,
        adapter: AdapterPort,
        connection: Connection,
        *,
        host: HostPort | None = None,
        transport: TransportPort | None = None,
    ) -> None:
        self._app = app
        self._adapter = adapter
        self._connection = connection
        self._host = host
        self._transport = transport
        self._state = StepState()
        self._pending: dict[str, Event] = {}

    def handle_webhook(self, raw: RawInbound, *, trusted: bool = False) -> list[Result]:
        """Process one raw inbound payload end-to-end. Returns per-command results.

        `trusted=True` skips signature checks: poll and socket inbound are
        already authenticated by the bot token / gateway session.
        """
        if not trusted and not self._adapter.verify(raw, self._connection):
            return [Result.err(DecodeError(reason="Webhook signature verification failed"))]

        parse_result = self._adapter.parse(raw)
        if not parse_result.is_ok:
            return [parse_result]

        events: list[Event] = parse_result.value
        results: list[Result] = []
        admitted: list[tuple[Event, StepResult, str]] = []

        for event in events:
            ack = getattr(self._adapter, "acknowledge", None)
            if callable(ack):
                ack_result = ack(event, self._connection)
                if ack_result is not None:
                    results.append(self._maybe_dispatch(ack_result))

            key = self._key(event)
            sr = step(
                self._state,
                event,
                self._app,
                channel_name=self._channel_of(event),
                overlap_key=key,
            )
            if sr.dropped:
                continue
            if not sr.commands:
                self._pending[key] = event
                continue
            admitted.append((event, sr, key))

        for event, sr, key in admitted:
            try:
                results.extend(self._execute(event, sr))
            finally:
                results.extend(self._drain(key, sr.matched_rule))

        return results

    def _key(self, event: Event) -> str:
        return self._adapter.overlap_key(event)

    def _channel_of(self, event: Event) -> str:
        channel_of = getattr(self._adapter, "channel_of", None)
        if callable(channel_of):
            return str(channel_of(event))
        return self._adapter.name

    def _match(self, event: Event) -> Rule | None:
        channel_name = self._channel_of(event)
        for rule in self._app.rules:
            if evaluate(rule.predicate, event, channel_name=channel_name):
                return rule
        return None

    def _execute(self, event: Event, sr: StepResult) -> list[Result]:
        commands: list[Command] = []
        results_so_far: list[Result] = []
        handler_ran = False
        for cmd in sr.commands:
            if not isinstance(cmd, Host) and not handler_ran:
                # Anything the kernel emits before the handler (typing, an ack)
                # exists to be seen WHILE the agent thinks. Batching it with the
                # reply makes it pointless, so send it now.
                results_so_far.append(
                    self._maybe_dispatch(self._adapter.execute(cmd, self._connection))
                )
                continue
            if isinstance(cmd, Host):
                handler_ran = True
                if self._host is not None:
                    sink = _Sink(self)
                    commands.extend(
                        self._host.run(
                            cmd.handler_id,
                            event,
                            skipped_count=sr.skipped_count,
                            sink=sink,
                        )
                    )
                    results_so_far.extend(sink.results)
            else:
                commands.append(cmd)

        results: list[Result] = results_so_far
        for cmd in commands:
            results.append(self._maybe_dispatch(self._adapter.execute(cmd, self._connection)))
        return results

    def _drain(self, key: str, rule: Rule | None) -> list[Result]:
        if rule is None:
            return []
        current = self._state.get_overlap(key)
        drained = drain_transition(current, rule.overlap.policy)
        self._state.set_overlap(key, drained.new_state)
        if drained.decision != OverlapDecision.EXECUTE:
            return []
        pending = self._pending.pop(key, None)
        if pending is None:
            return []
        matched = self._match(pending)
        if matched is None:
            return []
        thread_id = pending.thread_id
        replay = StepResult(
            commands=[Typing(thread_id=thread_id), Host(handler_id=matched.handler_id)],
            matched_rule=matched,
            skipped_count=drained.skipped_count,
        )
        return self._execute(pending, replay) + self._drain(key, matched)

    def _maybe_dispatch(self, exec_result: Result) -> Result:
        if not exec_result.is_ok or self._transport is None:
            return exec_result
        sent = exec_result.value
        if isinstance(sent, Sent) and sent.raw.get("transport"):
            dispatched = self._transport.dispatch(sent)
            if dispatched.is_ok:
                read = getattr(self._adapter, "posted_id", None)
                if callable(read):
                    mid = read(dispatched.value)
                    if mid:
                        dispatched.value.message_id = mid
            return dispatched
        return exec_result


class _Sink:
    """StreamSink over a live ProcessInterpreter: send one Command right now.

    This is the only path that performs an effect while a handler is still
    running. It exists so a long reply can be streamed (post, then edit) rather
    than appearing all at once when the handler returns.
    """

    def __init__(self, runner: ProcessInterpreter) -> None:
        self._runner = runner
        self.results: list[Result] = []
        caps = set()
        cap_fn = getattr(runner._adapter, "capabilities", None)
        if callable(cap_fn):
            try:
                caps = set(cap_fn())
            except Exception:  # a capability probe must never break a handler
                caps = set()
        self.can_stream = runner._transport is not None and Capability.EDIT.value in caps

    def emit(self, command: Command) -> str:
        result = self._runner._maybe_dispatch(
            self._runner._adapter.execute(command, self._runner._connection)
        )
        self.results.append(result)
        if not result.is_ok:
            return ""
        sent = result.value
        return getattr(sent, "message_id", "") or ""
