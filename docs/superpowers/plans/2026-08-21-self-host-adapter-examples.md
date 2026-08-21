# Self-host adapter examples Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One self-host Python example per catalog adapter that exercises every Command that adapter actually plans, using the same inbound verbs the SDK already has (`handle`, `poll`, `listen`).

**Architecture:** Handlers live in `examples/<channel>/app.py` as `register(cx)`. The process file (`bot.py`) only does paperwork: `channels.add(via="self-host", …)` plus the inbound loop. Webhook channels share `examples/serve.py` so we do not copy `BaseHTTPRequestHandler` eleven times. Discord and Slack self-host over `listen()` (catalog socket inbound). Hosted-all-channels is out of scope; Telegram hosted already exists as `examples/telegram/hosted.py`.

**Tech Stack:** Python 3.10+, existing `caspian` package, stdlib `http.server`, optional `caspian[discord]` / `caspian[slack-socket]` for `listen()`.

## Global Constraints

- Adapters stay codec (parse + plan). I/O is `TransportPort.dispatch` / `handle` / `poll` / `listen`.
- One `execute(Command)` / plan, not per-I/O methods on the facade.
- Overlap is a kernel concern; `step()` is first-match-wins — register specific `command` / `data` rules before the catch-all.
- Config on `Connection` at `channels.add` time (`via="self-host"`).
- Catalog is one fact: `packages/python/src/caspian/catalog.py`. Example capabilities must be a subset of that row.
- Registry must match catalog. Do not add a channel that is not a catalog row.
- Fail-closed verify: missing secret → False. Poll/listen use `trusted=True`.
- Telegram self-host webhook uses `webhook_secret` + `X-Telegram-Bot-Api-Secret-Token`; `add(webhook_url=…)` registers it.
- Do not ship `cx.command()` / Matcher. Filters are `OnMessageOptions.command` and `OnActionOptions.data`.
- Hosted-only names (bluesky, …) are not this plan.
- Do not commit `.env` or tokens. `.env.example` keys only.
- Tests drive `register(cx)` through `cx.handle` + `RecordingTransport` with a fixture body from that adapter's existing parse tests. No live network in CI.

---

## File map

| Path | Role |
|---|---|
| `examples/serve.py` | Shared HTTP: POST → `cx.handle(channel, body, headers)`; optional Meta GET challenge; optional TwiML response body |
| `examples/README.md` | Index of channels, inbound verb, env keys |
| `examples/telegram/app.py` | Already in tree — shared handlers |
| `examples/telegram/bot.py` | Already in tree — self-host webhook |
| `examples/telegram/hosted.py` | Already in tree — `Caspian(api_key)` + `cx.run()` |
| `examples/<channel>/app.py` | `register(cx)` — commands that adapter supports |
| `examples/<channel>/bot.py` | `add` + inbound loop |
| `examples/<channel>/.env.example` | Empty secrets |
| `examples/<channel>/README.md` | How to run |
| `packages/python/tests/test_examples_self_host.py` | Import `register`, fire one fixture per channel, assert a planned native |

Inbound by catalog row:

| Channel | Inbound | `add` secrets (self-host) | Process verb |
|---|---|---|---|
| telegram | webhook or poll | `bot_token`, `webhook_url`, `webhook_secret` | `handle` (done) or `poll` |
| discord | socket only | `bot_token` | `listen("discord")` |
| slack | webhook or socket | `bot_token`, `signing_secret`; socket also `app_token` | `listen("slack")` |
| email | webhook | `from_address` (unsigned) | `handle` |
| sms | webhook | `account_sid`, `auth_token`, `from_number`, `webhook_url` | `handle` |
| voice | webhook | same Twilio keys as SMS (`twilio_sig` uses `webhook_url`) | `handle`, HTTP **returns** TwiML |
| whatsapp | webhook | `access_token`, `phone_number_id`, `app_secret` | `handle` + GET `hub.challenge` |
| messenger | webhook | `page_access_token`, `app_secret` | `handle` + GET `hub.challenge` |
| imessage | webhook | `api_key`, `webhook_secret`, `relay_url` | `handle` |
| x | webhook | `bearer_token`, `consumer_secret` | `handle` + GET CRC |
| linear | webhook | `api_key`, `webhook_secret` | `handle` |

---

### Task 1: Shared webhook HTTP helper

**Files:**
- Create: `examples/serve.py`
- Create: `packages/python/tests/test_examples_serve.py`

