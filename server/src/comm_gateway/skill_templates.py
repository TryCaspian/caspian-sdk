"""Framework scaffolds served at /SKILL/{slug}.md — the "custom agent" fork.

Each spoke is a complete, writable-from-context scaffold for one agent
framework in one language. The shape is identical everywhere:

    my-agent/
    ├── agent.(py|ts)     the brain — pure framework code, exports ask()
    ├── caspian/          the comms layer — pure Caspian, imports ask()
    ├── .env.example
    └── (package.json | pyproject.toml)

The seam is the whole design: framework imports never appear inside caspian/,
Caspian imports never appear outside it. A framework change touches agent.*;
an SDK change touches caspian/. The caspian/ folder is byte-identical across
every spoke of a language.

Python note, load-bearing: the installed SDK's import name is also `caspian`.
A caspian/ FOLDER is safe only while it has no __init__.py (a plain folder
loses to an installed regular package under PEP 420; with __init__.py it
shadows the SDK and every import breaks). The scaffold and the spoke text both
say so, because a coding agent's instinct is to "helpfully" add __init__.py.

Spokes use the 1.0 API (Caspian / channels.add / on_message). Text is served
verbatim except the literal {BASE_URL}, replaced per request — .format() is
unusable here because the embedded code is full of braces.
"""

from __future__ import annotations

# ─── the shared caspian/ layer, python ───────────────────────────────────────

PY_CASPIAN_BOT = '''"""Caspian comms layer. This folder gives the agent an address.

The brain (../agent.py) never imports Caspian; this folder never implements
the brain. Delete this folder and the agent still thinks - nobody can reach it.

RULES
- Run it:   python caspian/bot.py
- NEVER add an __init__.py to this folder. The installed SDK's import name is
  also `caspian`; an __init__.py here shadows it and every import breaks.
"""

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from caspian import Caspian   # the SDK (pip install caspian-sdk)
from agent import ask         # the brain

# ---- connections: which channels this agent is reachable on -----------------
# Hosted mode: the Caspian gateway owns credentials and inbound. Email needs
# nothing; Telegram always needs a BotFather token, hosted or not.
cx = Caspian(api_key=os.environ["CASPIAN_API_KEY"])
cx.channels.add("email")
if os.environ.get("TELEGRAM_BOT_TOKEN"):
    cx.channels.add("telegram", bot_token=os.environ["TELEGRAM_BOT_TOKEN"])

# Self-host instead (your process talks to the platform, no gateway):
#   cx = Caspian()
#   cx.channels.add("telegram", via="self-host",
#                   bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
#                   webhook_url="https://your.server/telegram")
#   then feed your HTTP route's bytes to cx.handle("telegram", body, headers),
#   or hold a socket with cx.listen("discord") / cx.listen("slack").

# Optional allowlist: comma-separated sender addresses/ids. Empty = everyone.
# Set this when the brain can act on your machine (a coding agent CLI) - an
# open inbox to a tool-wielding agent is remote code execution by DM.
_ALLOWED = {
    s.strip() for s in os.environ.get("CASPIAN_ALLOWED_SENDERS", "").split(",") if s.strip()
}

# ---- handlers: one rule answers every channel -------------------------------
@cx.on_message({"overlap": "queue", "ack": "On it, one moment..."})
def handle(thread, msg, ctx):
    if _ALLOWED and msg.sender not in _ALLOWED:
        return
    with thread.stream() as out:      # posts once, then edits as it writes
        out.append(ask(msg.text))


if __name__ == "__main__":
    print("caspian: polling for inbound - message any connected channel")
    cx.run()
'''

# ─── the shared caspian/ layer, typescript ───────────────────────────────────

TS_CASPIAN_CONNECTIONS = '''/* caspian/connections.ts - which channels this agent is reachable on.
   Hosted mode: the Caspian gateway owns credentials and inbound. */
import { Caspian } from "caspian-sdk"

export const cx = new Caspian()

export async function connect(): Promise<void> {
  await cx.channels.add("email", { via: "hosted" })
  if (process.env.TELEGRAM_BOT_TOKEN) {
    await cx.channels.add("telegram", {
      via: "hosted",
      bot_token: process.env.TELEGRAM_BOT_TOKEN,
    })
  }
  // Self-host instead (no gateway): via: "self-host" plus your own tokens,
  // then feed your HTTP route to cx.handle(channel, body, headers), or hold
  // a socket with cx.listen("discord") / cx.listen("slack").
}
'''

