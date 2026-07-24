"""Client-level tests against a mock HTTP transport (no gateway needed)."""

import json

import httpx
import pytest
from caspian_sdk import (
    AccountRequiredError,
    CommClient,
    CommError,
    InsufficientCreditError,
    Message,
    MessageStream,
)

API_KEY = "comm_test_key"


def _client(handler) -> CommClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://gw.test")
    return CommClient(api_key=API_KEY, base_url="http://gw.test", http=http)


# --------------------------------------------------------------------------- #
# Auth & basic request shape
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #

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


def test_account_required_error():
    """401 with a structured account_required payload raises the typed error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={
            "detail": {
                "reason": "account_required",
                "message": "Sign in to Caspian to use paid channels.",
                "login_options": [{"url": "https://auth.example.com"}],
            }
        })

    client = _client(handler)
    try:
        with pytest.raises(AccountRequiredError) as excinfo:
            client.create_customer("Acme")
        assert excinfo.value.status_code == 401
        assert excinfo.value.reason == "account_required"
        assert "Sign in" in excinfo.value.message
        assert excinfo.value.login_options == [{"url": "https://auth.example.com"}]
    finally:
        client.close()


def test_insufficient_credit_error():
    """402/429 with a structured billing payload raises the typed error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={
            "detail": {
                "reason": "insufficient_credit",
                "message": "Out of Caspian credit.",
                "balance_cents": 0,
                "payment_options": [{"create": {"body": {"amount_cents": 2000}}}],
            }
        })

    client = _client(handler)
    try:
        with pytest.raises(InsufficientCreditError) as excinfo:
            client.create_customer("Acme")
        assert excinfo.value.status_code == 402
        assert excinfo.value.reason == "insufficient_credit"
        assert excinfo.value.balance_cents == 0
    finally:
        client.close()


def test_insufficient_credit_error_top_up():
    """The error shortcut delegates to the client's top-up endpoint."""

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        calls.append((request.method, request.url.path, body))
        if request.url.path == "/v1/billing/topup":
            return httpx.Response(200, json={"checkout_url": "https://stripe.example.com"})
        return httpx.Response(402, json={
            "detail": {
                "reason": "insufficient_credit",
                "message": "Out of Caspian credit.",
                "balance_cents": 0,
                "payment_options": [{"create": {"body": {"amount_cents": 5000}}}],
            }
        })

    client = _client(handler)
    try:
        with pytest.raises(InsufficientCreditError) as excinfo:
            client.create_customer("Acme")
        result = excinfo.value.top_up()
        assert result["checkout_url"] == "https://stripe.example.com"
        # Should pick the amount from the gateway's payment_options suggestion
        assert calls[-1] == ("POST", "/v1/billing/topup", {"amount_cents": 5000})
    finally:
        client.close()


# --------------------------------------------------------------------------- #
# Connection provisioning
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# Replies, blocks & media
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# Reactions & typing
# --------------------------------------------------------------------------- #

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


def test_typing_hits_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["method"] = request.method
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    try:
        client.typing("msg_1")
    finally:
        client.close()
    assert seen["path"] == "/v1/messages/msg_1/typing"
    assert seen["method"] == "POST"


# --------------------------------------------------------------------------- #
# Event dispatch (message / interaction / reaction)
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# Behaviour guides
# --------------------------------------------------------------------------- #

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


def test_channel_guide_returns_text():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/channels/slack/guide"
        return httpx.Response(200, text="Use threads.")

    client = _client(handler)
    try:
        guide = client.channel_guide("slack")
    finally:
        client.close()
    assert "threads" in guide


# --------------------------------------------------------------------------- #
# Streaming API (new)
# --------------------------------------------------------------------------- #

