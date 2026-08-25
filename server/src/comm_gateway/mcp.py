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
from urllib.parse import urlsplit

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.applications import Starlette

INSTRUCTIONS = (
    "Caspian gives this agent a two-way presence on real messaging channels "
    "(email, Slack, Discord, Telegram, SMS, X). It BOTH receives what people "
    "send and sends messages out.\n"
    "\n"
    "Setup, in order:\n"
    "1. No API key yet? Call get_started - it mints a free sandbox key with no "
    "signup. Put it in this server's Authorization header and reconnect.\n"
    "2. connect_channel: email connects instantly with zero credentials; Slack "
    "and Discord return an authorize link for the user to open and approve; "
    "Telegram takes a bot token.\n"
    "3. Slack only: installing to the workspace is NOT enough. The bot must "
    "also be added to a channel - tell the user to run /invite @<app name> in "
    "the channel it should watch, or to DM the app. Without that it receives "
    "nothing, and @mentions elsewhere never reach it.\n"
    "\n"
    "Receiving: call `inbox` to see what people sent (@mentions, DMs, emails). "
    "Each entry has a message_id. Never claim the inbox is empty without "
    "calling inbox first - and if it returns a hint, relay that hint to the "
    "user, because it explains what is missing.\n"
    "\n"
    "Sending: `reply(message_id, text)` answers in the same thread and channel "
    "it arrived on - this is the normal way to respond. `send_message` starts "
    "a NEW conversation with someone who has not written in. "
    "`read_conversation` gives the full thread for context.\n"
    "\n"
    "Inbound is not pushed to you: poll `inbox` when the user asks what came "
    "in, or after they say they have sent something."
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
                "Show authorize_url to the user as a clickable link. After they "
                "approve, list_connections shows it active. THEN tell them the "
                "second step, which is required for receiving: the bot must be "
                "added to a channel - run /invite @<app name> in that channel, "
                "or DM the app. Until then it receives nothing. Read what "
                "arrives with the inbox tool."
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

    @mcp.tool(annotations={"readOnlyHint": True})
    async def inbox(
        ctx: Context,
        limit: Annotated[int, Field(description="How many recent messages to return (1-100)")] = 20,
    ) -> dict:
        """Read messages people have sent to this agent — the inbound side.

        THIS is how you see @mentions, DMs and emails that arrived. Returns each
        message with a message_id: pass that to `reply` to answer in the same
        thread and channel. Call this whenever the user asks what came in, or
        asks you to answer someone. Newest first."""
        limit = max(1, min(int(limit), 100))
        # /events pages FORWARD from a cursor (oldest first), so walk to the end
        # and keep the tail — otherwise a busy project returns its oldest mail.
        events: list[dict] = []
        after, page = 0, 500
        for _ in range(20):  # cap the walk; 10k events is plenty of history
            batch = await _gw(ctx, "GET", "/events",
                              params={"type": "message.received",
                                      "after_seq": after, "limit": page})
            if not batch:
                break
            events = (events + batch)[-200:]
            after = batch[-1].get("seq", after)
            if len(batch) < page:
                break
        msgs = []
        for e in reversed(events):
            m = (e.get("data") or {}).get("message") or {}
            sender = m.get("sender") or {}
            msgs.append({
                "message_id": m.get("id"),
                "channel": m.get("channel"),
                "from": sender.get("display_name") or sender.get("address"),
                "text": m.get("text"),
                "conversation_id": m.get("conversation_id"),
                "received_at": m.get("created_at"),
            })
            if len(msgs) >= limit:
                break
        if msgs:
            return {"messages": msgs, "count": len(msgs)}
        # Empty is ambiguous — say WHY and what to do, per channel.
        conns = await _gw(ctx, "GET", "/connections")
        live = [c for c in (conns or []) if c.get("status") == "active"]
        setting_up = [c for c in (conns or []) if c.get("status") in ("provisioning", "pending")]
        awaiting = [c for c in (conns or []) if c.get("status") == "pending_oauth"]
        if not (live or setting_up or awaiting):
            return {"messages": [], "count": 0, "hint":
                    "No channel is connected yet. Call connect_channel first "
                    "(email needs no credentials)."}
        parts = []
        if live:
            parts.append("live on " + ", ".join(sorted({c["channel"] for c in live})))
        if setting_up:
            parts.append("still provisioning: "
                         + ", ".join(sorted({c["channel"] for c in setting_up})))
        if awaiting:
            parts.append("waiting for the user to approve the install: "
                         + ", ".join(sorted({c["channel"] for c in awaiting})))
        known = live + setting_up + awaiting
        chans = sorted({c.get("channel") for c in known if c.get("channel")})
        hint = (
            f"Nothing has arrived yet ({'; '.join(parts)}). "
            "Inbound only reaches the agent once someone actually writes to it."
        )
        if "slack" in chans:
            hint += (
                " For Slack: installing the app to the workspace is NOT enough — "
                "the bot must also be in a channel. Tell the user to invite it "
                "with /invite @<app name> in the channel they want it to watch, "
                "or to DM the app directly. It only receives @mentions in "
                "channels it has been added to."
            )
        return {"messages": [], "count": 0, "hint": hint}

    @mcp.tool(annotations={"readOnlyHint": True})
    async def read_conversation(
        ctx: Context,
        conversation_id: Annotated[str, Field(description="Conversation id (see inbox)")],
    ) -> list[dict]:
        """The full back-and-forth of one conversation, oldest first — both what
        they sent and what this agent replied. Use it for context before
        answering a follow-up."""
        return await _gw(ctx, "GET", f"/conversations/{conversation_id}/messages")

    @mcp.tool()
    async def reply(
        ctx: Context,
        message_id: Annotated[str, Field(description="Id of the inbound message to answer")],
        text: Annotated[str, Field(description="The reply text")],
    ) -> dict:
        """Answer a message someone sent, in the same thread and on the same
        channel it arrived on. Get the message_id from `inbox`."""
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

    # No caspian://conversations resource: resources get no per-request Context
    # in this SDK path, so it could not read the caller's key and returned an
    # empty list — which reads as "your inbox is empty" when it means "not
    # implemented". The `inbox` tool is the honest, authenticated read path.

    # ── prompts (one-click recipes) ──────────────────────────────────────────

    @mcp.prompt()
    def triage_inbox() -> str:
        """Summarize what came in and suggest replies."""
        return (
            "Call inbox to see what people have sent. Group by who is waiting on "
            "a reply, and for each suggest a one-line answer I can approve. "
            "After I approve, send them with the reply tool."
        )

    # DNS-rebinding protection defaults to localhost-only allowed hosts, which
    # 421s every request arriving via the public hostname. Keep the protection
    # on, but allow the host users actually reach us on (plus local binds).
    allowed_hosts = ["127.0.0.1:*", "localhost:*"]
    for url in (public_url, base_url):
        h = urlsplit(url).hostname if url else None
        if h and h not in ("127.0.0.1", "localhost"):
            allowed_hosts += [h, f"{h}:*"]
    security = TransportSecuritySettings(allowed_hosts=allowed_hosts)

    return mcp.streamable_http_app(
        streamable_http_path="/", stateless_http=True, transport_security=security
    )