TS_CASPIAN_HANDLERS = '''/* caspian/handlers.ts - one rule answers every channel. */
import { cx } from "./connections.ts"

/* Optional allowlist: comma-separated sender addresses/ids. Empty = everyone.
   Set this when the brain can act on your machine (a coding agent CLI) - an
   open inbox to a tool-wielding agent is remote code execution by DM. */
const ALLOWED = new Set(
  (process.env.CASPIAN_ALLOWED_SENDERS ?? "").split(",").map((s) => s.trim()).filter(Boolean),
)

export function register(ask: (text: string) => Promise<string>): void {
  cx.onMessage(
    { overlap: "queue", ack: "On it, one moment..." },
    async (thread, msg) => {
      const sender = (msg as { sender?: string }).sender ?? ""
      if (ALLOWED.size > 0 && !ALLOWED.has(sender)) return
      const text = msg.kind === "message" ? msg.text : ""
      const out = thread.stream()
      await out.append(await ask(text))
      await out.close()
    },
  )
}
'''

TS_CASPIAN_INDEX = '''/* caspian/index.ts - the comms layer's front door.
   The brain (../agent.ts) never imports Caspian; this folder never implements
   the brain. Delete this folder and the agent still thinks - nobody can
   reach it. */
import { connect, cx } from "./connections.ts"
import { register } from "./handlers.ts"

export async function start(ask: (text: string) => Promise<string>): Promise<void> {
  await connect()
  register(ask)
  console.log("caspian: polling for inbound - message any connected channel")
  const results = await cx.run({ apiKey: process.env.CASPIAN_API_KEY ?? "" })
  for (const r of results) {
    if (!(r as { ok: boolean }).ok) {
      console.error(JSON.stringify((r as { error: unknown }).error))
    }
  }
}
'''

TS_MAIN = '''import { ask } from "./agent.ts"
import { start } from "./caspian/index.ts"

await start(ask)
'''

ENV_BASE = """CASPIAN_API_KEY=
# optional - hosted telegram still needs a BotFather token
TELEGRAM_BOT_TOKEN=
# optional - restrict who the agent answers (comma-separated sender addresses)
CASPIAN_ALLOWED_SENDERS=
"""

# ─── the brains, one per framework and language ──────────────────────────────

_PY_OPENAI_AGENTS = '''"""The brain: OpenAI Agents SDK. Exports ask(); knows nothing of Caspian."""

from agents import Agent, Runner

agent = Agent(
    name="assistant",
    instructions=(
        "You are a helpful assistant on a messaging channel. Keep replies "
        "short and conversational - two paragraphs at most."
    ),
)


def ask(text: str) -> str:
    result = Runner.run_sync(agent, text or "Introduce yourself.", max_turns=8)
    return (result.final_output or "").strip() or "(no answer)"
'''

_TS_OPENAI_AGENTS = '''/* The brain: OpenAI Agents SDK. Exports ask(); knows nothing of Caspian. */
import { Agent, run } from "@openai/agents"

const agent = new Agent({
  name: "assistant",
  instructions:
    "You are a helpful assistant on a messaging channel. Keep replies " +
    "short and conversational - two paragraphs at most.",
})

export async function ask(text: string): Promise<string> {
  const result = await run(agent, text || "Introduce yourself.", { maxTurns: 8 })
  return (result.finalOutput ?? "").trim() || "(no answer)"
}
'''

_PY_LANGGRAPH = '''"""The brain: LangGraph. Exports ask(); knows nothing of Caspian."""

import os

from langchain.chat_models import init_chat_model
from langgraph.prebuilt import create_react_agent

model = init_chat_model(os.environ.get("MODEL", "openai:gpt-4o-mini"))
graph = create_react_agent(
    model,
    tools=[],
    prompt=(
        "You are a helpful assistant on a messaging channel. Keep replies "
        "short and conversational - two paragraphs at most."
    ),
)


def ask(text: str) -> str:
    out = graph.invoke(
        {"messages": [{"role": "user", "content": text or "Introduce yourself."}]}
    )
    return out["messages"][-1].content
'''

