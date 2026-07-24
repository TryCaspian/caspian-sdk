"""Client-level tests against a mock HTTP transport (no gateway needed)."""

import json

import httpx
import pytest
from caspian_sdk import CommClient, CommError


API_KEY = "comm_test_key"


def _client(handler) -> CommClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://gw.test")
    return CommClient(api_key=API_KEY, base_url="http://gw.test", http=http)


def test_requests_carry_bearer_auth():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["path"] = request.url.path
        return httpx.Response(201, json={"id": "cus_1", "name": "Acme"})

    client = _client(handler)
    try:
        customer = client.create_customer("Acme")
    finally:
        client.close()
    assert customer["id"] == "cus_1"
    assert seen["auth"] == f"Bearer {API_KEY}"
    assert seen["path"] == "/v1/customers"


def test_error_maps_to_comm_error_with_detail():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "bot_token is required"})

    client = _client(handler)
    with pytest.raises(CommError) as excinfo:
        try:
            client.connect_telegram(bot_token=None)
        finally:
            client.close()
    assert excinfo.value.status_code == 422
    assert "bot_token" in str(excinfo.value)


def test_connect_email_waits_for_provisioning():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["display_name"] == "Acme Support"
            return httpx.Response(
                201, json={"id": "conn_1", "status": "provisioning", "address": None}
            )
        return httpx.Response(
            200, json={"id": "conn_1", "status": "active", "address": "acme@agents.example.com"}
        )

    client = _client(handler)
    try:
        connection = client.connect_email(display_name="Acme Support", poll_interval=0.01)
    finally:
        client.close()
    assert connection["status"] == "active"
    assert connection["address"] == "acme@agents.example.com"
    assert calls[0] == ("POST", "/v1/connections/email")
    assert ("GET", "/v1/connections/conn_1") in calls


def test_connect_no_wait_returns_immediately():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": "conn_2", "status": "provisioning"})

    client = _client(handler)
    try:
        connection = client.connect_email(wait=False)
    finally:
        client.close()
    assert connection["status"] == "provisioning"


def test_provisioning_failure_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"id": "conn_3", "status": "provisioning"})
        return httpx.Response(
            200, json={"id": "conn_3", "status": "failed", "error": "domain not verified"}
        )

    client = _client(handler)
    with pytest.raises(CommError) as excinfo:
        try:
            client.connect_email(poll_interval=0.01)
        finally:
            client.close()
    assert excinfo.value.status_code == 502
    assert "domain not verified" in str(excinfo.value)


def test_reply_and_send_message_forward_blocks():
    from caspian_sdk import blocks as b

    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"delivered": True})

    payload = [
        b.heading("Order shipped"),
        b.buttons([{"label": "Track", "url": "https://x/track"}]),
    ]

    client = _client(handler)
    try:
        client.reply("msg_1", text="Order shipped", blocks=payload)
        client.send_message("conv_1", blocks=payload)
    finally:
        client.close()

    assert bodies[0][0] == "/v1/messages/msg_1/reply"
    assert bodies[0][1] == {"text": "Order shipped", "html": None, "blocks": payload,
                            "media": None}
    assert bodies[1][0] == "/v1/conversations/conv_1/messages"
    assert bodies[1][1] == {"text": None, "html": None, "blocks": payload, "media": None}


def test_reply_and_send_forward_media():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"delivered": True})

    media = [{"url": "https://x/i.png", "mime_type": "image/png", "name": "i.png"}]
    client = _client(handler)
    try:
        client.reply("msg_1", text="here", media=media)
        client.send_message("conv_1", media=media)
    finally:
        client.close()
    assert bodies[0][1] == {"text": "here", "html": None, "blocks": None, "media": media}
    assert bodies[1][1] == {"text": None, "html": None, "blocks": None, "media": media}


def test_react_hits_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(202, json={"ok": True, "reacted": True})

    client = _client(handler)
    try:
        client.react("msg_1", "👍")
    finally:
        client.close()
    assert seen["path"] == "/v1/messages/msg_1/react"
    assert seen["body"] == {"emoji": "👍"}


