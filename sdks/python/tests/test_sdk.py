"""Client-level tests against a mock HTTP transport (no gateway needed)."""

import asyncio
import json
import threading

import httpx
import pytest
from caspian_sdk import (
    AccountRequiredError,
    CommClient,
    CommError,
    InsufficientCreditError,
    WebhookResult,
    WebhookVerificationError,
)
from caspian_sdk.client import _MessageScheduler

API_KEY = "comm_test_key"


def _client(handler) -> CommClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://gw.test")
    return CommClient(api_key=API_KEY, base_url="http://gw.test", http=http)


def _message_event(seq: int, conversation_id: str, text: str) -> dict:
    return {
        "seq": seq,
        "type": "message.received",
        "data": {
            "message": {
                "id": f"msg_{seq}",
                "conversation_id": conversation_id,
                "connection_id": "conn_1",
                "text": text,
            }
        },
    }


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


def test_error_with_non_object_json_body_maps_to_comm_error():
    # A proxy/gateway can answer with valid JSON that isn't an object; that
    # must surface as a CommError, not an AttributeError from `.get`.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json=["gateway error"])

    client = _client(handler)
    with pytest.raises(CommError) as excinfo:
        try:
            client.connect_telegram(bot_token="123:abc")
        finally:
            client.close()
    assert excinfo.value.status_code == 502
    assert "gateway error" in str(excinfo.value)


def test_account_required_maps_from_401():
    """A 401 with reason=account_required raises the typed AccountRequiredError,
    carrying the sign-in message and raw login_options for callers to react."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "detail": {
                    "reason": "account_required",
                    "message": "Sign in to use paid channels.",
                    "login_options": [{"start": "/v1/auth/device/start"}],
                }
            },
        )

    client = _client(handler)
    with pytest.raises(AccountRequiredError) as excinfo:
        try:
            client.connect_x(access_token="a", user_id="1")
        finally:
            client.close()
    err = excinfo.value
    assert isinstance(err, CommError)
    assert err.status_code == 401
    assert err.reason == "account_required"
    assert err.detail == "Sign in to use paid channels."
    assert err.login_options == [{"start": "/v1/auth/device/start"}]


def test_insufficient_credit_maps_from_402():
    """A 402 with reason=insufficient_credit raises InsufficientCreditError with
    the structured balance and payment_options the gateway returns."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            402,
            json={
                "detail": {
                    "reason": "insufficient_credit",
                    "message": "Out of credit.",
                    "balance_cents": 42,
                    "payment_options": [
                        {"url": "https://pay/1", "create": {"body": {"amount_cents": 5000}}}
                    ],
                }
            },
        )

    client = _client(handler)
    with pytest.raises(InsufficientCreditError) as excinfo:
        try:
            client.reply("m1", text="hi")
        finally:
            client.close()
    err = excinfo.value
    assert isinstance(err, CommError)
    assert err.status_code == 402
    assert err.reason == "insufficient_credit"
    assert err.detail == "Out of credit."
    assert err.balance_cents == 42
    assert err.payment_options[0]["url"] == "https://pay/1"


