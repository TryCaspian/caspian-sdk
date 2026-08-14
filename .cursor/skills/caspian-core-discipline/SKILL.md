---
name: caspian-core-discipline
description: >-
  Review and enforce the Caspian A-core: programs as data, no I/O in the
  kernel, Effect (Context.Tag / Layer / Schema) in TypeScript and frozen
  Pydantic models + Protocol ports in Python. Use when writing or reviewing
  anything under core/, when adding an Event/Command, when a PR touches the
  kernel, or when checking that the codebase still follows the A plan.
---

# Caspian core discipline (plan A)

Applies to `core/` (TS and Python). This is the layer users never import.
Everything here must be **decidable without a network**.

Companion skills: `caspian-sdk-surface` (the B facade + adapters),
`functional-dsl` (why), `sdk-reliability` (fault/capacity/change).

## The one rule

> The kernel turns `(state, event, app)` into **commands as data**.
> If you cannot compute that without HTTP, a clock, or randomness, it does not
> belong in core.

Effects are described in core and executed by Layers/interpreters at the edge.

## What lives in core

| In core | Not in core |
|---|---|
| `Event` (Message / Action / Reaction) | Telegram `Update` parsing |
| `Command` (Post / Edit / React / Typing / Subscribe / SetState / Host) | `sendMessage`, Bot API |
| Predicates, `Rule`, `App` | `onMessage` facade sugar |
| Overlap state machine (queue / debounce / drop / parallel) | Redis, Postgres, `asyncio.Queue` |
| Error ADT | retries, backoff sleeps |
| Thread id **shape** | thread id **encoding** per platform (adapter) |
| Port declarations (tags / Protocols) | Port implementations |

## TypeScript: Effect

### Schema first

Every `Event`, `Command`, `Connection`, and error is an `effect/Schema` type.
Decode at the boundary; the kernel only ever sees decoded values.

```ts
export const Message = Schema.Struct({
  _tag: Schema.Literal("Message"),
  threadId: ThreadId,
  text: Schema.String,
  chatKind: Schema.Literal("dm", "group", "channel"),
});
export const Event = Schema.Union(Message, Action, Reaction);
```

Rules:

- No `unknown` / `any` crossing into core. Parse, don't validate.
- Branded ids (`ThreadId`, `ConnectionId`), not bare `string`.
- Adding a platform field to a shared Event is a **promotion decision** — it
  needs two channels that share the concept. Otherwise it stays on `raw`.

### Ports are `Context.Tag`, implementations are `Layer`

```ts
export class AdapterPort extends Context.Tag("AdapterPort")<
  AdapterPort,
  {
    readonly parse: (raw: RawInbound) => Effect.Effect<ReadonlyArray<Event>, DecodeError>;
    readonly execute: (cmd: Command, conn: Connection) => Effect.Effect<Sent, AdapterError>;
    readonly overlapKey: (event: Event) => string;
  }
>() {}
```

- Core `import`s the tag, never a Layer.
- Every port needs at least: production Layer, test Layer, and (for effectful
  deps) a failure/chaos Layer. That is the `sdk-reliability` redundancy item,
  satisfied structurally.
- `Host` (the customer's agent) is a port too — not a raw closure the kernel
  invokes blindly.

### Errors are values, `try/catch` is banned in core

```ts
export class DecodeError extends Schema.TaggedError<DecodeError>()("DecodeError", {
  reason: Schema.String,
}) {}
```

- Public functions return `Effect<A, CaspianError, R>` with a **closed** error
  union. No `Effect<A, unknown>`, no `Effect.die` for expected failures.
- No `throw` in core. No `catchAll` that swallows into a generic error.
- Exhaustive `Match` over the error union; a new constructor must break the build.

### Banned imports in `core/`

`node:*`, `fetch`, any HTTP client, `Date.now`, `Math.random`, `setTimeout`,
Redis/PG clients, `@caspian/telegram` (or any adapter), the provisioning client.
Time and entropy come from Effect's `Clock` / `Random` services.

## Python: Pydantic

Same shape, different tools. Effect is the reference; Python approximates it.

### Frozen models, parse don't validate

```python
class Message(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["message"] = "message"
    thread_id: ThreadId
    text: str
    chat_kind: Literal["dm", "group", "channel"]

Event = Annotated[Message | Action | Reaction, Field(discriminator="kind")]
```

- `frozen=True` and `extra="forbid"` everywhere in core. A silently ignored
  field is a future bug.
- Discriminated unions, not `dict[str, Any]` with `if "callback_query" in d`.
- `NewType` / constrained types for ids; never a bare `str` for a thread id.
- Validation happens **once**, at decode. Downstream code may not re-check.

### Ports are `Protocol`, results are values

```python
class AdapterPort(Protocol):
    def parse(self, raw: RawInbound) -> Result[list[Event], DecodeError]: ...
    def execute(self, cmd: Command, conn: Connection) -> Result[Sent, AdapterError]: ...
    def overlap_key(self, event: Event) -> str: ...
```

- Return `Result` (or a `CaspianError` union), do not raise across the core
  boundary. Foreign exceptions are folded into the ADT by the interpreter.
- `match` over the error union with an exhaustiveness assert
  (`assert_never`) so a new constructor fails type-check.

### Banned imports in `core/`

`httpx`, `requests`, `asyncio`, `time`, `random`, `os`, `datetime.now`,
database drivers, any provider SDK, the provisioning client. Inject a clock
and entropy if you need them.

## Parity between the two languages

The Python and TypeScript kernels are the *same program*. Keep them honest:

- Shared golden vectors: `(app, event) → commands` fixtures both implementations
  replay and must match byte-for-byte.
- Same error constructor names. Same overlap semantics
  (`queue` drains **latest** and reports `skipped`; bounded; buttons never
  share the text queue).
- If one language gains a feature, the vector file is part of the same PR.

## Review checklist

- [ ] Does core compute commands without I/O, clock, or randomness?
- [ ] Is every Event/Command a Schema (TS) or frozen Pydantic model (Py)?
- [ ] Are ids branded/`NewType`, not bare strings?
- [ ] Is the error type a closed union, matched exhaustively?
- [ ] No `throw` / `try-catch` / `raise` crossing the core boundary?
- [ ] Are ports declared as tags/Protocols with ≥1 test implementation?
- [ ] Does `Host` (customer agent) enter through a port, not an ad-hoc callback?
- [ ] Is overlap a pure transition (no queue object) with a mandatory bound?
- [ ] Did a platform-only concept get pushed to `raw`/adapter instead of core?
- [ ] Are golden vectors updated so TS and Python cannot drift?

## Red flags

- `Effect<A, unknown>` or `except Exception:` inside core.
- `if channel == "telegram"` in core — that is an adapter concern.
- A Command whose payload is `dict` / `Record<string, unknown>`.
- Overlap implemented with `asyncio.Queue` / `setInterval` in the kernel.
- Core importing anything from `provision/` or an adapter package.
- New public behavior with no Event/Command constructor behind it.

## CI enforcement (make it a build error, not a review habit)

- **TS:** `dependency-cruiser` forbidding `core/**` → `adapters/**`,
  `provision/**`, `node:*`, HTTP clients; `eslint` no-restricted-globals for
  `Date`, `Math.random`, `setTimeout`; strict mode, no implicit `any`.
- **Python:** `import-linter` contract for `core` (forbidden: `httpx`,
  `asyncio`, `time`, `random`, `os`, adapters, provision); `mypy --strict`;
  a test that every model in core is `frozen`.
- **Both:** golden-vector conformance job; property tests for overlap laws
  (ordering, boundedness, `drop ∘ queue = drop`).
