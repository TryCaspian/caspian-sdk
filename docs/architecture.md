# Architecture

You write `onMessage` and `thread.post`. Caspian turns that into a list of rules the kernel can test without talking to Slack or Telegram. Adapters are the only code that knows a channel. A runner — our gateway, your process, or a test — sends the messages and remembers the conversation. Hosted is the default; self-host is opt-in.

## A turn

Every message takes the same path. The adapter translates; the kernel decides; your handler replies.

```mermaid
sequenceDiagram
    participant Channel
    participant Adapter
    participant Kernel
    participant Handler as Your handler

    Channel->>Adapter: inbound
    Adapter->>Kernel: Event
    Kernel->>Handler: matching rule
    Handler->>Kernel: thread.post
    Kernel->>Adapter: Command
    Adapter->>Channel: send
```

## Hosted

The platform talks to Caspian, not to you. Caspian ACKs immediately, then delivers the event to your app.

```mermaid
sequenceDiagram
    participant Channel
    participant Gateway as Caspian gateway
    participant App as Your app

    Channel->>Gateway: webhook
    Gateway-->>Channel: ACK
    Gateway->>App: Event
    App->>App: onMessage
    App->>Gateway: thread.post
    Gateway->>Channel: send
```

## Self-host

The platform hits your webhook. Same kernel, no Caspian in the middle.

```mermaid
sequenceDiagram
    participant Channel
    participant App as Your app

    Channel->>App: webhook
    App->>App: parse, onMessage
    App->>Channel: send
```