def test_monthly_cap_reached_maps_from_429():
    """A 429 spend-cap block also raises InsufficientCreditError (429 shares the
    typed billing error with 402), preserving the 429 status code."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"detail": {"reason": "monthly_cap_reached", "message": "Capped."}},
        )

    client = _client(handler)
    with pytest.raises(InsufficientCreditError) as excinfo:
        try:
            client.reply("m1", text="hi")
        finally:
            client.close()
    err = excinfo.value
    assert err.status_code == 429
    assert err.reason == "monthly_cap_reached"
    assert err.detail == "Capped."


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


def test_connect_telegram_waits_for_provisioning():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        calls.append((request.method, request.url.path, body))
        if request.method == "POST":
            return httpx.Response(
                201,
                json={"id": "conn_tg", "status": "provisioning", "address": None},
            )
        return httpx.Response(
            200,
            json={
                "id": "conn_tg",
                "status": "active",
                "address": "@acme_support_bot",
            },
        )

    client = _client(handler)
    try:
        connection = client.connect_telegram(
            bot_token="123456:ABC-DEF",
            display_name="Acme Telegram Support",
            poll_interval=0.01,
        )
    finally:
        client.close()
    assert connection["status"] == "active"
    assert connection["address"] == "@acme_support_bot"
    assert calls[0][0] == "POST"
    assert calls[0][1] == "/v1/connections/telegram"
    assert calls[0][2]["bot_token"] == "123456:ABC-DEF"
    assert calls[0][2]["display_name"] == "Acme Telegram Support"
    assert ("GET", "/v1/connections/conn_tg", {}) in [
        (m, p, b) for m, p, b in calls
    ]


def test_connect_no_wait_returns_immediately():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": "conn_2", "status": "provisioning"})

    client = _client(handler)
    try:
        connection = client.connect_email(wait=False)
    finally:
        client.close()
    assert connection["status"] == "provisioning"


def test_connect_and_install_github_use_expected_contract():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            201,
            json={
                "id": "conn_gh",
                "status": "pending_oauth",
                "authorize_url": "https://github.com/apps/caspian/installations/new",
            },
        )

    client = _client(handler)
    try:
        connected = client.connect_github(
            github_app_id="123",
            github_app_slug="my-app",
            github_private_key="pem",
            github_webhook_secret="secret",
            customer_id="cus_1",
        )
        installed = client.install_github(display_name="Review Agent")
    finally:
        client.close()

    assert connected["status"] == "pending_oauth"
    assert installed["authorize_url"].startswith("https://github.com/apps/")
    assert seen[0][0] == "/v1/connections/github"
    assert seen[0][1]["github_app_slug"] == "my-app"
    assert seen[0][1]["receive_mode"] == "mentions"
    assert seen[1][0] == "/v1/connections/github/install"
    assert seen[1][1]["display_name"] == "Review Agent"


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


def test_message_carries_chat_type_to_handler():
    """A Slack DM and a channel message differ only by chat_type, so the field
    has to survive onto Message for a handler to tell them apart."""
    events = [
        {
            "seq": 1,
            "type": "message.received",
            "data": {
                "customer_id": "cus_1", "agent_id": "agt_1",
                "message": {
                    "id": "m1", "conversation_id": "c1", "connection_id": "cn1",
                    "channel": "slack", "text": "ping", "chat_type": "dm",
                },
            },
        },
        {
            "seq": 2,
            "type": "message.received",
            "data": {
                "customer_id": "cus_1", "agent_id": "agt_1",
                "message": {
                    "id": "m2", "conversation_id": "c2", "connection_id": "cn1",
                    "channel": "slack", "text": "lunch?", "chat_type": "channel",
                },
            },
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/events":
            after = int(dict(request.url.params).get("after_seq", 0))
            return httpx.Response(200, json=[] if after >= 2 else events)
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    seen = []
    client.on_message(lambda m: seen.append(m))
    try:
        client.dispatch_pending(0)
    finally:
        client.close()
    assert [m.chat_type for m in seen] == ["dm", "channel"]


def test_message_chat_type_defaults_to_none_when_absent():
    """Channels with no DM/group distinction (email) omit chat_type entirely;
    the field must default rather than raise."""
    events = [
        {
            "seq": 1,
            "type": "message.received",
            "data": {
                "customer_id": "cus_1", "agent_id": "agt_1",
                "message": {
                    "id": "m1", "conversation_id": "c1", "connection_id": "cn1",
                    "channel": "email", "text": "hello",
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
    assert seen[0].chat_type is None


def test_dispatch_pending_skips_malformed_events_and_keeps_draining():
    """A record without a usable payload is skipped; the rest of the batch
    still dispatches and the cursor advances (data is optional in the schema)."""
    events = [
        {"seq": 1, "type": "message.received", "data": {}},
        {"seq": 2, "type": "interaction.received"},
        {"seq": 3, "type": "reaction.received", "data": None},
        _message_event(4, "conv_1", "still alive"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/events":
            after = int(dict(request.url.params).get("after_seq", 0))
            return httpx.Response(200, json=[] if after >= 4 else events)
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    seen = []
    client.on_message(lambda m: seen.append(m.text))
    client.on_interaction(lambda i: seen.append("interaction"))
    client.on_reaction(lambda r: seen.append("reaction"))
    try:
        last = client.dispatch_pending(0)
    finally:
        client.close()
    assert last == 4
    assert seen == ["still alive"]


def test_scheduler_contains_dispatch_errors_for_non_message_events():
    """listen() hands non-message events straight to dispatch; an error there
    must be swallowed so it cannot kill the polling loop."""

    def boom(event: dict) -> None:
        raise RuntimeError("boom")

    scheduler = _MessageScheduler(boom, "queue", 500)
    try:
        scheduler.submit({"seq": 1, "type": "interaction.received"})
    finally:
        scheduler.close()


def test_queue_serializes_each_conversation_and_keeps_others_moving():
    client = _client(lambda request: httpx.Response(200, json={}))
    first_started = threading.Event()
    release_first = threading.Event()
    other_finished = threading.Event()
    seen = []

    @client.on_message
    def handle(message):
        if message.text == "first":
            first_started.set()
            release_first.wait(timeout=1)
        seen.append(message.text)
        if message.text == "other":
            other_finished.set()

    scheduler = _MessageScheduler(client._dispatch_event, "queue", 500)
    try:
        scheduler.submit(_message_event(1, "conv_1", "first"))
        assert first_started.wait(timeout=1)
        scheduler.submit(_message_event(2, "conv_1", "second"))
        scheduler.submit(_message_event(3, "conv_2", "other"))
        assert other_finished.wait(timeout=1)
        release_first.set()
        scheduler.close()
    finally:
        release_first.set()
        client.close()

    assert seen == ["other", "first", "second"]


def test_listen_uses_queue_by_default():
    client = _client(lambda request: httpx.Response(200, json={}))
    release_first = threading.Event()
    seen = []
    polls = 0

    def events(**kwargs):
        nonlocal polls
        polls += 1
        if polls == 1:
            return [
                _message_event(1, "conv_1", "first"),
                _message_event(2, "conv_1", "second"),
            ]
        release_first.set()
        raise KeyboardInterrupt

    @client.on_message
    def handle(message):
        if message.text == "first":
            release_first.wait(timeout=1)
        seen.append(message.text)

    client.events = events
    try:
        with pytest.raises(KeyboardInterrupt):
            client.listen(from_seq=0, poll_interval=0)
    finally:
        release_first.set()
        client.close()

    assert seen == ["first", "second"]


def test_queue_continues_after_handler_error():
    client = _client(lambda request: httpx.Response(200, json={}))
    seen = []

    @client.on_message
    def handle(message):
        if message.text == "bad":
            raise RuntimeError("boom")
        seen.append(message.text)

    scheduler = _MessageScheduler(client._dispatch_event, "queue", 500)
    try:
        scheduler.submit(_message_event(1, "conv_1", "bad"))
        scheduler.submit(_message_event(2, "conv_1", "good"))
        scheduler.close()
    finally:
        client.close()

    assert seen == ["good"]


def test_debounce_keeps_only_the_latest_message():
    client = _client(lambda request: httpx.Response(200, json={}))
    latest_started = threading.Event()
    release_latest = threading.Event()
    after_handled = threading.Event()
    seen = []

    @client.on_message
    def handle(message):
        if message.text == "latest":
            latest_started.set()
            release_latest.wait(timeout=1)
        seen.append(message.text)
        if message.text == "after":
            after_handled.set()

    scheduler = _MessageScheduler(client._dispatch_event, "debounce", 10)
    try:
        scheduler.submit(_message_event(1, "conv_1", "first"))
        scheduler.submit(_message_event(2, "conv_1", "second"))
        scheduler.submit(_message_event(3, "conv_1", "latest"))
        assert latest_started.wait(timeout=1)
        scheduler.submit(_message_event(4, "conv_1", "after"))
        release_latest.set()
        assert after_handled.wait(timeout=1)
        scheduler.close()
    finally:
        release_latest.set()
        client.close()

    assert seen == ["latest", "after"]


def test_drop_ignores_messages_while_a_handler_is_running():
    client = _client(lambda request: httpx.Response(200, json={}))
    started = threading.Event()
    release = threading.Event()
    seen = []

    @client.on_message
    def handle(message):
        started.set()
        release.wait(timeout=1)
        seen.append(message.text)

    scheduler = _MessageScheduler(client._dispatch_event, "drop", 500)
    try:
        scheduler.submit(_message_event(1, "conv_1", "first"))
        assert started.wait(timeout=1)
        scheduler.submit(_message_event(2, "conv_1", "second"))
        scheduler.submit(_message_event(3, "conv_1", "third"))
        release.set()
        scheduler.close()
    finally:
        release.set()
        client.close()

    assert seen == ["first"]


def test_parallel_allows_handlers_for_one_conversation_to_overlap():
    client = _client(lambda request: httpx.Response(200, json={}))
    first_started = threading.Event()
    second_finished = threading.Event()
    release_first = threading.Event()
    seen = []

    @client.on_message
    def handle(message):
        if message.text == "first":
            first_started.set()
            release_first.wait(timeout=1)
        seen.append(message.text)
        if message.text == "second":
            second_finished.set()

    scheduler = _MessageScheduler(client._dispatch_event, "parallel", 500)
    try:
        scheduler.submit(_message_event(1, "conv_1", "first"))
        assert first_started.wait(timeout=1)
        scheduler.submit(_message_event(2, "conv_1", "second"))
        assert second_finished.wait(timeout=1)
        release_first.set()
        scheduler.close()
    finally:
        release_first.set()
        client.close()

    assert set(seen) == {"first", "second"}


def test_listen_rejects_invalid_overlap_options():
    client = _client(lambda request: httpx.Response(200, json=[]))
    try:
        with pytest.raises(ValueError, match="concurrency"):
            client.listen(from_seq=0, concurrency="invalid")
        with pytest.raises(ValueError, match="debounce_ms"):
            client.listen(from_seq=0, debounce_ms=-1)
    finally:
        client.close()


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


def test_stream_post_edit_on_telegram():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"id": "out_1", "delivered": True})

    client = _client(handler)
    from caspian_sdk import Message

    msg = Message(
        id="msg_1", conversation_id="c1", connection_id="cn1",
        customer_id="cus_1", agent_id="agt_1", channel="telegram",
        sender=None, subject=None, text="hi", html=None, media=[], _client=client,
    )
    try:
        with msg.stream(throttle=0) as s:
            s.append("Hello")
            s.append(" world")
    finally:
        client.close()
    # first append → reply, second append → edit (throttle=0), exit → final edit
    assert bodies[0][1] == "/v1/messages/msg_1/reply"
    assert bodies[0][2]["text"] == "Hello"
    edits = [b for b in bodies if "/edit" in b[1]]
    assert len(edits) >= 1
    assert edits[-1][2]["text"] == "Hello world"


def test_stream_fallback_on_email():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"id": "out_1", "delivered": True})

    client = _client(handler)
    from caspian_sdk import Message

    msg = Message(
        id="msg_1", conversation_id="c1", connection_id="cn1",
        customer_id="cus_1", agent_id="agt_1", channel="email",
        sender=None, subject=None, text="hi", html=None, media=[], _client=client,
    )
    try:
        with msg.stream() as s:
            s.append("one ")
            assert len(bodies) == 0  # nothing sent yet
            s.append("two")
    finally:
        client.close()
    assert len(bodies) == 1
    assert bodies[0][0] == "/v1/messages/msg_1/reply"
    assert bodies[0][1]["text"] == "one two"


def test_stream_error_midstream():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"id": "out_1", "delivered": True})

    client = _client(handler)
    from caspian_sdk import Message

    msg = Message(
        id="msg_1", conversation_id="c1", connection_id="cn1",
        customer_id="cus_1", agent_id="agt_1", channel="email",
        sender=None, subject=None, text="hi", html=None, media=[], _client=client,
    )
    try:
        try:
            with msg.stream() as s:
                s.append("partial")
                raise ValueError("boom")
        except ValueError:
            pass
    finally:
        client.close()
    # partial text still delivered on exit
    assert len(bodies) == 1
    assert bodies[0][1]["text"] == "partial"


def test_stream_empty():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.url.path)
        return httpx.Response(200, json={"id": "out_1"})

    client = _client(handler)
    from caspian_sdk import Message

    msg = Message(
        id="msg_1", conversation_id="c1", connection_id="cn1",
        customer_id="cus_1", agent_id="agt_1", channel="telegram",
        sender=None, subject=None, text="hi", html=None, media=[], _client=client,
    )
    try:
        with msg.stream() as _:
            pass  # no appends
    finally:
        client.close()
    assert len(bodies) == 0  # nothing sent


def test_stream_no_double_send_on_reply_failure():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if "/reply" in request.url.path:
            raise httpx.ConnectError("simulated timeout")
        return httpx.Response(200, json={"id": "out_1"})

    client = _client(handler)
    from caspian_sdk import Message

    msg = Message(
        id="msg_1", conversation_id="c1", connection_id="cn1",
        customer_id="cus_1", agent_id="agt_1", channel="telegram",
        sender=None, subject=None, text="hi", html=None, media=[], _client=client,
    )
    try:
        with pytest.raises(httpx.ConnectError):
            with msg.stream() as s:
                s.append("hello")
    finally:
        client.close()
    reply_calls = [c for c in calls if "/reply" in c]
    assert len(reply_calls) == 1


def test_stream_final_flush_skips_when_unchanged():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"id": "out_1", "delivered": True})

    client = _client(handler)
    from caspian_sdk import Message

    msg = Message(
        id="msg_1", conversation_id="c1", connection_id="cn1",
        customer_id="cus_1", agent_id="agt_1", channel="telegram",
        sender=None, subject=None, text="hi", html=None, media=[], _client=client,
    )
    try:
        with msg.stream(throttle=0) as s:
            s.append("Hello")
            s.append(" world")
    finally:
        client.close()
    edit_calls = [b for b in bodies if "/edit" in b[0]]
    final_edit = edit_calls[-1]
    assert final_edit[1]["text"] == "Hello world"
    all_texts = [b[1]["text"] for b in bodies]
    assert all_texts[-1] != all_texts[-2] or len(edit_calls) == 1


def test_stream_raises_when_reply_returns_no_id():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"delivered": True})

    client = _client(handler)
    from caspian_sdk import Message

    msg = Message(
        id="msg_1", conversation_id="c1", connection_id="cn1",
        customer_id="cus_1", agent_id="agt_1", channel="telegram",
        sender=None, subject=None, text="hi", html=None, media=[], _client=client,
    )
    try:
        with pytest.raises(RuntimeError, match="no message id"):
            with msg.stream(throttle=0) as s:
                s.append("a")
                s.append("b")
                s.append("c")
    finally:
        client.close()
    reply_calls = [c for c in calls if "/reply" in c]
    assert len(reply_calls) == 1


def test_stream_retries_edit_when_message_not_sent_yet():
    """When edit() gets the specific 'not sent yet' 400, retry with backoff."""
    calls = []
    edit_attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if "/reply" in request.url.path:
            return httpx.Response(200, json={"id": "out_1", "delivered": True})
        # First edit attempt fails with the race condition error
        if "/edit" in request.url.path:
            edit_attempts.append(1)
            if len(edit_attempts) == 1:
                return httpx.Response(
                    400, json={"detail": "Can only edit an outbound message that was sent"}
                )
            # Second attempt succeeds
            return httpx.Response(200, json={"id": "out_1", "delivered": True})
        return httpx.Response(200, json={})

    client = _client(handler)
    from caspian_sdk import Message

    msg = Message(
        id="msg_1", conversation_id="c1", connection_id="cn1",
        customer_id="cus_1", agent_id="agt_1", channel="telegram",
        sender=None, subject=None, text="hi", html=None, media=[], _client=client,
    )
    try:
        with msg.stream(throttle=0) as s:
            s.append("Hello")
            s.append(" world")  # This triggers the edit which will retry
    finally:
        client.close()
    
    # Should have 1 reply + 2 edit attempts (first fails, second succeeds)
    assert len(edit_attempts) == 2
    edit_calls = [c for c in calls if "/edit" in c[1]]
    assert len(edit_calls) == 2


def test_stream_does_not_retry_different_400_errors():
    """A different 400 error should propagate immediately, not retry."""
    calls = []
    edit_attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if "/reply" in request.url.path:
            return httpx.Response(200, json={"id": "out_1", "delivered": True})
        if "/edit" in request.url.path:
            edit_attempts.append(1)
            # Different 400 error (not the race condition)
            return httpx.Response(400, json={"detail": "Message already deleted"})
        return httpx.Response(200, json={})

    client = _client(handler)
    from caspian_sdk import Message

    msg = Message(
        id="msg_1", conversation_id="c1", connection_id="cn1",
        customer_id="cus_1", agent_id="agt_1", channel="telegram",
        sender=None, subject=None, text="hi", html=None, media=[], _client=client,
    )
    try:
        with pytest.raises(CommError) as excinfo:
            with msg.stream(throttle=0) as s:
                s.append("Hello")
                s.append(" world")  # This triggers edit which will fail
    finally:
        client.close()
    
    # Should fail with the specific error, and only attempt once per call (no retry)
    assert excinfo.value.status_code == 400
    assert "already deleted" in excinfo.value.detail
    # Two edit attempts total (one from append, one from flush); each tries once (no retries)
    assert len(edit_attempts) == 2


def test_channel_cap_reached_maps_from_429():
    """A 429 channel cap block raises InsufficientCreditError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"detail": {"reason": "channel_cap_reached", "message": "Capped."}},
        )

    client = _client(handler)
    with pytest.raises(InsufficientCreditError) as excinfo:
        try:
            client.reply("m1", text="hi")
        finally:
            client.close()
    err = excinfo.value
    assert err.status_code == 429
    assert err.reason == "channel_cap_reached"
    assert err.detail == "Capped."