**Interfaces:**
- Consumes: `Caspian.handle(channel, body, headers) -> list[Result]`
- Produces: `serve(cx, channel, *, host, port, verify_token="", twiml=False) -> None`

- [ ] **Step 1: Write the failing test for Meta challenge echo**

Create `packages/python/tests/test_examples_serve.py` with repo-root on `sys.path`, then:

```python
from examples.serve import challenge_response


def test_meta_subscribe_challenge_is_plain_text() -> None:
    body, status, content_type = challenge_response(
        {"hub.mode": ["subscribe"], "hub.verify_token": ["tok"], "hub.challenge": ["abc"]},
        verify_token="tok",
    )
    assert status == 200
    assert body == b"abc"
    assert content_type == "text/plain"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/python/tests/test_examples_serve.py::test_meta_subscribe_challenge_is_plain_text -v`

Expected: FAIL, `ModuleNotFoundError` or `challenge_response` not defined.

- [ ] **Step 3: Write `examples/serve.py`**

```python
"""Stdlib HTTP front for cx.handle. Not part of the public SDK."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from caspian import Caspian
from caspian.core.ports import Result


def challenge_response(
    query: dict[str, list[str]],
    *,
    verify_token: str,
) -> tuple[bytes, int, str]:
    mode = (query.get("hub.mode") or [""])[0]
    token = (query.get("hub.verify_token") or [""])[0]
    challenge = (query.get("hub.challenge") or [""])[0]
    if mode == "subscribe" and verify_token and token == verify_token:
        return challenge.encode(), 200, "text/plain"
    return b"", 403, "text/plain"


def _twiml_of(results: list[Result]) -> str:
    for result in results:
        if result.is_ok and isinstance(result.value.raw, dict):
            markup = result.value.raw.get("twiml")
            if isinstance(markup, str) and markup:
                return markup
    return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def serve(
    cx: Caspian,
    channel: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    verify_token: str = "",
    twiml: bool = False,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = parse_qs(urlparse(self.path).query)
            body, status, content_type = challenge_response(
                query, verify_token=verify_token
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            headers = {k: v for k, v in self.headers.items()}
            results = cx.handle(channel, raw, headers)
            for result in results:
                if not result.is_ok:
                    print(result.error, flush=True)
            out = _twiml_of(results).encode() if twiml else b""
            self.send_response(200)
            if twiml:
                self.send_header("Content-Type", "text/xml")
                self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            if out:
                self.wfile.write(out)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            print(format % args, flush=True)

    HTTPServer((host, port), Handler).serve_forever()
```

X CRC GET is Task 11, not this file yet.

- [ ] **Step 4: Run the test and make sure it passes**

Run: `uv run pytest packages/python/tests/test_examples_serve.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add examples/serve.py packages/python/tests/test_examples_serve.py
git commit -m "$(cat <<'EOF'
Share one stdlib HTTP front for self-host webhooks.

Webhook examples should call handle(), not copy BaseHTTPRequestHandler.
EOF
)"
```

---

### Task 2: Discord self-host (`listen`)

**Files:**
- Create: `examples/discord/app.py`
- Create: `examples/discord/bot.py`
- Create: `examples/discord/.env.example`
- Create: `examples/discord/README.md`
- Modify: `packages/python/tests/test_examples_self_host.py` (create if missing)

**Interfaces:**
- Consumes: `Caspian.listen("discord")`, `channels.add("discord", via="self-host", bot_token=…)`
- Produces: `register(cx: Caspian) -> None`

Discord catalog inbound is **socket only**. Do not use `handle("discord")` for guild messages. Extra: `caspian[discord]`.

- [ ] **Step 1: Write a failing interpret test**

```python
from caspian import Caspian
from caspian.core.types import Message, ThreadId


def test_discord_help_posts_menu() -> None:
    from examples.discord.app import register

    cx = Caspian(dispatch=False)
    register(cx)
    event = Message(
        thread_id=ThreadId("discord:1"),
        text="/help",
        chat_kind="channel",
    )
    result = cx.interpret().run(cx.app, event, channel_name="discord")
    assert any(getattr(c, "tag", "") == "Host" for c in result.commands)
```

- [ ] **Step 2: Run test — expect import fail**

Run: `uv run pytest packages/python/tests/test_examples_self_host.py::test_discord_help_posts_menu -v`

Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Implement `examples/discord/app.py` and `bot.py`**

`examples/discord/app.py`:

