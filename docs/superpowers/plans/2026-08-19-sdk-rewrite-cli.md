# SDK Rewrite CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the CommClient-era `caspian` CLI with a namespaced thin client of the rewrite B surface, matching `docs/caspian-prd.md` §3.1.

**Architecture:** Same shape as treg: `catalog` discovers, `call <id>` invokes, other nouns are resources with `ls`/`add`/`tail`. The CLI is a thin client of B, not a second API. Argv desugars to Intent data, then `runIntent` interprets via the hosted `GatewayClient`. **One verb per job.** `channels watch` is not a job; following events is `threads tail`.

**Tech Stack:** TypeScript, bun, Effect (`packages/cli`). Imports the rewrite SDK (`packages/typescript`: `toRequest`, `fakeGatewayClient` / `httpGatewayClient`). Golden catalog JSON under `vectors/cli_catalog.json`. Tests are `bun test`. The CLI must not import `caspian/telegram` (or any adapter).

> An early cut of this plan targeted Python `apps/cli`. That is not the product. `apps/cli` stays the legacy CommClient CLI. The rewrite CLI is bun + Effect in `packages/cli`.

## One way to do each thing

The PRD example block listed `call post`, `slack post`, `telegram send-photo`, and `threads reply` as illustrations of namespaces. They are **not** four send APIs. Shipping all of them is the CommClient `connect*` pile in a new hat.

| Job | The one command | Not also |
|---|---|---|
| Get a key | `caspian init` / `caspian login` | sandbox mint |
| Mint / list identities | `caspian channels add` / `caspian channels ls` | `connect`, `status`, `watch` |
| Find what you can do | `caspian catalog` / `search` / `get` | invoking from catalog |
| **Do something** (send, edit, react, photo, dm) | **`caspian call <id>`** | `caspian slack post`, `caspian telegram send-photo`, `caspian threads reply` |
| List conversations | `caspian threads ls` | — |
| Follow events | `caspian threads tail` | `channels watch`, `listen` |

`caspian call` is `cx.tools({ preset: "outbound" })`. Every catalog row is a `call` id. Abstract: `caspian call post`. Native: `caspian call telegram.send-photo`. Same command, different id. Adding a channel adds catalog rows, not argv programs.

```bash
caspian login

caspian channels add telegram
caspian channels add discord --name Maya
caspian channels add telegram --via self-host --bot-token "$TG" \
  --webhook-url https://myapp.example.com/hook
caspian channels ls

caspian catalog
caspian catalog search "send a photo"
caspian catalog get telegram.send-photo

caspian call post --thread telegram:123:456 --text "shipping now"
caspian call post --thread slack:C123:ts --text "shipped"
caspian call telegram.send-photo --thread telegram:123:456 --file ./graph.png

caspian threads ls --channel telegram
caspian threads tail telegram:123:456
```

`threads reply … --text` is `call post`. Do not ship it. If someone types it, exit with `use: caspian call post --thread … --text …`.

`channels watch` is `threads tail` without a thread id. Do not ship it. If someone types it, exit with `use: caspian threads tail`.

`channels add` and `channels ls` are not the same: add mints a connection; ls prints connections. `threads ls` and `threads tail` are not the same: ls lists conversations (a table, then exit); tail follows events (a stream). Different resources (`connections` vs `conversations` vs `events`), so the same verb `ls` on `channels` vs `threads` is correct — it is not a second send path.

## This is already treg-shaped

treg's public CLI is:

```bash
treg catalog search "find a work email"
treg catalog get hunter.people.email.find
treg call hunter.people.email.find --query domain=reddit.com
treg login
```

Not `treg hunter people email find` and not `treg tiktok user-profile`. The vendor is an id you **call**, not a program you grow. Caspian copies that:

```bash
caspian catalog search "send a photo"
caspian catalog get telegram.send-photo
caspian call telegram.send-photo --thread telegram:123:456 --file ./graph.png
caspian call post --thread slack:C123:ts --text "shipped"
```

Caspian is not a tool proxy, so it has two extra nouns treg does not: **channels** (identity on Telegram/Slack) and **threads** (conversations + events). Those stay namespaced the same way (`channels add`, `threads tail`). They must not grow a third send path.