def test_account_required_defaults_when_gateway_omits_fields():
    """When 401 reason=account_required omits message and login_options, defaults are populated."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"detail": {"reason": "account_required"}},
        )

    client = _client(handler)
    with pytest.raises(AccountRequiredError) as excinfo:
        try:
            client.connect_x(access_token="a", user_id="1")
        finally:
            client.close()
    err = excinfo.value
    assert err.message == "Sign in to Caspian to use paid channels."
    assert err.detail == "Sign in to Caspian to use paid channels."
    assert err.login_options == []


def test_insufficient_credit_defaults_when_gateway_omits_fields():
    """When 402 reason=insufficient_credit omits message/balance/payment_options, defaults apply."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            402,
            json={"detail": {"reason": "insufficient_credit"}},
        )

    client = _client(handler)
    with pytest.raises(InsufficientCreditError) as excinfo:
        try:
            client.reply("m1", text="hi")
        finally:
            client.close()
    err = excinfo.value
    assert err.message == "Out of Caspian credit."
    assert err.detail == "Out of Caspian credit."
    assert err.balance_cents is None
    assert err.payment_options == []


def test_401_unrecognized_reason_falls_through_to_comm_error():
    """A 401 with an unrecognized reason raises plain CommError, not AccountRequiredError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"detail": {"reason": "invalid_token", "message": "Invalid API key"}},
        )

    client = _client(handler)
    with pytest.raises(CommError) as excinfo:
        try:
            client.connect_x(access_token="a", user_id="1")
        finally:
            client.close()
    err = excinfo.value
    assert type(err) is CommError
    assert not isinstance(err, AccountRequiredError)
    assert err.status_code == 401
    assert "Invalid API key" in str(err)


def test_402_unrecognized_reason_falls_through_to_comm_error():
    """A 402 with an unrecognized reason raises plain CommError, not InsufficientCreditError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            402,
            json={"detail": {"reason": "payment_required_other", "message": "Payment needed"}},
        )

    client = _client(handler)
    with pytest.raises(CommError) as excinfo:
        try:
            client.reply("m1", text="hi")
        finally:
            client.close()
    err = excinfo.value
    assert type(err) is CommError
    assert not isinstance(err, InsufficientCreditError)
    assert err.status_code == 402
    assert "Payment needed" in str(err)


