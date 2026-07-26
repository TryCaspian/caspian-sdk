import sys
import threading
from contextlib import contextmanager

import httpx
import pytest

from caspian_sdk import (
    CommClient,
    InMemoryStateAdapter,
    RedisStateAdapter,
    StateLockTimeoutError,
)
from caspian_sdk import state as state_module


class FakeRedis:
    def __init__(self):
        self.now = 0.0
        self.values: dict[str, tuple[str, float | None]] = {}

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def set(self, key, value, *, nx=False, ex=None, px=None):
        self._purge()
        if nx and key in self.values:
            return None
        ttl = ex if ex is not None else px / 1000 if px is not None else None
        self.values[key] = (value, self.now + ttl if ttl is not None else None)
        return True

    def eval(self, _script, _numkeys, key, token):
        self._purge()
        current = self.values.get(key)
        if current and current[0] == token:
            del self.values[key]
            return 1
        return 0

    def _purge(self):
        for key, (_, expires_at) in list(self.values.items()):
            if expires_at is not None and expires_at <= self.now:
                del self.values[key]


class RecordingState:
    def __init__(self):
        self.event_ids = set()
        self.locked = []
        self.released = []

    def seen(self, event_id):
        duplicate = event_id in self.event_ids
        self.event_ids.add(event_id)
        return duplicate

    @contextmanager
    def lock(self, conversation_id):
        self.locked.append(conversation_id)
        try:
            yield
        finally:
            self.released.append(conversation_id)


def test_client_deduplicates_and_locks_all_event_types():
    state = RecordingState()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    client = CommClient(
        api_key="test",
        http=httpx.Client(base_url="https://example.test", transport=transport),
        state=state,
    )
    handled = []
    client.on_message(lambda message: handled.append(("message", message.conversation_id)))
    client.on_interaction(
        lambda interaction: handled.append(("interaction", interaction.conversation_id))
    )
    client.on_reaction(lambda reaction: handled.append(("reaction", reaction.action)))
    events = [
        {
            "seq": 1,
            "type": "message.received",
            "data": {
                "message": {
                    "id": "message",
                    "conversation_id": "message-conversation",
                    "connection_id": "connection",
                }
            },
        },
        {
            "seq": 2,
            "type": "interaction.received",
            "data": {"conversation_id": "interaction-conversation"},
        },
        {
            "seq": 3,
            "type": "reaction.received",
            "data": {
                "action": "added",
                "source_message": {"conversation_id": "reaction-conversation"},
            },
        },
    ]

    try:
        assert all(client._dispatch_event(event) for event in events)
        assert client._dispatch_event(events[0]) is False
    finally:
        client.close()

    assert handled == [
        ("message", "message-conversation"),
        ("interaction", "interaction-conversation"),
        ("reaction", "added"),
    ]
    assert state.locked == [
        "message-conversation",
        "interaction-conversation",
        "reaction-conversation",
    ]
    assert state.released == state.locked


def test_in_memory_dedup_expires(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(state_module.time, "monotonic", lambda: now[0])
    state = InMemoryStateAdapter(dedup_ttl_seconds=10)

    assert state.seen("event") is False
    assert state.seen("event") is True
    now[0] = 11
    assert state.seen("event") is False


def test_in_memory_locks_are_fifo_and_cleaned():
    state = InMemoryStateAdapter()
    first_started = threading.Event()
    release_first = threading.Event()
    order = []

    def worker(name):
        with state.lock("conversation"):
            order.append(name)
            if name == "first":
                first_started.set()
                release_first.wait(timeout=1)

    first = threading.Thread(target=worker, args=("first",))
    second = threading.Thread(target=worker, args=("second",))
    first.start()
    assert first_started.wait(timeout=1)
    second.start()
    release_first.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert order == ["first", "second"]
    assert state._locks == {}


def test_redis_dedup_and_expiry():
    redis = FakeRedis()
    state = RedisStateAdapter(redis, dedup_ttl_seconds=10)

    assert state.seen("event") is False
    assert state.seen("event") is True
    redis.advance(11)
    assert state.seen("event") is False


def test_redis_from_url_reports_optional_dependency(monkeypatch):
    monkeypatch.setitem(sys.modules, "redis", None)
    with pytest.raises(RuntimeError, match="optional 'redis' dependency"):
        RedisStateAdapter.from_url("redis://localhost:6379")


def test_redis_release_only_deletes_owned_lock():
    redis = FakeRedis()
    first = RedisStateAdapter(redis, lock_ttl_seconds=1, lock_wait_timeout_seconds=0)
    second = RedisStateAdapter(redis, lock_ttl_seconds=1, lock_wait_timeout_seconds=0)
    first_lock = first.lock("conversation")
    first_lock.__enter__()
    redis.advance(2)
    second_lock = second.lock("conversation")
    second_lock.__enter__()
    first_lock.release()

    assert redis.values["caspian:lock:conversation"][0] != ""
    second_lock.release()
    assert "caspian:lock:conversation" not in redis.values


def test_redis_lock_timeout():
    redis = FakeRedis()
    state = RedisStateAdapter(redis, lock_wait_timeout_seconds=0)
    held = state.lock("conversation")
    held.__enter__()
    try:
        with pytest.raises(StateLockTimeoutError):
            state.lock("conversation").__enter__()
    finally:
        held.release()
