# caspian-adapters

Channel adapters for AI-agent communication — **Slack, Discord, GitHub issues/PRs, Telegram (bot + user-account), Instagram DM, Facebook Messenger, X, email (AWS SES), Google Meet, and GSM-modem SMS** — all behind one small provider interface: `provision` / `send` / `reply` / `parse_webhook`, with per-channel capability negotiation.

Bring your own platform credentials; each adapter speaks the platform's official API and verifies its webhooks (Slack signing secret, GitHub/Meta `X-Hub-Signature-256`, Telegram secret header, X CRC, SES SNS signatures).

```python
from caspian_adapters import Settings, build_providers

providers = build_providers(Settings(providers="fake"))  # in-memory email for dev
email = providers["fake"]
result = email.send("inbox-1", OutboundMessage(to=["dev@example.com"], text="hi"))
```

## Inbound event types

`parse_webhook` returns a list of `InboundEvent` — one platform endpoint multiplexes
several kinds of event over the same signature envelope, so a single parse call
yields a mixed list rather than the caller guessing which parser to run:

| Type | Meaning | Capability |
| --- | --- | --- |
| `InboundMessage` | A human sent text (or media) | `receive` (baseline) |
| `InboundReaction` | An emoji was added to or removed from one of our messages | `reactions` |
| `InboundCommand` | An explicit `/command` invocation | `slash_commands` |

Reactions and commands are separate types on purpose. An agent that greps `/deploy`
out of message text also fires when someone *quotes* the command mid-sentence; an
explicit event has no such ambiguity. Adapters declare `Capability.REACTIONS` /
`Capability.SLASH_COMMANDS` only when the platform really carries them, so email
never pretends to have Slack reactions.

Note that Slack posts slash commands as `application/x-www-form-urlencoded` rather
than JSON, on a separate Request URL, signed over the same raw body — see
`slack.py` for how one verification path serves both encodings.

Additional providers (hosted channels like WhatsApp Business numbers, phone/voice, iMessage, RCS) register through the `caspian.providers` entry-point group:

```toml
[project.entry-points."caspian.providers"]
my-channel = "my_pkg.providers:build_my_channel"
```

Part of [Caspian](https://github.com/TryCaspian/caspian-sdk). Managed channels with the same interface: [trycaspianai.com](https://trycaspianai.com).
