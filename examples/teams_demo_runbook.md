# Teams Adapter End-to-End Demo Runbook

## Fake provider (no Azure needed — for local proof)

### 1. Start the gateway

```bash
cd server
COMM_PROVIDER=fake-teams COMM_BOOTSTRAP_API_KEY=demo_key uv run --with uvicorn comm-gateway
```

### 2. Run the demo agent (new terminal)

```bash
cd ..   # repo root
CASPIAN_BASE_URL=http://127.0.0.1:8000 CASPIAN_API_KEY=demo_key \
  uv run --with httpx python examples/teams_autoreply.py
```

Expected output:
```
Teams connection active: conn_<id>  status=active
Provider address: teams:demo-<hex>
Simulated webhook -> HTTP 204
Listening (Ctrl+C to stop)
Inbound from user-aad-id: 'Hello from Teams!'
```

Gateway log confirms reply:
```
POST /internal/providers/fake-teams/webhooks  204
POST /v1/messages/<id>/reply                  201
```

Screenshot these two terminal windows together.

---

## Bot Framework Emulator (real Bot Framework protocol, no Azure)

The [Bot Framework Emulator](https://github.com/microsoft/BotFramework-Emulator/releases)
speaks the same Activity protocol as Teams and exercises the real `teams`
provider end to end — inbound webhook parsing, conversation routing, and
outbound replies over the connector API — without an Azure Bot registration.

### 1. Start the gateway (emulator mode)

```bash
cd server
COMM_PROVIDER=teams COMM_TEAMS_ALLOW_EMULATOR=1 COMM_BOOTSTRAP_API_KEY=demo_key \
  uv run --with uvicorn comm-gateway
```

`COMM_TEAMS_ALLOW_EMULATOR=1` skips connector JWT verification for activities
with `channelId: "emulator"` and sends unauthenticated replies to the
emulator's localhost serviceUrl. Local development only.

### 2. Start the model backend

Codex proxy from the codex-agent-template checkout (uses a ChatGPT
subscription, no API key):

```bash
uv run uvicorn server.codex_proxy:app --port 8088
```

Or skip the proxy and use any OpenAI-compatible key via
`LLM_BACKEND=openai OPENAI_API_KEY=sk-...`.

### 3. Run the AI agent (new terminal)

```bash
CASPIAN_BASE_URL=http://127.0.0.1:8000 CASPIAN_API_KEY=demo_key \
  uv run --with openai-agents python examples/teams_ai_agent.py
```

It prints the webhook path, e.g.
`/internal/providers/teams/webhooks/emulator-demo`.

### 4. Connect the emulator

Open Bot → Bot URL:

```
http://localhost:8000/internal/providers/teams/webhooks/emulator-demo
```

Leave Microsoft App ID and password **empty** → Connect → type a message.

### 5. Proof

The agent terminal shows `Inbound from ...` and `AI reply: ...`; the emulator
chat shows the LLM-generated reply. Screenshot the emulator conversation next
to the agent terminal.

---

## Real Azure Bot (manual runbook)

### Prerequisites
- Azure Bot registration (create at portal.azure.com → Azure Bot)
- Note `App ID` and `App Password` (client secret)
- ngrok or similar to expose local gateway

### 1. Expose gateway publicly

```bash
ngrok http 8000
# note the https URL, e.g. https://abc123.ngrok.io
```

### 2. Set Bot Framework messaging endpoint

In Azure portal → your Bot → Configuration:
```
Messaging endpoint: https://abc123.ngrok.io/internal/providers/teams/webhooks
```

### 3. Start gateway

```bash
cd server
COMM_PROVIDER=teams \
COMM_TEAMS_MESSAGING_ENDPOINT=https://abc123.ngrok.io/internal/providers/teams/webhooks \
COMM_BOOTSTRAP_API_KEY=demo_key \
  uv run --with uvicorn comm-gateway
```

### 4. Run demo agent

```bash
CASPIAN_BASE_URL=http://127.0.0.1:8000 \
CASPIAN_API_KEY=demo_key \
TEAMS_APP_ID=<your-app-id> \
TEAMS_APP_PASSWORD=<your-app-password> \
  uv run --with httpx python examples/teams_autoreply.py --real
```

### 5. Send a message in Teams

Install the bot in a Teams channel or chat, send any message.
The agent prints the inbound text and replies automatically.

Screenshot: Teams chat showing bot reply + terminal showing `Inbound from ...: '...'`
