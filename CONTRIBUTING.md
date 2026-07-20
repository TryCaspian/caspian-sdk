# Contributing

Thanks for helping build Caspian's open core.

## Setup

```bash
git clone https://github.com/Clawies/caspian.git
cd caspian && uv sync
uv run pytest        # everything should be green before you start
uv run ruff check .
```

## What lives where

- `packages/adapters` — channel adapters. Each adapter implements the small provider interface in `caspian_adapters/base.py`: `provision` / `send` / `reply` / `parse_webhook` (+ optional `typing`, OAuth hooks), a `capabilities` set, and webhook signature verification.
- `sdks/python` — the `caspian-sdk` client.
- `apps/cli` — the `comm` CLI.

## Adding a new channel adapter

1. Implement the provider interface in a new module under `packages/adapters/src/caspian_adapters/`.
2. Register it in `registry.py` (or ship it as your own package via the `caspian.providers` entry-point group — no fork needed).
3. Add an in-memory fake that consumes the platform's real inbound payload shape, so integrations can be tested offline.
4. Add tests: payload normalization, webhook signature verification (accept + reject), and any routing rules.
5. Use only the platform's **official API**. Adapters that automate private/unofficial protocols, evade platform enforcement, or enable bulk unsolicited messaging will not be accepted.

## Ground rules

- `uv run pytest` and `uv run ruff check .` must pass.
- No secrets in code, tests, or fixtures — use obviously-fake placeholder values.
- Webhook verification is not optional: if the platform signs its webhooks, the adapter must verify the signature and reject mismatches.

## Reporting security issues

See [SECURITY.md](SECURITY.md) — please don't open public issues for vulnerabilities.
