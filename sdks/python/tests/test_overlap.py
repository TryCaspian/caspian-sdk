"""Tests for on_overlap concurrency strategies (Issue #32, Phase 2: queue + drop).

Each test documents HOW it proves the strategy worked, not just that it passed.
"""

import threading
import time

import httpx
import pytest
from caspian_sdk import CommClient

API_KEY = "comm_test_key"


# ── shared test infrastructure ─────────────────────────────────────────────────

def _client(handler) -> CommClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://gw.test")
    return CommClient(api_key=API_KEY, base_url="http://gw.test", http=http)


def _message_event(seq: int, text: str, conv_id: str = "conv_1") -> dict:
    return {
        "seq": seq,
        "type": "message.received",
        "data": {
            "customer_id": "cus_1",
            "agent_id": "agt_1",
            "message": {
                "id": f"msg_{seq}",
                "conversation_id": conv_id,
                "connection_id": "conn_1",
                "channel": "email",
                "text": text,
            },
        },
    }


def _one_shot(events: list[dict]):
    """HTTP mock: return all events on the first poll, empty list on subsequent polls."""
    served = [False]

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/events":
            if not served[0]:
                served[0] = True
                return httpx.Response(200, json=events)
            return httpx.Response(200, json=[])
        # Typing indicator and any other side-effect calls succeed silently.
        return httpx.Response(200, json={"ok": True})

    return handle


# ── queue strategy ─────────────────────────────────────────────────────────────

def test_queue_all_messages_handled_in_order():
    """
    Proof of queue correctness across three dimensions:

    ORDER: `order` records message text as each handler runs.  Asserting
    order == ["first", "second", "third"] proves the queue is FIFO.

    NO OVERLAP: `in_flight_count` is incremented at handler entry and
    decremented at exit (both under `counter_lock`).  `max_in_flight` tracks
    its peak.  If queue ever ran two handlers simultaneously, peak would be 2.
    The 20 ms sleep ensures the slot is held long enough that a concurrent
    call would definitely increment the counter before the first decrements.
    Asserting max_in_flight == 1 proves strict serialization.

    NONE DROPPED: order having all three items proves every message ran.
    """
    events = [
        _message_event(1, "first"),
        _message_event(2, "second"),
        _message_event(3, "third"),
    ]
    client = _client(_one_shot(events))

    order: list[str] = []
    in_flight_count = 0
    max_in_flight = 0
    counter_lock = threading.Lock()

    @client.on_message
    def handler(message):
        nonlocal in_flight_count, max_in_flight
        with counter_lock:
            in_flight_count += 1
            if in_flight_count > max_in_flight:
                max_in_flight = in_flight_count
        order.append(message.text)
        time.sleep(0.02)  # hold the slot; concurrent calls would raise max_in_flight
        with counter_lock:
            in_flight_count -= 1

    try:
        client.dispatch_pending(on_overlap="queue")
    finally:
        client.close()

    assert order == ["first", "second", "third"], f"wrong order: {order}"
    assert max_in_flight == 1, f"handlers overlapped: max concurrent = {max_in_flight}"


def test_queue_is_the_default():
    """
    Proof: dispatch_pending() with no on_overlap argument must behave as queue.

    Verified by asserting all messages are handled in order — if the default
    were parallel or no-op, the behaviour would differ.
    """
    events = [_message_event(1, "a"), _message_event(2, "b"), _message_event(3, "c")]
    client = _client(_one_shot(events))
    seen: list[str] = []
    client.on_message(lambda m: seen.append(m.text))
    try:
        client.dispatch_pending()  # no on_overlap= → "queue"
    finally:
        client.close()
    assert seen == ["a", "b", "c"]


def test_queue_handler_exception_does_not_stop_subsequent_messages():
    """
    Proof: a handler that raises must not prevent later queued messages.

    msg1's handler raises.  _run_message_handlers wraps each call in
    try/except, so _queue_worker continues its drain loop.  If the exception
    propagated out of the loop, `seen` would be empty.  Asserting
    seen == ["second", "third"] proves the worker ran on after the failure.

    dispatch_pending returning normally (not raising) proves the exception
    did not escape the thread boundary.
    """
    events = [
        _message_event(1, "first"),
        _message_event(2, "second"),
        _message_event(3, "third"),
    ]
    client = _client(_one_shot(events))
    seen: list[str] = []

    @client.on_message
    def handler(message):
        if message.text == "first":
            raise ValueError("intentional error in on_message handler")
        seen.append(message.text)

    try:
        last_seq = client.dispatch_pending(on_overlap="queue")
    finally:
        client.close()

    assert last_seq == 3
    assert seen == ["second", "third"]


