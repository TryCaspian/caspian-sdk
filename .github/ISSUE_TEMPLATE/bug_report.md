---
name: Bug report
about: Something broke — an adapter, the SDK, or the CLI
labels: bug
---

**Which package**
`caspian-adapters` / `caspian-sdk` (Python) / `caspian-sdk` (npm) / `comm` CLI

**Channel** (if adapter-related)
slack / discord / telegram / telegram-user / instagram / facebook / x / ses / gmeet / gsm-modem / fake-*

**What happened**
What you did, what you expected, what you got instead. Tracebacks welcome.

**Repro**
Minimal code or steps. Use the in-memory fakes (`fake`, `fake-telegram`, `fake-slack`, ...) if the bug reproduces offline.

**Versions**
`pip show caspian-sdk caspian-adapters` / `npm ls caspian-sdk`, Python/Node version, OS.

⚠️ Never paste real tokens, page access tokens, signing secrets, or message content from real users.