## Can the SDK support this?

Yes — the CLI must not invent jobs the SDK cannot name. Map, then fill gaps in B (TypeScript first) so argv is a skin.

| CLI | SDK today (`packages/typescript` / `python/`) | Do |
|---|---|---|
| `channels add` | `cx.channels.add` (Python: omit via = hosted. TS: still requires `via` — fix TS to match PRD) | already the identity write |
| `channels ls` | Python `ChannelManager.list()` / `added()`. TS keeps connections in a private Map | add `cx.channels.ls()` on TS |
| `channels watch` | nothing; do not add | reject in CLI |
| `catalog` / `search` / `get` | `cx.tools` is the abstract slice only; no native catalog | add `cx.catalog` as a view over Command tools + adapter planned methods (data, not adapters imported into the facade). CLI loads the same `vectors/cli_catalog.json` |
| `call <id>` | `cx.tools({ preset: "outbound" }).post_message.execute(args)` | CLI `call` **is** that execute. Do not add `cx.call` as a third send next to `thread.post` + `tools`. Handlers keep `thread.post`. Outbound agents/CLI keep tools execute. One send per audience. |
| `threads ls` | hosted `GET /v1/conversations` exists on the gateway; not on B | add `cx.threads.ls({ channel? })` that asks the runner (gateway when hosted, memory/process store when self-host) |
| `threads tail` | `thread.recent()` is in-handler history; hosted `GET /v1/events` is the poller | add `cx.threads.tail(threadId?)` as the outbound follow. `thread.recent()` stays the handler read of the same store — not a second CLI |

Handlers (`onMessage` + `thread.post`) stay the Chat SDK. CLI/coding agents never register handlers; they only `channels` + `catalog` + `call` + `threads`. That is the same split as treg (no “write a bot” path) plus Caspian's bot path.

## Global Constraints

- CLI is treg-shaped: `catalog` discovers, `call <id>` invokes. No `caspian telegram <verb>` program.
- Every CLI job must have a B name (table above). If B is missing, add it (or the shared `vectors/cli_catalog.json`) in the same PR as the CLI command — do not let argv become the source of truth.
- Omit `--via` means hosted. `--via self-host` is opt-in. Never invent `via: oauth` or `via: credentials`.
- One token: `CASPIAN_API_KEY`. Channel secrets stay on the gateway for hosted. Self-host `--bot-token` is local provision.
- Thread ids are `telegram:…` / `slack:…`, never platform chat ids.
- CLI must not `from caspian.adapters…` and must not `if channel == "telegram"` in `call` / `channels`.
- Wire JSON is snake_case. Flag names are kebab-case.
- TDD: failing test first. No network in unit tests — inject a recording HTTP port.
- Author/committer: Dipanshu Singh `<dipanshuhappy@gmail.com>`.
- Out of this plan: `caspian run ./bot.ts`, MCP, billing/topup, domains. Keep `caspian login`.

---

## File map

| Path | Role |
|---|---|
| `packages/cli/src/intent.ts` | Intent tagged union (data) |
| `packages/cli/src/desugar.ts` | `argv → Effect<Intent, UsageError>` |
| `packages/cli/src/catalog.ts` | load `vectors/cli_catalog.json` |
| `packages/cli/src/run.ts` | interpret Intent via injected `GatewayClient` |
| `packages/cli/src/main.ts` | bun bin: parse, run, print; `login` / `init` |
| `vectors/cli_catalog.json` | ids you can `call` |
| `packages/cli/test/desugar.test.ts` | argv → Intent |
| `packages/cli/test/call.test.ts` | call → hosted outbound `toRequest` |
| `packages/cli/test/channels.test.ts` | channels add/ls |
| `packages/cli/test/catalog.test.ts` | catalog search/get (no invoke) |
| `packages/cli/test/threads.test.ts` | threads ls/tail only |
| `packages/cli/README.md` | one-way examples |

Do **not** keep adding to the 500-line `main.py` dispatcher. Split first.

Delete: top-level `connect`, `status`, `listen`, `test-email`, `domains`, `billing`, `topup`. No `caspian <channel> <verb>` parser.