def test_queue_conversations_are_independent():
    """
    Proof: queue state is per conversation_id.

    Two conversations each get one message.  Both handlers must run.  If the
    in_flight flag were shared across conversations, conv_2's message would
    be blocked or dropped.
    """
    events = [
        _message_event(1, "c1", conv_id="conv_1"),
        _message_event(2, "c2", conv_id="conv_2"),
    ]
    client = _client(_one_shot(events))
    seen: list[str] = []
    client.on_message(lambda m: seen.append(m.text))
    try:
        client.dispatch_pending(on_overlap="queue")
    finally:
        client.close()
    assert set(seen) == {"c1", "c2"}


# ── drop strategy ──────────────────────────────────────────────────────────────

def test_drop_only_first_message_handled():
    """
    Proof of drop correctness:

    ONLY ONE RUNS: dispatch_pending processes all three events in the main
    loop before any worker thread executes.  msg1's dispatch sets in_flight=True
    and spawns a worker.  By the time msg2 and msg3 are dispatched, in_flight
    is already True — they are logged and returned immediately, no thread
    spawned.  `seen` having exactly one item proves no queuing happened.

    IT'S THE FIRST: asserting seen[0] == "first" proves the in-flight handler
    was for the first arrival, not a later one.
    """
    events = [
        _message_event(1, "first"),
        _message_event(2, "second"),
        _message_event(3, "third"),
    ]
    client = _client(_one_shot(events))
    seen: list[str] = []
    client.on_message(lambda m: seen.append(m.text))
    try:
        client.dispatch_pending(on_overlap="drop")
    finally:
        client.close()

    assert len(seen) == 1, f"expected 1 handler call, got {len(seen)}: {seen}"
    assert seen[0] == "first"


def test_drop_handler_exception_does_not_kill_listener():
    """
    Proof: exception in a drop handler must not propagate to dispatch_pending.

    _run_message_handlers wraps every handler in try/except.  If that broke,
    dispatch_pending would raise.  It returning normally and returning the
    correct seq proves the exception was contained inside the worker thread.
    """
    events = [_message_event(1, "boom")]
    client = _client(_one_shot(events))

    @client.on_message
    def handler(message):
        raise RuntimeError("simulated on_message crash")

    try:
        last_seq = client.dispatch_pending(on_overlap="drop")
    finally:
        client.close()

    assert last_seq == 1


def test_drop_conversations_are_independent():
    """
    Proof: a drop for conv_1 must not affect conv_2.

    If the in_flight flag were global rather than per-conversation, conv_2's
    message would be dropped because conv_1's handler is in flight.  Both
    appearing in `seen` proves the state is partitioned by conversation_id.
    """
    events = [
        _message_event(1, "c1-first",  conv_id="conv_1"),
        _message_event(2, "c1-second", conv_id="conv_1"),  # dropped — conv_1 in flight
        _message_event(3, "c2-first",  conv_id="conv_2"),  # independent conversation
    ]
    client = _client(_one_shot(events))
    seen: list[str] = []
    client.on_message(lambda m: seen.append(m.text))
    try:
        client.dispatch_pending(on_overlap="drop")
    finally:
        client.close()

    assert "c1-first"  in seen
    assert "c1-second" not in seen, "c1-second should have been dropped"
    assert "c2-first"  in seen, "c2 is a different conversation; drop on c1 must not affect it"


# ── validation ─────────────────────────────────────────────────────────────────

def test_unknown_strategy_raises_immediately():
    """
    Proof: an unimplemented on_overlap value must raise before any events are
    processed.  `seen` being empty after the ValueError confirms the check
    fires at entry, not mid-dispatch.
    """
    client = _client(_one_shot([_message_event(1, "x")]))
    seen: list[str] = []
    client.on_message(lambda m: seen.append(m.text))
    try:
        with pytest.raises(ValueError, match="not yet implemented"):
            client.dispatch_pending(on_overlap="bogus")
    finally:
        client.close()
    assert seen == [], "handler ran despite invalid strategy — validation happened too late"


