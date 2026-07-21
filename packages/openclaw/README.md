# openclaw-caspian

Give an [OpenClaw](https://github.com/openclaw/openclaw) agent one Caspian identity across every channel — **iMessage without a Mac, WhatsApp Business, real phone/SMS**, email, Slack, Discord, Telegram, Instagram.

```bash
openclaw plugins install openclaw-caspian   # clawhub:openclaw-caspian once listed
```

Config: set `COMM_API_KEY` (mint one: `POST {base}/v1/projects/sandbox`, no signup) and `COMM_BASE_URL`. Optional channel config: `channels` allowlist, `allowFrom` sender allowlist, `displayName`.

One Caspian conversation = one OpenClaw session (`caspian:<channel>:<conversation>`); replies thread natively. Paid hosted channels use prepaid credit — a 402 tells the agent exactly how to top up (`POST /v1/billing/topup`), no dashboard.

> **Beta.** v1 declares text + threaded replies only (no media/live-preview claims we can't prove). Remaining before ClawHub listing: contract-test proofs against a live OpenClaw checkout. Built on [`caspian-sdk`](https://www.npmjs.com/package/caspian-sdk) (zero deps). Apache-2.0.
