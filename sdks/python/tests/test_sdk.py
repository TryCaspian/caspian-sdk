"""Client-level tests against a mock HTTP transport (no gateway needed)."""

import json
import threading

import httpx
import pytest
from caspian_sdk import (
    AccountRequiredError,
    CommClient,
    CommError,
    InsufficientCreditError,
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
    assert bodies[0][1] == {"text": "Order shipped", "html": None, "blocks": payload, "media": None}
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
                "connection_id": "conn_1",
                "customer_id": "cus_1",
                "agent_id": "agt_1",
                "conversation_id": "conv_1",
                "value": "reorder_123",
                "source_message": {"id": "msg_9"},
                "sender": {"address": "u"},
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
                "connection_id": "conn_1",
                "customer_id": "cus_1",
                "agent_id": "agt_1",
                "emoji": "thumbsup",
                "action": "added",
                "source_message": {"id": "msg_9"},
                "sender": {"address": "u"},
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
                "customer_id": "cus_1",
                "agent_id": "agt_1",
                "message": {
                    "id": "m1",
                    "conversation_id": "c1",
                    "connection_id": "cn1",
                    "channel": "email",
                    "text": "see attached",
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