# ── race-condition stress test ─────────────────────────────────────────────────

def test_queue_no_serialization_failure_under_many_concurrent_conversations():
    """
    Why the specific dispatch/cleanup race is not directly unit-testable, and
    what this test verifies instead.

    THE RACE (now fixed): _dispatch_message could release _conv_states_lock
    before acquiring state.lock, letting a cleanup thread delete the state
    entry in the gap.  The next arrival for that conversation would find no
    entry, create fresh state, and spawn a second concurrent worker, breaking
    the per-conversation serialization guarantee.

    WHY WE CANNOT REPRODUCE IT IN A TEST: triggering it requires inserting a
    sleep between the two lock acquisitions inside production code, which we
    deliberately do not do.  The fix — holding both locks contiguously
    (outer _conv_states_lock, inner state.lock) for the full get-or-create +
    in_flight check — makes the window zero-width.  Correctness is guaranteed
    by code inspection of the consistent lock ordering in _dispatch_message,
    _queue_worker, and _drop_worker.

    WHAT THIS TEST DOES INSTEAD: 20 conversations x 3 messages each, messages
    interleaved across conversations (all firsts, then all seconds, then all
    thirds) to maximise the chance that a conversation's second message arrives
    while its first worker is mid-cleanup.  If the old race existed and caused
    two concurrent workers for the same conversation, max_in_flight_per_conv
    would exceed 1 for that conversation.  Serialization failures would also
    corrupt the per-conversation order list.
    """
    N = 20
    events: list[dict] = []
    seq = 0
    for label in ("first", "second", "third"):
        for conv in range(N):
            seq += 1
            events.append(_message_event(
                seq=seq,
                text=f"conv{conv}-{label}",
                conv_id=f"conv_{conv}",
            ))

    client = _client(_one_shot(events))

    per_conv_order: dict[str, list[str]] = {f"conv_{i}": [] for i in range(N)}
    in_flight_per_conv: dict[str, int] = {f"conv_{i}": 0 for i in range(N)}
    max_per_conv: dict[str, int] = {f"conv_{i}": 0 for i in range(N)}
    tlock = threading.Lock()

    @client.on_message
    def handler(message):
        cid = message.conversation_id
        label = message.text.split("-", 1)[1]
        with tlock:
            in_flight_per_conv[cid] += 1
            if in_flight_per_conv[cid] > max_per_conv[cid]:
                max_per_conv[cid] = in_flight_per_conv[cid]
        per_conv_order[cid].append(label)
        time.sleep(0.005)  # hold the slot long enough to detect concurrent calls
        with tlock:
            in_flight_per_conv[cid] -= 1

    try:
        client.dispatch_pending(on_overlap="queue")
    finally:
        client.close()

    for conv_id in per_conv_order:
        assert per_conv_order[conv_id] == ["first", "second", "third"], (
            f"{conv_id} wrong order: {per_conv_order[conv_id]}"
        )
        assert max_per_conv[conv_id] == 1, (
            f"{conv_id} overlap: max concurrent in-flight = {max_per_conv[conv_id]}"
        )


# ── listen() strategy tests ────────────────────────────────────────────────────
#
# Why these tests exist: all Phase 2 tests above run through dispatch_pending.
# listen() has an identical batch dispatch loop that had the same race
# (thread.start() called immediately, allowing a fast worker to clear
# in_flight before the next event in the batch was dispatched).  These tests
# exercise listen() directly to prove the fix covers both code paths.


def _listen_http(events: list[dict]):
    """
    Mock that returns `events` on the first poll (after_seq=0) and an empty
    list on all subsequent polls.  Typing indicator and other side-effect
    paths return 200 silently.
    """
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/events":
            after = int(dict(request.url.params).get("after_seq", 0))
            return httpx.Response(200, json=events if after == 0 else [])
        return httpx.Response(200, json={"ok": True})
    return handle


