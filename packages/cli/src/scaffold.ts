/**
 * caspian init project --new — file catalog for a hosted-email agent.
 *
 * Stacks wrap cx.tools(thread) as that framework's tools. Caspian is the
 * channel layer; the framework is reasoning.
 */
export const INIT_STACKS = [
  "openai-python",
  "openai-ts",
  "mastra",
  "ai-sdk",
] as const

export type InitStack = (typeof INIT_STACKS)[number]

export const STACK_USAGE =
  "use: caspian init project --new --stack openai-python|openai-ts|mastra|ai-sdk"

export type ScaffoldFile = {
  readonly path: string
  readonly contents: string
}

const INSTRUCTIONS =
  "You are a helpful agent on email. Always reply with the post_message tool. " +
  "Use start_typing while you think. Thread ids look like email:… / telegram:… — " +
  "never a platform chat id."

export const ASK_STACK = [
  "Which stack?",
  "",
  "  1) openai-python   OpenAI Agents SDK (Python)",
  "  2) openai-ts       OpenAI Agents SDK (TypeScript)",
  "  3) mastra          Mastra (TypeScript)",
  "  4) ai-sdk          Vercel AI SDK (TypeScript)",
  "",
  "Choice [1/2/3/4]: ",
].join("\n")

export const ASK_STACK_NEEDED = [
  "Which stack?",
  "",
  "  caspian init project --new --stack openai-python",
  "  caspian init project --new --stack openai-ts",
  "  caspian init project --new --stack mastra",
  "  caspian init project --new --stack ai-sdk",
  "",
  "Re-run with --stack, or run caspian init project --new in a terminal to choose.",
].join("\n")

export const parseStackChoice = (raw: string): InitStack | undefined => {
  const token = raw.trim().toLowerCase()
  if (token === "1" || token === "openai-python") return "openai-python"
  if (token === "2" || token === "openai-ts") return "openai-ts"
  if (token === "3" || token === "mastra") return "mastra"
  if (token === "4" || token === "ai-sdk") return "ai-sdk"
  return undefined
}

export const occupiedReason = (dir: string): string =>
  `${dir} already has a package.json or pyproject.toml. Use --force to overwrite.`

export const runHint = (stack: InitStack): string => {
  switch (stack) {
    case "openai-python":
      return "uv run main.py"
    case "openai-ts":
    case "mastra":
    case "ai-sdk":
      return "bun install && bun run start"
  }
}

const GITIGNORE = [".env", "node_modules/", "__pycache__/", ".venv/", ""].join(
  "\n",
)

const README = (stack: InitStack): string =>
  [
    "# Caspian agent",
    "",
    `Hosted email + ${stack}. Caspian is the channel layer; the model replies with tools.`,
    "",
    "1. Set `OPENAI_API_KEY` in `.env` (Caspian keys are already there).",
    `2. ${runHint(stack)}`,
    "3. `caspian channels add email` if this process did not mint an inbox.",
    "",
  ].join("\n")

const PYPROJECT = [
  "[project]",
  'name = "caspian-bot"',
  'version = "0.1.0"',
  'requires-python = ">=3.10"',
  "dependencies = [",
  '  "caspian-sdk",',
  '  "openai-agents",',
  "]",
  "",
].join("\n")

