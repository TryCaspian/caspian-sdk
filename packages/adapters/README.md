# caspian-adapters

Channel adapters for AI-agent communication — **Slack, Discord, GitHub issues/PRs, Telegram (bot + user-account), Instagram DM, Facebook Messenger, X, email (AWS SES), Google Meet, and GSM-modem SMS** — all behind one small provider interface: `provision` / `send` / `reply` / `parse_webhook`, with per-channel capability negotiation.

Bring your own platform credentials; each adapter speaks the platform's official API and verifies its webhooks (Slack signing secret, GitHub/Meta `X-Hub-Signature-256`, Telegram secret header, X CRC, SES SNS signatures).

```python
from caspian_adapters import Settings, build_providers

providers = build_providers(Settings(providers="fake"))  # in-memory email for dev
email = providers["fake"]
result = email.send("inbox-1", OutboundMessage(to=["dev@example.com"], text="hi"))
```

Additional providers (hosted channels like WhatsApp Business numbers, phone/voice, iMessage, RCS) register through the `caspian.providers` entry-point group:

```toml
[project.entry-points."caspian.providers"]
my-channel = "my_pkg.providers:build_my_channel"
```

Part of [Caspian](https://github.com/TryCaspian/caspian-sdk). Managed channels with the same interface: [trycaspianai.com](https://trycaspianai.com).