def test_listen_queue_burst_handled_in_order():
    """
    Proof: listen()'s poll loop applies queue serialization to a same-conv burst.

    Without the fix (listen() starting threads immediately), a fast worker
    could clear in_flight before msg2 is dispatched, causing msg2+msg3 to
    create fresh state and run concurrently rather than being enqueued.
    `all_done.wait()` ensures we don't assert before all handlers run.
    `seen == ["first", "second", "third"]` proves FIFO order with no drops.
    """
    events = [
        _message_event(1, "first"),
        _message_event(2, "second"),
        _message_event(3, "third"),
    ]
    client = _client(_listen_http(events))
    seen: list[str] = []
    all_done = threading.Event()

    @client.on_message
    def handler(message):
        seen.append(message.text)
        if len(seen) == 3:
            all_done.set()

    t = threading.Thread(
        target=client.listen,
        kwargs={"from_seq": 0, "on_overlap": "queue", "poll_interval": 0.005},
        daemon=True,
    )
    t.start()

    assert all_done.wait(timeout=5.0), f"not all messages handled in time; seen={seen}"
    assert seen == ["first", "second", "third"]
    client.close()


def test_listen_drop_burst_only_first_handled():
    """
    Proof: listen()'s poll loop applies drop semantics to a same-conv burst.

    Without the fix, the worker for msg1 could finish and clear in_flight
    before msg2 is dispatched, causing msg2 to create fresh state and run.
    With the fix, all three events are dispatched (in_flight set) before any
    worker starts, so msg2 and msg3 see in_flight=True and are dropped.

    `first_done.wait()` ensures msg1's handler has run.  The subsequent
    `time.sleep(0.05)` gives any incorrectly-spawned handler time to append
    to `seen` before the assertion.  `len(seen) == 1` then proves msg2+msg3
    were never dispatched to a handler.
    """
    events = [
        _message_event(1, "first"),
        _message_event(2, "second"),
        _message_event(3, "third"),
    ]
    client = _client(_listen_http(events))
    seen: list[str] = []
    first_done = threading.Event()

    @client.on_message
    def handler(message):
        seen.append(message.text)
        first_done.set()

    t = threading.Thread(
        target=client.listen,
        kwargs={"from_seq": 0, "on_overlap": "drop", "poll_interval": 0.005},
        daemon=True,
    )
    t.start()

    assert first_done.wait(timeout=5.0), "handler never ran"
    time.sleep(0.05)  # grace period: an incorrect concurrent call would appear here

    assert len(seen) == 1, f"expected 1 handler call, got {len(seen)}: {seen}"
    assert seen[0] == "first"
    client.close()


def test_listen_queue_burst_no_overlap():
    """
    Stronger queue proof for listen(): explicitly detect concurrent handler
    execution, not just order.

    test_listen_queue_burst_handled_in_order passed EVEN WITH the listen()
    bug reverted, because three separate workers (each running one message)
    still append in start-order by luck on a GIL-constrained machine.
    This test closes that gap: the max_in_flight counter proves no two handlers
    ran simultaneously, which CANNOT be true if separate workers are spawned.
    """
    events = [
        _message_event(1, "first"),
        _message_event(2, "second"),
        _message_event(3, "third"),
    ]
    client = _client(_listen_http(events))

    in_flight_count = 0
    max_in_flight = 0
    counter_lock = threading.Lock()
    all_done = threading.Event()
    seen: list[str] = []

    @client.on_message
    def handler(message):
        nonlocal in_flight_count, max_in_flight
        with counter_lock:
            in_flight_count += 1
            if in_flight_count > max_in_flight:
                max_in_flight = in_flight_count
        seen.append(message.text)
        time.sleep(0.02)  # hold the slot; concurrent calls raise max_in_flight above 1
        with counter_lock:
            in_flight_count -= 1
        if len(seen) == 3:
            all_done.set()

    t = threading.Thread(
        target=client.listen,
        kwargs={"from_seq": 0, "on_overlap": "queue", "poll_interval": 0.005},
        daemon=True,
    )
    t.start()

    assert all_done.wait(timeout=5.0), f"not all messages handled; seen={seen}"
    assert seen == ["first", "second", "third"], f"wrong order: {seen}"
    assert max_in_flight == 1, f"handlers overlapped in listen(): max concurrent = {max_in_flight}"
    client.close()


# ── Phase 3 tests: debounce & parallel ─────────────────────────────────────────

