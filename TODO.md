# Repo cleanup follow-ups

Removed the legacy clients and docs. Track replacements here.

## Deleted (do not restore as-is)

- `sdks/python` — replaced by `packages/python/` (`caspian`)
- `sdks/typescript` — replaced by `packages/typescript/` (`caspian`)
- `apps/cli` — old `comm` / `caspian` CLI (no replacement yet)
- `examples/` — all targeted the published `caspian-sdk` API
- Root `README.md`, `README.zh-CN.md`, `CONTRIBUTING.md`, `llms.txt`

## Replace

- [ ] **README.md** — monorepo overview for the new layout (`packages/python/`, `packages/typescript/`, `server/`, other `packages/*`).
- [ ] **README.zh-CN.md** — Chinese mirror of the new README (optional until EN ships).
- [ ] **CONTRIBUTING.md** — fork/PR flow, `uv sync` + `packages/python` tests, `packages/typescript` bun CI, adapter rules if still relevant.
- [ ] **llms.txt** — agent-oriented integration guide against the new `caspian` Python/TS surfaces (not `caspian-sdk` / `CommClient`).
- [ ] **examples/** — small runnable samples on `packages/python/` and `packages/typescript/` (not `caspian_sdk` / `CommClient`).
- [ ] **CLI** — decide whether to revive a `caspian` CLI (init / connect / listen) against the new SDK or gateway, or drop the product surface.
- [ ] **interactive_test.py** — rewrite against `packages/python/` (Telegram interactions + blocks) or drop.

## Left as-is (packages/)

- `packages/openclaw` and `packages/opencode` still depend on the published npm package `caspian-sdk`. Do not delete those packages; migrate their deps to `caspian` / `packages/typescript` in a follow-up when ready.