def test_account_required_login_delegates_to_client():
    """AccountRequiredError.login() delegates to CommClient.login()."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/connections/x":
            return httpx.Response(
                401,
                json={"detail": {"reason": "account_required", "message": "Sign in required"}},
            )
        if request.url.path == "/v1/auth/device/start":
            calls.append("start")
            return httpx.Response(
                200,
                json={
                    "device_code": "dev_123",
                    "verification_uri": "https://auth.example.com",
                    "interval": 0.01,
                },
            )
        if request.url.path == "/v1/auth/device/token":
            calls.append("token")
            return httpx.Response(200, json={"status": "approved"})
        return httpx.Response(404)

    client = _client(handler)
    try:
        with pytest.raises(AccountRequiredError) as excinfo:
            client.connect_x(access_token="a", user_id="1")
        res = excinfo.value.login(poll_interval=0.01)
    finally:
        client.close()
    assert res["status"] == "approved"
    assert calls == ["start", "token"]


def test_insufficient_credit_top_up_uses_explicit_amount():
    """InsufficientCreditError.top_up(amount_cents) passes explicit amount to CommClient."""
    topup_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/billing/topup":
            topup_calls.append(json.loads(request.content))
            return httpx.Response(200, json={"checkout_url": "https://stripe.com/pay"})
        return httpx.Response(
            402,
            json={"detail": {"reason": "insufficient_credit"}},
        )

    client = _client(handler)
    try:
        with pytest.raises(InsufficientCreditError) as excinfo:
            client.reply("m1", text="hi")
        res = excinfo.value.top_up(amount_cents=3500)
    finally:
        client.close()
    assert res["checkout_url"] == "https://stripe.com/pay"
    assert topup_calls == [{"amount_cents": 3500}]


def test_insufficient_credit_top_up_uses_suggested_amount_from_payment_options():
    """InsufficientCreditError.top_up() uses suggested amount from payment_options."""
    topup_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/billing/topup":
            topup_calls.append(json.loads(request.content))
            return httpx.Response(200, json={"checkout_url": "https://stripe.com/pay"})
        return httpx.Response(
            402,
            json={
                "detail": {
                    "reason": "insufficient_credit",
                    "payment_options": [{"create": {"body": {"amount_cents": 5000}}}],
                }
            },
        )

    client = _client(handler)
    try:
        with pytest.raises(InsufficientCreditError) as excinfo:
            client.reply("m1", text="hi")
        res = excinfo.value.top_up()
    finally:
        client.close()
    assert res["checkout_url"] == "https://stripe.com/pay"
    assert topup_calls == [{"amount_cents": 5000}]


def test_insufficient_credit_top_up_fallback_when_no_payment_options():
    """InsufficientCreditError.top_up() falls back to 2000 cents when payment_options is empty."""
    topup_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/billing/topup":
            topup_calls.append(json.loads(request.content))
            return httpx.Response(200, json={"checkout_url": "https://stripe.com/pay"})
        return httpx.Response(
            402,
            json={"detail": {"reason": "insufficient_credit", "payment_options": []}},
        )

    client = _client(handler)
    try:
        with pytest.raises(InsufficientCreditError) as excinfo:
            client.reply("m1", text="hi")
        res = excinfo.value.top_up()
    finally:
        client.close()
    assert res["checkout_url"] == "https://stripe.com/pay"
    assert topup_calls == [{"amount_cents": 2000}]


def test_insufficient_credit_top_up_respects_explicit_zero():
    """InsufficientCreditError.top_up(amount_cents=0) must not be silently
    overridden to the 2000-cent default (a truthy check bug, not an is-None
    check, previously treated 0 the same as "not passed")."""
    topup_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/billing/topup":
            topup_calls.append(json.loads(request.content))
            return httpx.Response(200, json={"checkout_url": "https://stripe.com/pay"})
        return httpx.Response(
            402,
            json={"detail": {"reason": "insufficient_credit"}},
        )

    client = _client(handler)
    try:
        with pytest.raises(InsufficientCreditError) as excinfo:
            client.reply("m1", text="hi")
        res = excinfo.value.top_up(amount_cents=0)
    finally:
        client.close()
    assert res["checkout_url"] == "https://stripe.com/pay"
    assert topup_calls == [{"amount_cents": 0}]


def test_non_json_error_body_hits_value_error_fallback():
    """An error response with non-JSON text triggers ValueError on json() and falls back to text."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    client = _client(handler)
    with pytest.raises(CommError) as excinfo:
        try:
            client.connect_telegram(bot_token="123:abc")
        finally:
            client.close()
    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == "Internal Server Error"
    assert "Internal Server Error" in str(excinfo.value)


