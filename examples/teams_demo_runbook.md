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

## Real Azure Bot (production proof)

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
