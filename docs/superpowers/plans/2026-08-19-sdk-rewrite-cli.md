# SDK Rewrite CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the CommClient-era `caspian` CLI with a namespaced thin client of the rewrite B surface, matching `docs/caspian-prd.md` §3.1.

**Architecture:** Argv is syntax. A pure `desugar(argv) → Intent` maps flags onto the same option objects as `packages/typescript` (`channels.add`, `cx.tools` outbound, `thread.*`). Hosted I/O is an injected HTTP port that already exists in `python/src/caspian/hosted/` (provisioning + outbound). The CLI must not import adapters, must not name Telegram in the abstract `call` path, and must not grow top-level `connect*` commands.

**Tech Stack:** Python 3.10+, httpx, argparse (already `apps/cli`), pytest. Design source: `packages/typescript` (`src/tools/derive.ts`, `src/provision/add.ts`, `src/hosted/outbound.ts`). Runtime helper: `python/src/caspian` provision + hosted outbound. Golden catalog JSON under `vectors/`.

## Global Constraints

- Design from `packages/typescript` B, not `sdks/typescript` CommClient and not `sdks/python`.
- Namespaces are **channel / resource / verb** (`caspian channels add`, `caspian call post`, `caspian telegram send-photo`). No top-level `connect`, `status`, `listen`, `test-email`.
- Omit `--via` means hosted. `--via self-host` is opt-in. Never invent `via: oauth` or `via: credentials`.
- One token: `CASPIAN_API_KEY`. Channel secrets stay on the gateway for hosted. Self-host `--bot-token` is local provision, not a second product.
- `caspian call *` is `cx.tools({ preset: "outbound" })`. Thread ids are `telegram:…` / `slack:…`, never platform chat ids.
- Native verbs (`caspian telegram send-photo`) come from the catalog, which is a view over adapter planned methods, not new kernel Commands.
- CLI code must not `from caspian.adapters…` and must not `if channel == "telegram"` in `call` / `channels`.
- Wire JSON is snake_case. Flag names are kebab-case (`--bot-token`, `--webhook-url`, `--thread`).
- TDD: failing test first. No network in unit tests — inject a recording HTTP port.
- Author/committer: Dipanshu Singh `<dipanshuhappy@gmail.com>`.
- Out of this plan: `caspian run ./bot.ts`, MCP, billing/topup, custom email domains. Keep `caspian login` (device flow) because hosted needs a key.

---

## File map

| Path | Role |
|---|---|
| `apps/cli/src/caspian_cli/argv.py` | argparse: namespaces only |
| `apps/cli/src/caspian_cli/intent.py` | frozen dataclasses: `Intent` union |
| `apps/cli/src/caspian_cli/desugar.py` | `argv → Intent` (pure) |
| `apps/cli/src/caspian_cli/catalog.py` | load `vectors/cli_catalog.json` |
| `apps/cli/src/caspian_cli/run.py` | interpret Intent via injected `Gateway` |
| `apps/cli/src/caspian_cli/gateway.py` | Protocol + httpx implementation |
| `apps/cli/src/caspian_cli/main.py` | `main()`: parse, desugar, run, print |
| `vectors/cli_catalog.json` | abstract tools + native methods |
| `apps/cli/tests/test_desugar.py` | argv → Intent |
| `apps/cli/tests/test_call.py` | call → hosted outbound paths |
| `apps/cli/tests/test_channels.py` | channels add/ls hosted vs self-host |
| `apps/cli/tests/test_catalog.py` | catalog search/get |
| `apps/cli/tests/test_threads.py` | threads ls/tail/reply |
| `apps/cli/README.md` | replace connect-era docs |

Do **not** keep adding to the 500-line `main.py` dispatcher. Split first.

Delete (after the new commands exist): top-level `connect`, `status`, `listen`, `test-email`, `domains`, `billing`, `topup` parsers. `init` stays as “mint sandbox key” under auth (`caspian login` / `caspian init`) because coding agents still need a key.

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
class ChannelsWatch:
    pass


@dataclass(frozen=True)
class Call:
    """Abstract tool. `name` is a tools.derive name (`post_message`), not a Command tag."""
    name: str
    args: dict  # snake_case, includes thread_id for outbound


@dataclass(frozen=True)
class NativeCall:
    """Catalog native method, e.g. id='telegram.send-photo'."""
    id: str
    args: dict


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


@dataclass(frozen=True)
class ThreadsReply:
    thread_id: str
    text: str