_TS_LANGGRAPH = '''/* The brain: LangGraph JS. Exports ask(); knows nothing of Caspian. */
import { ChatOpenAI } from "@langchain/openai"
import { createReactAgent } from "@langchain/langgraph/prebuilt"

const graph = createReactAgent({
  llm: new ChatOpenAI({ model: process.env.MODEL ?? "gpt-4o-mini" }),
  tools: [],
  prompt:
    "You are a helpful assistant on a messaging channel. Keep replies " +
    "short and conversational - two paragraphs at most.",
})

export async function ask(text: string): Promise<string> {
  const out = await graph.invoke({
    messages: [{ role: "user", content: text || "Introduce yourself." }],
  })
  const last = out.messages.at(-1)
  return typeof last?.content === "string" ? last.content : JSON.stringify(last?.content)
}
'''

_PY_CLAUDE_AGENT = '''"""The brain: Claude Agent SDK. Exports ask(); knows nothing of Caspian."""

import anyio
from claude_agent_sdk import AssistantMessage, TextBlock, query


def ask(text: str) -> str:
    async def go() -> str:
        parts: list[str] = []
        async for message in query(prompt=text or "Introduce yourself."):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
        return "".join(parts).strip() or "(no answer)"

    return anyio.run(go)
'''

_TS_CLAUDE_AGENT = '''/* The brain: Claude Agent SDK. Exports ask(); knows nothing of Caspian. */
import { query } from "@anthropic-ai/claude-agent-sdk"

export async function ask(text: string): Promise<string> {
  let out = ""
  for await (const message of query({ prompt: text || "Introduce yourself." })) {
    if (message.type === "assistant") {
      for (const block of message.message.content) {
        if (block.type === "text") out += block.text
      }
    }
  }
  return out.trim() || "(no answer)"
}
'''

_TS_VERCEL_AI = '''/* The brain: Vercel AI SDK. Exports ask(); knows nothing of Caspian. */
import { openai } from "@ai-sdk/openai"
import { generateText } from "ai"

export async function ask(text: string): Promise<string> {
  const { text: reply } = await generateText({
    model: openai(process.env.MODEL ?? "gpt-4o-mini"),
    system:
      "You are a helpful assistant on a messaging channel. Keep replies " +
      "short and conversational - two paragraphs at most.",
    prompt: text || "Introduce yourself.",
  })
  return reply.trim() || "(no answer)"
}
'''

_PY_PLAIN = '''"""The brain: one plain model call, no framework. Swap freely later.

Works with any OpenAI-compatible endpoint (OpenAI, OpenRouter, a local
server) - set OPENAI_BASE_URL to switch.
"""

import os

import httpx

SYSTEM = (
    "You are a helpful assistant on a messaging channel. Keep replies "
    "short and conversational - two paragraphs at most."
)


def ask(text: str) -> str:
    reply = httpx.post(
        f"{os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1')}/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
        json={
            "model": os.environ.get("MODEL", "gpt-4o-mini"),
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": text or "Introduce yourself."},
            ],
        },
        timeout=60,
    ).json()
    if "error" in reply:
        return f"(model error: {reply['error']})"
    return reply["choices"][0]["message"]["content"].strip()
'''

_TS_PLAIN = '''/* The brain: one plain model call, no framework. Swap freely later.
   Works with any OpenAI-compatible endpoint - set OPENAI_BASE_URL to switch. */

const SYSTEM =
  "You are a helpful assistant on a messaging channel. Keep replies " +
  "short and conversational - two paragraphs at most."

export async function ask(text: string): Promise<string> {
  const base = process.env.OPENAI_BASE_URL ?? "https://api.openai.com/v1"
  const response = await fetch(`${base}/chat/completions`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${process.env.OPENAI_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: process.env.MODEL ?? "gpt-4o-mini",
      messages: [
        { role: "system", content: SYSTEM },
        { role: "user", content: text || "Introduce yourself." },
      ],
    }),
  })
  const reply = (await response.json()) as {
    choices?: Array<{ message?: { content?: string } }>
    error?: unknown
  }
  if (reply.error) return `(model error: ${JSON.stringify(reply.error)})`
  return reply.choices?.[0]?.message?.content?.trim() ?? "(no answer)"
}
'''


