# Context: Caspian Issue #32 — Overlapping-Message Concurrency Strategies

## Working agreement (read this first, every session)

- **Before starting any phase**, summarize in plain English exactly what you are about to code — which files, which functions, what behavior — and **wait for explicit approval** before writing or editing anything.
- **Never bundle phases.** Finish one, stop, report what changed and why, wait for the next go-ahead.
- If something in the actual codebase contradicts an assumption in this doc (e.g. `_dispatch_event` looks different than described), **stop and flag it** rather than guessing and proceeding.
- Philosophy for this whole task: **small and correct beats big and broken.** When in doubt, cut scope, not tests.
- Do not touch any channel adapter code, gateway event delivery, or add distributed/multi-process locking — these are explicitly out of scope (see below).

---

## Issue summary (source of truth)

**Problem:** When a human sends multiple messages in quick succession while a handler is still running, naive listeners either run handlers concurrently and double-reply, or process sequentially with no defined policy. Caspian's `listen()` / `_dispatch_event` currently process events one-by-one from the poll loop with **no per-conversation concurrency policy**.

**Goal:** Add an explicit, per-`conversation_id` concurrency policy to `listen()` (Python) and its TypeScript equivalent, with at least these strategies:

| Strategy | Behavior |
|---|---|
| `parallel` (`burst`) | Process overlapping messages concurrently (document the risks) |
| `queue` | Serialize handlers per conversation |
| `debounce` | Wait briefly; handle only the latest message (or merge — document the choice) |
| `drop` | If a handler is already in-flight for that conversation, drop/skip new ones (document which one is dropped) |

**Default strategy: `queue`** (safest default for agents).

**Proposed API shape** (design is part of the challenge — not fixed):
```python
client.listen(concurrency="queue")
# or
client.listen(on_overlap="debounce", debounce_ms=500)
```

**Files in scope:**

| Path | Why |
|---|---|
| `sdks/python/src/caspian_sdk/client.py` | `on_message`, `_dispatch_event`, `dispatch_pending`, `listen` — primary implementation site |
| `sdks/typescript/src/client.ts` | Mirror behavior (async handlers / `AbortSignal` if useful) |
| `sdks/python/tests/test_sdk.py` | Mocked event-burst tests |
| `sdks/typescript/test/client.test.ts` | Same, TS side |
| `sdks/python/README.md` + `sdks/typescript/README.md` | Document strategies + default |
| `examples/autoreply.py` | Optional — show recommended `queue` usage |

