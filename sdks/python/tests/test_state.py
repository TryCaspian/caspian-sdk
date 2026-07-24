"""Unit & integration tests for state/dedup adapters in Python SDK."""

import threading
import time

import httpx
from caspian_sdk import (
    CommClient,
    InMemoryStateAdapter,
    RedisStateAdapter,
)


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        return True

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def delete(self, key: str) -> int:
        if key in self.store:
            del self.store[key]
            return 1
        return 0

    def eval(self, script: str, numkeys: int, key: str, arg: str) -> int:
        if self.get(key) == arg:
            self.delete(key)
            return 1
        return 0


def test_in_memory_state_adapter_seen():
    adapter = InMemoryStateAdapter()
    assert adapter.seen("evt_1") is False
    assert adapter.seen("evt_1") is True
    assert adapter.seen("evt_2") is False


def test_in_memory_state_adapter_locking():
    adapter = InMemoryStateAdapter()
    execution_order = []

    def task(name: str, delay: float):
        with adapter.lock("conv_1"):
            execution_order.append(f"{name}_start")
            time.sleep(delay)
            execution_order.append(f"{name}_end")

    t1 = threading.Thread(target=task, args=("t1", 0.05))
    t2 = threading.Thread(target=task, args=("t2", 0.01))

    t1.start()
    time.sleep(0.01)  # Ensure t1 acquires first
    t2.start()

    t1.join()
    t2.join()

    assert execution_order == ["t1_start", "t1_end", "t2_start", "t2_end"]


def test_redis_state_adapter_seen():
    redis_client = FakeRedis()
    adapter = RedisStateAdapter(redis_client)

    assert adapter.seen("evt_1") is False
    assert adapter.seen("evt_1") is True
    assert adapter.seen("evt_2") is False


def test_redis_state_adapter_locking():
    redis_client = FakeRedis()
    adapter = RedisStateAdapter(redis_client)

    execution_order = []

    def task(name: str, delay: float):
        with adapter.lock("conv_1"):
            execution_order.append(f"{name}_start")
            time.sleep(delay)
            execution_order.append(f"{name}_end")

    t1 = threading.Thread(target=task, args=("t1", 0.05))
    t2 = threading.Thread(target=task, args=("t2", 0.01))

    t1.start()
    time.sleep(0.01)
    t2.start()

    t1.join()
    t2.join()

    assert execution_order == ["t1_start", "t1_end", "t2_start", "t2_end"]


def test_client_dispatch_drops_duplicates():
    events = [
        {
            "id": "evt_1",
            "seq": 1,
            "type": "message.received",
            "data": {
                "customer_id": "c1",
                "agent_id": "a1",
                "message": {
                    "id": "m1",
                    "conversation_id": "conv_1",
                    "connection_id": "conn_1",
                    "text": "hello 1",
                },
            },
        },
        {
            "id": "evt_1",
            "seq": 2,
            "type": "message.received",
            "data": {
                "customer_id": "c1",
                "agent_id": "a1",
                "message": {
                    "id": "m1",
                    "conversation_id": "conv_1",
                    "connection_id": "conn_1",
                    "text": "hello 1 duplicate",
                },
            },
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        after = int(dict(request.url.params).get("after_seq", 0))
        return httpx.Response(200, json=[] if after >= 2 else events)

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://gw.test")
    adapter = InMemoryStateAdapter()
    client = CommClient(api_key="test", base_url="http://gw.test", http=http, state_adapter=adapter)

    received = []
    client.on_message(lambda m: received.append(m.text))

    try:
        client.dispatch_pending(0)
    finally:
        client.close()

    assert len(received) == 1
    assert received[0] == "hello 1"