Intent = (
    ChannelsAdd | ChannelsLs | ChannelsWatch | Call | NativeCall
    | CatalogList | CatalogSearch | CatalogGet
    | ThreadsLs | ThreadsTail | ThreadsReply
)
```

Hosted dispatch for `Call(name="post_message", args={...})` must produce the same gateway request as `packages/typescript/src/hosted/outbound.ts` for `Post`.

---

### Task 1: Split argv + Intent, kill top-level connect in tests

**Files:**
- Create: `apps/cli/src/caspian_cli/intent.py` (types above)
- Create: `apps/cli/src/caspian_cli/argv.py`
- Create: `apps/cli/src/caspian_cli/desugar.py`
- Create: `apps/cli/tests/test_desugar.py`
- Modify: `apps/cli/src/caspian_cli/main.py` (thin `main()` only, after later tasks; this task can leave main importing desugar)

**Interfaces:**
- Consumes: nothing
- Produces: `parse_argv(argv: list[str]) -> Intent` (raises `SystemExit` on bad argv)

- [ ] **Step 1: Write the failing test**

```python
# apps/cli/tests/test_desugar.py
from caspian_cli.desugar import parse_argv
from caspian_cli.intent import Call, ChannelsAdd


def test_channels_add_telegram_omitting_via_is_hosted():
    intent = parse_argv(["channels", "add", "telegram"])
    assert intent == ChannelsAdd(channel="telegram", via="hosted")


def test_channels_add_self_host_requires_bot_token():
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


def test_call_post_is_outbound_post_message():
    intent = parse_argv([
        "call", "post",
        "--thread", "telegram:123:456",
        "--text", "shipping now",
    ])
    assert intent == Call(
        name="post_message",
        args={"thread_id": "telegram:123:456", "text": "shipping now"},
    )


