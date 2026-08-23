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

# ---- handlers: one rule answers every channel -------------------------------
@cx.on_message({"overlap": "queue", "ack": "On it, one moment..."})
def handle(thread, msg, ctx):
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

export function register(ask: (text: string) => Promise<string>): void {
  cx.onMessage(
    { overlap: "queue", ack: "On it, one moment..." },
    async (thread, msg) => {
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
    return f"""# Caspian x {meta["title"]}

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

If B, ask which framework, then fetch ONLY that guide and follow it:

  {BASE_URL}/SKILL/openai-agents-python.md
  {BASE_URL}/SKILL/openai-agents-typescript.md
  {BASE_URL}/SKILL/langgraph-python.md
  {BASE_URL}/SKILL/langgraph-typescript.md
  {BASE_URL}/SKILL/claude-agent-sdk-python.md
  {BASE_URL}/SKILL/claude-agent-sdk-typescript.md
  {BASE_URL}/SKILL/vercel-ai-typescript.md
  {BASE_URL}/SKILL/plain-python.md          (no framework, plain LLM calls)
  {BASE_URL}/SKILL/plain-typescript.md

Every scaffold has the same shape: agent.* is the brain (pure framework code
exporting ask()), and a caspian/ folder is the comms layer (connections +
handlers). One imports the other exactly once. If A, keep reading.

"""
