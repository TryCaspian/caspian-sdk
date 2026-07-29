"""Tests for state and deduplication adapters (InMemory and Redis) and dispatch integration."""

import asyncio
import sys
import threading

import httpx
import pytest
from caspian_sdk import CommClient, InMemoryStateAdapter, RedisStateAdapter


def _client(handler, state=None) -> CommClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://gw.test")
    return CommClient(api_key="comm_test_key", base_url="http://gw.test", http=http, state=state)


def _message_event(seq: int, conversation_id: str, text: str, msg_id: str | None = None) -> dict:
    return {
        "seq": seq,
        "type": "message.received",
        "data": {
            "message": {
                "id": msg_id or f"msg_{seq}",
                "conversation_id": conversation_id,
                "connection_id": "conn_1",
                "text": text,
            }
        },
    }


# ---- InMemoryStateAdapter tests -------------------------------------------

def test_in_memory_seen_dedup():
    async def _test():
        adapter = InMemoryStateAdapter()
        assert await adapter.seen("evt_1") is True
        assert await adapter.seen("evt_1") is False
        assert await adapter.seen("evt_2") is True
    asyncio.run(_test())


def test_in_memory_eviction_cap():
    async def _test():
        adapter = InMemoryStateAdapter(max_size=2)
        assert await adapter.seen("evt_1") is True
        assert await adapter.seen("evt_2") is True
        # Maximum size reached (2); inserting evt_3 evicts oldest (evt_1)
        assert await adapter.seen("evt_3") is True
        assert await adapter.seen("evt_2") is False  # Still present in memory
        assert await adapter.seen("evt_1") is True   # Evicted, so seen() claims it as new
    asyncio.run(_test())


def test_in_memory_lock_concurrency():
    async def _test():
        adapter = InMemoryStateAdapter()
        
        async with adapter.lock("conv_1") as acquired1:
            assert acquired1 is True
            
            # Second lock attempt for same conversation while active should fail
            async with adapter.lock("conv_1") as acquired2:
                assert acquired2 is False

        # After first lock exits, new lock attempt for conv_1 should succeed
        async with adapter.lock("conv_1") as acquired3:
            assert acquired3 is True
    asyncio.run(_test())


# ---- RedisStateAdapter tests (using fakeredis) ----------------------------

def test_redis_seen_dedup():
    async def _test():
        import fakeredis.aioredis
        fake_redis = fakeredis.aioredis.FakeRedis()
        adapter = RedisStateAdapter(redis_client=fake_redis, seen_ttl=60)

        assert await adapter.seen("evt_100") is True
        assert await adapter.seen("evt_100") is False

        # Check key exists and has TTL
        assert await fake_redis.get("event:evt_100") == b"1"
        ttl = await fake_redis.ttl("event:evt_100")
        assert 0 < ttl <= 60
    asyncio.run(_test())


def test_redis_lock_concurrency_and_lua_release():
    async def _test():
        import fakeredis.aioredis
        fake_redis = fakeredis.aioredis.FakeRedis()
        adapter = RedisStateAdapter(redis_client=fake_redis, lock_ttl=10)

        async with adapter.lock("conv_99") as acquired1:
            assert acquired1 is True
            assert await fake_redis.exists("lock:conv_99") == 1

            # Attempt concurrent lock while locked
            async with adapter.lock("conv_99") as acquired2:
                assert acquired2 is False

        # After exiting context, Lua script releases the key
        assert await fake_redis.exists("lock:conv_99") == 0

        # Can acquire again after release
        async with adapter.lock("conv_99") as acquired3:
            assert acquired3 is True
    asyncio.run(_test())


def test_redis_missing_dependency_error():
    # If redis module is missing, constructing RedisStateAdapter raises ImportError
    original_modules = dict(sys.modules)
    try:
        sys.modules["redis"] = None
        sys.modules["redis.asyncio"] = None
        try:
            RedisStateAdapter()
        except ImportError as exc:
            assert "redis-py is required" in str(exc)
        else:
            pytest.fail("Expected ImportError when redis-py is missing")
    finally:
        sys.modules.update(original_modules)


# ---- Dispatch Level Tests --------------------------------------------------

def test_client_dispatch_deduplication():
    events_received = []

    def mock_handler(request):
        return httpx.Response(200, json={})

    client = _client(mock_handler)

    @client.on_message
    def handle(msg):
        events_received.append(msg.id)

    event = _message_event(seq=1, conversation_id="conv_1", text="Hello", msg_id="msg_dup")

    # Dispatch same event twice
    client._dispatch_event(event)
    client._dispatch_event(event)

    # Handler should run exactly once
    assert events_received == ["msg_dup"]


def test_client_dispatch_lock_concurrency():
    events_run = []
    evt1_started = threading.Event()
    evt1_finish = threading.Event()

    adapter = InMemoryStateAdapter()
    client = _client(lambda req: httpx.Response(200, json={}), state=adapter)

    @client.on_message
    def handle(msg):
        events_run.append(msg.id)
        if msg.id == "m1":
            evt1_started.set()
            evt1_finish.wait(timeout=1)

    evt1 = _message_event(seq=1, conversation_id="conv_a", text="Msg 1", msg_id="m1")
    evt2 = _message_event(seq=2, conversation_id="conv_a", text="Msg 2", msg_id="m2")
    evt3 = _message_event(seq=3, conversation_id="conv_a", text="Msg 3", msg_id="m3")

    # Start evt1 in background thread so its handler holds conv_a lock
    t = threading.Thread(target=client._dispatch_event, args=(evt1,))
    t.start()

    # Wait until evt1 is executing inside handler holding lock
    assert evt1_started.wait(timeout=1)

    # Dispatch evt2 for same conversation while evt1 holds lock -> must be skipped
    client._dispatch_event(evt2)
    assert events_run == ["m1"]

    # Allow evt1 to finish
    evt1_finish.set()
    t.join()

    # Dispatch evt3 after evt1 releases lock -> must acquire and run
    client._dispatch_event(evt3)
    assert events_run == ["m1", "m3"]
