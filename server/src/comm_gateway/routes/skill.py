"""Serve SKILL.md - the agent-readable integration guide.

Coding agents (Claude Code, Codex, Cursor) fetch this and perform the whole
integration on the developer's behalf. Keep it imperative, exact, and short.
Channel facts come from GET /v1/channels — do not re-author them here.
"""

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

router = APIRouter()

REPO = "https://github.com/TryCaspian/caspian-sdk"

SKILL_TEMPLATE = """# caspian: one handler, every channel

You are integrating Caspian. It gives an agent a real presence on many channels
behind ONE `on_message` handler and `thread.post()` — the same code answers on
every channel. The gateway is {base}.

## How to drive this (read first)

The developer may just say "add Caspian" without naming channels. Do this:

1. Steps 1–3 below (sign-in, install, the shared handler) — always needed.
2. Discover which channels are connectable RIGHT NOW:
   `curl -s {base}/v1/channels -H "Authorization: Bearer $CASPIAN_API_KEY"`
   Only offer those. A channel may appear more than once (different `provider`);
   when it does, ASK which provider and pass `provider=` to `channels.add`.
   If they ask for a channel that is not in that list, say it is not live on
   this gateway yet — do not invent a connect call.
3. ASK which of the live channels they want. Email is the safe default (no
   token). Others need a token or an OAuth click — see Connect.
4. `channels.add(...)` each chosen channel, then verify.

Adding a channel is another `channels.add` — never a new handler.

## 1. Sign in

If `CASPIAN_API_KEY` is already in the project's `.env`, SKIP this step.

Sign in first. There is no anonymous key. Preferred:

```bash
caspian init project    # browser sign-in; writes ./.env for the SDK
# or: caspian login     # sign in only; CLI secret is ~/.caspian/.env
```

`caspian init project` asks which folder (default: this one) and writes
`CASPIAN_API_KEY` + `CASPIAN_BASE_URL={base}` to that folder's `.env`.
`caspian login` writes the CLI secret under `~/.caspian/.env`, not the repo.

If you cannot run the CLI, do the same device-auth (no Authorization header):

```bash
curl -s -X POST {base}/v1/auth/device/start \\
  -H 'Content-Type: application/json' -d '{{}}'
```

SHOW the developer `verification_uri_complete` and WAIT for them to sign in.
Poll `POST {base}/v1/auth/device/token` with `{{"device_code": ...}}` until
`status` is `approved`. Write the returned `api_key` to `.env` as
`CASPIAN_API_KEY` and `CASPIAN_BASE_URL={base}`. The developer never signs in
again for this project.

If a later call returns **402**, credit is low: `GET {base}/v1/billing`. SHOW
the dashboard link and WAIT: {dashboard}
Poll billing until `balance_cents` rises, then retry. Autopay:
`PUT {base}/v1/billing/autopay` (a `monthly_cap_cents` is required).

## 2. Install

```bash
uv add caspian-sdk
# or: pip install caspian-sdk
# TypeScript: bun add caspian-sdk  (class Caspian, methods onMessage / channels.add / run)
```

Hosted jobs need the key from step 1. `caspian channels add <channel>` is the
CLI twin of `channels.add`; omit `--via` for hosted.

## 3. Integrate — one handler for every channel

Pass `api_key` into `Caspian(...)` (it does not read `.env` by itself). Write the
handler ONCE. `via` defaults to **hosted**: the gateway owns inbound; this
process polls `GET /v1/events` via `cx.run()`.

STOP before connecting email — ASK the mailbox NAME (the part before @) and WAIT.
Do not call a bare `channels.add("email")`; that mints a random local-part.
Once they name it: `channels.add("email", username="<their choice>")` →
<choice>@{email_domain}. A 409 returns `suggestions` — show those and let them pick.

```python
import os

from caspian import Caspian, HandlerContext, Message, Thread

cx = Caspian(
    api_key=os.environ["CASPIAN_API_KEY"],
    base_url=os.environ.get("CASPIAN_BASE_URL", "{base}"),
)

cx.channels.add("email", username="<ask them>")

@cx.on_message({{"channel": "email"}})
def handle(thread: Thread, msg: Message, ctx: HandlerContext) -> None:
    thread.post(your_agent_logic(msg.text))  # plain text

cx.run()  # hosted: poll the gateway forever
```

Wire `your_agent_logic` to the developer's agent (OpenAI Agents SDK, LangGraph,
plain LLM). Same handler for every channel you add.

Optional, recommended — inject channel etiquette into the system prompt:

`curl -s {base}/v1/behavior-prompt -H "Authorization: Bearer $CASPIAN_API_KEY"`
(or one channel: `{base}/v1/channels/slack/guide`). Offer it; don't force it.

## 4. Verify

With the integration running, deliver a test email:

```bash
curl -s -X POST {base}/v1/test-emails -H "Authorization: Bearer $CASPIAN_API_KEY" \\
  -H 'Content-Type: application/json' -d '{{"text":"hello, are you alive?"}}'
```

Then `GET {base}/v1/events?type=message.sent`. If that event exists, you are done.
Tell the developer the agent email. If they send a real mail and see nothing,
check spam first — a new sending domain has no reputation yet.

## Connect (hosted)

`channels.add(channel, **fields)`. Hosted is the default (`via` omitted).

- email: ASK mailbox `username`. Print the returned address.
- telegram: ASK BotFather `bot_token` (hosted does not mint a bot).
- discord / slack: ASK `display_name`. SHOW `authorize_url` if present.
- anything else: credential names on that `/v1/channels` row. Same handler.

Self-host (`via="self-host"`) is opt-in: this process owns inbound.

- Socket (discord, slack): `cx.listen("discord")` / `cx.listen("slack")`.
- Webhook: `cx.handle("<channel>", body, headers)` — copy `{repo}/tree/main/examples/<channel>`.
- Telegram without a public URL: `cx.poll("telegram")`.
- OAuth poll: `GET {base}/v1/connections/{{id}}` until `status` is `active`.

Do not mix: hosted inbound is `handle("gateway", …)` / `run()`, never
`handle("telegram", …)` on a hosted connection.

## Notes

- `thread.post(text)` replies on the incoming thread. `thread.send(text)` is a
  standalone send. Buttons: `@cx.on_action({{"channel": "…", "data": "…"}})`.
- `channels.add` for an already-active hosted connection is idempotent (returns
  the existing one).
- `cx.run()` / `cx.listen()` block. Persist your own cursor if you need
  exactly-once across restarts; a single run already dedupes.
- REST: {base}/docs
"""


@router.get("/SKILL.md", response_class=PlainTextResponse)
@router.get("/llms.txt", response_class=PlainTextResponse)
def skill(request: Request) -> str:
    base = str(request.base_url).rstrip("/")
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto == "https" and base.startswith("http://"):
        base = "https://" + base.removeprefix("http://")
    settings = request.app.state.settings
    dashboard = settings.billing_dashboard_url
    email_domain = settings.ses_domain or "your-agent-domain.com"
    return SKILL_TEMPLATE.format(
        base=base, repo=REPO, dashboard=dashboard, email_domain=email_domain
    )
