# Examples

Minimal, runnable agents built on `caspian-sdk`. Each file is self-contained and
documented at the top - read the docstring, set your API key, and run it.

## Prerequisites

- Set `CASPIAN_API_KEY` (get one from the [dashboard](https://dashboard.trycaspianai.com)).
- `CASPIAN_BASE_URL` is optional - it defaults to the hosted gateway at
  `https://api.trycaspianai.com`.

```bash
export CASPIAN_API_KEY=...
uv run python examples/<file>.py
```

## Index

| File | What it shows | Channel |
| --- | --- | --- |
| [`autoreply.py`](./autoreply.py) | The core loop: connect, `on_message`, `listen` | Email |
| [`email_triage.py`](./email_triage.py) | Keyword-classify inbound mail and reply per category | Email |
| [`slack_support_bot.py`](./slack_support_bot.py) | One-click install + replies with rich message blocks | Slack |
| [`reminder.py`](./reminder.py) | Proactive outbound with `initiate()` | SMS |