_PY_CREWAI = '''"""The brain: CrewAI. Exports ask(); knows nothing of Caspian."""

import os

from crewai import Agent, Crew, Task

agent = Agent(
    role="Messaging assistant",
    goal="Answer whoever writes in, briefly and helpfully.",
    backstory=(
        "You are a helpful assistant reachable on messaging channels. Keep "
        "replies short and conversational - two paragraphs at most."
    ),
    llm=os.environ.get("MODEL", "gpt-4o-mini"),
)


def ask(text: str) -> str:
    task = Task(
        description=text or "Introduce yourself.",
        expected_output="A short, conversational reply.",
        agent=agent,
    )
    result = Crew(agents=[agent], tasks=[task]).kickoff()
    return str(result).strip() or "(no answer)"
'''

_PY_AUTOGEN = '''"""The brain: Microsoft AutoGen. Exports ask(); knows nothing of Caspian."""

import asyncio
import os

from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient

model = OpenAIChatCompletionClient(model=os.environ.get("MODEL", "gpt-4o-mini"))
agent = AssistantAgent(
    "assistant",
    model_client=model,
    system_message=(
        "You are a helpful assistant on a messaging channel. Keep replies "
        "short and conversational - two paragraphs at most."
    ),
)


def ask(text: str) -> str:
    async def go() -> str:
        result = await agent.run(task=text or "Introduce yourself.")
        last = result.messages[-1]
        content = getattr(last, "content", "")
        return content if isinstance(content, str) else str(content)

    return asyncio.run(go()).strip() or "(no answer)"
'''

_PY_LLAMAINDEX = '''"""The brain: LlamaIndex. Exports ask(); knows nothing of Caspian."""

import asyncio
import os

from llama_index.core.agent.workflow import FunctionAgent
from llama_index.llms.openai import OpenAI

agent = FunctionAgent(
    llm=OpenAI(model=os.environ.get("MODEL", "gpt-4o-mini")),
    system_prompt=(
        "You are a helpful assistant on a messaging channel. Keep replies "
        "short and conversational - two paragraphs at most."
    ),
    tools=[],
)


def ask(text: str) -> str:
    response = asyncio.run(agent.run(text or "Introduce yourself."))
    return str(response).strip() or "(no answer)"
'''

_TS_LLAMAINDEX = '''/* The brain: LlamaIndex.TS. Exports ask(); knows nothing of Caspian. */
import { openai } from "@llamaindex/openai"
import { agent } from "@llamaindex/workflow"

const assistant = agent({
  llm: openai({ model: process.env.MODEL ?? "gpt-4o-mini" }),
  systemPrompt:
    "You are a helpful assistant on a messaging channel. Keep replies " +
    "short and conversational - two paragraphs at most.",
  tools: [],
})

export async function ask(text: string): Promise<string> {
  const result = await assistant.run(text || "Introduce yourself.")
  return String(result.data.result ?? result).trim() || "(no answer)"
}
'''

_TS_MASTRA = '''/* The brain: Mastra. Exports ask(); knows nothing of Caspian. */
import { openai } from "@ai-sdk/openai"
import { Agent } from "@mastra/core/agent"

const assistant = new Agent({
  name: "assistant",
  instructions:
    "You are a helpful assistant on a messaging channel. Keep replies " +
    "short and conversational - two paragraphs at most.",
  model: openai(process.env.MODEL ?? "gpt-4o-mini"),
})

export async function ask(text: string): Promise<string> {
  const result = await assistant.generate(text || "Introduce yourself.")
  return result.text.trim() || "(no answer)"
}
'''

_PY_LANGCHAIN = '''"""The brain: LangChain v1. Exports ask(); knows nothing of Caspian."""

import os

from langchain.agents import create_agent

agent = create_agent(
    model=os.environ.get("MODEL", "openai:gpt-4o-mini"),
    tools=[],
    system_prompt=(
        "You are a helpful assistant on a messaging channel. Keep replies "
        "short and conversational - two paragraphs at most."
    ),
)


def ask(text: str) -> str:
    out = agent.invoke(
        {"messages": [{"role": "user", "content": text or "Introduce yourself."}]}
    )
    return out["messages"][-1].content
'''