```python
from caspian import Action, Button, Caspian, HandlerContext, Message, Thread

HELP = "/help menu\n/send standalone\nanything else is echoed."
MENU = (Button(label="ok", data="ok"),)


def register(cx: Caspian) -> None:
    @cx.on_message({"channel": "discord", "command": ["help", "start"]})
    def on_help(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.post(HELP, actions=MENU)

    @cx.on_message({"channel": "discord", "command": "send"})
    def on_send(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.send("standalone")

    @cx.on_message({"channel": "discord", "command": "typing", "overlap": "drop"})
    def on_typing(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.typing()
        thread.post("done thinking.")

    @cx.on_message({"channel": "discord", "command": "pin", "kind": "channel"})
    def on_pin(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        thread.pin(msg.message_id)

    @cx.on_action({"channel": "discord", "data": "ok"})
    def on_ok(thread: Thread, action: Action, ctx: HandlerContext) -> None:
        thread.post("ok")

    @cx.on_message({"channel": "discord"})
    def on_echo(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
        if msg.text.strip():
            thread.post(msg.text)
```

Do not call `thread.forward` (Discord does not plan Forward).

`examples/discord/bot.py`:

```python
import os
from caspian import Caspian
from app import register

token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
if not token:
    raise SystemExit("Set DISCORD_BOT_TOKEN, then rerun.")

cx = Caspian()
cx.channels.add("discord", via="self-host", bot_token=token)
register(cx)

if __name__ == "__main__":
    cx.listen("discord")
```

`.env.example`: `DISCORD_BOT_TOKEN=`

README: Message Content Intent; install extra `discord`.

- [ ] **Step 4: Run the unit test**

Run: `uv run pytest packages/python/tests/test_examples_self_host.py::test_discord_help_posts_menu -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add examples/discord packages/python/tests/test_examples_self_host.py
git commit -m "$(cat <<'EOF'
Add a Discord self-host example over listen().

Guild messages have no HTTP webhook in this SDK; the catalog inbound is socket.
EOF
)"
```

---

### Task 3: Slack self-host (`listen` Socket Mode)

**Files:**
- Create: `examples/slack/app.py`
- Create: `examples/slack/bot.py`
- Create: `examples/slack/.env.example`
- Create: `examples/slack/README.md`
- Modify: `packages/python/tests/test_examples_self_host.py`

**Interfaces:**
- Consumes: `listen("slack")`, `add(..., bot_token, app_token=…)`
- Produces: `register(cx)`

Use Socket Mode so there is no public URL. Slack Events `url_verification` currently parses to `[]` with no HTTP echo — do not use the webhook path in this example.

- [ ] **Step 1: Failing test**

```python
def test_slack_help_posts_menu() -> None:
    from examples.slack.app import register
    from caspian import Caspian
    from caspian.core.types import Message, ThreadId

    cx = Caspian(dispatch=False)
    register(cx)
    event = Message(thread_id=ThreadId("slack:C1"), text="/help", chat_kind="channel")
    result = cx.interpret().run(cx.app, event, channel_name="slack")
    assert any(getattr(c, "tag", "") == "Host" for c in result.commands)
```

- [ ] **Step 2: Run — expect import fail**

Run: `uv run pytest packages/python/tests/test_examples_self_host.py::test_slack_help_posts_menu -v`

- [ ] **Step 3: Implement**

`examples/slack/app.py` — channel `"slack"`. Exercise `thread.send_blocks`. Include `/help`, `/blocks`, echo. No `thread.pin`.

```python
@cx.on_message({"channel": "slack", "command": "blocks"})
def on_blocks(thread, msg, ctx):
    thread.send_blocks((), text="blocks", actions=(Button(label="ok", data="ok"),))
```

`examples/slack/bot.py`:

```python
import os
from caspian import Caspian
from app import register

token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
app_token = os.environ.get("SLACK_APP_TOKEN", "").strip()
if not token or not app_token:
    raise SystemExit("Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN (xapp-), then rerun.")

cx = Caspian()
cx.channels.add(
    "slack",
    via="self-host",
    bot_token=token,
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET", ""),
    app_token=app_token,
)
register(cx)

if __name__ == "__main__":
    cx.listen("slack")
```

`.env.example`: `SLACK_BOT_TOKEN=` `SLACK_APP_TOKEN=` `SLACK_SIGNING_SECRET=`

README: Socket Mode, `connections:write`, extra `caspian[slack-socket]`.