def test_debounce_burst_runs_latest_only():
    events = [
        _message_event(1, "first"),
        _message_event(2, "second"),
        _message_event(3, "third"),
    ]
    client = _client(_one_shot(events))
    seen = []
    done = threading.Event()

    @client.on_message
    def handler(message):
        seen.append(message.text)
        done.set()

    # Burst them immediately
    assert client.dispatch_pending(on_overlap="debounce", debounce_ms=100) == 3

    # The timers were canceled; only the 3rd message's timer fired.
    # dispatch_pending provides a synchronous guarantee, so it's already done.
    assert len(seen) == 1
    assert seen[0] == "third"
    client.close()

def test_debounce_spaced_runs_each():
    events = [
        _message_event(1, "first"),
        _message_event(2, "second"),
        _message_event(3, "third"),
    ]
    client = _client(_one_shot(events))
    seen = []
    
    # We can trust dispatch_pending to block until the handler is fully done
    @client.on_message
    def handler(message):
        seen.append(message.text)

    # Dispatch msg 1
    client._http = httpx.Client(transport=httpx.MockTransport(_one_shot([events[0]])), base_url="http://gw.test")
    assert client.dispatch_pending(on_overlap="debounce", debounce_ms=50) == 1

    # Dispatch msg 2
    client._http = httpx.Client(transport=httpx.MockTransport(_one_shot([events[1]])), base_url="http://gw.test")
    assert client.dispatch_pending(on_overlap="debounce", debounce_ms=50) == 2

    # Dispatch msg 3
    client._http = httpx.Client(transport=httpx.MockTransport(_one_shot([events[2]])), base_url="http://gw.test")
    assert client.dispatch_pending(on_overlap="debounce", debounce_ms=50) == 3

    assert seen == ["first", "second", "third"]
    client.close()

def test_debounce_overlap_queues_behind_running_handler():
    events = [
        _message_event(1, "first"),
        _message_event(2, "second"),
    ]
    client = _client(_one_shot([events[0]]))
    seen = []
    
    first_started = threading.Event()
    first_finish = threading.Event()
    second_done = threading.Event()

    @client.on_message
    def handler(message):
        seen.append(message.text)
        if message.text == "first":
            first_started.set()
            first_finish.wait(timeout=5.0)
        elif message.text == "second":
            second_done.set()

    # Dispatch msg 1 in a background thread so it can stay in flight.
    t1 = threading.Thread(
        target=client.dispatch_pending,
        kwargs={"on_overlap": "debounce", "debounce_ms": 50},
        daemon=True,
    )
    t1.start()
    assert first_started.wait(timeout=2.0)

    # Now msg 1 is IN FLIGHT.
    # Dispatch msg 2. It will debounce, timer will fire, see in_flight_count=1,
    # and append to pending.
    client._http = httpx.Client(
        transport=httpx.MockTransport(_one_shot([events[1]])), base_url="http://gw.test"
    )
    assert client.dispatch_pending(on_overlap="debounce", debounce_ms=50) == 2
    
    # Wait for msg 2's timer to definitely fire and append to pending
    time.sleep(0.15)
    
    # Prove msg 2 hasn't run concurrently
    assert "second" not in seen

    # Let msg 1 finish
    first_finish.set()
    
    # Wait for the first dispatch_pending to complete
    t1.join(timeout=2.0)
    
    # Msg 2 was queued behind msg 1, so msg 2's dispatch_pending returned early.
    # We must wait for the queue drain loop to process msg 2.
    assert second_done.wait(timeout=2.0)
    assert seen == ["first", "second"]
    client.close()

def test_debounce_close_cancels_timer():
    client = _client(_one_shot([_message_event(1, "first")]))
    seen = []

    @client.on_message
    def handler(message):
        seen.append(message.text)

    # Use dispatch_pending in a background thread so we can call close() concurrently
    t = threading.Thread(
        target=client.dispatch_pending,
        kwargs={"on_overlap": "debounce", "debounce_ms": 500},
        daemon=True,
    )
    t.start()
    
    # Wait briefly so the timer starts (but < 500ms)
    time.sleep(0.05)
    
    # Immediately close before the 500ms timer fires
    client.close()
    
    # Wait for the background dispatch_pending to finish (which it should immediately 
    # since the timer was cancelled and its join() unblocks, skipping the handler).
    t.join(timeout=1.0)
    
    assert len(seen) == 0

