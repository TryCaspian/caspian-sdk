# Architecture

You write `onMessage` and `thread.post`. Caspian turns that into a list of rules the kernel can test without talking to Slack or Telegram. Adapters are the only code that knows a channel. A runner — our gateway, your process, or a test — sends the messages and remembers the conversation. Hosted is the default; self-host is opt-in.

```mermaid
flowchart TB
    You[Your agent] --> SDK[SDK]
    SDK --> Kernel[Kernel]
    Kernel --> Runner[Runner]
    Runner --> Adapters[Adapters]
    Adapters --> Channels[Slack · Discord · Telegram · …]
```