- [ ] **Step 4: Test PASS**

Run: `uv run pytest packages/python/tests/test_examples_self_host.py::test_slack_help_posts_menu -v`

- [ ] **Step 5: Commit**

```bash
git add examples/slack packages/python/tests/test_examples_self_host.py
git commit -m "$(cat <<'EOF'
Add a Slack self-host example over Socket Mode listen().

Avoid the Events API handshake until parse/handle can echo url_verification.
EOF
)"
```

---

### Task 4: Email self-host webhook

**Files:**
- Create: `examples/email/app.py`
- Create: `examples/email/bot.py`
- Create: `examples/email/.env.example`
- Create: `examples/email/README.md`
- Modify: `packages/python/tests/test_examples_self_host.py`

**Interfaces:**
- Consumes: `handle("email", …)`, `add("email", via="self-host", from_address=…)` (unsigned verify)
- Produces: `register(cx)`

- [ ] **Step 1: Failing test** using simplified JSON the adapter already parses:

```python
import json
from caspian import Caspian
from caspian.interpreters.transport import RecordingTransport


def test_email_help_plans_smtp() -> None:
    from examples.email.app import register

    rec = RecordingTransport()
    cx = Caspian(transport=rec)
    cx.channels.add("email", via="self-host", from_address="bot@example.com")
    register(cx)
    body = json.dumps(
        {"from": "a@b.c", "to": "bot@example.com", "subject": "x", "body": "/help", "message_id": "<1>"}
    ).encode()
    results = cx.handle("email", body, {})
    assert any(r.is_ok and r.value.raw.get("transport") == "smtp" for r in results)
```

- [ ] **Step 2: Run — expect import fail**

- [ ] **Step 3: Implement** `register` with `/help` and catch-all echo via `thread.post`. `bot.py` uses `serve(cx, "email", port=…)`. `from_address` from `EMAIL_FROM`.

- [ ] **Step 4: Test PASS**

- [ ] **Step 5: Commit**

```bash
git add examples/email packages/python/tests/test_examples_self_host.py
git commit -m "$(cat <<'EOF'
Add an email self-host example over handle().

Inbound is unsigned JSON/SNS; outbound is the smtp transport description.
EOF
)"
```

---

### Task 5: SMS self-host (Twilio)

**Files:**
- Create: `examples/sms/app.py`
- Create: `examples/sms/bot.py`
- Create: `examples/sms/.env.example`
- Create: `examples/sms/README.md`
- Modify: `packages/python/tests/test_examples_self_host.py`

**Interfaces:**
- Consumes: `add(..., account_sid, auth_token, from_number, webhook_url)` — `twilio_sig` fail-closes without `webhook_url` + `auth_token`
- Produces: `register(cx)`

Commands the SMS adapter plans: `Post`, `Reply`, `SendMedia`. No buttons, pin, react.

- [ ] **Step 1: Failing test** — form body `From=+1&Body=/help` with valid `X-Twilio-Signature` built like `packages/python/tests/test_pack.py` `test_twilio_sig_accepts_valid_form`. `webhook_url` in connection config must equal the public URL Twilio signed.

- [ ] **Step 2: Run — expect import fail**

- [ ] **Step 3: Implement** `app.py` (`/help`, echo). `bot.py` `serve(cx, "sms")` after `add`. Env: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`, `SMS_WEBHOOK_URL`, `PORT`.

- [ ] **Step 4: Test PASS**

- [ ] **Step 5: Commit**

```bash
git add examples/sms packages/python/tests/test_examples_self_host.py
git commit -m "$(cat <<'EOF'
Add a Twilio SMS self-host example over handle().

Signature verify is the public URL plus form params; webhook_url must match.
EOF
)"
```

---

### Task 6: Voice self-host (TwiML response)

**Files:**
- Create: `examples/voice/app.py`
- Create: `examples/voice/bot.py`
- Create: `examples/voice/.env.example`
- Create: `examples/voice/README.md`
- Modify: `packages/python/tests/test_examples_self_host.py`

**Interfaces:**
- Consumes: `serve(..., twiml=True)`, adapter plans `transport: twiml`
- Produces: HTTP 200 `text/xml` containing `<Say>`

- [ ] **Step 1: Failing test** — `CallSid` + `SpeechResult` form, Twilio signature, assert some result `raw["twiml"]` contains `<Say>`.

- [ ] **Step 2: Run — expect import fail**

- [ ] **Step 3: Implement** `register` echoes speech via `thread.post`. `bot.py` `serve(cx, "voice", twiml=True, port=…)`. Same Twilio env names as SMS plus `VOICE_WEBHOOK_URL`.

- [ ] **Step 4: Test PASS**

- [ ] **Step 5: Commit**

```bash
git add examples/voice packages/python/tests/test_examples_self_host.py
git commit -m "$(cat <<'EOF'
Add a Twilio Voice self-host example that returns TwiML from handle().

