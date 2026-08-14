# AGENTS.md

## Cursor Cloud specific instructions

This is the **Caspian SDK monorepo**: Python SDK + CLI, a self-hostable FastAPI
**gateway** (`server/`), a TypeScript SDK, and two framework plugins
(`packages/openclaw`, `packages/opencode`). Standard dev commands live in
[`CONTRIBUTING.md`](./CONTRIBUTING.md) and [`server/README.md`](./server/README.md);
the notes below only cover non-obvious things.

### Toolchains
- Three toolchains are used: **uv** (Python, installs to `~/.local/bin`),
  **node/npm** (preinstalled), and **bun** (installs to `~/.bun/bin`). The update
  script installs `uv` and `bun` and pre-syncs deps. `bun` adds itself to
  `~/.bashrc`; if `uv`/`bun` are not on `PATH` in a fresh shell, use
  `export PATH="$HOME/.local/bin:$HOME/.bun/bin:$PATH"`.

### Python: two SEPARATE uv projects
- The repo root is a uv **workspace** (`sdks/python` + `apps/cli`) with its own
  `.venv`. `server/` is a **separate** uv project with its own `.venv`. Run each
  set of commands from its own directory; `uv sync` in one does not cover the other.
- **Gotcha:** `server/pyproject.toml` has no test dependency group, so
  `cd server && uv run pytest` fails with "Failed to spawn: pytest". Run the
  gateway's ~391 tests with `cd server && uv run --with pytest pytest`.
- Root Python lint/test (from repo root): `uv run ruff check .` and `uv run pytest`
  (76 tests). `ruff check .` from the root also lints `server/`.

### Running the gateway (no Docker needed)
- Docker is **not** available in this environment; ignore the `docker compose up`
  path in the docs and use the local path: `cd server && uv run comm-gateway`
  → serves on `http://127.0.0.1:8000` (`/docs` for OpenAPI).
- Defaults to **SQLite** (`server/comm.db`) + the in-memory **`fake`** provider, so
  it boots with no credentials. Set `COMM_BOOTSTRAP_API_KEY=comm_dev_key_change_me`
  to seed a default project + usable API key on first boot.
- Startup logs a harmless `no provider configured for 'fake-linear'` warning from a
  seeded demo connection — not a failure.

### End-to-end testing without real channels
- Point the SDK at the local gateway: `CommClient(base_url="http://127.0.0.1:8000",
  api_key="comm_dev_key_change_me")`, then `connect_email()` (zero-config, no creds).
- Inject an inbound message through the real pipeline with `client.test_email(...)`
  (`POST /v1/test-emails`) instead of needing a live channel; a running
  `client.listen()` loop will dispatch it to your `@on_message` handler.

### TypeScript SDK and plugins
- `sdks/typescript` (npm): `npm ci`, `npm run typecheck`, `npm test` (vitest, 58),
  `npm run build`.
- `packages/openclaw` (npm): `npm install`, `npm run typecheck`, `npm test`
  (vitest, 6), `npm run build`.
- `packages/opencode` (**bun** — see its `.cursor/rules`): `bun install`,
  `bun run typecheck`, `bun test` (99), `bun run build`.