def test_message_stream_edit_mode():
    """Native edit-mode streaming: open → append → flush → finalize."""

    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, body))
        if request.url.path == "/v1/messages/msg_1/stream" and request.method == "POST":
            return httpx.Response(200, json={"mode": "edit", "stream_id": "stream_1"})
        if request.url.path == "/v1/streams/stream_1" and request.method == "PATCH":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/v1/streams/stream_1/finalize" and request.method == "POST":
            return httpx.Response(200, json={"ok": True, "message_id": "msg_final"})
        return httpx.Response(404)

    client = _client(handler)
    try:
        stream = MessageStream(client, "msg_1")
        stream.append("Hello")
        stream.append(" world")
        # Cancel the debounce timer and flush manually so the test stays fast
        if stream._timer:
            stream._timer.cancel()
        stream._flush()
        result = stream.finalize()
    finally:
        client.close()

    assert result == {"ok": True, "message_id": "msg_final"}
    assert requests[0] == ("POST", "/v1/messages/msg_1/stream", {})
    # First append triggers an immediate flush in edit mode
    assert requests[1] == ("PATCH", "/v1/streams/stream_1", {"text": "Hello"})
    # Manual flush after second append
    assert requests[2] == ("PATCH", "/v1/streams/stream_1", {"text": "Hello world"})
    assert requests[3] == ("POST", "/v1/streams/stream_1/finalize", {"text": "Hello world"})


def test_message_stream_final_mode():
    """Channels that only support final replies fall back to a single post."""

    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, body))
        if request.url.path == "/v1/messages/msg_1/stream" and request.method == "POST":
            return httpx.Response(200, json={"mode": "final"})
        if request.url.path == "/v1/messages/msg_1/reply" and request.method == "POST":
            return httpx.Response(200, json={"delivered": True})
        return httpx.Response(404)

    client = _client(handler)
    try:
        stream = MessageStream(client, "msg_1")
        stream.append("Hello")
        stream.append(" world")
        result = stream.finalize()
    finally:
        client.close()

    assert result == {"delivered": True}
    assert requests[0] == ("POST", "/v1/messages/msg_1/stream", {})
    # No intermediate PATCH requests — only the final reply
    assert requests[1] == (
        "POST",
        "/v1/messages/msg_1/reply",
        {"text": "Hello world", "html": None, "blocks": None, "media": None},
    )


def test_message_stream_context_manager():
    """The context manager calls finalize on exit."""

    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, body))
        if request.url.path == "/v1/messages/msg_1/stream" and request.method == "POST":
            return httpx.Response(200, json={"mode": "edit", "stream_id": "stream_1"})
        if request.url.path == "/v1/streams/stream_1" and request.method == "PATCH":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/v1/streams/stream_1/finalize" and request.method == "POST":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    client = _client(handler)
    try:
        with MessageStream(client, "msg_1") as stream:
            stream.append("Hello")
    finally:
        client.close()

    assert requests[-1] == ("POST", "/v1/streams/stream_1/finalize", {"text": "Hello"})


def test_message_stream_error_on_open():
    """Errors during stream opening are propagated as CommError."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/messages/msg_1/stream":
            return httpx.Response(500, json={"detail": "gateway error"})
        return httpx.Response(404)

    client = _client(handler)
    try:
        stream = MessageStream(client, "msg_1")
        with pytest.raises(CommError) as excinfo:
            stream.append("Hello")
        assert excinfo.value.status_code == 500
        assert "gateway error" in str(excinfo.value)
    finally:
        client.close()


def test_message_stream_finalize_without_append():
    """Finalizing a never-used stream is a no-op."""

    client = _client(lambda r: httpx.Response(404))
    try:
        stream = MessageStream(client, "msg_1")
        assert stream.finalize() is None
    finally:
        client.close()


def test_message_stream_via_message():
    """Message.stream() returns a MessageStream bound to the right client + id."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"mode": "edit", "stream_id": "stream_1"})

    client = _client(handler)
    try:
        message = Message(
            id="msg_1",
            conversation_id="c1",
            connection_id="cn1",
            customer_id="cus_1",
            agent_id="agt_1",
            channel="slack",
            sender=None,
            subject=None,
            text="hi",
            html=None,
            _client=client,
        )
        stream = message.stream()
        assert stream._message_id == "msg_1"
        assert stream._client is client
    finally:
        client.close()


def test_message_typing_and_react():
    """Message convenience methods delegate to the client correctly."""

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        seen.append((request.method, request.url.path, body))
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    try:
        message = Message(
            id="msg_1",
            conversation_id="c1",
            connection_id="cn1",
            customer_id="cus_1",
            agent_id="agt_1",
            channel="slack",
            sender=None,
            subject=None,
            text="hi",
            html=None,
            _client=client,
        )
        message.typing()
        message.react("👍")
    finally:
        client.close()

    assert ("POST", "/v1/messages/msg_1/typing", {}) in seen
    assert ("POST", "/v1/messages/msg_1/react", {"emoji": "👍"}) in seen