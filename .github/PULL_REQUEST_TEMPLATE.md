**What & why**


**Checklist**
- [ ] `uv run pytest` green
- [ ] `uv run ruff check .` clean
- [ ] TypeScript changes: `cd packages/typescript && bun install && bun run ci`
- [ ] TypeScript CLI: `cd packages/cli && bun install && bun run ci`
- [ ] New adapter? → official platform API only, webhook signature verification, an offline fake, and tests (normalize + verify-accept + verify-reject)
- [ ] No real credentials anywhere — obviously-fake placeholders only