const PYTHON_MAIN = [
  '"""Hosted email agent — OpenAI Agents SDK + Caspian."""',
  "",
  "from __future__ import annotations",
  "",
  "import os",
  "from pathlib import Path",
  "",
  "from agents import Agent, Runner, function_tool",
  "from caspian import Caspian, HandlerContext, Message, Thread",
  "",
  "",
  "def load_dotenv(path: str = '.env') -> None:",
  "    text = Path(path).read_text() if Path(path).is_file() else ''",
  "    for line in text.splitlines():",
  "        stripped = line.strip()",
  "        if stripped == '' or stripped.startswith('#') or '=' not in stripped:",
  "            continue",
  "        key, _, value = stripped.partition('=')",
  "        os.environ.setdefault(key.strip(), value.strip())",
  "",
  "",
  "load_dotenv()",
  "",
  'api_key = os.environ.get("CASPIAN_API_KEY", "").strip()',
  'base_url = os.environ.get("CASPIAN_BASE_URL", "").strip()',
  "if not api_key:",
  '    raise SystemExit("Set CASPIAN_API_KEY in .env (caspian init), then rerun.")',
  'if not os.environ.get("OPENAI_API_KEY", "").strip():',
  '    raise SystemExit("Set OPENAI_API_KEY in .env, then rerun.")',
  "",
  "cx = Caspian(api_key=api_key, base_url=base_url)",
  'inbox = cx.channels.add("email")',
  'print(f"hosted email {inbox.address or inbox.id}", flush=True)',
  "",
  `INSTRUCTIONS = ${JSON.stringify(INSTRUCTIONS)}`,
  "",
  "",
  "def tools_for(thread: Thread):",
  "    bound = cx.tools(thread)",
  "",
  "    @function_tool",
  "    def post_message(text: str) -> str:",
  '        """Send a message to the current thread."""',
  '        bound.execute("post_message", {"text": text})',
  '        return "sent"',
  "",
  "    @function_tool",
  "    def edit_message(message_id: str, text: str) -> str:",
  '        """Edit an existing message."""',
  '        bound.execute("edit_message", {"message_id": message_id, "text": text})',
  '        return "ok"',
  "",
  "    @function_tool",
  "    def add_reaction(message_id: str, emoji: str) -> str:",
  '        """React to a message with an emoji."""',
  '        bound.execute("add_reaction", {"message_id": message_id, "emoji": emoji})',
  '        return "ok"',
  "",
  "    @function_tool",
  "    def start_typing() -> str:",
  '        """Show typing indicator."""',
  '        bound.execute("start_typing", {})',
  '        return "ok"',
  "",
  "    @function_tool",
  "    def send_dm(thread_id: str, text: str) -> str:",
  '        """Send a DM to a thread id (email:… / telegram:…), never a chat id."""',
  '        bound.execute("send_dm", {"thread_id": thread_id, "text": text})',
  '        return "sent"',
  "",
  "    return [post_message, edit_message, add_reaction, start_typing, send_dm]",
  "",
  "",
  '@cx.on_message({"channel": "email"})',
  "async def on_email(thread: Thread, msg: Message, ctx: HandlerContext) -> None:",
  "    if not msg.text.strip():",
  "        return",
  "    before = len(thread.commands)",
  '    cx.tools(thread).execute("start_typing", {})',
  "    agent = Agent(name='Caspian', instructions=INSTRUCTIONS, tools=tools_for(thread))",
  "    result = await Runner.run(agent, msg.text)",
  "    if len(thread.commands) <= before + 1:",
  "        text = str(result.final_output or '').strip()",
  "        if text:",
  "            thread.post(text)",
  "",
  "",
  'if __name__ == "__main__":',
  '    print("polling gateway /v1/events", flush=True)',
  "    cx.run()",
  "",
].join("\n")

const PACKAGE_JSON = (deps: Record<string, string>): string =>
  `${JSON.stringify(
    {
      name: "caspian-bot",
      private: true,
      type: "module",
      scripts: { start: "bun index.ts" },
      dependencies: { "caspian-sdk": "^1.0.0", ...deps },
    },
    null,
    2,
  )}\n`

const TS_BOOT = [
  'import { Caspian } from "caspian-sdk"',
  "",
  'const apiKey = process.env["CASPIAN_API_KEY"] || ""',
  'const baseUrl = process.env["CASPIAN_BASE_URL"] || ""',
  'const openaiKey = process.env["OPENAI_API_KEY"] || ""',
  "if (!apiKey) {",
  '  throw new Error("Set CASPIAN_API_KEY in .env (caspian init), then rerun.")',
  "}",
  "if (!openaiKey) {",
  '  throw new Error("Set OPENAI_API_KEY in .env, then rerun.")',
  "}",
  "",
  "const cx = new Caspian()",
  'const inbox = await cx.channels.add("email", { via: "hosted" })',
  'console.log("hosted email", inbox.id)',
  `const INSTRUCTIONS = ${JSON.stringify(INSTRUCTIONS)}`,
].join("\n")

const TS_HANDLER_OPEN = [
  "",
  'cx.onMessage({ channel: "email" }, async (thread, msg) => {',
  "  const text = msg.text.trim()",
  "  if (!text) return",
  '  const tools = cx.tools(thread, { preset: "messenger" })',
  "  await tools.start_typing?.execute({})",
  "  let posted = false",
].join("\n")