---

## Intent types (lock these names)

```python
# apps/cli/src/caspian_cli/intent.py
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ChannelsAdd:
    channel: str
    via: Literal["hosted", "self-host"]
    display_name: str = ""
    bot_token: str = ""
    webhook_url: str = ""
    inbound: bool = True


@dataclass(frozen=True)
class ChannelsLs:
    pass


@dataclass(frozen=True)
class Call:
    """The only mutate/send intent. `id` is a catalog id (`post`, `telegram.send-photo`)."""
    id: str
    args: dict  # snake_case; outbound post includes thread_id


@dataclass(frozen=True)
class CatalogList:
    pass


@dataclass(frozen=True)
class CatalogSearch:
    query: str


@dataclass(frozen=True)
class CatalogGet:
    id: str


@dataclass(frozen=True)
class ThreadsLs:
    channel: str = ""


@dataclass(frozen=True)
class ThreadsTail:
    thread_id: str


Intent = (
    ChannelsAdd | ChannelsLs | Call
    | CatalogList | CatalogSearch | CatalogGet
    | ThreadsLs | ThreadsTail
)
```

There is no `NativeCall` and no `ThreadsReply`. Native rows are `Call(id="telegram.send-photo", …)`.

Hosted dispatch for `Call(id="post", …)` must produce the same gateway request as `packages/typescript/src/hosted/outbound.ts` for `Post`.

---

### Task 1: Split argv + Intent, kill connect and duplicate send paths

**Files:**
- Create: `apps/cli/src/caspian_cli/intent.py` (types above)
- Create: `apps/cli/src/caspian_cli/argv.py`
- Create: `apps/cli/src/caspian_cli/desugar.py`
- Create: `apps/cli/tests/test_desugar.py`

**Interfaces:**
- Consumes: nothing
- Produces: `parse_argv(argv: list[str]) -> Intent` (raises `SystemExit` on bad argv)

- [ ] **Step 1: Write the failing test**

```python
# apps/cli/tests/test_desugar.py
import pytest
from caspian_cli.desugar import parse_argv
from caspian_cli.intent import Call, ChannelsAdd


def test_channels_add_telegram_omitting_via_is_hosted():
    intent = parse_argv(["channels", "add", "telegram"])
    assert intent == ChannelsAdd(channel="telegram", via="hosted")


def test_channels_add_self_host():
    intent = parse_argv([
        "channels", "add", "telegram",
        "--via", "self-host",
        "--bot-token", "123:abc",
        "--webhook-url", "https://example.com/hook",
    ])
    assert intent == ChannelsAdd(
        channel="telegram",
        via="self-host",
        bot_token="123:abc",
        webhook_url="https://example.com/hook",
    )


def test_call_post_is_the_send_path():
    intent = parse_argv([
        "call", "post",
        "--thread", "telegram:123:456",
        "--text", "shipping now",
    ])
    assert intent == Call(
        id="post",
        args={"thread_id": "telegram:123:456", "text": "shipping now"},
    )


def test_call_native_id_is_still_call():
    intent = parse_argv([
        "call", "telegram.send-photo",
        "--thread", "telegram:123:456",
        "--file", "./graph.png",
    ])
    assert intent == Call(
        id="telegram.send-photo",
        args={"thread_id": "telegram:123:456", "file": "./graph.png"},
    )


def test_connect_is_error():
    with pytest.raises(SystemExit):
        parse_argv(["connect", "telegram"])


def test_channel_verb_is_error_use_call():
    with pytest.raises(SystemExit, match="caspian call"):
        parse_argv(["telegram", "send-photo", "--thread", "telegram:1", "--file", "x.png"])


def test_threads_reply_is_error_use_call_post():
    with pytest.raises(SystemExit, match="caspian call post"):
        parse_argv(["threads", "reply", "telegram:123:456", "--text", "on my way"])


def test_channels_watch_is_error_use_threads_tail():
    with pytest.raises(SystemExit, match="caspian threads tail"):
        parse_argv(["channels", "watch"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/cli && uv run pytest tests/test_desugar.py -v`

Expected: FAIL with `ModuleNotFoundError: caspian_cli.desugar`.

