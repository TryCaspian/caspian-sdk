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

Onboarding is agent-native, mirroring SKILL.md: add the bare URL with no key,
call get_started (the one keyless tool) to mint a sandbox project, put the
returned key in the client config, then connect_channel — Slack/Discord hand
back an authorize link to click, email connects with zero credentials. The
dashboard is never a required step.

What the agent gets on connect (all self-described through the protocol):
  tools     get_started, connect_channel, list_channels, list_connections,
            reply, send_message
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
    "Slack, Discord, Telegram, SMS, X). No API key yet? Call get_started - it "
    "mints a free sandbox key with no signup; put it in this server's "
    "Authorization header and reconnect. Then connect_channel: email connects "
    "instantly with zero credentials, Slack and Discord return an authorize "
    "link for the user to click, Telegram takes a bot token. Sending needs a "
    "connected channel: call list_channels to see what is live and "
    "list_connections for the account's addresses. Incoming conversations are "
    "readable as resources under caspian://conversations; reply to one with "
    "the reply tool. The same message reaches a person on whatever channel "
    "they used."
)


def build_mcp(*, base_url: str, public_url: str = "") -> Starlette:
    """Return the mountable MCP ASGI app. `base_url` is where /v1 lives
    (the gateway itself, e.g. http://127.0.0.1:8000); `public_url` is the
    https base users reach us on, used in config snippets shown to them."""
    shown_url = public_url or base_url

    mcp = MCPServer(name="caspian", instructions=INSTRUCTIONS, version="0.1.0")

    def _key(ctx: Context) -> str:
        # Stateless: identity is in THIS request, never remembered.
        auth = (ctx.headers or {}).get("authorization", "")
        if not auth.startswith("Bearer "):
            raise ValueError(
                "No API key on this request. Call the get_started tool first - "
                "it mints a free sandbox key with no signup. Then add it to "
                'this MCP server\'s config as {"headers": {"Authorization": '
                '"Bearer comm_..."}} and retry.'
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

    @mcp.tool()
    async def get_started(
        name: Annotated[
            str, Field(description="A name for the project, e.g. 'my-agent'")
        ] = "my-agent",
    ) -> dict:
        """Mint a free sandbox API key - no signup, no dashboard. Call this
        when no Authorization header is configured yet.

        Returns the key and the exact MCP client config to write. If you (the
        agent) can edit this server's entry in the MCP config file, add the
        header yourself and reconnect; otherwise show the snippet to the user."""
        # Deliberately keyless: this is the bootstrap. Same endpoint SKILL.md uses.
        async with httpx.AsyncClient(base_url=base_url, timeout=30) as client:
            r = await client.post("/v1/projects/sandbox", json={"name": name})
            if r.status_code >= 400:
                raise ValueError(f"sandbox mint failed -> {r.status_code}: {r.text[:200]}")
            out = r.json()
        return {
            "api_key": out["api_key"],
            "project_id": out["project_id"],
            "mcp_config": {
                "mcpServers": {
                    "caspian": {
                        "url": f"{shown_url}/mcp",
                        "headers": {"Authorization": f"Bearer {out['api_key']}"},
                    }
                }
            },
            "next": (
                "Add the Authorization header to this server's MCP config and "
                "reconnect, then call connect_channel (email needs no "
                "credentials) and send_message."
            ),
        }

    @mcp.tool()
    async def connect_channel(
        ctx: Context,
        channel: Annotated[
            str,
            Field(description="Channel to connect: email, slack, discord, or telegram"),
        ],
        bot_token: Annotated[
            str | None,
            Field(description="Bot token, only for telegram (from @BotFather)"),
        ] = None,
    ) -> dict:
        """Connect a channel so the agent can send and receive on it.

        email: instant, zero credentials - the result includes the agent's
        address. slack / discord: installs the shared Caspian app; the result
        contains an authorize_url - show it to the user as a clickable link,
        they approve in their workspace/server and the connection goes live
        (verify with list_connections). telegram: pass a bot_token."""
        ch = channel.strip().lower()
        if ch == "email":
            return await _gw(ctx, "POST", "/connections/email", json={})
        if ch in ("slack", "discord"):
            conn = await _gw(ctx, "POST", f"/connections/{ch}/install", json={})
            conn["next"] = (
                "Show authorize_url to the user as a link. After they approve, "
                "list_connections shows the connection active."
            )
            return conn
        if ch == "telegram":
            if not bot_token:
                raise ValueError(
                    "telegram needs a bot_token. Ask the user to create a bot "
                    "with @BotFather and paste the token, then call again."
                )
            return await _gw(ctx, "POST", "/connections/telegram", json={"bot_token": bot_token})
        raise ValueError(
            f"Unknown channel '{channel}'. This tool handles email, slack, "
            "discord, telegram; for other channels see the credentials guide "
            f"at {shown_url}/SKILL.md."
        )

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