def test_handle_webhook_dispatches_message():
    import hashlib
    import hmac

    client = _client(lambda req: httpx.Response(200, json={}))

    seen = []

    @client.on_message
    def handle(msg):
        seen.append(msg)

    secret = "whsec_test123"
    payload = json.dumps(_message_event(1, "conv_1", "hello from webhook")).encode("utf-8")
    signature = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    # Missing signature raises error
    with pytest.raises(WebhookVerificationError, match="missing"):
        client.handle_webhook(payload, {}, secret)

    # Bad signature raises error
    with pytest.raises(WebhookVerificationError, match="mismatch"):
        client.handle_webhook(payload, {"x-caspian-signature": "sha256=invalid"}, secret)

    # Valid signature dispatches event
    res = client.handle_webhook(payload, {"x-caspian-signature": signature}, secret)
    assert res == WebhookResult(status="ok", event_id="1", event_type="message.received")
    assert len(seen) == 1
    assert seen[0].text == "hello from webhook"
    client.close()


def test_handle_webhook_dispatches_interaction_and_reaction():
    import hashlib
    import hmac

    client = _client(lambda req: httpx.Response(200, json={}))
    seen_interactions = []
    seen_reactions = []

    client.on_interaction(seen_interactions.append)
    client.on_reaction(seen_reactions.append)

    secret = "whsec_test123"
    event = {
        "id": "evt_interaction_1",
        "type": "interaction.received",
        "data": {
            "connection_id": "conn_1",
            "customer_id": "cus_1",
            "agent_id": "agt_1",
            "value": "btn_click",
        },
    }
    payload = json.dumps(event).encode("utf-8")
    sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    res = client.handle_webhook(payload, {"x-caspian-signature": sig}, secret)
    assert res.status == "ok"
    assert res.event_id == "evt_interaction_1"
    assert len(seen_interactions) == 1
    assert seen_interactions[0].value == "btn_click"
    client.close()