const TS_HANDLER_CLOSE = [
  "  if (!posted) {",
  "    const fallback = String(reply).trim()",
  "    if (fallback) await thread.post(fallback)",
  "  }",
  "})",
  "",
  'console.log("polling gateway /v1/events")',
  "await cx.run({ apiKey, baseUrl: baseUrl || undefined })",
  "",
].join("\n")

const OPENAI_TS = [
  'import { Agent, run, tool } from "@openai/agents"',
  'import { z } from "zod"',
  TS_BOOT,
  TS_HANDLER_OPEN,
  "  const agent = new Agent({",
  '    name: "Caspian",',
  "    instructions: INSTRUCTIONS,",
  "    tools: [",
  "      tool({",
  '        name: "post_message",',
  "        description: tools.post_message.description,",
  "        parameters: z.object({ text: z.string() }),",
  "        execute: async ({ text }) => {",
  "          posted = true",
  "          await tools.post_message.execute({ text })",
  '          return "sent"',
  "        },",
  "      }),",
  "      tool({",
  '        name: "edit_message",',
  "        description: tools.edit_message?.description ?? \"Edit a message.\",",
  "        parameters: z.object({ message_id: z.string(), text: z.string() }),",
  "        execute: async ({ message_id, text }) => {",
  "          await tools.edit_message?.execute({ message_id, text })",
  '          return "ok"',
  "        },",
  "      }),",
  "      tool({",
  '        name: "add_reaction",',
  "        description: tools.add_reaction?.description ?? \"React to a message.\",",
  "        parameters: z.object({ message_id: z.string(), emoji: z.string() }),",
  "        execute: async ({ message_id, emoji }) => {",
  "          await tools.add_reaction?.execute({ message_id, emoji })",
  '          return "ok"',
  "        },",
  "      }),",
  "      tool({",
  '        name: "start_typing",',
  "        description: tools.start_typing?.description ?? \"Show typing.\",",
  "        parameters: z.object({}),",
  "        execute: async () => {",
  "          await tools.start_typing?.execute({})",
  '          return "ok"',
  "        },",
  "      }),",
  "      tool({",
  '        name: "send_dm",',
  "        description: tools.send_dm.description,",
  "        parameters: z.object({ thread_id: z.string(), text: z.string() }),",
  "        execute: async ({ thread_id, text }) => {",
  "          await tools.send_dm.execute({ thread_id, text })",
  '          return "sent"',
  "        },",
  "      }),",
  "    ],",
  "  })",
  "  const result = await run(agent, text)",
  "  const reply = result.finalOutput ?? \"\"",
  TS_HANDLER_CLOSE,
].join("\n")

const MASTRA_TS = [
  'import { Agent } from "@mastra/core/agent"',
  'import { createTool } from "@mastra/core/tools"',
  'import { z } from "zod"',
  TS_BOOT,
  TS_HANDLER_OPEN,
  "  const agent = new Agent({",
  '    id: "caspian-email",',
  '    name: "Caspian",',
  "    instructions: INSTRUCTIONS,",
  '    model: "openai/gpt-4o",',
  "    tools: {",
  "      post_message: createTool({",
  '        id: "post_message",',
  "        description: tools.post_message.description,",
  "        inputSchema: z.object({ text: z.string() }),",
  "        execute: async ({ text }) => {",
  "          posted = true",
  "          await tools.post_message.execute({ text })",
  '          return "sent"',
  "        },",
  "      }),",
  "      edit_message: createTool({",
  '        id: "edit_message",',
  '        description: tools.edit_message?.description ?? "Edit a message.",',
  "        inputSchema: z.object({ message_id: z.string(), text: z.string() }),",
  "        execute: async ({ message_id, text }) => {",
  "          await tools.edit_message?.execute({ message_id, text })",
  '          return "ok"',
  "        },",
  "      }),",
  "      add_reaction: createTool({",
  '        id: "add_reaction",',
  '        description: tools.add_reaction?.description ?? "React to a message.",',
  "        inputSchema: z.object({ message_id: z.string(), emoji: z.string() }),",
  "        execute: async ({ message_id, emoji }) => {",
  "          await tools.add_reaction?.execute({ message_id, emoji })",
  '          return "ok"',
  "        },",
  "      }),",
  "      start_typing: createTool({",
  '        id: "start_typing",',
  '        description: tools.start_typing?.description ?? "Show typing.",',
  "        inputSchema: z.object({}),",
  "        execute: async () => {",
  "          await tools.start_typing?.execute({})",
  '          return "ok"',
  "        },",
  "      }),",
  "      send_dm: createTool({",
  '        id: "send_dm",',
  "        description: tools.send_dm.description,",
  "        inputSchema: z.object({ thread_id: z.string(), text: z.string() }),",
  "        execute: async ({ thread_id, text }) => {",
  "          await tools.send_dm.execute({ thread_id, text })",
  '          return "sent"',
  "        },",
  "      }),",
  "    },",
  "  })",
  "  const result = await agent.generate(text)",
  '  const reply = result.text ?? ""',
  TS_HANDLER_CLOSE,
].join("\n")