Voice is response-based; the HTTP layer must write raw twiml, not only dispatch.
EOF
)"
```

---

### Task 7: WhatsApp self-host

**Files:**
- Create: `examples/whatsapp/app.py`
- Create: `examples/whatsapp/bot.py`
- Create: `examples/whatsapp/.env.example`
- Create: `examples/whatsapp/README.md`
- Modify: `packages/python/tests/test_examples_self_host.py`

**Interfaces:**
- Consumes: `add(..., access_token, phone_number_id, app_secret)`, GET challenge via `serve(verify_token=…)`
- Produces: `register(cx)` using Post/Reply/SendMedia/React as the adapter plans them

- [ ] **Step 1: Failing test** — payload shape from `packages/python/tests/test_adapter_whatsapp.py`. HMAC `X-Hub-Signature-256` with `app_secret`.

- [ ] **Step 2: Run — expect import fail**

- [ ] **Step 3: Implement** `/help` + echo + one button post. `bot.py`: `serve(cx, "whatsapp", verify_token=os.environ["WHATSAPP_VERIFY_TOKEN"])`. Env: `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`, `PORT`.

- [ ] **Step 4: Test PASS**

- [ ] **Step 5: Commit**

```bash
git add examples/whatsapp packages/python/tests/test_examples_self_host.py
git commit -m "$(cat <<'EOF'
Add a WhatsApp Cloud API self-host example over handle().

Meta subscribe handshake is GET hub.challenge on the shared HTTP front.
EOF
)"
```

---

### Task 8: Messenger self-host

**Files:**
- Create: `examples/messenger/app.py`
- Create: `examples/messenger/bot.py`
- Create: `examples/messenger/.env.example`
- Create: `examples/messenger/README.md`
- Modify: `packages/python/tests/test_examples_self_host.py`

Same HTTP + HMAC as WhatsApp. `add(..., page_access_token, app_secret)`. Fixture from `packages/python/tests/test_adapter_messenger.py`.

- [ ] **Step 1: Failing test** (import `examples.messenger.app`)

- [ ] **Step 2: Run — expect import fail**

- [ ] **Step 3: Implement** `/help`, echo, typing. `serve(cx, "messenger", verify_token=…)`.

- [ ] **Step 4: Test PASS**

- [ ] **Step 5: Commit**

```bash
git add examples/messenger packages/python/tests/test_examples_self_host.py
git commit -m "$(cat <<'EOF'
Add a Messenger self-host example over handle().

Same Meta GET challenge as WhatsApp; page_access_token is the send secret.
EOF
)"
```

---

### Task 9: iMessage self-host (relay)

**Files:**
- Create: `examples/imessage/app.py`
- Create: `examples/imessage/bot.py`
- Create: `examples/imessage/.env.example`
- Create: `examples/imessage/README.md`
- Modify: `packages/python/tests/test_examples_self_host.py`

**Interfaces:**
- Consumes: `add(..., api_key, webhook_secret, relay_url=…)`, verify header `X-Relay-Signature`

- [ ] **Step 1: Failing test** from `packages/python/tests/test_adapter_imessage.py` inbound JSON + HMAC hex.

- [ ] **Step 2: Run — expect import fail**

- [ ] **Step 3: Implement** `/help` + echo. `serve(cx, "imessage")`. Env: `IMESSAGE_API_KEY`, `IMESSAGE_WEBHOOK_SECRET`, `IMESSAGE_RELAY_URL`.

- [ ] **Step 4: Test PASS**

- [ ] **Step 5: Commit**

```bash
git add examples/imessage packages/python/tests/test_examples_self_host.py
git commit -m "$(cat <<'EOF'
Add an iMessage relay self-host example over handle().