def test_handle_webhook_idempotent_same_event_id():
    import hashlib
    import hmac

    client = _client(lambda req: httpx.Response(200, json={}))
    seen = []

    @client.on_message
    def handle(msg):
        seen.append(msg)

    secret = "whsec_test123"
    evt = _message_event(1, "conv_1", "duplicate test")
    evt["id"] = "same_event_id"
    payload = json.dumps([evt, evt]).encode("utf-8")
    sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    res = client.handle_webhook(payload, {"X-Caspian-Signature": sig}, secret)
    assert res.status == "ok"
    assert len(seen) == 1
    client.close()


def test_handle_webhook_cross_invocation_dedup():
    """Duplicate event across two separate handle_webhook() calls is skipped."""
    import hashlib
    import hmac

    client = _client(lambda req: httpx.Response(200, json={}))
    seen = []

    @client.on_message
    def handle(msg):
        seen.append(msg)

    secret = "whsec_dedup_test"
    evt = _message_event(1, "conv_1", "dedup test")
    evt["id"] = "evt_cross_dedup_1"

    # First invocation — event should be dispatched.
    payload1 = json.dumps(evt).encode("utf-8")
    sig1 = "sha256=" + hmac.new(secret.encode(), payload1, hashlib.sha256).hexdigest()
    res1 = client.handle_webhook(payload1, {"x-caspian-signature": sig1}, secret)
    assert res1.status == "ok"
    assert len(seen) == 1

    # Second invocation with same event — should be suppressed by dedup cache.
    payload2 = json.dumps(evt).encode("utf-8")
    sig2 = "sha256=" + hmac.new(secret.encode(), payload2, hashlib.sha256).hexdigest()
    res2 = client.handle_webhook(payload2, {"x-caspian-signature": sig2}, secret)
    assert res2.status == "ignored"
    assert len(seen) == 1  # handler NOT called again
    client.close()


