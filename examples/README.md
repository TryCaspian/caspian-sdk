# Examples

Minimal, runnable agents built on `caspian-sdk`. Each file is self-contained and
documented at the top - read the docstring, set your API key, and run it.

**New here?** Start with [`email_triage.py`](./email_triage.py) or
[`autoreply.py`](./autoreply.py) - they run with just an API key (email works on
the hosted gateway out of the box). The others need channel credentials (a bot
token, a Slack app, or a carrier account), noted in the Setup column below.

## Prerequisites

- Set `CASPIAN_API_KEY` (get one from the [dashboard](https://dashboard.trycaspianai.com)).
- `CASPIAN_BASE_URL` is optional - it defaults to the hosted gateway at
  `https://api.trycaspianai.com`.

```bash
export CASPIAN_API_KEY=...
uv run python examples/<file>.py
```

## Index

| File | What it shows | Channel | Setup |
| --- | --- | --- | --- |
| [`autoreply.py`](./autoreply.py) | The core loop: connect, `on_message`, `listen` | Email | API key only |
| [`email_triage.py`](./email_triage.py) | Keyword-classify inbound mail and reply per category | Email | API key only |
| [`one_handler_three_channels.py`](./one_handler_three_channels.py) | One `on_message` handler serving three channels at once | Discord + Telegram + Email | Discord + Telegram bot tokens |
| [`slack_support_bot.py`](./slack_support_bot.py) | One-click install + replies with rich message blocks | Slack | Open the printed install URL |
| [`slack_slow_agent.py`](./slack_slow_agent.py) | `listen(ack=)` so a slow handler survives Slack's timing | Slack | Bring-your-own Slack app creds |
| [`telegram_reminders.py`](./telegram_reminders.py) | Agent-initiated reminder via `send_message` | Telegram | Telegram bot token |
| [`reminder.py`](./reminder.py) | Proactive cold-start with `initiate()` | SMS | Bring-your-own carrier (Twilio/Telnyx) |

Bring-your-own credentials are passed as keyword arguments to the matching
`connect_*` call - see each file's docstring for the exact variables.
