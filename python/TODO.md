# Python rewrite (`python/`) — follow-ups

Tracked against PR #185. Reviewed with the `.cursor` skills
`sdk-reliability`, `functional-dsl`, `languages-as-libraries`, and
`meta-minimal-languages` (companions: `caspian-core-discipline`,
`caspian-sdk-surface`).

---

## 1. Documentation and IntelliSense

The kernel is typed for *internal* mypy, not for a bot author in an IDE.
Handlers are `Callable[..., Any]`, the public package exports only
`Caspian` and `Thread`, and there is no `py.typed`. Hovering `msg.text`
or `cx.channels.add(` does not explain the contract.

- [ ] Add `src/caspian/py.typed` and ship it in the wheel (PEP 561) so
      installed `caspian` is a typed package.
- [ ] Re-export the types handlers actually use from `caspian`:
      `Message`, `Action`, `HandlerContext`, `Stream`, `Attachment`,
      `Block`, `Button`, `Result`. Update `__all__`.
- [ ] Replace `Handler = Callable[..., Any]` with a typed Protocol so
      `def reply(thread, msg, ctx)` autocompletes `Thread` / `Message` /
      `HandlerContext`. Overload `on_message` vs `on_action` so `msg` is
      not the eight-variant `Event` union.
- [ ] Type `on_message` / `on_action` options (`channel`, `kind`,
      `overlap`, `bound`) as a TypedDict — not `dict[str, Any]`.
- [ ] Give `ChannelManager.add` the same named kwargs as
      `provision.Channels.add` (`via`, `bot_token`, `webhook_url`, …).
      `**options: Any` hides them from IntelliSense.
- [ ] Add `Field(description=...)` (or attribute docstrings) on core
      models: `Message.reply_to`, `topic_id`, `raw`, `Attachment.file_id`,
      overlap policies, error constructors. One-line class docs are not
      enough for hover.
- [ ] Document predicate helpers (`message()`, `dm()`, `channel()`).
      Either implement `message & channel("telegram") & ~dm()` or delete
      that claim from `predicates.py` — there is no `__and__` / `__or__`
      / `__invert__`.
- [ ] Add `python/README.md`: install, `on_message` + `handle` /
      `poll` / `run`, hosted vs self-host, how to type a handler.
- [ ] Export `Stream` from `Thread`’s public surface; document
      `thread.stream()` as the Chat-SDK equivalent of the published
      SDK’s `message.stream()`.

---

## 2. Skill review: `sdk-reliability`

**Already in good shape**

- Failure is data on the I/O boundary: `Result` + closed `CaspianError`
  union; `ChaosTransport` / `RecordingTransport` / `FakeGatewayClient`
  sit behind the same ports.
- Critical path `step()` is small, pure, and total enough to test
  without a network. Overlap is a pure FSM with a bound (default 16).
- Import-linter keeps I/O out of core. `test_skill_gates.py` freezes
  every core BaseModel and asserts chaos returns an error *value*.

**Gaps**

- [ ] **Fault — failover, not only chaos.** `ChaosTransport` always
      fails. There is no symmetric failover interpreter (retry / backup
      transport / degraded send). `HttpTransport` opens a new
      `httpx.Client` per dispatch and has no health check.
- [ ] **Failure as data on the public surface.** `channels.add` raises
      `KeyError` / `provision.ProvisionError` (an Exception, distinct
      from `core.errors.ProvisionError`). `handle` / `poll` already
      return `Result`; add should too, or at least one error type.
- [ ] **`Result` is not generic.** `value: Any` means callers cannot
      match on `list[Event]` vs `Sent`. Make `Result[T]` and type
      `AdapterPort.parse` / `execute` accordingly.
- [ ] **Capacity.** No client-side rate limiter, no backpressure on
      outbound HTTP, no SLO/health surface. Overlap bound is the only
      explicit limit; `_pending` is a dict of latest-per-key (fine) but
      unbounded in the number of keys.
- [ ] **Change.** `python/` is not in the uv workspace or CI
      `testpaths` (root `pyproject.toml` still tests `sdks/python`
      only). No API-diff / semver gate. Hosted paths never hit the live
      gateway.
- [ ] **Critical-path hygiene.** `ProcessInterpreter` uses
      `getattr(adapter, "verify"|"acknowledge"|"channel_of")` — those
      methods are not on `AdapterPort`, so the CP is structurally
      optional. Put them on the Protocol. Replace
      `except Exception` in `_Sink` with a typed probe.
- [ ] **Overlap exhaustiveness.** `overlap_transition`’s `case _:`
      defaults to EXECUTE. A new policy should fail type-check
      (`assert_never`), not silently run.

---