_TS_LANGCHAIN = '''/* The brain: LangChain JS v1. Exports ask(); knows nothing of Caspian. */
import { createAgent } from "langchain"

const agent = createAgent({
  model: process.env.MODEL ?? "openai:gpt-4o-mini",
  tools: [],
})

export async function ask(text: string): Promise<string> {
  const out = await agent.invoke({
    messages: [{ role: "user", content: text || "Introduce yourself." }],
  })
  const last = out.messages.at(-1)
  const content = last?.content
  return (typeof content === "string" ? content : JSON.stringify(content)).trim()
}
'''


# ─── coding-agent brains: the CLI you already run IS the agent ────────────────
# ask() shells out to the coding agent in headless mode. Each reply is a fresh
# non-interactive run in the working directory. Powerful and dangerous: the
# brain can edit files and run commands, so option C's scaffold sets an
# allowlist by default. `cwd` scopes what it can touch.

_PY_CLAUDE_CODE = '''"""The brain: Claude Code in headless mode. Exports ask().

`claude -p` runs one non-interactive turn and prints the result. This gives a
messaging channel a direct line to the same coding agent you use in the
terminal - it can read and change files in WORKDIR and run tools. Keep the
allowlist on.
"""

import os
import subprocess

WORKDIR = os.environ.get("AGENT_WORKDIR", ".")


def ask(text: str) -> str:
    result = subprocess.run(
        ["claude", "-p", text or "Introduce yourself."],
        cwd=WORKDIR, capture_output=True, text=True, timeout=600,
    )
    out = (result.stdout or "").strip()
    return out or (result.stderr or "").strip() or "(no output)"
'''

_PY_CODEX = '''"""The brain: OpenAI Codex CLI in headless mode. Exports ask().

`codex exec` runs one non-interactive task and prints the result. Same power
and same risk as any coding agent with tools - keep the allowlist on.
"""

import os
import subprocess

WORKDIR = os.environ.get("AGENT_WORKDIR", ".")


def ask(text: str) -> str:
    result = subprocess.run(
        ["codex", "exec", text or "Introduce yourself."],
        cwd=WORKDIR, capture_output=True, text=True, timeout=600,
    )
    out = (result.stdout or "").strip()
    return out or (result.stderr or "").strip() or "(no output)"
'''

_PY_OPENCODE = '''"""The brain: OpenCode in headless mode. Exports ask().

`opencode run` executes one prompt non-interactively and prints the result.
Same power and risk as any tool-wielding coding agent - keep the allowlist on.
"""

import os
import subprocess

WORKDIR = os.environ.get("AGENT_WORKDIR", ".")


def ask(text: str) -> str:
    result = subprocess.run(
        ["opencode", "run", text or "Introduce yourself."],
        cwd=WORKDIR, capture_output=True, text=True, timeout=600,
    )
    out = (result.stdout or "").strip()
    return out or (result.stderr or "").strip() or "(no output)"
'''

# ─── spoke assembly ──────────────────────────────────────────────────────────

