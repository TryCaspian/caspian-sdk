# Contributing

Thanks for helping build Caspian's open core.

## Ways to contribute

New here? Good places to start:

- **Good first issues** - browse the [`good first issue`](https://github.com/TryCaspian/caspian-sdk/labels/good%20first%20issue) label.
- **Examples** - add or improve a runnable agent in [`examples/`](./examples); small, self-contained, well-documented.
- **Docs fixes** - typos, unclear steps, missing prerequisites. Small doc PRs are very welcome.
- **New language SDKs** - port the client to Go, Ruby, Rust, etc. (see `sdks/` for the Python and TypeScript references).
- **Channel adapters** - add support for a new platform (see "Adding a new channel adapter" below).

## Fork & pull request

You don't have push access to this repo, so contribute through a fork:

1. **Fork** this repo (top-right on GitHub), then clone your fork:
   ```bash
   git clone https://github.com/<your-username>/caspian-sdk.git
   cd caspian-sdk
   git remote add upstream https://github.com/TryCaspian/caspian-sdk.git
   ```
2. Create a branch: `git checkout -b my-change`.
3. Make your change and keep tests + lint green (see below).
4. Push to your fork and open a **pull request** against `TryCaspian/caspian-sdk:main`.

## Setup

This is a small monorepo: a **Python** side (a uv workspace: SDK, adapters, CLI) and
a **TypeScript** SDK.

**Python (SDK, adapters, CLI):**

```bash
uv sync
uv run pytest        # everything should be green before you start
uv run ruff check .
```

**TypeScript SDK** (`sdks/typescript`):

```bash
cd sdks/typescript
npm install
npm run build
npm test             # vitest
npm run typecheck    # tsc --noEmit
```

## What lives where

- `packages/adapters` — channel adapters. Each adapter implements the small provider interface in `caspian_adapters/base.py`: `provision` / `send` / `reply` / `parse_webhook` (+ optional `typing`, OAuth hooks), a `capabilities` set, and webhook signature verification.
- `sdks/python` — the Python `caspian-sdk` client.
- `sdks/typescript` — the TypeScript / JavaScript `caspian-sdk` client (published to npm).
- `apps/cli` — the `caspian` CLI. It ships as a separate package `caspian-cli` (`pip install caspian-cli`, or run without installing via `uvx caspian-cli`), while `caspian-sdk` is the library. `comm` is a legacy alias only.

## Adding a new channel adapter

1. Implement the provider interface in a new module under `packages/adapters/src/caspian_adapters/`.
2. Register it in `registry.py` (or ship it as your own package via the `caspian.providers` entry-point group — no fork needed).
3. Add an in-memory fake that consumes the platform's real inbound payload shape, so integrations can be tested offline.
4. Add tests: payload normalization, webhook signature verification (accept + reject), and any routing rules.
5. Use only the platform's **official API**. Adapters that automate private/unofficial protocols, evade platform enforcement, or enable bulk unsolicited messaging will not be accepted.

## Ground rules

- Python: `uv run pytest` and `uv run ruff check .` must pass.
- TypeScript: in `sdks/typescript`, `npm test` and `npm run typecheck` must pass.
- No secrets in code, tests, or fixtures — use obviously-fake placeholder values.
- Webhook verification is not optional: if the platform signs its webhooks, the adapter must verify the signature and reject mismatches.
- Commit messages: concise imperative subject ("add reminder example", not "added"/"adds"), no emojis, and reference the issue number when there is one (e.g. `fix email triage classifier (#42)`).

## Questions / community

- Open a thread in [GitHub Discussions](https://github.com/TryCaspian/caspian-sdk/discussions) for questions, ideas, or help.
<!-- Discord invite: TODO -->

## Reporting security issues

See [SECURITY.md](SECURITY.md) — please don't open public issues for vulnerabilities.