def test_parallel_burst_runs_all_concurrently():
    events = [
        _message_event(1, "first"),
        _message_event(2, "second"),
        _message_event(3, "third"),
    ]
    client = _client(_one_shot(events))
    
    in_flight_count = 0
    max_in_flight = 0
    counter_lock = threading.Lock()
    all_done = threading.Event()
    seen = []

    @client.on_message
    def handler(message):
        nonlocal in_flight_count, max_in_flight
        with counter_lock:
            in_flight_count += 1
            if in_flight_count > max_in_flight:
                max_in_flight = in_flight_count
        seen.append(message.text)
        # Sleep so they actually overlap
        time.sleep(0.1)
        with counter_lock:
            in_flight_count -= 1
        if len(seen) == 3:
            all_done.set()

    assert client.dispatch_pending(on_overlap="parallel") == 3
    assert all_done.wait(timeout=2.0)
    
    # Parallel should hit max_in_flight = 3 for the 3 messages
    assert max_in_flight == 3
    assert len(seen) == 3
    client.close()

@pytest.mark.parametrize("strategy", ["queue", "drop"])
def test_queue_and_drop_max_in_flight_assertion(strategy):
    events = [
        _message_event(1, "first"),
        _message_event(2, "second"),
        _message_event(3, "third"),
    ]
    client = _client(_one_shot(events))
    
    max_in_flight = 0
    counter_lock = threading.Lock()
    done = threading.Event()
    seen = []

    @client.on_message
    def handler(message):
        nonlocal max_in_flight
        conv_id = message.conversation_id
        
        with client._conv_states_lock:
            state = client._conv_states.get(conv_id)
            current_in_flight = state.in_flight_count if state else 0
            
        with counter_lock:
            if current_in_flight > max_in_flight:
                max_in_flight = current_in_flight
                
        seen.append(message.text)
        time.sleep(0.05)
        
        if strategy == "drop":
            done.set()
        elif strategy == "queue" and len(seen) == 3:
            done.set()

    assert client.dispatch_pending(on_overlap=strategy) == 3
    assert done.wait(timeout=2.0)
    
    assert max_in_flight == 1, f"{strategy} failed max_in_flight assertion"
    client.close()

def test_parallel_handler_exception_does_not_stop_subsequent_messages():
    events = [
        _message_event(1, "first"),
        _message_event(2, "second"),
    ]
    client = _client(_one_shot(events))
    
    first_started = threading.Event()
    second_done = threading.Event()
    seen = []

    @client.on_message
    def handler(message):
        if message.text == "first":
            first_started.set()
            raise ValueError("First failed")
        else:
            seen.append(message.text)
            second_done.set()

    # dispatch_pending joins all spawned threads before returning, so by the
    # time the assertion runs below, both handlers have fully completed.
    client.dispatch_pending(on_overlap="parallel")
    
    assert second_done.wait(timeout=2.0)
    assert seen == ["second"]
    client.close()

def test_debounce_handler_exception_does_not_stop_queued_messages():
    events = [
        _message_event(1, "first"),
        _message_event(2, "second"),
    ]
    client = _client(_one_shot([events[0]]))
    seen = []
    
    first_started = threading.Event()
    first_fail_now = threading.Event()
    second_done = threading.Event()

    @client.on_message
    def handler(message):
        if message.text == "first":
            first_started.set()
            first_fail_now.wait(timeout=2.0)
            raise ValueError("First failed")
        else:
            seen.append(message.text)
            second_done.set()

    # Dispatch msg 1 in a background thread so it stays in flight
    t1 = threading.Thread(
        target=client.dispatch_pending,
        kwargs={"on_overlap": "debounce", "debounce_ms": 50},
        daemon=True,
    )
    t1.start()
    assert first_started.wait(timeout=2.0)

    # Now msg 1 is IN FLIGHT.
    # Dispatch msg 2. It will debounce, timer will fire, see in_flight_count=1,
    # and append to pending.
    client._http = httpx.Client(
        transport=httpx.MockTransport(_one_shot([events[1]])), base_url="http://gw.test"
    )
    assert client.dispatch_pending(on_overlap="debounce", debounce_ms=50) == 2
    
    # Let msg 1 raise the exception
    first_fail_now.set()
    
    # Wait for the background dispatch_pending to complete
    t1.join(timeout=2.0)
    
    # Wait for msg 2 to run despite msg 1 failing
    assert second_done.wait(timeout=2.0)
    assert seen == ["second"]
    client.close()
