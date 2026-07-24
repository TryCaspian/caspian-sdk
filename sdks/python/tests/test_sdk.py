"""Client-level tests against a mock HTTP transport (no gateway needed)."""

import json
import time
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

def test_queue_preserves_order():
    processed = []

    client = CommClient(api_key="test")

    def handler(message):
        processed.append(message.text)

    client.on_message(handler)

    event1 = {
    "type": "message.received",
    "data": {
        "customer_id": "cus_1",
        "agent_id": "agt_1",
        "message": {
            "id": "1",
            "conversation_id": "conv1",
            "connection_id": "cn1",
            "channel": "email",
            "text": "first",
        },
    },
}

    event2 = {
    "type": "message.received",
    "data": {
        "customer_id": "cus_2",
        "agent_id": "agt_2",
        "message": {
            "id": "2",
            "conversation_id": "conv1",
            "connection_id": "cn2",
            "channel": "email",
            "text": "second",
        },
    },
}

    event3 = {
    "type": "message.received",
    "data": {
        "customer_id": "cus_3",
        "agent_id": "agt_3",
        "message": {
            "id": "3",
            "conversation_id": "conv1",
            "connection_id": "cn3",
            "channel": "email",
            "text": "third",
        },
    },
}

    client._route_event(event1)
    client._route_event(event2)
    client._route_event(event3)

    while len(processed) < 3:
        time.sleep(0.01)

    assert processed == ["first", "second", "third"]

def test_drop_skips_overlapping_messages():
    processed = []

    client = CommClient(api_key="test")
    client._concurrency = "drop"

    def handler(message):
        time.sleep(0.05)
        processed.append(message.text)

    client.on_message(handler)

    event1 = {
        "type": "message.received",
        "data": {
            "customer_id": "cus_1",
            "agent_id": "agt_1",
            "message": {
                "id": "1",
                "conversation_id": "conv1",
                "connection_id": "cn1",
                "channel": "email",
                "text": "first",
            },
        },
    }
    event2 = {
        "type": "message.received",
        "data": {
            "customer_id": "cus_2",
            "agent_id": "agt_2",
            "message": {
                "id": "2",
                "conversation_id": "conv1",
                "connection_id": "cn2",
                "channel": "email",
                "text": "second",
            },
        },
    }
    client._route_event(event1)
    client._route_event(event2)

    while len(processed) < 1:
        time.sleep(0.01)

    assert processed == ["first"]
def test_parallel_processes_overlapping_messages():
    processed = []

    client = CommClient(api_key="test")
    client._concurrency = "parallel"

    def handler(message):
        time.sleep(0.05)
        processed.append(message.text)

    client.on_message(handler)

    event1 = {
        "type": "message.received",
        "data": {
            "customer_id": "cus_1",
            "agent_id": "agt_1",
            "message": {
                "id": "1",
                "conversation_id": "conv1",
                "connection_id": "cn1",
                "channel": "email",
                "text": "first",
            },
        },
    }

    event2 = {
        "type": "message.received",
        "data": {
            "customer_id": "cus_2",
            "agent_id": "agt_2",
            "message": {
                "id": "2",
                "conversation_id": "conv1",
                "connection_id": "cn2",
                "channel": "email",
                "text": "second",
            },
        },
    }
    client._route_event(event1)
    client._route_event(event2)

    while len(processed) < 2:
        time.sleep(0.01)

    assert sorted(processed) == ["first", "second"]