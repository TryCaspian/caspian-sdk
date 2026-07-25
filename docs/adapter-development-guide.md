# Adapter Development Guide

> **Audience:** contributors adding a new channel adapter to the Caspian
> communication gateway.
>
> This guide consolidates the implementation patterns that are otherwise spread
> across `base.py`, `registry.py`, `config.py`, the `fakes/` directory, and
> the test suite. For the general contribution workflow (fork, branch, lint,
> PR), see [CONTRIBUTING.md](../CONTRIBUTING.md).

---

## Table of contents

1. [Overview](#overview)
2. [Implementing the ChannelProvider interface](#implementing-the-channelprovider-interface)
3. [Capability declarations](#capability-declarations)
4. [Inbound verification (`parse_webhook`)](#inbound-verification-parse_webhook)
5. [Payload normalization into InboundMessage](#payload-normalization-into-inboundmessage)
6. [Optional methods](#optional-methods)
7. [Fake provider implementation](#fake-provider-implementation)
8. [Registry and configuration updates](#registry-and-configuration-updates)
9. [Plugin providers (entry-point alternative)](#plugin-providers-entry-point-alternative)
10. [Recommended test coverage](#recommended-test-coverage)
11. [Reference adapters by transport type](#reference-adapters-by-transport-type)
12. [Checklist](#checklist)

---

## Overview

Every channel in the gateway is a **provider** — a plain Python class that
satisfies the `ChannelProvider` protocol defined in
[`providers/base.py`](../server/src/comm_gateway/providers/base.py). The
gateway never imports provider internals directly; it calls only the methods
declared on the protocol. This means your adapter can wrap any transport (HTTP
API, WebSocket, serial port, …) as long as it exposes the same four core
methods.

```
server/src/comm_gateway/
├── providers/
│   ├── base.py              # ChannelProvider protocol + data classes
│   ├── registry.py          # factory that builds providers from config
│   ├── fakes/               # in-memory test doubles
│   │   ├── fake.py          # reference fake (email)
│   │   └── fake_telegram.py # reference fake (webhook-based)
│   ├── modem.py             # minimal real adapter (good first read)
│   ├── telegram.py          # mid-complexity adapter (webhook + API)
│   └── slack.py             # full-featured adapter (OAuth + interactions)
├── config.py                # Settings — environment variables
└── routes/webhooks.py       # webhook receiver (calls parse_webhook)
```

---

## Implementing the ChannelProvider interface

Your adapter class must expose the following **class-level attributes** and
**methods** (the protocol uses structural subtyping, so you do *not* need to
inherit from `ChannelProvider`):

### Required attributes

| Attribute              | Type                | Description |
|------------------------|---------------------|-------------|
| `name`                 | `str`               | Unique provider name, used in config, routes, and the registry (e.g. `"telegram"`, `"gsm-modem"`). |
| `channel`              | `str`               | Logical channel name exposed to the API (e.g. `"email"`, `"telegram"`, `"phone"`). Multiple providers may serve one channel. |
| `capabilities`         | `frozenset[str]`    | Set of `Capability.*` values this transport supports (see next section). |
| `connect_credentials`  | `tuple[str, ...]`   | Credential field names a connect request must supply. Empty for transports the deployment fully owns. |

### Required methods

```python
def provision(self, request: ProvisionRequest) -> ProvisionResult:
    """Allocate or verify the channel resource (phone number, bot user,
    email inbox, …). Called once when a connection is created."""

def send(
    self,
    provider_inbox_id: str,
    message: OutboundMessage,
    credentials: Mapping[str, str] | None = None,
) -> SendResult:
    """Send a proactive message to an existing conversation."""

def reply(
    self,
    provider_inbox_id: str,
    provider_message_id: str,
    message: OutboundMessage,
    credentials: Mapping[str, str] | None = None,
) -> SendResult:
    """Reply to a specific inbound message."""

def parse_webhook(
    self,
    payload: bytes,
    headers: Mapping[str, str],
    credentials: Mapping[str, str] | None = None,
) -> list[InboundMessage]:
    """Verify and normalize a raw webhook delivery into InboundMessages."""
```

### Minimal skeleton

```python
"""Acme Chat adapter."""

from collections.abc import Mapping

from .base import (
    Capability,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    WebhookVerificationError,
)


class AcmeChatProvider:
    name = "acme-chat"
    channel = "acme-chat"
    capabilities = frozenset({Capability.RECEIVE, Capability.REPLY, Capability.SEND})
    connect_credentials = ("api_key",)

    def __init__(self, webhook_secret: str = "") -> None:
        self._webhook_secret = webhook_secret

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        # Call the platform's API to register a webhook / verify a bot, etc.
        return ProvisionResult(
            address="@acme_bot",
            provider_resource_id="acme_resource_123",
        )

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        # Call the platform's "send message" API
        return SendResult(provider_message_id="chat:msg_1", provider_thread_id="chat")

    def reply(
        self, provider_inbox_id: str, provider_message_id: str, message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        # Call the platform's "reply" API
        return SendResult(provider_message_id="chat:msg_2", provider_thread_id="chat")

    def parse_webhook(
        self, payload: bytes, headers: Mapping[str, str], credentials=None
    ) -> list[InboundMessage]:
        # 1. Verify the webhook signature — raise WebhookVerificationError on failure.
        # 2. Parse the JSON/form body.
        # 3. Return normalized InboundMessage(s).
        ...
```

---

## Capability declarations

Capabilities tell the gateway what a transport can and cannot do. The gateway
checks them *before* offering an operation, so callers get an honest 422
instead of a silent failure.

```python
from .base import Capability
```

| Constant                    | Meaning |
|-----------------------------|---------|
| `Capability.RECEIVE`        | Receive inbound messages (baseline — almost always declared). |
| `Capability.REPLY`          | Reply to a specific inbound message (baseline). |
| `Capability.SEND`           | Proactively message an existing conversation. |
| `Capability.INITIATE`       | Cold-start a brand-new conversation (SMS, email, …). |
| `Capability.GROUP_VISIBILITY` | See all group messages, not just @-mentions. |
| `Capability.EDIT_INBOUND`   | Receive edits to inbound messages. |
| `Capability.BACKFILL`       | Fetch history from before the connection existed. |
| `Capability.PRESENCE`       | Online / last-seen / typing of the other party. |
| `Capability.READ_RECEIPTS`  | Know when a sent message was read. |
| `Capability.AUTO_JOIN`      | Join a group or channel autonomously. |
| `Capability.SEE_BOTS`       | Receive messages authored by other bots. |
| `Capability.SECRET_CHATS`   | End-to-end secret chats. |
| `Capability.OTP`            | Receives 3rd-party verification codes (real SIM reliable). |
| `Capability.INTERACTIONS`   | Button taps / message components round-trip back. |
| `Capability.MEDIA`          | Send and/or receive file attachments. |
| `Capability.REACTIONS`      | Add emoji reactions and receive reaction events. |

`RECEIVE` and `REPLY` are **baseline** (always granted). Declare only the
additional capabilities your transport genuinely supports. When in doubt, leave
a capability out — it is easier to add later than to remove.

**Example (GSM modem):**

```python
capabilities = frozenset({
    Capability.RECEIVE,
    Capability.REPLY,
    Capability.SEND,
    Capability.INITIATE,   # can cold-start a conversation (SMS)
    Capability.OTP,         # receives 3rd-party verification codes
})
```

---

## Inbound verification (`parse_webhook`)

**Webhook verification is not optional.** If the platform signs its webhooks,
your adapter must verify the signature and raise `WebhookVerificationError` on
mismatch.

The gateway calls `parse_webhook` from the webhook route
([`routes/webhooks.py`](../server/src/comm_gateway/routes/webhooks.py)). A
raised `WebhookVerificationError` becomes an HTTP 400.

### Common patterns

**HMAC header verification** (e.g. Telegram's `X-Telegram-Bot-Api-Secret-Token`):

```python
import hmac
from .base import WebhookVerificationError, lower_headers

def parse_webhook(self, payload, headers, credentials=None):
    secret = (credentials or {}).get("webhook_secret")
    if secret:
        received = lower_headers(headers).get("x-platform-signature") or ""
        if not hmac.compare_digest(received, secret):
            raise WebhookVerificationError("signature mismatch")
    ...
```

**Timestamp + HMAC** (e.g. Slack, Twilio):

```python
# Reject stale timestamps, then HMAC over `v0:{timestamp}:{body}`.
```

**No webhook** (e.g. GSM modem — inbound comes from a poll loop):

```python
def parse_webhook(self, payload, headers, credentials=None):
    raise NotImplementedError("inbound arrives via the poll loop, not webhooks")
```

### Two webhook URL shapes

The gateway exposes two webhook patterns in `routes/webhooks.py`:

| URL pattern | Use case |
|-------------|----------|
| `/internal/providers/{provider}/webhooks` | Deployment-wide (one URL for all connections). Used by SES, fakes, and providers with a `route_key` method (Slack). |
| `/internal/providers/{provider}/webhooks/{resource_id}` | Per-connection (Telegram bots, Twilio numbers). The `resource_id` routes to the connection whose stored credentials verify the payload. |

If your platform assigns a unique endpoint per bot / number, use the scoped
shape and set `provider_resource_id` in `provision` to the value that appears
in the URL.

---

## Payload normalization into InboundMessage

Every inbound event must be normalized into one or more `InboundMessage`
dataclasses. The key fields:

```python
InboundMessage(
    external_event_id=...,     # globally unique; used for deduplication
    provider_inbox_id=...,     # routes to the connection (e.g. bot_id, inbox_id)
    provider_message_id=...,   # unique message id (composite "thread:msg" is fine)
    provider_thread_id=...,    # groups messages into conversations
    sender_address=...,        # who sent it (username, email, phone number)
    sender_name=...,           # display name (optional)
    text=...,                  # plain-text body
    html=...,                  # rich body (optional, email-oriented)
    chat_type=...,             # "private" | "group" | "channel" | ...
    edited=False,              # True for edited-message updates
    auto_generated=False,      # True for auto-responders/bounces (never auto-reply)
    kind="message",            # "message" | "interaction" | "reaction"
    media=[...],               # file attachments: [{"url": ..., "mime_type": ...}]
)
```

### Conventions

- **Composite IDs:** When you need both a thread ID and a message ID in a
  single string, use the `"{thread}:{message}"` convention. Use
  `split_composite_id()` from `base.py` to split them back. Composite IDs
  never leave the providers package.

- **`external_event_id`** must be globally unique across all deliveries of the
  same event. Include the bot/inbox id and a platform-unique component (e.g.
  `f"{bot_id}:{update_id}"`). This is the deduplication key.

- **`provider_inbox_id`** tells the ingest queue which connection owns this
  message. For per-connection webhooks this is the resource id; for shared
  webhooks it's whatever the `route_key` returns.

- **Interactions** (`kind="interaction"`): set the `action` dict:
  ```python
  action={"value": decoded_callback_value, "source_message_id": "..."}
  ```

- **Reactions** (`kind="reaction"`): set the `reaction` dict:
  ```python
  reaction={"emoji": "👍", "action": "added", "source_message_id": "..."}
  ```

---

## Optional methods

These are **not** part of the `ChannelProvider` protocol but are called by the
gateway when present. Implement them only when the transport supports the
feature:

| Method | When it's called |
|--------|-----------------|
| `initiate(provider_inbox_id, recipient, message) -> SendResult` | Cold-start a new conversation (requires `Capability.INITIATE`). |
| `backfill(provider_inbox_id, thread_id, limit) -> list[InboundMessage]` | Fetch history (requires `Capability.BACKFILL`). |
| `typing(provider_thread_id, credentials) -> None` | Show a typing indicator while the agent thinks. |
| `react(provider_message_id, emoji, credentials) -> None` | Add an emoji reaction (requires `Capability.REACTIONS`). |
| `parse_interaction(payload, headers, credentials) -> list[InboundMessage]` | Button taps on a separate endpoint (Slack interactivity). |
| `route_key(payload: bytes) -> str \| None` | Route a shared webhook to the correct connection (Slack). |
| `meta_verify(query_params) -> str \| None` | Answer a GET challenge handshake (Meta platforms). |
| `verify_challenge(query_params) -> dict \| None` | Answer a CRC challenge (X / Twitter). |
| `release(provider_resource_id, provider_pod_id) -> None` | Deprovision a number or resource. |

---

## Fake provider implementation

Every adapter ships with an **in-memory fake** under
[`providers/fakes/`](../server/src/comm_gateway/providers/fakes/). Fakes are
used for local development and testing. They must:

1. **Mirror the real adapter's capabilities** — either copy them directly or
   reference the real class:
   ```python
   capabilities = TelegramProvider.capabilities
   ```

2. **Accept zero-config construction** — no API keys, no network calls.

3. **Record outbound calls** in `self.sent` / `self.replies` lists so tests
   can assert on them.

4. **Consume real inbound payload shapes** — the fake's `parse_webhook` should
   call the real adapter's normalization function whenever possible, so tests
   exercise the same parsing code path:
   ```python
   from ..telegram import parse_update
   def parse_webhook(self, payload, headers, credentials=None):
       data = json.loads(payload)
       return parse_update(data, self.bot_id)
   ```

5. **Provide a `webhook_payload(...)` factory** — a convenience method that
   builds a realistic inbound payload for tests:
   ```python
   def webhook_payload(self, *, chat_id=4242, text="Hi there", ...) -> dict:
       ...
   ```

### Naming convention

| Real provider | Fake provider | Fake name |
|---------------|---------------|-----------|
| `telegram.py` | `fakes/fake_telegram.py` | `"fake-telegram"` |
| `modem.py`    | `fakes/fake_modem.py`    | `"fake-modem"` |
| `slack.py`    | `fakes/fake_social.py`   | `"fake-slack"` |

The fake's `name` attribute is always `"fake-{channel}"` or
`"fake-{provider}"`.

### Reference fakes

- **Simplest:** [`fake_modem.py`](../server/src/comm_gateway/providers/fakes/fake_modem.py)
  — no verification, records sends, builds payloads.
- **With verification:** [`fake_telegram.py`](../server/src/comm_gateway/providers/fakes/fake_telegram.py)
  — reuses the real `parse_update`, supports an optional webhook secret.
- **Shared base:** [`fake_channels.py`](../server/src/comm_gateway/providers/fakes/fake_channels.py)
  — shows how to build a shared `_FakeTwilioChannel` base for multiple
  Twilio-backed channels (WhatsApp, RCS).

---

## Registry and configuration updates

After writing the adapter and its fake, wire them into the gateway:

### 1. Add Settings fields (`config.py`)

Add your provider's environment variables to the
[`Settings`](../server/src/comm_gateway/config.py) class. All fields use the
`COMM_` prefix automatically (via `env_prefix="COMM_"`).

```python
# In config.py
acme_api_key: str = ""
acme_webhook_secret: str = ""
acme_base_url: str = "https://api.acme.chat"
```

These become `COMM_ACME_API_KEY`, `COMM_ACME_WEBHOOK_SECRET`, etc. in `.env`.

### 2. Register in `registry.py`

Add a branch to `_build_one()` that constructs your provider from settings:

```python
if name == "acme-chat":
    from .acme_chat import AcmeChatProvider

    return AcmeChatProvider(
        webhook_secret=settings.acme_webhook_secret,
        base_url=settings.acme_base_url,
    )
```

And the corresponding fake:

```python
if name == "fake-acme-chat":
    from .fakes.fake_acme_chat import FakeAcmeChatProvider

    return FakeAcmeChatProvider()
```

> **Note:** Imports inside `_build_one` are intentionally lazy so the gateway
> only loads providers it actually uses.

### 3. Update `.env.example`

Add commented-out entries for your provider's settings with sensible
descriptions so deployers know what to fill in.

---

## Plugin providers (entry-point alternative)

If you prefer not to fork, you can ship your adapter as a standalone Python
package. Register a builder function under the `caspian.providers`
entry-point group in your package's `pyproject.toml`:

```toml
[project.entry-points."caspian.providers"]
acme-chat = "my_package.acme:build_provider"
```

The builder has the signature `build(name: str, settings: Settings) ->
ChannelProvider`. The gateway discovers it automatically via
`importlib.metadata.entry_points`.

---

## Recommended test coverage

Tests live in [`server/tests/`](../server/tests/). Every new adapter should
include tests for:

### Payload normalization

Test that `parse_webhook` (or the standalone `parse_*` function) correctly
converts the platform's real JSON / form payloads into `InboundMessage`:

```python
def test_parse_text_message(provider):
    payload = provider.webhook_payload(text="Hello")
    messages = provider.parse_webhook(
        json.dumps(payload).encode(), {}
    )
    assert len(messages) == 1
    assert messages[0].text == "Hello"
    assert messages[0].sender_address == "customer"
```

### Webhook signature verification

Always test **both** the accept and reject cases:

```python
def test_valid_signature_accepted(provider):
    payload = provider.webhook_payload()
    messages = provider.parse_webhook(
        json.dumps(payload).encode(),
        {"X-Platform-Signature": "valid_token"},
        credentials={"webhook_secret": "valid_token"},
    )
    assert len(messages) == 1

def test_invalid_signature_rejected(provider):
    with pytest.raises(WebhookVerificationError):
        provider.parse_webhook(
            b'{}',
            {"X-Platform-Signature": "wrong"},
            credentials={"webhook_secret": "correct"},
        )
```

### End-to-end flow (via the test app)

Use the shared `conftest.py` fixtures to exercise the full
provision → inbound → reply cycle:

```python
def test_inbound_creates_event(app, client, run_jobs):
    # 1. Create customer + agent + connection
    # 2. POST a webhook payload to /internal/providers/{name}/webhooks
    # 3. run_jobs() to process
    # 4. Assert on /v1/events
```

See [`test_telegram_flow.py`](../server/tests/test_telegram_flow.py) for the
canonical example.

### Recommended test matrix

| Area | What to test |
|------|-------------|
| Normalization | Text messages, media/attachments, edited messages, interactions (button taps), reactions |
| Verification | Valid signature accepted, invalid signature rejected, missing signature rejected |
| Deduplication | Same `external_event_id` delivered twice → only one event |
| Routing | Correct `provider_inbox_id` maps to the right connection |
| Edge cases | Unsupported update types return `[]`, malformed JSON raises `WebhookVerificationError` |

---

## Reference adapters by transport type

Use these as starting points depending on how your platform's transport works:

| Transport type | Reference adapter | Why |
|----------------|-------------------|-----|
| **Webhook + REST API** (most common) | [`telegram.py`](../server/src/comm_gateway/providers/telegram.py) | Clean webhook verification, per-bot routing, media handling. |
| **Minimal / hardware** | [`modem.py`](../server/src/comm_gateway/providers/modem.py) | Smallest possible adapter (~100 lines). No webhook, poll-based inbound. |
| **OAuth install** | [`slack.py`](../server/src/comm_gateway/providers/slack.py) | OAuth code exchange, token refresh, pool routing, interactivity endpoint. |
| **Form-encoded webhooks (Twilio)** | [`twilio_whatsapp.py`](../server/src/comm_gateway/providers/twilio_whatsapp.py) | Twilio request-signing, form body parsing. |
| **Meta Graph API** | [`meta_messaging.py`](../server/src/comm_gateway/providers/meta_messaging.py) | Meta webhook verification (app secret HMAC), verify-token challenge. |
| **Full-featured social** | [`x.py`](../server/src/comm_gateway/providers/x.py) | OAuth 1.0a, CRC challenge, DM polling, interactions. |

---

## Checklist

Before opening your PR, verify:

- [ ] Adapter class satisfies the `ChannelProvider` protocol (all four methods + three attributes).
- [ ] `capabilities` frozenset declares only genuinely supported capabilities.
- [ ] `parse_webhook` verifies the platform's webhook signature (or documents why not).
- [ ] `parse_webhook` raises `WebhookVerificationError` on invalid payloads.
- [ ] Composite IDs follow the `"{thread}:{message}"` convention (use `split_composite_id`).
- [ ] In-memory fake mirrors the real adapter's capabilities and provides a `webhook_payload()` factory.
- [ ] Fake's `parse_webhook` reuses the real adapter's normalization function when possible.
- [ ] Settings fields added to `config.py` with `COMM_` prefix.
- [ ] Provider and fake registered in `registry.py` (lazy imports).
- [ ] `.env.example` updated with commented-out entries.
- [ ] Tests cover: normalization, signature verification (accept + reject), deduplication, edge cases.
- [ ] `uv run pytest` and `uv run ruff check .` pass.
- [ ] No secrets in code, tests, or fixtures — use obviously fake placeholder values.
- [ ] Only the platform's **official API** is used.