def test_handle_webhook_dedup_allows_different_events():
    """Different event IDs are dispatched normally (no false suppression)."""
    import hashlib
    import hmac

    client = _client(lambda req: httpx.Response(200, json={}))
    seen = []

    @client.on_message
    def handle(msg):
        seen.append(msg)

    secret = "whsec_diff_events"

    for i in range(3):
        evt = _message_event(i + 1, "conv_1", f"msg {i}")
        evt["id"] = f"evt_unique_{i}"
        payload = json.dumps(evt).encode("utf-8")
        sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        res = client.handle_webhook(payload, {"x-caspian-signature": sig}, secret)
        assert res.status == "ok"

    assert len(seen) == 3
    client.close()


def test_handle_webhook_records_dedup_only_after_successful_dispatch():
    """A handler exception must surface as status="error" and must NOT be
    recorded in the dedup cache, so the provider's retry of the same event
    reaches the handler again instead of being silently swallowed."""
    import hashlib
    import hmac

    client = _client(lambda req: httpx.Response(200, json={}))
    seen = []
    should_fail = True

    @client.on_message
    def handle(msg):
        if should_fail:
            raise RuntimeError("boom")
        seen.append(msg)

    secret = "whsec_retry_test"
    evt = _message_event(1, "conv_1", "retry me")
    evt["id"] = "evt_retry_1"
    payload = json.dumps(evt).encode("utf-8")
    sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    # First delivery: handler raises -> must report status="error" and must
    # NOT be recorded as processed.
    res1 = client.handle_webhook(payload, {"x-caspian-signature": sig}, secret)
    assert res1.status == "error"
    assert seen == []

    # Provider retries the same event (same id, same payload). Because the
    # first attempt was not recorded, the handler must run again.
    should_fail = False
    res2 = client.handle_webhook(payload, {"x-caspian-signature": sig}, secret)
    assert res2.status == "ok"
    assert len(seen) == 1

    # A third delivery of the now-successfully-processed event is deduped.
    res3 = client.handle_webhook(payload, {"x-caspian-signature": sig}, secret)
    assert res3.status == "ignored"
    assert len(seen) == 1
    client.close()