The adapter speaks the HTTP-JSON bridge, not Apple; webhook_secret is HMAC.
EOF
)"
```

---

### Task 10: Linear self-host

**Files:**
- Create: `examples/linear/app.py`
- Create: `examples/linear/bot.py`
- Create: `examples/linear/.env.example`
- Create: `examples/linear/README.md`
- Modify: `packages/python/tests/test_examples_self_host.py`

Adapter plans GraphQL `commentCreate` for Post/Reply. No media/buttons.

- [ ] **Step 1: Failing test** from `packages/python/tests/test_adapter_linear.py` + `Linear-Signature` HMAC.

- [ ] **Step 2: Run — expect import fail**

- [ ] **Step 3: Implement** echo as `thread.post`. `serve(cx, "linear")`. Env: `LINEAR_API_KEY`, `LINEAR_WEBHOOK_SECRET`.

- [ ] **Step 4: Test PASS**

- [ ] **Step 5: Commit**

```bash
git add examples/linear packages/python/tests/test_examples_self_host.py
git commit -m "$(cat <<'EOF'
Add a Linear self-host example that comments on issues via handle().

Outbound is GraphQL; inbound HMAC is Linear-Signature.
EOF
)"
```

---

### Task 11: X self-host (Account Activity CRC)

**Files:**
- Create: `examples/x/app.py`
- Create: `examples/x/bot.py`
- Create: `examples/x/.env.example`
- Create: `examples/x/README.md`
- Modify: `examples/serve.py` — GET `crc_token` → `{"response_token": "sha256=<hmac>"}` using `consumer_secret`
- Modify: `packages/python/tests/test_examples_serve.py`
- Modify: `packages/python/tests/test_examples_self_host.py`

**Interfaces:**
- Consumes: `add(..., bearer_token, consumer_secret)`, verify `X-Twitter-Webhooks-Signature`

- [ ] **Step 1: Failing CRC test**

```python
import base64, hashlib, hmac, json


def test_x_crc_response_token() -> None:
    from examples.serve import crc_response

    secret = "cons"
    token = "crc"
    body, status, content_type = crc_response(token, consumer_secret=secret)
    digest = hmac.new(secret.encode(), token.encode(), hashlib.sha256).digest()
    expected = "sha256=" + base64.b64encode(digest).decode()
    assert status == 200
    assert json.loads(body) == {"response_token": expected}
    assert content_type == "application/json"
```

- [ ] **Step 2: Run — expect fail**

- [ ] **Step 3: Add `crc_response` and wire `serve(..., consumer_secret=)` GET. Implement `examples/x/app.py` (`/help` + echo, `thread.post` only). `bot.py` `serve(cx, "x", consumer_secret=…)`.

- [ ] **Step 4: Tests PASS** (`test_x_crc_response_token` and an X help interpret test)

- [ ] **Step 5: Commit**

```bash
git add examples/x examples/serve.py packages/python/tests/test_examples_serve.py packages/python/tests/test_examples_self_host.py
git commit -m "$(cat <<'EOF'
Add an X Account Activity self-host example, including CRC GET.

Twitter signs crc_token with consumer_secret; handle() never sees that GET.
EOF
)"
```

---

### Task 12: Examples index README

**Files:**
- Create: `examples/README.md`

- [ ] **Step 1: Write the index**

Table: channel, script, inbound verb, required env. Point Telegram at both `bot.py` and `hosted.py`. State hosted-all-channels is not this work. Point at `docs/superpowers/plans/2026-08-21-self-host-adapter-examples.md`.

- [ ] **Step 2: Commit**

```bash
git add examples/README.md
git commit -m "$(cat <<'EOF'
Index self-host examples by catalog channel and inbound verb.
EOF
)"
```

---

## Spec coverage

| Requirement | Task |
|---|---|
| Telegram hosted sibling, same handlers | Already in tree (`app.py` / `hosted.py`); not re-done here |
| Self-host every catalog adapter | Tasks 2–11 (telegram self-host already in tree) |
| Shared webhook HTTP | Task 1, extended in Task 11 for CRC |
| Socket channels use `listen` | Tasks 2–3 |
| Voice returns TwiML | Task 6 |
| Meta GET challenge | Tasks 1, 7, 8 |
| CI tests without live network | every task’s example tests |
| No hosted-only channels | Global constraints |

## Placeholder scan

No TBD. Slack Events API handshake is explicitly deferred (Socket Mode). X CRC is Task 11, not a stub in Task 1.

## Type consistency

- `register(cx: Caspian) -> None` on every `app.py`
- `serve(cx, channel, *, host, port, verify_token="", twiml=False, consumer_secret="")`
- `challenge_response` / `crc_response` are pure and unit-tested
