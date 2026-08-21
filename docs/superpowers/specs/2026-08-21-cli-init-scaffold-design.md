# CLI `init project --new` scaffolding

Date: 2026-08-21
Status: approved

## Goal

`caspian init project --new` writes a runnable hosted-email agent that uses
Caspian as the channel layer and one of four agent frameworks for reasoning.
Python `cx.tools(thread).execute(...)` enqueues on the handler thread (TypeScript
parity), so scaffolds can wrap Caspian tools without a second `thread.post`.

## Stacks

| `--stack`         | Language | Framework              |
|-------------------|----------|------------------------|
| `openai-python`   | Python   | OpenAI Agents SDK      |
| `openai-ts`       | TypeScript | OpenAI Agents SDK    |
| `mastra`          | TypeScript | Mastra               |
| `ai-sdk`          | TypeScript | Vercel AI SDK        |

Interactive: `caspian init project --new` asks. `--stack NAME` skips.
Non-TTY without `--stack` prints the list and exits.

`--new` writes into cwd, or `--path DIR` (both allowed). Refuse if
`package.json` or `pyproject.toml` exists unless `--force`.

## App shape

Hosted email: `channels.add("email", via="hosted")`. Inbound `on_message` binds
`cx.tools(thread)` as that framework’s tools. The model replies with
`post_message` (and may type / edit / react / `send_dm`). If it never calls a
tool, fall back to posting `final_output` / generated text.

`.env`: `CASPIAN_API_KEY`, `CASPIAN_BASE_URL`, `OPENAI_API_KEY=` (empty).
CLI secret in `~/.caspian/.env` is unchanged (no OpenAI key there).

## Python tools footgun

`ToolSet.execute` today builds a throwaway `Thread` and returns its commands.
The handler’s `thread` is untouched, so a hosted turn sends nothing.

Fix: when a thread is bound, also enqueue those commands onto it (including
`send_dm`, whose `Command.thread_id` is the DM target). Unbound `execute` is
unchanged.

## CLI

Template catalog in `packages/cli/src/scaffold.ts` (path → contents).
`InitIO.writeFiles`. Remove the `TODO(init-project-scaffold)` path.