def test_unknown_top_level_connect_is_error():
    import pytest
    with pytest.raises(SystemExit):
        parse_argv(["connect", "telegram"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/cli && uv run pytest tests/test_desugar.py -v`

Expected: FAIL with `ModuleNotFoundError: caspian_cli.desugar` (or import error).

- [ ] **Step 3: Write minimal argparse + desugar**

`argv.py` has subparsers: `channels` (`add|ls|watch`), `call`, `catalog`, `threads`, plus existing `login`/`init` left wired in main. `desugar.py` maps parsed namespace → Intent. Omit `--via` → `"hosted"`. `call post` → `Call(name="post_message", ...)`. Do not implement HTTP yet.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/cli && uv run pytest tests/test_desugar.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/cli/src/caspian_cli/intent.py apps/cli/src/caspian_cli/argv.py \
  apps/cli/src/caspian_cli/desugar.py apps/cli/tests/test_desugar.py
git commit -m "feat(cli): desugar namespaced argv into B intents"
```

---

### Task 2: Catalog as data (abstract tools + one native method)

**Files:**
- Create: `vectors/cli_catalog.json`
- Create: `apps/cli/src/caspian_cli/catalog.py`
- Create: `apps/cli/tests/test_catalog.py`
- Modify: `apps/cli/src/caspian_cli/desugar.py` (catalog + `telegram send-photo`)

**Interfaces:**
- Consumes: `parse_argv`
- Produces: `load_catalog() -> list[CatalogEntry]`; `NativeCall` for `caspian telegram send-photo`

Catalog entry shape (lock this JSON):

```json
{
  "id": "call.post",
  "kind": "abstract",
  "tool": "post_message",
  "cli": ["call", "post"],
  "summary": "Post text to a thread id (telegram:… / slack:…)."
}
```

```json
{
  "id": "telegram.send-photo",
  "kind": "native",
  "channel": "telegram",
  "method": "sendPhoto",
  "cli": ["telegram", "send-photo"],
  "command_tag": "SendMedia",
  "summary": "Send a photo on Telegram via SendMedia."
}
```

Abstract ids in v1: `call.post`, `call.send-dm` (from TS `deriveTools` outbound). Native v1: `telegram.send-photo`, `slack.post` (PRD examples). More natives later — adding a channel adds catalog rows, not argv special cases beyond `caspian <channel> <verb>` looked up by id.

- [ ] **Step 1: Write the failing test**

```python
# apps/cli/tests/test_catalog.py
from caspian_cli.catalog import load_catalog, search_catalog, get_catalog
from caspian_cli.desugar import parse_argv
from caspian_cli.intent import CatalogGet, CatalogSearch, NativeCall


def test_catalog_lists_abstract_post_and_telegram_send_photo():
    ids = {e["id"] for e in load_catalog()}
    assert "call.post" in ids
    assert "telegram.send-photo" in ids


def test_catalog_search_photo():
    hits = search_catalog("send a photo")
    assert any(e["id"] == "telegram.send-photo" for e in hits)


def test_catalog_get():
    entry = get_catalog("telegram.send-photo")
    assert entry["command_tag"] == "SendMedia"
    assert entry["cli"] == ["telegram", "send-photo"]


def test_argv_telegram_send_photo():
    intent = parse_argv([
        "telegram", "send-photo",
        "--thread", "telegram:123:456",
        "--file", "./graph.png",
    ])
    assert intent == NativeCall(
        id="telegram.send-photo",
        args={"thread_id": "telegram:123:456", "file": "./graph.png"},
    )


def test_argv_catalog_search():
    assert parse_argv(["catalog", "search", "send a photo"]) == CatalogSearch(
        query="send a photo"
    )


def test_argv_catalog_get():
    assert parse_argv(["catalog", "get", "telegram.send-photo"]) == CatalogGet(
        id="telegram.send-photo"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/cli && uv run pytest tests/test_catalog.py -v`

Expected: FAIL (`catalog` module missing).

- [ ] **Step 3: Add JSON + loader + argv lookup**

`caspian <channel> <verb>` is not a Python `if channel == "telegram"` chain. Resolve `f"{channel}.{verb}"` with kebab→id (`send-photo` → `telegram.send-photo`) against the catalog. Unknown channel/verb → SystemExit listing catalog search hint.

- [ ] **Step 4: Run tests**

Run: `cd apps/cli && uv run pytest tests/test_catalog.py tests/test_desugar.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add vectors/cli_catalog.json apps/cli/src/caspian_cli/catalog.py \
  apps/cli/tests/test_catalog.py apps/cli/src/caspian_cli/desugar.py
git commit -m "feat(cli): catalog as data for call.* and native verbs"
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

Hosted `channels add telegram` → `POST /v1/connections/telegram` (same as `python/src/caspian/hosted/provisioning.py`). Slack/Discord hosted that need OAuth → `POST /v1/connections/{channel}/install` when the catalog/channel kind says so — **do not** special-case in argv; use a `hosted_path` field on catalog channel rows or a tiny table `HOSTED_INSTALL = frozenset({"slack", "discord", "x", "github"})` copied from provisioning.py (that file already documents `/install`).

Self-host: do **not** POST the bot token to the gateway. Return a JSON record `{channel, via: "self-host", webhook_url, inbound}` on stdout (provision paperwork). Missing `--bot-token` on self-host → SystemExit with the same message as Python `Channels.add`.

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
    assert out["channel"] == "telegram"


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
- Consumes: `Call`
- Produces: gateway `POST /v1/conversations/{conversation}/messages` with `{text}`

Conversation id from thread id: same as TS `conversationOf` — strip the first `channel:` prefix (`telegram:123:456` → `123:456`). Do not send `chat_id`.

Map `call` names from catalog `tool` field through a table that mirrors `deriveTools` outbound:

| CLI | tool | Command tag | gateway |
|---|---|---|---|
| `call post` | `post_message` | `Post` | `POST /v1/conversations/{cid}/messages` |
| `call send-dm` | `send_dm` | `Initiate` | `POST /v1/connections/{channel}/initiate` — **skip in v1 if connection id is not in the thread id**; only implement post in this task |

v1 of this task: **post only**. send-dm is Task 4b in the same PR if the initiate path is obvious from `hosted/outbound.ts` (`/v1/connections/{id}/initiate`). Prefer post only if initiate needs a connection id the CLI does not have.

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


def test_call_post_never_sends_chat_id():
    gw = RecordingGateway()
    run_intent(
        parse_argv(["call", "post", "--thread", "slack:C123:ts", "--text", "hi"]),
        gateway=gw,
    )
    body = gw.calls[0][2]
    assert "chat_id" not in body
    assert "thread_id" not in body
```

- [ ] **Step 2–4:** fail, implement conversationOf + Post mapping (copy comments from `packages/typescript/src/hosted/outbound.ts`), pass.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(cli): caspian call post is outbound Post through the gateway"
```

---

### Task 5: `threads ls|tail|reply`

**Files:**
- Modify: `apps/cli/src/caspian_cli/run.py`, `desugar.py`
- Create: `apps/cli/tests/test_threads.py`

**Interfaces:**
- `threads ls --channel telegram` → `GET /v1/conversations` (filter client-side by channel prefix if the gateway has no query; if `connection_id` is required, `channels ls` first then list — keep the CLI interface as PRD).
- `threads tail telegram:123:456` → `GET /v1/events` polling (recording gateway returns a list; print and stop in tests). Do not implement a live loop in the unit test; `run_intent` takes `max_events=1` in tests.
- `threads reply telegram:123:456 --text "on my way"` → `Call`-equivalent `POST /v1/conversations/123:456/messages` (same as post). Reply in the PRD is abstract post, not Telegram `sendMessage` with `reply_to`.

- [ ] **Step 1: Failing tests** for ls / reply paths (tail can assert a single GET `/v1/events`).

- [ ] **Step 2–4:** implement.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(cli): threads ls/tail/reply over gateway conversations and events"
```

---

### Task 6: Native `telegram send-photo` via catalog (SendMedia, not a new Command)

**Files:**
- Modify: `apps/cli/src/caspian_cli/run.py`
- Modify: `apps/cli/tests/test_catalog.py` or `tests/test_native.py`

Hosted outbound today has **no** send-photo endpoint (`outbound.ts` marks many tags unsupported). For hosted native photo, do not 404 silently.

Behavior:
- If catalog entry `command_tag` is `SendMedia` and via is hosted: exit non-zero with the same wording as TS `unsupported("SendMedia")` — *unless* the gateway grows an endpoint. Check `packages/typescript/src/hosted/outbound.ts` at implementation time; if still unsupported, the test **asserts the error**, not a POST.
- Self-host is out of band for this CLI (no local adapter import). Native verbs are documented as hosted-when-the-gateway-can.

This task is the honesty gate: the PRD example `caspian telegram send-photo` must either hit a real gateway path or fail with “not available in hosted mode”. No fake success.

- [ ] **Step 1: Test that hosted send-photo raises SystemExit matching `SendMedia is not available in hosted mode`** (update if outbound.ts gained a path).

- [ ] **Step 2–4:** implement NativeCall handler from catalog `command_tag`.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(cli): native catalog verbs fail loudly when hosted has no endpoint"
```

---

### Task 7: Wire `main()`, drop CommClient commands, README

**Files:**
- Modify: `apps/cli/src/caspian_cli/main.py`
- Modify: `apps/cli/tests/test_main.py` (keep env/config/http tests; delete connect/listen assertions)
- Modify: `apps/cli/README.md`
- Modify: `apps/cli/pyproject.toml` description

`main()`:

```python
def main(argv: list[str] | None = None) -> None:
    intent = parse_argv(sys.argv[1:] if argv is None else argv)
    gw = HttpxGateway.from_env()
    result = run_intent(intent, gateway=gw)
    print_json_or_table(result)
```

Keep `login` / `init` as they exist (device flow + sandbox mint) — they are not B, they are how you get `CASPIAN_API_KEY`.

- [ ] **Step 1:** Test `caspian --help` lists `channels`, `call`, `catalog`, `threads`, `login` and does **not** list `connect`.

- [ ] **Step 2–4:** implement, update README to the PRD examples.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(cli): replace connect-era surface with PRD namespaces"
```

---

### Task 8: CI + README snippet in CONTRIBUTING

**Files:**
- Modify: `.github/workflows/ci.yml` only if `apps/cli` tests are not already in `uv run pytest`
- Modify: `CONTRIBUTING.md` CLI section: `cd apps/cli && uv run pytest`

Root `pyproject.toml` `testpaths` currently is `apps/cli/tests`, `sdks/python/tests`. Keep cli tests there.

- [ ] **Step 1:** Run `cd apps/cli && uv run pytest` and `cd packages/typescript && bun run ci` (no TS production changes expected).

- [ ] **Step 2: Commit** only if CONTRIBUTING/CI needed changing.

---

## Explicitly not in this plan

| Item | Why |
|---|---|
| `caspian run ./bot.ts --hosted` | Process launcher; different product from the HTTP CLI |
| MCP `catalog_search` | Same namespaces later; not needed to prove the CLI |
| Billing / topup / domains | CommClient-era; not in PRD CLI block |
| Importing `caspian.adapters` | Facade/CLI law |
| Copying `sdks/typescript` `connectTelegram` | Pile of top-level commands |
| Making `--via` required | PRD: omit via = hosted (even if TS `addChannel` still requires it) |

---

## Spec coverage

| PRD line | Task |
|---|---|
| `caspian login` | Task 7 (keep existing) |
| `caspian channels add telegram` | 1, 3 |
| `channels add … --via self-host` | 1, 3 |
| `channels ls` | 3 |
| `channels watch` | 5 (events poll; alias of tail without thread) — implement as `GET /v1/events` in Task 5 |
| `caspian call post --thread … --text` | 1, 4 |
| `caspian telegram send-photo` | 2, 6 |
| `caspian slack post` | 2 (catalog row) + 4-style hosted Post if `slack.post` is abstract Post on a slack thread — add catalog `kind: abstract` with `tool: post_message` |
| `caspian catalog` / search / get | 2, 7 |
| `threads ls / tail / reply` | 5 |
| `caspian run` | excluded |
| MCP | excluded |
| `caspian call post` sends as hosted identity, no channel token | 4 + env key only |

---

## Type consistency

- `Call.name` is tool name `post_message`, never `Post`.
- `NativeCall.id` is catalog id `telegram.send-photo`.
- `ChannelsAdd.via` is `"hosted" | "self-host"`.
- Gateway paths match `python/src/caspian/hosted/provisioning.py` and `packages/typescript/src/hosted/outbound.ts`.
- Thread id format `channel:rest`; conversationOf drops only the first segment.
