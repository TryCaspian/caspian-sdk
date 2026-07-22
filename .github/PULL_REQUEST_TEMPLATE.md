**What & why**


**Checklist**
- [ ] `uv run pytest` green
- [ ] `uv run ruff check .` clean
- [ ] TypeScript changes: `cd sdks/typescript && npm ci && npm test && npm run typecheck`
- [ ] New adapter? → official platform API only, webhook signature verification, an offline fake, and tests (normalize + verify-accept + verify-reject)
- [ ] No real credentials anywhere — obviously-fake placeholders only
