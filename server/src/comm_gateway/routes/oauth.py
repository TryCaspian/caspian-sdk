"""OAuth install callbacks for Slack / Instagram / Facebook.

The developer's agent gets an authorize URL from connect; the human approves in
the browser; the platform redirects here with a code. We exchange it for the
per-connection token, activate the connection, and route the human back with a
plain confirmation page.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from ..crypto import read_credentials, write_credentials
from ..jobs import emit_event, enqueue
from ..models import Connection
from ..providers.discord import set_bot_nickname

log = logging.getLogger("comm.oauth")

router = APIRouter(prefix="/v1/oauth")

_DONE_PAGE = (
    "<html><body style='font-family:sans-serif;text-align:center;margin-top:15%'>"
    "<h2>{title}</h2><p>{body}</p><p>You can close this window.</p></body></html>"
)


@router.get("/discord/callback", response_class=HTMLResponse)
async def discord_install_callback(request: Request) -> HTMLResponse:
    """Shared Discord bot install callback.

    Unlike Slack (a token exchange), Discord's bot-add flow redirects here with the
    `guild_id` of the server the developer just added the shared bot to. We map that
    guild to the pending connection (matched by state) and activate it - the shared
    bot is already in their server, so there's nothing to provision. Inbound then
    routes by guild_id to this connection (see listeners + parse_gateway_message)."""
    params = request.query_params
    if params.get("error"):
        return HTMLResponse(
            _DONE_PAGE.format(title="Authorization declined", body=params.get("error")),
            status_code=400,
        )
    state = params.get("state")
    guild_id = params.get("guild_id")
    if not state or not guild_id:
        raise HTTPException(status_code=400, detail="Missing state or guild_id")

    with request.app.state.session_factory() as session:
        pending = session.execute(
            select(Connection).where(
                Connection.provider == "discord",
                Connection.status == "pending_oauth",
            )
        ).scalars().all()
        connection = next(
            (c for c in pending if read_credentials(c).get("oauth_state") == state), None
        )
        if connection is None:
            raise HTTPException(status_code=400, detail="Unknown or expired state")

        taken = session.execute(
            select(Connection).where(
                Connection.provider == "discord",
                Connection.provider_resource_id == str(guild_id),
                Connection.status.in_(("provisioning", "active")),
            )
        ).scalars().first()
        if taken is not None:
            connection.status = "failed"
            connection.error = "This Discord server is already connected to an agent"
            session.commit()
            return HTMLResponse(
                _DONE_PAGE.format(title="Already connected",
                                  body="This server is already linked to an agent."),
                status_code=409,
            )

        creds = read_credentials(connection)
        creds["guild_id"] = str(guild_id)
        nickname = creds.get("nickname")
        write_credentials(connection, creds)
        connection.provider_resource_id = str(guild_id)
        connection.address = f"discord://guild/{guild_id}"
        connection.status = "active"
        connection.error = None
        emit_event(
            session, connection.project_id, "connection.active",
            {"connection": {"id": connection.id, "channel": "discord",
                            "provider_resource_id": str(guild_id)}},
        )
        session.commit()

    # Give the shared bot the developer's custom name in this server (best-effort;
    # the connection works either way, it just shows the shared name if this fails).
    settings = request.app.state.settings
    if nickname and settings.discord_bot_token:
        try:
            await run_in_threadpool(
                set_bot_nickname, settings.discord_base_url,
                settings.discord_bot_token, str(guild_id), nickname,
            )
        except Exception as exc:
            log.warning("could not set bot nickname in guild %s: %s", guild_id, exc)

    return HTMLResponse(
        _DONE_PAGE.format(
            title="Connected",
            body="The agent is now in your Discord server. Mention it to chat.",
        )
    )


@router.get("/x/callback", response_class=HTMLResponse)
async def x_oauth_callback(request: Request) -> HTMLResponse:
    """One-click X install callback (OAuth 1.0a 3-legged / "Sign in with X").

    X redirects here with oauth_token + oauth_verifier. We exchange them for the
    account's NON-EXPIRING access token/secret + numeric id, activate the
    connection, and the DM poller starts polling it. The account is now the bot."""
    params = request.query_params
    if params.get("denied") or params.get("error"):
        return HTMLResponse(
            _DONE_PAGE.format(title="Authorization declined",
                              body=params.get("error") or "You declined the request."),
            status_code=400,
        )
    oauth_token = params.get("oauth_token")
    oauth_verifier = params.get("oauth_verifier")
    if not oauth_token or not oauth_verifier:
        raise HTTPException(status_code=400, detail="Missing oauth_token or oauth_verifier")

    provider = request.app.state.providers.get("x")
    if provider is None:
        raise HTTPException(status_code=404, detail="X is not configured on this gateway")

    with request.app.state.session_factory() as session:
        pending = session.execute(
            select(Connection).where(
                Connection.provider == "x",
                Connection.status == "pending_oauth",
            )
        ).scalars().all()
        connection = next(
            (c for c in pending if read_credentials(c).get("oauth_token") == oauth_token), None
        )
        if connection is None:
            raise HTTPException(status_code=400, detail="Unknown or expired oauth_token")

        stored = read_credentials(connection)
        try:
            result = await run_in_threadpool(
                provider.oauth_access_token, oauth_token, oauth_verifier,
                stored.get("oauth_token_secret", ""),
            )
        except Exception as exc:
            log.warning("x oauth exchange failed for %s: %s", connection.id, exc)
            connection.status = "failed"
            connection.error = str(exc)
            session.commit()
            return HTMLResponse(
                _DONE_PAGE.format(title="Connect failed", body=str(exc)), status_code=400
            )

        credentials = {
            "access_token": result["access_token"],
            "access_secret": result["access_secret"],
            "user_id": result["user_id"],
        }
        if result.get("username"):
            credentials["username"] = result["username"]
        write_credentials(connection, credentials)
        connection.provider_resource_id = result["user_id"] or None
        connection.status = "provisioning"
        enqueue(session, "provision_connection", {"connection_id": connection.id})
        emit_event(
            session, connection.project_id, "connection.authorized",
            {"connection_id": connection.id, "channel": connection.channel},
        )
        session.commit()

    handle = result.get("username") or result["user_id"]
    return HTMLResponse(
        _DONE_PAGE.format(title="Connected",
                          body=f"@{handle} is now your agent on X. DM it to chat.")
    )


@router.get("/{provider_name}/callback", response_class=HTMLResponse)
async def oauth_callback(provider_name: str, request: Request) -> HTMLResponse:
    provider = request.app.state.providers.get(provider_name)
    if provider is None or not getattr(provider, "oauth", False):
        raise HTTPException(status_code=404, detail="Unknown OAuth provider")

    params = request.query_params
    if params.get("error"):
        return HTMLResponse(
            _DONE_PAGE.format(title="Authorization declined", body=params.get("error")),
            status_code=400,
        )
    code = params.get("code")
    state = params.get("state")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    with request.app.state.session_factory() as session:
        connection = session.execute(
            select(Connection).where(
                Connection.provider == provider.name,
                Connection.status == "pending_oauth",
            )
        ).scalars().all()
        connection = next(
            (c for c in connection if read_credentials(c).get("oauth_state") == state),
            None,
        )
        if connection is None:
            raise HTTPException(status_code=400, detail="Unknown or expired state")

        stored = read_credentials(connection)
        redirect_uri = stored.get("redirect_uri", "")
        # preserve the connection's own Slack app credentials across the exchange
        app_creds = {
            k: stored[k]
            for k in ("slack_client_id", "slack_client_secret", "slack_signing_secret")
            if k in stored
        }
        try:
            result = provider.exchange_code(code, redirect_uri, app=app_creds or None)
        except Exception as exc:  # provider raises WebhookVerificationError / httpx errors
            log.warning("oauth exchange failed for %s: %s", connection.id, exc)
            connection.status = "failed"
            connection.error = str(exc)
            session.commit()
            return HTMLResponse(
                _DONE_PAGE.format(title="Install failed", body=str(exc)), status_code=400
            )

        credentials = {**app_creds, **result["credentials"]}
        credentials["address"] = result.get("address", provider.name)
        credentials["provider_resource_id"] = result.get("provider_resource_id", "")
        # Carry forward shared-app branding (the developer's posting name/icon).
        for k in ("display_name", "icon_url"):
            if stored.get(k):
                credentials[k] = stored[k]
        write_credentials(connection, credentials)
        connection.provider_resource_id = result.get("provider_resource_id") or None
        connection.status = "provisioning"
        enqueue(session, "provision_connection", {"connection_id": connection.id})
        emit_event(
            session,
            connection.project_id,
            "connection.authorized",
            {"connection_id": connection.id, "channel": connection.channel},
        )
        session.commit()

    return HTMLResponse(
        _DONE_PAGE.format(
            title="Connected", body=f"Your agent is now on {provider.channel}."
        )
    )
