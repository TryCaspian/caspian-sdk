"""Microsoft Teams AI agent — OpenAI Agents SDK wired through the Caspian Teams adapter.

The LLM side follows the codex-agent-template layout: LLM_BACKEND picks the
model backend, everything else is backend-agnostic.

Two ways to run:

  EMULATOR (no Azure needed — Bot Framework Emulator as the Teams client):
    1. Gateway:  COMM_PROVIDER=teams COMM_TEAMS_ALLOW_EMULATOR=1 \
                   COMM_BOOTSTRAP_API_KEY=demo_key uv run --with uvicorn comm-gateway
    2. Model:    codex proxy on :8088 (uv run uvicorn server.codex_proxy:app --port 8088
                 in the codex-agent-template checkout), or LLM_BACKEND=openai + key
    3. Agent:    CASPIAN_BASE_URL=http://127.0.0.1:8000 CASPIAN_API_KEY=demo_key \
                   uv run --with openai-agents python examples/teams_ai_agent.py
    4. Emulator: "Open Bot", endpoint
                 http://localhost:8000/internal/providers/teams/webhooks/emulator-demo
                 (leave App ID / password empty), then chat.

  REAL AZURE BOT:
    Same as above plus TEAMS_APP_ID / TEAMS_APP_PASSWORD env vars and the
    ngrok-exposed webhook registered in Azure (see teams_demo_runbook.md).

Backends (LLM_BACKEND):
  codex  (default)  ChatGPT subscription via local codex proxy on :8088
  openai            any chat-completions endpoint via OPENAI_BASE_URL + OPENAI_API_KEY
"""

import json
import os
import uuid

from agents import (
    Agent,
    OpenAIChatCompletionsModel,
    OpenAIResponsesModel,
    Runner,
    set_tracing_disabled,
)
from openai import AsyncOpenAI

from caspian_sdk import CommClient

set_tracing_disabled(True)

INSTRUCTIONS = (
    "You are a helpful assistant replying inside Microsoft Teams. "
    "Answer concisely in plain text — a short paragraph at most, no markdown tables."
)


def _load_key() -> str:
    """API key, preferring OPENAI_API_KEY env, else the Codex auth file.

    Keeping the key in ~/.codex/auth.json means it never appears on a command
    line or in process listings.
    """
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    with open(os.path.expanduser("~/.codex/auth.json")) as f:
        return json.load(f)["OPENAI_API_KEY"]


def build_model():
    backend = os.environ.get("LLM_BACKEND", "codex")
    if backend == "codex":
        client = AsyncOpenAI(
            base_url=os.environ.get("CODEX_PROXY_BASE_URL", "http://localhost:8088/v1"),
            api_key="codex-proxy",  # proxy ignores it; SDK requires a value
        )
        return OpenAIResponsesModel(
            model=os.environ.get("CODEX_MODEL", "gpt-5.5"), openai_client=client
        )
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    # agentrouter serves the Codex Responses backend and fingerprints the client:
    # a plain bearer request is rejected as an "unauthorized client". Sending the
    # Codex CLI's own headers (originator / OpenAI-Beta / session_id) authenticates
    # it, and it speaks the Responses API rather than chat-completions.
    if "agentrouter" in base_url:
        client = AsyncOpenAI(
            base_url=base_url,
            api_key=_load_key(),
            default_headers={
                "OpenAI-Beta": "responses=experimental",
                "originator": "codex_cli_rs",
                "session_id": str(uuid.uuid4()),
            },
        )
        return OpenAIResponsesModel(
            model=os.environ.get("MODEL", "gpt-5.6-sol"), openai_client=client
        )
    client = AsyncOpenAI(base_url=base_url, api_key=_load_key())
    return OpenAIChatCompletionsModel(
        model=os.environ.get("MODEL", "gpt-4o-mini"), openai_client=client
    )


ai_agent = Agent(name="Teams AI Agent", instructions=INSTRUCTIONS, model=build_model())

client = CommClient()
customer = client.create_customer("Demo Corp")
agent = client.create_agent("Teams AI Bot")

# app_id doubles as the webhook resource id: the emulator posts to
# /internal/providers/teams/webhooks/<app_id>. The password is unused in
# emulator mode (outbound to a localhost serviceUrl is unauthenticated) but
# the connect body requires it.
app_id = os.environ.get("TEAMS_APP_ID", "emulator-demo")
app_password = os.environ.get("TEAMS_APP_PASSWORD", "emulator-placeholder")
connection = client._connect(
    "teams", customer["id"], agent["id"], app_id=app_id, app_password=app_password
)
print(f"Teams connection active: {connection['id']}  status={connection['status']}")
print(f"Webhook path: /internal/providers/teams/webhooks/{app_id}")


@client.on_message
def handle(message):
    sender = (message.sender or {}).get("address", "unknown")
    text = message.text or ""
    print(f"Inbound from {sender}: {text!r}")
    result = Runner.run_sync(ai_agent, text)
    answer = str(result.final_output)

    print(f"AI reply: {answer!r}")
    message.reply(answer)


print("Listening (Ctrl+C to stop)")
client.listen()