const AI_SDK_TS = [
  'import { generateText, stepCountIs, tool } from "ai"',
  'import { openai } from "@ai-sdk/openai"',
  'import { z } from "zod"',
  TS_BOOT,
  TS_HANDLER_OPEN,
  "  const result = await generateText({",
  '    model: openai("gpt-4o"),',
  "    system: INSTRUCTIONS,",
  "    prompt: text,",
  "    stopWhen: stepCountIs(8),",
  "    tools: {",
  "      post_message: tool({",
  "        description: tools.post_message.description,",
  "        inputSchema: z.object({ text: z.string() }),",
  "        execute: async ({ text }) => {",
  "          posted = true",
  "          await tools.post_message.execute({ text })",
  '          return "sent"',
  "        },",
  "      }),",
  "      edit_message: tool({",
  '        description: tools.edit_message?.description ?? "Edit a message.",',
  "        inputSchema: z.object({ message_id: z.string(), text: z.string() }),",
  "        execute: async ({ message_id, text }) => {",
  "          await tools.edit_message?.execute({ message_id, text })",
  '          return "ok"',
  "        },",
  "      }),",
  "      add_reaction: tool({",
  '        description: tools.add_reaction?.description ?? "React to a message.",',
  "        inputSchema: z.object({ message_id: z.string(), emoji: z.string() }),",
  "        execute: async ({ message_id, emoji }) => {",
  "          await tools.add_reaction?.execute({ message_id, emoji })",
  '          return "ok"',
  "        },",
  "      }),",
  "      start_typing: tool({",
  '        description: tools.start_typing?.description ?? "Show typing.",',
  "        inputSchema: z.object({}),",
  "        execute: async () => {",
  "          await tools.start_typing?.execute({})",
  '          return "ok"',
  "        },",
  "      }),",
  "      send_dm: tool({",
  "        description: tools.send_dm.description,",
  "        inputSchema: z.object({ thread_id: z.string(), text: z.string() }),",
  "        execute: async ({ thread_id, text }) => {",
  "          await tools.send_dm.execute({ thread_id, text })",
  '          return "sent"',
  "        },",
  "      }),",
  "    },",
  "  })",
  '  const reply = result.text ?? ""',
  TS_HANDLER_CLOSE,
].join("\n")

const common = (stack: InitStack): ScaffoldFile[] => [
  { path: ".gitignore", contents: GITIGNORE },
  { path: "README.md", contents: README(stack) },
]

export const filesFor = (stack: InitStack): ReadonlyArray<ScaffoldFile> => {
  switch (stack) {
    case "openai-python":
      return [
        ...common(stack),
        { path: "pyproject.toml", contents: PYPROJECT },
        { path: "main.py", contents: PYTHON_MAIN },
      ]
    case "openai-ts":
      return [
        ...common(stack),
        {
          path: "package.json",
          contents: PACKAGE_JSON({
            "@openai/agents": "^0.3.0",
            zod: "^3.25.0",
          }),
        },
        { path: "index.ts", contents: OPENAI_TS },
      ]
    case "mastra":
      return [
        ...common(stack),
        {
          path: "package.json",
          contents: PACKAGE_JSON({
            "@mastra/core": "^0.24.0",
            zod: "^3.25.0",
          }),
        },
        { path: "index.ts", contents: MASTRA_TS },
      ]
    case "ai-sdk":
      return [
        ...common(stack),
        {
          path: "package.json",
          contents: PACKAGE_JSON({
            ai: "^5.0.0",
            "@ai-sdk/openai": "^2.0.0",
            zod: "^3.25.0",
          }),
        },
        { path: "index.ts", contents: AI_SDK_TS },
      ]
  }
}