- [ ] **Step 3: Write minimal argparse + desugar**

Subparsers: `channels` (`add|ls`), `call` (id is a free string looked up later), `catalog`, `threads` (`ls|tail`). Omit `--via` → `"hosted"`. `call post` → `Call(id="post", …)`. `channels watch` and `threads reply` are SystemExit pointing at the one command. Do not implement HTTP.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/cli && uv run pytest tests/test_desugar.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/cli/src/caspian_cli/intent.py apps/cli/src/caspian_cli/argv.py \
  apps/cli/src/caspian_cli/desugar.py apps/cli/tests/test_desugar.py
git commit -m "feat(cli): one send path — caspian call <id>"
```

---

### Task 2: Catalog as data (discover only)

**Files:**
- Create: `vectors/cli_catalog.json`
- Create: `apps/cli/src/caspian_cli/catalog.py`
- Create: `apps/cli/tests/test_catalog.py`

**Interfaces:**
- Consumes: nothing
- Produces: `load_catalog()`, `search_catalog(q)`, `get_catalog(id)` — never a Gateway call

Catalog entry shape:

```json
{
  "id": "post",
  "tool": "post_message",
  "command_tag": "Post",
  "summary": "Post text to a thread id (telegram:… / slack:…)."
}
```

```json
{
  "id": "telegram.send-photo",
  "tool": "send_media",
  "command_tag": "SendMedia",
  "channel": "telegram",
  "method": "sendPhoto",
  "summary": "Send a photo. Same as call; this id is the catalog name."
}
```

v1 rows: `post`, `send-dm`, `telegram.send-photo`. Slack text is `call post --thread slack:…`, not a second id.

- [ ] **Step 1: Write the failing test**

```python
# apps/cli/tests/test_catalog.py
from caspian_cli.catalog import load_catalog, search_catalog, get_catalog
from caspian_cli.desugar import parse_argv
from caspian_cli.intent import CatalogGet, CatalogSearch


def test_catalog_lists_post_and_telegram_send_photo():
    ids = {e["id"] for e in load_catalog()}
    assert "post" in ids
    assert "telegram.send-photo" in ids
    assert "slack.post" not in ids


def test_catalog_search_photo():
    hits = search_catalog("send a photo")
    assert any(e["id"] == "telegram.send-photo" for e in hits)


def test_catalog_get():
    entry = get_catalog("telegram.send-photo")
    assert entry["command_tag"] == "SendMedia"