**Acceptance criteria (final checklist — revisit at the end of every phase):**
- [ ] Python + TypeScript APIs expose the same strategy set and the same default semantics
- [ ] Policies are per-conversation, not a global mutex (unless explicitly documented otherwise)
- [ ] Unit tests simulate bursty `message.received` events for the same `conversation_id` and assert `queue` / `debounce` / `drop` behavior
- [ ] Handler exceptions still do not kill the listener (today's resilience is preserved)
- [ ] Docs explain when to use each strategy, aimed at agent builders
- [ ] `uv run pytest` passes
- [ ] `uv run ruff check .` passes
- [ ] TS: `npm test` passes
- [ ] TS: `npm run typecheck` passes

**Explicitly out of scope:**
- Distributed locking across multiple agent processes (single-process listen loop is enough)
- Changing gateway event delivery
- Any channel adapter changes (Telegram, Slack, Discord, etc.)

---

## Phase 0 — Orientation (read-only, no code)

**Goal:** Understand the current dispatch loop before touching it.

- Read `sdks/python/src/caspian_sdk/client.py` in full, focusing on `on_message`, `_dispatch_event`, `dispatch_pending`, `listen`.
- Read `sdks/typescript/src/client.ts` for the mirrored logic.
- Read `sdks/python/tests/test_sdk.py` and `sdks/typescript/test/client.test.ts` to learn the existing mocking/test patterns (how events are simulated, what test doubles exist for channels).
- Confirm: is dispatch currently `await`-sequential (one handler fully finishes before the next event is pulled), or is there already any concurrency? This determines how invasive `parallel` will be.
- Confirm: is there already a per-conversation state store of any kind, or does one need to be introduced?

**Output of this phase:** A short written summary (no code) of:
1. How dispatch currently works, in your own words.
2. Whether `parallel` requires changing the core loop's control flow or can be layered on top.
3. Any open questions before design begins.

**Do not write any implementation code in this phase.**

---

## Phase 1 — API & design (Python side, design only)

**Goal:** Lock the concrete API shape and internal design before implementation.

- Decide the exact signature: `listen(concurrency=...)` vs `listen(on_overlap=..., debounce_ms=...)` vs both — propose one, with reasoning.
- Decide the shape of per-conversation state (e.g. `dict[conversation_id, ConversationState]`) and what each strategy needs to track (in-flight task, pending debounce timer, queued messages).
- Decide how `debounce` resolves the "latest text vs. merge" ambiguity — pick one, document why.
- Decide how exceptions inside a handler are caught so the listener survives, for **each** strategy (this can't be an afterthought — `parallel` and `queue` fail differently).
- Write this design as a short docstring/comment block at the top of the relevant section of `client.py` — no strategy logic yet.

**Output of this phase:** the design committed as comments/docstrings only, plus a one-paragraph summary for approval.

---

## Phase 2 — Python implementation: `queue` and `drop`

**Goal:** Implement the two simplest, most mechanically checkable strategies first.

- Implement `queue`: serialize handler execution per `conversation_id` (e.g. `asyncio.Lock` or per-conversation `asyncio.Queue`).
- Implement `drop`: if a handler is in-flight for that conversation, skip the new event (log/document that it's dropped, not silently lost).
- Wire both into `_dispatch_event` / `dispatch_pending` behind the `concurrency` parameter.
- Preserve existing single-conversation, non-overlapping behavior exactly (no regression when there's no burst).

**Output of this phase:** working `queue` + `drop`, no tests yet, ready for a quick manual sanity check before moving on.

---

## Phase 3 — Python implementation: `debounce` and `parallel`

**Goal:** Implement the two trickier strategies.

- Implement `debounce`: on a new message for a conversation with a pending timer, cancel the old timer and start a new one; only invoke the handler once the window elapses with no new message. Handle listener shutdown mid-debounce cleanly (no orphaned timers).
- Implement `parallel`: spawn the handler as a fire-and-forget task rather than awaiting it inline, per the Phase 0 finding on whether this needs core loop changes. Document the risk (out-of-order replies) inline as the issue requests.
- Set `queue` as the default when no strategy is specified.

**Output of this phase:** all four strategies implemented in Python, defaulting to `queue`.

---

## Phase 4 — Python tests

**Goal:** Cover the acceptance criteria's testing bar exactly.

- Simulate bursty `message.received` events for the same `conversation_id` (3+ messages in quick succession) and assert:
  - `queue`: handlers run strictly in order, none dropped, none overlapping.
  - `drop`: only the first (or documented) message's handler runs; others are skipped, not queued.
  - `debounce`: only the final message's handler runs, after the debounce window.
  - `parallel`: all handlers run concurrently (assert via timing or a shared counter, not just "no crash").
- Add a test confirming a handler exception does **not** kill the listener, for at least `queue` and `parallel`.
- Add a test confirming the default (no `concurrency` argument) behaves as `queue`.
- Run `uv run pytest` and `uv run ruff check .` — both must pass before moving on.

**Output of this phase:** green test suite, ruff clean.

---

## Phase 5 — TypeScript port

**Goal:** Mirror Phases 1–3 in `client.ts` with identical semantics, camelCase naming.

- Port the same API shape (documenting any unavoidable naming differences, e.g. `concurrency` vs `onOverlap`).
- Implement `queue`, `drop`, `debounce`, `parallel` using TS-appropriate primitives (Promise chaining / a simple mutex pattern for `queue`; `setTimeout`/`clearTimeout` for `debounce`; fire-and-forget `Promise` for `parallel`).
- Default to `queue`, matching Python.
- Preserve exception resilience (unhandled promise rejections must not kill the listener).

**Output of this phase:** TS implementation complete, no tests yet.

---

## Phase 6 — TypeScript tests

**Goal:** Same coverage bar as Phase 4, TS side.

- Mirror all Python test scenarios in `sdks/typescript/test/client.test.ts`.
- Run `npm test` and `npm run typecheck` — both must pass before moving on.

**Output of this phase:** green TS test suite, typecheck clean.

---

## Phase 7 — Documentation

**Goal:** Make the feature legible to agent builders, per acceptance criteria.

- Update `sdks/python/README.md` and `sdks/typescript/README.md`: explain all four strategies, the default, and **when to use each** (not just what they do).
- Optionally update `examples/autoreply.py` to show the recommended `queue` usage.
- Keep this concise — no marketing language, just clear guidance a developer would actually use.

**Output of this phase:** docs updated, ready for final review.

---

## Phase 8 — Final self-review before opening the PR

**Goal:** Verify against the acceptance criteria checklist above, line by line.

- Re-run: `uv run pytest`, `uv run ruff check .`, `npm test`, `npm run typecheck` — all four, one last time, in a clean state.
- Read the full diff top to bottom. Cut anything that isn't strictly necessary for the acceptance criteria (no drive-by refactors, no unrelated cleanup).
- Confirm no adapter files, gateway files, or distributed-locking code were touched.
- Draft a PR description: what was added, the API shape chosen, the default and why, a short note on the `parallel` risk tradeoff, and how to run the tests.

**Output of this phase:** a clean, reviewable diff and a PR description draft, presented for approval before anything is pushed or opened as a PR.