# --- async handlers ----------------------------------------------------------


def _draining_events_transport(events, on_request=None):
    """Serve ``events`` once from /v1/events, then an empty batch; 200 elsewhere."""
    last_seq = events[-1]["seq"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/events":
            after = int(dict(request.url.params).get("after_seq", 0))
            return httpx.Response(200, json=[] if after >= last_seq else events)
        if on_request is not None:
            on_request(request)
        return httpx.Response(200, json={"delivered": True})

    return handler


def test_async_handler_is_awaited_and_can_reply():
    replies = []

    def record(request: httpx.Request) -> None:
        if request.url.path.endswith("/reply"):
            replies.append(json.loads(request.content))

    client = _client(_draining_events_transport([_message_event(1, "conv_1", "hello")], record))
    seen: list[str] = []

    @client.on_message
    async def handle(message) -> None:
        await asyncio.sleep(0.01)
        seen.append(message.text)
        message.reply(f"echo {message.text}")

    try:
        client.dispatch_pending(0)
    finally:
        client.close()

    # The coroutine ran to completion before dispatch_pending returned.
    assert seen == ["hello"]
    assert [r["text"] for r in replies] == ["echo hello"]


def test_sync_and_async_handlers_run_in_registration_order():
    client = _client(_draining_events_transport([_message_event(1, "conv_1", "order")]))
    calls: list[str] = []

    @client.on_message
    def first(message) -> None:
        calls.append("sync-first")

    @client.on_message
    async def second(message) -> None:
        calls.append("async-second")

    @client.on_message
    def third(message) -> None:
        calls.append("sync-third")

    try:
        client.dispatch_pending(0)
    finally:
        client.close()

    assert calls == ["sync-first", "async-second", "sync-third"]


def test_async_handler_error_is_contained_and_later_handlers_still_run():
    client = _client(_draining_events_transport([_message_event(1, "conv_1", "boom")]))
    seen: list[str] = []

    @client.on_message
    async def bad(message) -> None:
        raise RuntimeError("async handler failure")

    @client.on_message
    async def good(message) -> None:
        seen.append(message.text)

    try:
        client.dispatch_pending(0)  # must not raise
    finally:
        client.close()

    assert seen == ["boom"]


def test_async_handlers_share_one_background_event_loop():
    events = [_message_event(1, "conv_1", "first"), _message_event(2, "conv_1", "second")]
    client = _client(_draining_events_transport(events))
    loops = []

    @client.on_message
    async def handle(message) -> None:
        loops.append(asyncio.get_running_loop())

    try:
        client.dispatch_pending(0)
    finally:
        client.close()

    assert len(loops) == 2
    assert loops[0] is loops[1]


def test_on_interaction_accepts_async_handler():
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
    client = _client(_draining_events_transport(events))
    values: list[str] = []

    @client.on_interaction
    async def handle(interaction) -> None:
        await asyncio.sleep(0)
        values.append(interaction.value)

    try:
        client.dispatch_pending(0)
    finally:
        client.close()

    assert values == ["reorder_123"]