def test_argv_catalog_does_not_invoke():
    assert parse_argv(["catalog", "search", "send a photo"]) == CatalogSearch(
        query="send a photo"
    )
    assert parse_argv(["catalog", "get", "telegram.send-photo"]) == CatalogGet(
        id="telegram.send-photo"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/cli && uv run pytest tests/test_catalog.py -v`

Expected: FAIL (`catalog` module missing).

- [ ] **Step 3: Add JSON + loader**

Unknown `call` id → SystemExit `unknown id; caspian catalog search …`.

- [ ] **Step 4: Run tests**

Run: `cd apps/cli && uv run pytest tests/test_catalog.py tests/test_desugar.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add vectors/cli_catalog.json apps/cli/src/caspian_cli/catalog.py \
  apps/cli/tests/test_catalog.py
git commit -m "feat(cli): catalog discovers call ids, it does not send"
```

---

### Task 3: Injected Gateway + `channels add/ls`

**Files:**
- Create: `apps/cli/src/caspian_cli/gateway.py`
- Create: `apps/cli/src/caspian_cli/run.py`
- Create: `apps/cli/tests/test_channels.py`

**Interfaces:**
- Consumes: `ChannelsAdd`, `ChannelsLs`
- Produces: `class Gateway(Protocol)` with `request(method, path, json=None) -> object`

Hosted `channels add telegram` → `POST /v1/connections/telegram` (`python/src/caspian/hosted/provisioning.py`). Slack/Discord OAuth hosted → `POST /v1/connections/{channel}/install` from `HOSTED_INSTALL = frozenset({"slack", "discord", "x", "github"})` copied from that file — not from argv.

Self-host: do **not** POST the bot token. Print `{channel, via: "self-host", webhook_url, inbound}`. Missing `--bot-token` → SystemExit like Python `Channels.add`.

- [ ] **Step 1: Write the failing test**

```python
# apps/cli/tests/test_channels.py
from caspian_cli.desugar import parse_argv
from caspian_cli.run import run_intent


class RecordingGateway:
    def __init__(self):
        self.calls = []
        self.responses = [{"id": "conn_1", "channel": "telegram", "status": "active"}]

    def request(self, method, path, json=None):
        self.calls.append((method, path, json))
        return self.responses.pop(0)


def test_hosted_channels_add_posts_connection():
    gw = RecordingGateway()
    out = run_intent(parse_argv(["channels", "add", "telegram"]), gateway=gw)
    assert gw.calls == [("POST", "/v1/connections/telegram", {"wait": True})]
    assert out["id"] == "conn_1"


def test_self_host_does_not_call_gateway():
    gw = RecordingGateway()
    out = run_intent(
        parse_argv([
            "channels", "add", "telegram",
            "--via", "self-host",
            "--bot-token", "123:abc",
            "--webhook-url", "https://example.com/hook",
        ]),
        gateway=gw,
    )
    assert gw.calls == []
    assert out["via"] == "self-host"


def test_channels_ls_gets_connections():
    gw = RecordingGateway()
    gw.responses = [[{"id": "conn_1", "channel": "telegram"}]]
    out = run_intent(parse_argv(["channels", "ls"]), gateway=gw)
    assert gw.calls == [("GET", "/v1/connections", None)]
    assert out[0]["id"] == "conn_1"
```

- [ ] **Step 2: Run to see fail**

Run: `cd apps/cli && uv run pytest tests/test_channels.py -v`

Expected: FAIL (run.py missing).

- [ ] **Step 3: Implement `run_intent` + Protocol**

- [ ] **Step 4: Tests pass**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(cli): channels add/ls via hosted gateway or local self-host record"
```

---

### Task 4: `caspian call post` → hosted outbound Post

**Files:**
- Modify: `apps/cli/src/caspian_cli/run.py`
- Create: `apps/cli/tests/test_call.py`

**Interfaces:**
- Consumes: `Call` whose catalog `command_tag` is `Post`
- Produces: `POST /v1/conversations/{conversation}/messages` with `{text}`

`conversationOf`: drop the first `channel:` (`telegram:123:456` → `123:456`). Do not send `chat_id` or `thread_id` in the body.

- [ ] **Step 1: Failing test**

```python
# apps/cli/tests/test_call.py
from caspian_cli.desugar import parse_argv
from caspian_cli.run import run_intent


class RecordingGateway:
    def __init__(self):
        self.calls = []

    def request(self, method, path, json=None):
        self.calls.append((method, path, json))
        return {"ok": True, "id": "msg_1"}


def test_call_post_uses_conversation_messages():
    gw = RecordingGateway()
    run_intent(
        parse_argv(["call", "post", "--thread", "telegram:123:456", "--text", "shipping now"]),
        gateway=gw,
    )
    assert gw.calls == [
        ("POST", "/v1/conversations/123:456/messages", {"text": "shipping now"}),
    ]


def test_call_post_on_slack_is_the_same_command():
    gw = RecordingGateway()
    run_intent(
        parse_argv(["call", "post", "--thread", "slack:C123:ts", "--text", "shipped"]),
        gateway=gw,
    )
    assert gw.calls[0][1] == "/v1/conversations/C123:ts/messages"
    body = gw.calls[0][2]
    assert "chat_id" not in body
    assert "thread_id" not in body
```

- [ ] **Step 2–4:** fail, implement from `packages/typescript/src/hosted/outbound.ts`, pass.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(cli): caspian call post is the only text send"
```

---

### Task 5: `threads ls|tail` (read only)

**Files:**
- Modify: `apps/cli/src/caspian_cli/run.py`, `desugar.py`
- Create: `apps/cli/tests/test_threads.py`

**Interfaces:**
- `threads ls --channel telegram` → `GET /v1/conversations` (filter by channel prefix if needed). Snapshot, then exit.
- `threads tail [thread_id]` → `GET /v1/events` once in tests (`max_events=1`). Optional thread id; omitting it follows every conversation. This is the only event stream.

No reply.

- [ ] **Step 1: Failing tests** for ls / tail only.

- [ ] **Step 2–4:** implement.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(cli): threads ls/tail are read-only"
```

---

### Task 6: `caspian call telegram.send-photo` (still `call`)

**Files:**
- Modify: `apps/cli/src/caspian_cli/run.py`
- Create: `apps/cli/tests/test_call_native.py`

Look up `Call.id` in the catalog. Dispatch on `command_tag`, not on channel name.

Hosted outbound today has no SendMedia path (`outbound.ts` `unsupported`). Test **asserts the error** (`SendMedia is not available in hosted mode`), not a POST. If outbound.ts grows a path before this task, assert that path instead. No fake success.

- [ ] **Step 1:** Test `call telegram.send-photo` raises SystemExit matching hosted unsupported wording.

- [ ] **Step 2–4:** implement via catalog `command_tag`.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(cli): native ids go through call, fail loudly if hosted cannot"
```

---

### Task 7: Wire `main()`, drop CommClient commands, README

**Files:**
- Modify: `apps/cli/src/caspian_cli/main.py`
- Modify: `apps/cli/tests/test_main.py`
- Modify: `apps/cli/README.md`
- Modify: `apps/cli/pyproject.toml` description

```python
def main(argv: list[str] | None = None) -> None:
    intent = parse_argv(sys.argv[1:] if argv is None else argv)
    gw = HttpxGateway.from_env()
    result = run_intent(intent, gateway=gw)
    print_json_or_table(result)
```

Keep `login` / `init`. README shows only the one-way table, not the old PRD duplicate examples.

- [ ] **Step 1:** Test `--help` lists `channels`, `call`, `catalog`, `threads`, `login` and does **not** list `connect` or per-channel send.

- [ ] **Step 2–4:** implement.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(cli): replace connect-era surface with one-way namespaces"
```

---

### Task 8: CI + CONTRIBUTING

**Files:**
- Modify: `CONTRIBUTING.md` CLI section: `cd apps/cli && uv run pytest`
- Modify: `.github/workflows/ci.yml` only if cli tests are not already in `uv run pytest`

- [ ] **Step 1:** Run `cd apps/cli && uv run pytest`.

- [ ] **Step 2: Commit** only if docs/CI changed.

---

## Explicitly not in this plan

| Item | Why |
|---|---|
| `caspian run ./bot.ts` | Process launcher, not the HTTP CLI |
| `caspian telegram send-photo` as argv | Duplicate of `caspian call telegram.send-photo` |
| `caspian slack post` | Duplicate of `caspian call post --thread slack:…` |
| `caspian threads reply` | Duplicate of `caspian call post` |
| `caspian channels watch` | Duplicate of `caspian threads tail` |
| MCP | Same four nouns later |
| Billing / topup / domains | CommClient-era |
| Importing adapters | CLI law |
| Required `--via` | Omit via = hosted |

---

## Spec coverage

| PRD example | What we ship |
|---|---|
| `caspian login` | Task 7 (keep) |
| `channels add/ls` | 1, 3 |
| `channels watch` | rejected; tell user to `threads tail` |
| `call post --thread telegram:…` | 1, 4 |
| `call post --thread slack:…` | 4 (same command) |
| `telegram send-photo` | `call telegram.send-photo` (2, 6) |
| `catalog search/get` | 2, 7 |
| `threads ls/tail` | 5 |
| `threads reply` | rejected; tell user to `call post` |
| `caspian run` | excluded |

---

## Type consistency

- `Call.id` is a catalog id (`post`, `telegram.send-photo`), never a Command tag (`Post`).
- Catalog `tool` is the SDK tool name (`post_message`). Catalog `command_tag` is the kernel tag (`Post`).
- `ChannelsAdd.via` is `"hosted" | "self-host"`.
- Gateway paths match `python/src/caspian/hosted/provisioning.py` and `packages/typescript/src/hosted/outbound.ts`.
- Thread id format `channel:rest`; conversationOf drops only the first segment.