_FRAMEWORKS: dict[str, dict] = {
    "openai-agents-python": {
        "title": "OpenAI Agents SDK (Python)",
        "lang": "python",
        "agent": _PY_OPENAI_AGENTS,
        "install": "pip install caspian-sdk openai-agents",
        "extra_env": "OPENAI_API_KEY=",
    },
    "openai-agents-typescript": {
        "title": "OpenAI Agents SDK (TypeScript)",
        "lang": "typescript",
        "agent": _TS_OPENAI_AGENTS,
        "install": "npm install caspian-sdk @openai/agents",
        "extra_env": "OPENAI_API_KEY=",
    },
    "langgraph-python": {
        "title": "LangGraph (Python)",
        "lang": "python",
        "agent": _PY_LANGGRAPH,
        "install": "pip install caspian-sdk langgraph langchain langchain-openai",
        "extra_env": "OPENAI_API_KEY=\nMODEL=openai:gpt-4o-mini",
    },
    "langgraph-typescript": {
        "title": "LangGraph (TypeScript)",
        "lang": "typescript",
        "agent": _TS_LANGGRAPH,
        "install": "npm install caspian-sdk @langchain/langgraph @langchain/openai @langchain/core",
        "extra_env": "OPENAI_API_KEY=\nMODEL=gpt-4o-mini",
    },
    "claude-agent-sdk-python": {
        "title": "Claude Agent SDK (Python)",
        "lang": "python",
        "agent": _PY_CLAUDE_AGENT,
        "install": "pip install caspian-sdk claude-agent-sdk anyio",
        "extra_env": "ANTHROPIC_API_KEY=",
    },
    "claude-agent-sdk-typescript": {
        "title": "Claude Agent SDK (TypeScript)",
        "lang": "typescript",
        "agent": _TS_CLAUDE_AGENT,
        "install": "npm install caspian-sdk @anthropic-ai/claude-agent-sdk",
        "extra_env": "ANTHROPIC_API_KEY=",
    },
    "vercel-ai-typescript": {
        "title": "Vercel AI SDK (TypeScript)",
        "lang": "typescript",
        "agent": _TS_VERCEL_AI,
        "install": "npm install caspian-sdk ai @ai-sdk/openai",
        "extra_env": "OPENAI_API_KEY=",
    },
    "crewai-python": {
        "title": "CrewAI (Python)",
        "lang": "python",
        "agent": _PY_CREWAI,
        "install": "pip install caspian-sdk crewai",
        "extra_env": "OPENAI_API_KEY=\nMODEL=gpt-4o-mini",
    },
    "autogen-python": {
        "title": "Microsoft AutoGen (Python)",
        "lang": "python",
        "agent": _PY_AUTOGEN,
        "install": 'pip install caspian-sdk autogen-agentchat "autogen-ext[openai]"',
        "extra_env": "OPENAI_API_KEY=\nMODEL=gpt-4o-mini",
    },
    "llamaindex-python": {
        "title": "LlamaIndex (Python)",
        "lang": "python",
        "agent": _PY_LLAMAINDEX,
        "install": "pip install caspian-sdk llama-index",
        "extra_env": "OPENAI_API_KEY=\nMODEL=gpt-4o-mini",
    },
    "llamaindex-typescript": {
        "title": "LlamaIndex.TS (TypeScript)",
        "lang": "typescript",
        "agent": _TS_LLAMAINDEX,
        "install": "npm install caspian-sdk llamaindex @llamaindex/openai @llamaindex/workflow",
        "extra_env": "OPENAI_API_KEY=\nMODEL=gpt-4o-mini",
    },
    "mastra-typescript": {
        "title": "Mastra (TypeScript)",
        "lang": "typescript",
        "agent": _TS_MASTRA,
        "install": "npm install caspian-sdk @mastra/core @ai-sdk/openai",
        "extra_env": "OPENAI_API_KEY=\nMODEL=gpt-4o-mini",
    },
    "langchain-python": {
        "title": "LangChain (Python)",
        "lang": "python",
        "agent": _PY_LANGCHAIN,
        "install": "pip install caspian-sdk langchain langchain-openai",
        "extra_env": "OPENAI_API_KEY=\nMODEL=openai:gpt-4o-mini",
    },
    "langchain-typescript": {
        "title": "LangChain JS (TypeScript)",
        "lang": "typescript",
        "agent": _TS_LANGCHAIN,
        "install": "npm install caspian-sdk langchain @langchain/openai",
        "extra_env": "OPENAI_API_KEY=\nMODEL=openai:gpt-4o-mini",
    },
    "claude-code-python": {
        "title": "Claude Code (this coding agent, headless)",
        "lang": "python",
        "agent": _PY_CLAUDE_CODE,
        "install": "pip install caspian-sdk   # plus the Claude Code CLI on PATH",
        "extra_env": (
            "AGENT_WORKDIR=.\n"
            "# STRONGLY recommended - only these senders can drive the agent\n"
            "CASPIAN_ALLOWED_SENDERS=you@example.com"
        ),
        "coding_agent": True,
    },
    "codex-python": {
        "title": "OpenAI Codex CLI (this coding agent, headless)",
        "lang": "python",
        "agent": _PY_CODEX,
        "install": "pip install caspian-sdk   # plus the Codex CLI on PATH",
        "extra_env": (
            "AGENT_WORKDIR=.\n"
            "# STRONGLY recommended - only these senders can drive the agent\n"
            "CASPIAN_ALLOWED_SENDERS=you@example.com"
        ),
        "coding_agent": True,
    },
    "opencode-python": {
        "title": "OpenCode (this coding agent, headless)",
        "lang": "python",
        "agent": _PY_OPENCODE,
        "install": "pip install caspian-sdk   # plus the OpenCode CLI on PATH",
        "extra_env": (
            "AGENT_WORKDIR=.\n"
            "# STRONGLY recommended - only these senders can drive the agent\n"
            "CASPIAN_ALLOWED_SENDERS=you@example.com"
        ),
        "coding_agent": True,
    },
    "plain-python": {
        "title": "No framework (Python)",
        "lang": "python",
        "agent": _PY_PLAIN,
        "install": "pip install caspian-sdk httpx",
        "extra_env": "OPENAI_API_KEY=\n# any OpenAI-compatible endpoint works\nOPENAI_BASE_URL=https://api.openai.com/v1\nMODEL=gpt-4o-mini",
    },
    "plain-typescript": {
        "title": "No framework (TypeScript)",
        "lang": "typescript",
        "agent": _TS_PLAIN,
        "install": "npm install caspian-sdk",
        "extra_env": "OPENAI_API_KEY=\n# any OpenAI-compatible endpoint works\nOPENAI_BASE_URL=https://api.openai.com/v1\nMODEL=gpt-4o-mini",
    },
}