## 3. Skill review: `functional-dsl`

**Already in good shape**

- Programs are data: `App` / `Rule` / `Command`. Interpreters
  (`MemoryInterpreter`, `ProcessInterpreter`, hosted poll) are
  functions over that data.
- Denotation exists in `docs/caspian-a-plus-b.md` before the facade
  syntax. `on_message` desugars to `Rule`s.
- Initial encoding is the right call (introspection, golden vectors,
  hosted/self-host running the same `App`).
- Adapters parse wire bytes into Events (parse, don’t validate at the
  kernel). Unsupported Telegram commands return `AdapterError`, they
  do not no-op.

**Gaps**

- [ ] **Illegal states still representable.** `Call.args: dict[str, Any]`,
      `Block.content: dict[str, Any]`, `Message.raw` / `metadata`, and
      `Host` with `arbitrary_types_allowed`. Tighten or keep them
      explicitly on the untrusted boundary (`raw` only).
- [ ] **`Result` at every parse.** `test_vectors.parse_event` still
      `raise ValueError` for unknown kinds; vector decode should be
      `model_validate` on the discriminated `Event` union.
- [ ] **Laws as tests.** Identity / associativity for predicates,
      overlap laws beyond the few in `test_skill_gates` (boundedness,
      `drop` absorbing, queue-drains-latest already present). Property
      tests, not more examples.
- [ ] **Pretty-print / explain interpreter.** Memory + process + chaos
      exist; there is no “why didn’t it reply?” trace interpreter, which
      is the denotation the design docs sell.

---

## 4. Skill review: `languages-as-libraries`

The rewrite ships as a Python library over the host (no compiler fork).
B-surface sugar expands to a small core IR (`Event` / `Command` /
`Rule`). That matches the recipe.

**Gaps**

- [ ] **Static info does not persist for consumers** — no `py.typed`,
      no typed handler Protocol, no boundary contract when untyped
      user callbacks cross into the kernel. This is the same work as
      §1; it is a languages-as-libraries failure, not just docs.
- [ ] **Core IR analysis.** `AdapterPort` is incomplete vs what
      `ProcessInterpreter` actually calls (`verify`, `acknowledge`).
      Expand-to-core only works if the core forms are the ones the
      runner understands.
- [ ] **`cx.use(Rule)` is an undocumented escape hatch** — fine as a
      power-user module language, but it must not appear in the
      README before `on_message({...})` (`caspian-sdk-surface`).

---

## 5. Skill review: `meta-minimal-languages`

**Already in good shape**

- Adding a self-host channel is a new adapter + registry entry; hosted
  channels (bluesky, zulip, gmeet, rcs) no longer require a local
  adapter. Public `on_message` does not grow.
- `Call` is the escape hatch so the kernel does not absorb every
  platform verb.
- Tools are a view over Command models, not a second language.

**Gaps**

- [ ] **Command algebra grew 8 → 21.** Watch for a mega-language.
      Shared concept on ≥2 channels before promoting; otherwise keep
      it on the adapter / `Call`. `OpenModal` / `UpdateModal` exist in
      core but are **not** on `Thread` — either surface them or demote
      them.
- [ ] **Performance / adaptation interface is missing.** Overlap
      policy is the only first-class knob. No rate-limit, timeout,
      retry, or capacity object separate from domain calls
      (`thread.post`).
- [ ] **Platform special case in provision:** Telegram always requires
      `bot_token` even when hosted. If that is a real invariant, type
      it (per-channel provision schema); do not grow a chain of
      `if channel == "telegram"` in paperwork.
- [ ] **Runtime sensing.** No telemetry, tool-health, or contention
      events for the runner to adapt. Chaos is test-only.

---

## 6. Companion notes (`caspian-core-discipline` / `caspian-sdk-surface`)

Already enforced in spirit by `test_skill_gates.py` (overlap drain,
Host as a port, tools from Commands, inbound ownership, one pipeline,
frozen models). Remaining:

- [ ] `AdapterPort` should declare `verify` / `acknowledge` /
      `capabilities` / `format` (surface adapter laws), not duck-type
      them in the interpreter.
- [ ] Unsupported optional capabilities should be typed absent, not
      discovered at runtime via `"edit" in caps`.
- [ ] Golden vectors cover message/action/reaction only; newer events
      (`Edited`, `Deleted`, `MemberJoin`, …) and STREAM overlap have
      no shared vector. TS parity cannot be claimed until they do.
- [ ] `python/` still not in CI — the skill says make core contracts a
      **build error**, not a review habit.
- [ ] Cannot register an event webhook URL (`PUT /v1/webhook`).
      Receiving a pushed event works; announcing the URL does not.
