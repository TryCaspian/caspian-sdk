# caspian-mcp

Read-only MCP for Caspian conversations. `CommClient` fetches inbox data; a **regex Guard** Sanitizes before anything is returned to the host model.

This is a **separate package**. `pip install caspian-sdk` is unchanged (httpx + pydantic only). Mapping lives in **this MCP process** (stdio or private HTTP). It is not a Caspian-hosted vault.

## Tools

- `sanitize` / `restore` / `redaction_report` — Guard trio. One Mapping Id per process.
  `restore` is for local display only — do not send `restored_text` back to the host model.
- `list_inbox` — conversations + sanitized last-message preview (cap 20). Never raw bodies.
- `get_thread(conversation_id, limit=50, backfill=false)` — sanitized transcript.
- `brief_status(n=5, m=20)` — sanitized digest of newest n conversations × last m messages. **No model call inside the MCP.**

No `send`, `reply`, or `listen`.

## Install (from this repo)

```bash
uv sync --package caspian-mcp
```

Env: `CASPIAN_API_KEY` (and optional `CASPIAN_BASE_URL`) on the **MCP host**. Never give the Caspian key to the model client.

## Cursor — stdio

```json
{
  "mcpServers": {
    "caspian": {
      "command": "uv",
      "args": ["run", "--package", "caspian-mcp", "caspian-mcp"]
    }
  }
}
```

Do not run `caspian-mcp` in a normal terminal without an MCP client — it speaks stdio JSON-RPC.

## Private HTTP + bearer

Auth decides who may call **your** process. It does not move Mapping onto the laptop of a remote host model.

```bash
export MCP_AUTH_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export CASPIAN_API_KEY=...
caspian-mcp --http --host 127.0.0.1 --port 8765
```

HTTP mode **exits** if `MCP_AUTH_TOKEN` is unset. Use a token **distinct from** `CASPIAN_API_KEY`.

```json
{
  "mcpServers": {
    "caspian": {
      "url": "http://127.0.0.1:8765/mcp",
      "headers": {
        "Authorization": "Bearer <MCP_AUTH_TOKEN>"
      }
    }
  }
}
```

Default bind is loopback. Mapping is in-memory, TTL-bound, gone on restart. Not OAuth 2.1. Not a public/Smithery host.

## Honest limits

- Regex Categories only (email, IP, card, API-key shape, phone). No spaCy NER in this package.
- Conversation list has no last-message field on the API today, so previews fetch messages (capped).
- The host model never sees raw values **from these tools**; Channel Messages still pass through Caspian's API.