def _python_spoke(slug: str, meta: dict) -> str:
    env = ENV_BASE + meta["extra_env"] + "\n"
    danger = (
        "\n> SECURITY - read this. This scaffold wires a messaging channel "
        "directly to a coding agent that can read/edit files and run commands "
        "in AGENT_WORKDIR. Anyone who can message the connected address can "
        "make it act. ALWAYS set CASPIAN_ALLOWED_SENDERS to just yourself, "
        "point AGENT_WORKDIR at a scratch directory (not your home or a real "
        "repo) until you trust it, and prefer a channel only you can reach "
        "(email/Telegram DM), never a public group.\n"
        if meta.get("coding_agent") else ""
    )
    return f"""# Caspian x {meta["title"]}
{danger}

Scaffold a new agent. The framework is the brain; Caspian is how people reach
it. Two parts, one seam:

```
my-agent/
├── agent.py          # the brain - pure framework code, exports ask()
├── caspian/          # the comms layer - pure Caspian, imports ask()
│   └── bot.py
├── .env.example
└── requirements     # {meta["install"]}
```

The rule that keeps this maintainable: framework imports never appear inside
caspian/; Caspian imports never appear outside it. Swap the framework, keep
the folder; change channels, never touch the brain.

## WARNING - read before writing files

Do NOT create `caspian/__init__.py`. The folder must stay a plain directory.
The installed SDK's import name is also `caspian`, and Python resolves a local
package (a folder WITH `__init__.py`) ahead of the installed one - adding that
file breaks every `from caspian import ...` in the project. A plain folder is
safe: Python prefers the installed package.

## 1. Install

```bash
{meta["install"]}
```

## 2. Write `agent.py`

```python
{meta["agent"]}```

## 3. Write `caspian/bot.py`

```python
{PY_CASPIAN_BOT}```

## 4. Write `.env.example` (and fill `.env`)

```
{env}```

No CASPIAN_API_KEY yet? Mint a free sandbox key - no signup:

```bash
curl -s -X POST {{BASE_URL}}/v1/projects/sandbox \\
  -H 'Content-Type: application/json' -d '{{"name":"my-agent"}}'
```

## 5. Run

```bash
python caspian/bot.py
```

Email is connected instantly with zero credentials; the startup log prints the
agent's address. Message it, get a reply - that is the whole loop. Add more
channels in `caspian/bot.py` (see {{BASE_URL}}/SKILL.md for every channel's
credentials); the handler never changes.
"""


