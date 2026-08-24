"""Caspian MCP server — the gateway's capabilities as MCP tools/resources.

Mounted into the gateway FastAPI at /mcp (see main.py), so it deploys with the
gateway, is fronted by the same Caddy, and needs no separate process. Built on
the v2 MCP SDK (2026-07-28 spec): stateless Streamable HTTP, no sessions, the
project's API key rides in each request's Authorization header.

Architecture note — how tools reach the gateway. Rather than duplicate the
gateway's send/credit/loop-prevention logic, each tool calls the gateway's own
/v1/* endpoint with the caller's key. That reuses every guard for free. It is
one in-process-localhost HTTP hop; a later optimization is to extract a shared
service layer and call it directly, but correctness-by-reuse comes first.

What the agent gets on connect (all self-described through the protocol):
  tools     list_channels, list_connections, reply, send_message
  resources caspian://conversations, caspian://conversations/{id}/messages
  prompts   triage_inbox
"""

from __future__ import annotations

from typing import Annotated, Any

import httpx
from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field
from starlette.applications import Starlette

INSTRUCTIONS = (
    "Caspian gives this agent a presence on real messaging channels (email, "
    "Slack, Discord, Telegram, SMS, X). Sending needs a connected channel: "
    "call list_channels to see what is live and list_connections for the "
    "account's addresses. Incoming conversations are readable as resources "
    "under caspian://conversations; reply to one with the reply tool. The same "
    "message reaches a person on whatever channel they used."
)


def build_mcp(*, base_url: str) -> Starlette:
    """Return the mountable MCP ASGI app. `base_url` is where /v1 lives
    (the gateway itself, e.g. http://127.0.0.1:8000)."""

    mcp = MCPServer(name="caspian", instructions=INSTRUCTIONS, version="0.1.0")

    def _key(ctx: Context) -> str:
        # Stateless: identity is in THIS request, never remembered.
        auth = (ctx.headers or {}).get("authorization", "")
        if not auth.startswith("Bearer "):
            raise ValueError(
                "Missing CASPIAN_API_KEY. Add it as an Authorization: Bearer "
                "header in your MCP client config."
            )
        return auth.removeprefix("Bearer ").strip()

    async def _gw(ctx: Context, method: str, path: str, **kw: Any) -> Any:
        # One in-process call to the gateway's own REST API, with the user's key.
        async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
            r = await client.request(
                method, f"/v1{path}",
                headers={"Authorization": f"Bearer {_key(ctx)}"}, **kw,
            )
            if r.status_code >= 400:
                raise ValueError(f"{method} {path} -> {r.status_code}: {r.text[:200]}")
            return r.json() if r.content else {}

    # ── tools ────────────────────────────────────────────────────────────────

    @mcp.tool(annotations={"readOnlyHint": True})
    async def list_channels(ctx: Context) -> list[dict]:
        """List the channels that can send and receive right now, with what
        each supports. Call this before sending if unsure what is connected."""
        return await _gw(ctx, "GET", "/channels")

    @mcp.tool(annotations={"readOnlyHint": True})
    async def list_connections(ctx: Context) -> list[dict]:
        """List this account's active connections — the addresses it can send
        from and receive on (an email address, a Slack workspace, ...)."""
        return await _gw(ctx, "GET", "/connections")

    @mcp.tool()
    async def reply(
        ctx: Context,
        message_id: Annotated[str, Field(description="Id of the inbound message to answer")],
        text: Annotated[str, Field(description="The reply text")],
    ) -> dict:
        """Reply to a message someone sent, on the same conversation and
        channel it arrived on. Get message ids from the conversation resources."""
        return await _gw(ctx, "POST", f"/messages/{message_id}/reply", json={"text": text})

    @mcp.tool()
    async def send_message(
        ctx: Context,
        connection_id: Annotated[
            str, Field(description="Which connection to send from (see list_connections)")
        ],
        recipient: Annotated[str, Field(description="Recipient's address on that channel")],
        text: Annotated[str, Field(description="The message body")],
    ) -> dict:
        """Start a new conversation with a recipient (a cold first message).

        Requires a connection whose channel supports initiating (email does).
        To answer someone who already wrote in, use reply instead."""
        return await _gw(
            ctx, "POST", f"/connections/{connection_id}/initiate",
            json={"recipient": recipient, "text": text},
        )

    # ── resources (read-only, browsable) ─────────────────────────────────────

    @mcp.resource("caspian://conversations")
    async def conversations() -> list[dict]:
        """Recent conversations across every channel — who has written in."""
        # Resources have no per-request Context in this SDK path; the hosted
        # transport supplies auth via the connection. For the stage-1 local
        # build these are exercised through the tools; resources are wired in
        # stage 2 alongside the auth-context plumbing.
        return []

    # ── prompts (one-click recipes) ──────────────────────────────────────────

    @mcp.prompt()
    def triage_inbox() -> str:
        """Summarize unread conversations and suggest replies."""
        return (
            "List my connections and recent conversations, group who is waiting "
            "on a reply, and for each suggest a one-line response I can approve."
        )

    return mcp.streamable_http_app(streamable_http_path="/", stateless_http=True)
