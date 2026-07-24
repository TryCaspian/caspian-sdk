import threading
import time

import fakeredis
from caspian_sdk.state import InMemoryStateAdapter, RedisStateAdapter


def test_in_memory_state_adapter_seen():
    adapter = InMemoryStateAdapter(max_events=2)
    assert adapter.seen("evt_1") is False
    assert adapter.seen("evt_1") is True

    assert adapter.seen("evt_2") is False
    assert adapter.seen("evt_3") is False

    # evt_1 should be evicted because max_events=2
    assert adapter.seen("evt_1") is False


def test_in_memory_state_adapter_lock():
    adapter = InMemoryStateAdapter()

    result = []
    entered = threading.Event()

    def worker1():
        with adapter.lock("conv_1"):
            entered.set()
            result.append("w1_start")
            time.sleep(0.1)
            result.append("w1_end")

    def worker2():
        with adapter.lock("conv_1"):
            result.append("w2_start")
            result.append("w2_end")

    t1 = threading.Thread(target=worker1)
    t2 = threading.Thread(target=worker2)
    t1.start()
    assert entered.wait(timeout=1)
    t2.start()

    t1.join()
    t2.join()

    # w2 should strictly wait for w1 to finish
    assert result == ["w1_start", "w1_end", "w2_start", "w2_end"]


def test_redis_state_adapter_seen():
    r = fakeredis.FakeRedis()
    adapter = RedisStateAdapter(r)

    assert adapter.seen("evt_1") is False
    assert adapter.seen("evt_1") is True

    r.flushall()


def test_redis_state_adapter_lock():
    r = fakeredis.FakeRedis()
    adapter = RedisStateAdapter(r)

    result = []
    entered = threading.Event()

    def worker1():
        with adapter.lock("conv_2"):
            entered.set()
            result.append("w1_start")
            time.sleep(0.1)
            result.append("w1_end")

    def worker2():
        with adapter.lock("conv_2"):
            result.append("w2_start")
            result.append("w2_end")

    t1 = threading.Thread(target=worker1)
    t2 = threading.Thread(target=worker2)
    t1.start()
    assert entered.wait(timeout=1)
    t2.start()

    t1.join()
    t2.join()

    assert result == ["w1_start", "w1_end", "w2_start", "w2_end"]