def _typescript_spoke(slug: str, meta: dict) -> str:
    env = ENV_BASE + meta["extra_env"] + "\n"
    return f"""# Caspian x {meta["title"]}

Scaffold a new agent. The framework is the brain; Caspian is how people reach
it. Two parts, one seam:

```
my-agent/
├── agent.ts          # the brain - pure framework code, exports ask()
├── main.ts           # three lines: plug the brain into the doorway
├── caspian/          # the comms layer - pure Caspian
│   ├── index.ts
│   ├── connections.ts
│   └── handlers.ts
├── .env.example
└── package.json      # {meta["install"]}
```

The rule that keeps this maintainable: framework imports never appear inside
caspian/; Caspian imports never appear outside it. Swap the framework, keep
the folder; change channels, never touch the brain.

## 1. Install

```bash
{meta["install"]}
```

## 2. Write `agent.ts`

```ts
{meta["agent"]}```

## 3. Write the `caspian/` folder

`caspian/connections.ts`:

```ts
{TS_CASPIAN_CONNECTIONS}```

`caspian/handlers.ts`:

```ts
{TS_CASPIAN_HANDLERS}```

`caspian/index.ts`:

```ts
{TS_CASPIAN_INDEX}```

## 4. Write `main.ts`

```ts
{TS_MAIN}```

## 5. Write `.env.example` (and fill `.env`)

```
{env}```

No CASPIAN_API_KEY yet? Mint a free sandbox key - no signup:

```bash
curl -s -X POST {{BASE_URL}}/v1/projects/sandbox \\
  -H 'Content-Type: application/json' -d '{{"name":"my-agent"}}'
```

## 6. Run

```bash
bun --env-file=.env run main.ts     # or: npx tsx --env-file=.env main.ts
```

Email is connected instantly with zero credentials; the startup log prints the
agent's address. Message it, get a reply - that is the whole loop. Add more
channels in `caspian/connections.ts` (see {{BASE_URL}}/SKILL.md for every
channel's credentials); the handler never changes.
"""


def _build() -> dict[str, str]:
    spokes: dict[str, str] = {}
    for slug, meta in _FRAMEWORKS.items():
        builder = _python_spoke if meta["lang"] == "python" else _typescript_spoke
        spokes[slug] = builder(slug, meta)
    return spokes


SPOKES: dict[str, str] = _build()

FORK_SECTION = """## Two ways to use this guide - ask the developer FIRST

Before doing anything else, ask your human ONE question:

  A) Wire Caspian into the codebase we are in right now
     (an existing agent/app gains messaging channels - continue below)

  B) Scaffold a NEW custom agent from a template
     (a framework is the brain; Caspian is the comms layer)

  C) Make THIS coding agent reachable by message
     (Claude Code / Codex / OpenCode becomes the brain, in headless mode -
      you can then text your own coding agent. Powerful; read its security note.)

If C, fetch the guide for the coding agent you are:

  {BASE_URL}/SKILL/claude-code-python.md
  {BASE_URL}/SKILL/codex-python.md
  {BASE_URL}/SKILL/opencode-python.md

If B, ask which framework, then fetch ONLY that guide and follow it:

Agent frameworks (they orchestrate the model for you):

  {BASE_URL}/SKILL/openai-agents-python.md      {BASE_URL}/SKILL/openai-agents-typescript.md
  {BASE_URL}/SKILL/langchain-python.md          {BASE_URL}/SKILL/langchain-typescript.md
  {BASE_URL}/SKILL/langgraph-python.md          {BASE_URL}/SKILL/langgraph-typescript.md
  {BASE_URL}/SKILL/claude-agent-sdk-python.md   {BASE_URL}/SKILL/claude-agent-sdk-typescript.md
  {BASE_URL}/SKILL/llamaindex-python.md         {BASE_URL}/SKILL/llamaindex-typescript.md
  {BASE_URL}/SKILL/crewai-python.md             (python only)
  {BASE_URL}/SKILL/autogen-python.md            (python only)
  {BASE_URL}/SKILL/mastra-typescript.md         (typescript only)
  {BASE_URL}/SKILL/vercel-ai-typescript.md      (typescript only)

No framework - the developer calls a model SDK directly (OpenAI, Anthropic,
Google, or any OpenAI-compatible endpoint; set OPENAI_BASE_URL to switch):

  {BASE_URL}/SKILL/plain-python.md              {BASE_URL}/SKILL/plain-typescript.md

Every scaffold has the same shape: agent.* is the brain (pure framework code
exporting ask()), and a caspian/ folder is the comms layer (connections +
handlers). One imports the other exactly once. If the framework you want is
not listed, take the plain scaffold and swap agent.* - the caspian/ folder
never changes. If A, keep reading.

"""