def test_on_interaction_dispatches_and_replies():
    from caspian_sdk import Interaction

    events = [
        {
            "seq": 1,
            "type": "interaction.received",
            "data": {
                "connection_id": "conn_1", "customer_id": "cus_1", "agent_id": "agt_1",
                "conversation_id": "conv_1", "value": "reorder_123",
                "source_message": {"id": "msg_9"}, "sender": {"address": "u"},
            },
        }
    ]
    replies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/events":
            after = int(dict(request.url.params).get("after_seq", 0))
            return httpx.Response(200, json=[] if after >= 1 else events)
        replies.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"delivered": True})

    client = _client(handler)
    seen: list[Interaction] = []

    @client.on_interaction
    def handle(inter: Interaction) -> None:
        seen.append(inter)
        inter.reply(f"got {inter.value}")

    try:
        client.dispatch_pending(0)
    finally:
        client.close()
    assert len(seen) == 1
    assert seen[0].value == "reorder_123"
    assert seen[0].source_message["id"] == "msg_9"
    # reply routed to the source message
    assert replies[0][0] == "/v1/messages/msg_9/reply"
    assert replies[0][1]["text"] == "got reorder_123"


def test_on_reaction_dispatches():
    from caspian_sdk import Reaction

    events = [
        {
            "seq": 1,
            "type": "reaction.received",
            "data": {
                "connection_id": "conn_1", "customer_id": "cus_1", "agent_id": "agt_1",
                "emoji": "thumbsup", "action": "added",
                "source_message": {"id": "msg_9"}, "sender": {"address": "u"},
            },
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        after = int(dict(request.url.params).get("after_seq", 0))
        return httpx.Response(200, json=[] if after >= 1 else events)

    client = _client(handler)
    seen: list[Reaction] = []
    client.on_reaction(seen.append)
    try:
        client.dispatch_pending(0)
    finally:
        client.close()
    assert len(seen) == 1
    assert seen[0].emoji == "thumbsup"
    assert seen[0].action == "added"


def test_message_carries_media_to_handler():
    events = [
        {
            "seq": 1,
            "type": "message.received",
            "data": {
                "customer_id": "cus_1", "agent_id": "agt_1",
                "message": {
                    "id": "m1", "conversation_id": "c1", "connection_id": "cn1",
                    "channel": "email", "text": "see attached",
                    "media": [{"name": "r.pdf", "mime_type": "application/pdf"}],
                },
            },
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/events":
            after = int(dict(request.url.params).get("after_seq", 0))
            return httpx.Response(200, json=[] if after >= 1 else events)
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    seen = []
    client.on_message(lambda m: seen.append(m))
    try:
        client.dispatch_pending(0)
    finally:
        client.close()
    assert seen[0].media == [{"name": "r.pdf", "mime_type": "application/pdf"}]


def test_behavior_prompt_returns_text():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/behavior-prompt"
        return httpx.Response(200, text="## Slack\nUse threads.")

    client = _client(handler)
    try:
        guide = client.behavior_prompt()
    finally:
        client.close()
    assert "Slack" in guide

def _message_event(seq: int, msg_id: str, conv_id: str = "c1") -> dict:
    return {
        "seq": seq,
        "type": "message.received",
        "data": {
            "message": {
                "id": msg_id,
                "conversation_id": conv_id,
                "connection_id": "c",
            }
        },
    }

def test_concurrency_queue():
    import threading
    import time

    def handler(request):
        return httpx.Response(200, json=[])

    client = _client(handler)
    seen = []
    state_lock = threading.Lock()
    first_entered = threading.Event()
    overlap = False
    inside = 0

    def handle(m):
        nonlocal inside, overlap
        with state_lock:
            inside += 1
            overlap = overlap or inside > 1
        if m.id == "m1":
            first_entered.set()
            time.sleep(0.05)
        seen.append(m.id)
        with state_lock:
            inside -= 1

    client.on_message(handle)
    try:
        t1 = threading.Thread(
            target=client._handle_concurrency, args=(_message_event(1, "m1"), "queue", 500)
        )
        t2 = threading.Thread(
            target=client._handle_concurrency, args=(_message_event(2, "m2"), "queue", 500)
        )
        t1.start()
        assert first_entered.wait(0.5)
        t2.start()
        time.sleep(0.01)
        assert seen == []
        t1.join()
        t2.join()
    finally:
        client.close()
    assert seen == ["m1", "m2"]
    assert not overlap

def test_concurrency_parallel():
    import threading
    import time

    def handler(request):
        return httpx.Response(200, json=[])

    client = _client(handler)
    seen = []
    active = 0
    max_active = 0
    lock = threading.Lock()

    def handle(m):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            seen.append(m.id)
            active -= 1

    client.on_message(handle)
    try:
        start = time.monotonic()
        client._handle_concurrency(_message_event(1, "m1"), "parallel", 500)
        client._handle_concurrency(_message_event(2, "m2"), "parallel", 500)
        time.sleep(0.08)
        elapsed = time.monotonic() - start
    finally:
        client.close()
    assert sorted(seen) == ["m1", "m2"]
    assert max_active == 2
    assert elapsed < 0.10

def test_concurrency_drop():
    import threading

    events1 = [_message_event(1, "m1")]
    events2 = [_message_event(2, "m2")]
    
    def handler(request):
        nonlocal events1, events2
        after = int(dict(request.url.params).get("after_seq", 0))
        if after == 0 and events1:
            res = events1
            events1 = []
            return httpx.Response(200, json=res)
        if after == 1 and events2:
            res = events2
            events2 = []
            return httpx.Response(200, json=res)
        return httpx.Response(200, json=[])

    client = _client(handler)
    seen = []
    def handle(m):
        seen.append(m.id)
        if len(seen) == 1:
            # Re-entrant fetch from another thread while this is in-flight
            t = threading.Thread(
                target=client.dispatch_pending, args=(1,), kwargs={"concurrency": "drop"}
            )
            t.start()
            t.join()
            
    client.on_message(handle)
    try:
        client.dispatch_pending(0, concurrency="drop")
    finally:
        client.close()
    
    # m2 should be dropped because m1 was in-flight
    assert seen == ["m1"]


def test_concurrency_drop_check_and_set_is_atomic():
    """Verify that the drop mode's check-and-claim is atomic.

    This fires concurrent submit() calls for the same conversation_id under
    drop mode from multiple threads, released via a barrier to hit submit()
    at the same time. Only one should successfully run the handler.
    """
    import threading
    import time

    def handler(request):
        return httpx.Response(200, json=[])

    client = _client(handler)
    seen = []
    
    # Fire 20 concurrent drops for the same conversation
    N = 20
    start_barrier = threading.Barrier(N)

    def handle(m):
        seen.append(m.id)
        time.sleep(0.05)

    def dispatch(i):
        # We must use the same conversation ID "c1" to trigger the drop logic.
        event = _message_event(i, f"m{i}", conv_id="c1")
        start_barrier.wait(timeout=2.0)
        client._handle_concurrency(event, "drop", 500)

    client.on_message(handle)
    threads = []
    try:
        for i in range(N):
            t = threading.Thread(target=dispatch, args=(i,))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
    finally:
        client.close()

    assert len(seen) == 1


def test_concurrency_debounce():
    import time
    events = [_message_event(1, "m1"), _message_event(2, "m2"), _message_event(3, "m3")]
    def handler(request):
        after = int(dict(request.url.params).get("after_seq", 0))
        return httpx.Response(200, json=[] if after >= 3 else events)

    client = _client(handler)
    seen = []
    client.on_message(lambda m: seen.append(m))
    try:
        client.dispatch_pending(0, concurrency="debounce", debounce_ms=40)
        time.sleep(0.02)
        assert seen == []
        time.sleep(0.06)
    finally:
        client.close()
        
    assert len(seen) == 1
    assert seen[0].id == "m3"
    assert len(seen[0].coalesced_messages) == 2
    assert seen[0].coalesced_messages[0].id == "m1"
    assert seen[0].coalesced_messages[1].id == "m2"


@pytest.mark.parametrize("concurrency", ["queue", "parallel", "debounce", "drop"])
def test_concurrency_handler_exception_releases_state(concurrency):
    import time

    def handler(request):
        return httpx.Response(200, json=[])

    client = _client(handler)
    seen = []

    def handle(m):
        seen.append(m.id)
        if m.id == "boom":
            raise RuntimeError("boom")

    client.on_message(handle)
    try:
        client._handle_concurrency(_message_event(1, "boom"), concurrency, 10)
        time.sleep(0.05)
        client._handle_concurrency(_message_event(2, "ok"), concurrency, 10)
        time.sleep(0.05)
    finally:
        client.close()

    assert seen == ["boom", "ok"]
    sched = client._scheduler
    assert "c1" not in sched._in_flight
    assert "c1" not in sched._conv_locks
    assert "c1" not in sched._conv_debounce_timers
    assert "c1" not in sched._conv_debounce_events


# ---- New tests for the scheduler upgrade ------------------------------------


def test_listen_rejects_invalid_overlap_options():
    """Invalid concurrency mode or negative debounce_ms must raise ValueError
    immediately, not silently fall through to queue-like behavior."""

    def handler(request):
        return httpx.Response(200, json=[])

    client = _client(handler)
    try:
        with pytest.raises(ValueError, match="concurrency"):
            client.dispatch_pending(0, concurrency="bogus")

        with pytest.raises(ValueError, match="debounce_ms"):
            client.dispatch_pending(0, debounce_ms=-1)

        with pytest.raises(ValueError, match="concurrency"):
            client.listen(concurrency="invalid_mode")

        with pytest.raises(ValueError, match="debounce_ms"):
            client.listen(debounce_ms=-100)
    finally:
        client.close()


def test_cross_conversation_queue_runs_concurrently():
    """Two conversations under queue mode should run in parallel. If a handler
    sleeps 50ms per conversation, dispatching two conversations should complete
    in ~50ms (parallel), not ~100ms (serial)."""
    import threading
    import time

    def handler(request):
        return httpx.Response(200, json=[])

    client = _client(handler)
    done = threading.Event()
    seen = []

    def handle(m):
        seen.append(m.id)
        time.sleep(0.05)

    client.on_message(handle)
    try:
        start = time.monotonic()
        t1 = threading.Thread(
            target=client._handle_concurrency,
            args=(_message_event(1, "m1", "conv_A"), "queue", 500),
        )
        t2 = threading.Thread(
            target=client._handle_concurrency,
            args=(_message_event(2, "m2", "conv_B"), "queue", 500),
        )
        t1.start()
        t2.start()
        t1.join(timeout=2)
        t2.join(timeout=2)
        elapsed = time.monotonic() - start
    finally:
        client.close()

    assert sorted(seen) == ["m1", "m2"]
    # If they ran serially this would be ~100ms; parallel should be ~50-60ms.
    assert elapsed < 0.08, f"took {elapsed:.3f}s — conversations ran serially, not in parallel"


def test_shutdown_drains_pending_debounce():
    """When close() is called while a debounce timer is pending, the scheduler
    must flush (dispatch) the pending debounced events before returning, rather
    than silently dropping them."""
    import time

    def handler(request):
        return httpx.Response(200, json=[])

    client = _client(handler)
    seen = []
    client.on_message(lambda m: seen.append(m))

    # Submit a debounced event with a long timer so it won't fire on its own.
    client._handle_concurrency(
        _message_event(1, "pending_msg"), "debounce", 5000
    )
    # The event should not have fired yet.
    assert seen == []

    # close() should flush the pending event.
    client.close()
    assert len(seen) == 1
    assert seen[0].id == "pending_msg"
