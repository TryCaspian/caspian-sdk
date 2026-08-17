"""Provider webhook receiver.

Verify, deduplicate, persist, acknowledge. Normalization into our public
message model happens in the worker, never in the request path.

Two shapes:
- /internal/providers/{provider}/webhooks - deployment-wide (SES, fakes)
- /internal/providers/{provider}/webhooks/{resource_id} - per-connection
  (Telegram bots: the resource id routes to the connection whose stored
  credentials verify the payload)
"""

import json
import logging

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select

from ..crypto import read_credentials
from ..jobs import ingest_inbound
from ..models import Connection
from ..providers.base import WebhookVerificationError

log = logging.getLogger("comm.webhooks")

router = APIRouter(prefix="/internal/providers")


def _ingest(request: Request, provider, inbound) -> Response:
    ingest_inbound(request.app.state.session_factory, provider.name, inbound)
    # Some providers (Zulip outgoing webhooks) require a JSON response body or
    # they surface a "Failure! Invalid JSON in response" error in the channel.
    ack = getattr(provider, "webhook_ack_body", None)
    if ack is not None:
        return JSONResponse(ack)
    return Response(status_code=getattr(provider, "webhook_success_status", 204))


def _provider_or_404(request: Request, provider_name: str):
    provider = request.app.state.providers.get(provider_name)
    if provider is None:
        raise HTTPException(status_code=404, detail="Unknown provider")
    return provider


@router.get("/{provider_name}/webhooks")
async def verify_provider_webhook(provider_name: str, request: Request):
    """GET challenge handshake for providers that verify their webhook URL.

    - Meta (Instagram / Facebook / WhatsApp): echo hub.challenge as plaintext.
    - X Account Activity CRC: answer the crc_token with a signed JSON
      {"response_token": "sha256=..."} body.
    """
    provider = _provider_or_404(request, provider_name)
    if hasattr(provider, "meta_verify"):
        challenge = provider.meta_verify(dict(request.query_params))
        if challenge is not None:
            return PlainTextResponse(challenge)
    if hasattr(provider, "verify_challenge"):
        token = provider.verify_challenge(dict(request.query_params))
        if token is not None:
            return JSONResponse(token)
    raise HTTPException(status_code=403, detail="verification failed")


@router.post("/{provider_name}/webhooks", status_code=204)
async def receive_provider_webhook(provider_name: str, request: Request) -> Response:
    body = await request.body()
    # Slack's one-time Events API URL handshake echoes a challenge back. Answer
    # it before the provider lookup so the app's Events URL can be verified even
    # before this provider is enabled on the gateway (avoids a chicken-and-egg).
    if body:
        try:
            peek = json.loads(body)
        except ValueError:
            peek = None
        if isinstance(peek, dict) and peek.get("type") == "url_verification":
            return PlainTextResponse(peek.get("challenge", ""))
    provider = _provider_or_404(request, provider_name)

    # Providers with bring-your-own app credentials (Slack) share one events URL
    # across many apps. Route by a key in the payload (api_app_id) to the owning
    # connection, then verify with THAT connection's signing secret.
    credentials = None
    if hasattr(provider, "route_key"):
        key = provider.route_key(body)
        if key is None:
            raise HTTPException(status_code=400, detail="Cannot route webhook")
        with request.app.state.session_factory() as session:
            connection = session.execute(
                select(Connection).where(
                    Connection.provider == provider.name,
                    Connection.provider_resource_id == str(key),
                    Connection.status.in_(["provisioning", "active"]),
                )
            ).scalars().first()
        if connection is None:
            raise HTTPException(status_code=404, detail="Unknown app")
        credentials = read_credentials(connection)

    try:
        inbound = provider.parse_webhook(body, dict(request.headers), credentials=credentials)
    except WebhookVerificationError as exc:
        log.warning("webhook verification failed for %s: %s", provider_name, exc)
        raise HTTPException(status_code=400, detail="Webhook verification failed") from exc
    return _ingest(request, provider, inbound)


@router.post("/{provider_name}/interactions")
async def receive_provider_interaction(provider_name: str, request: Request) -> Response:
    """Interactivity endpoint for button taps (Slack Block Kit `block_actions`).

    Slack posts form-encoded interactions to the app's Interactivity Request URL
    (separate from the Events URL) and needs a 200 within 3s. We route by
    api_app_id:team_id to the owning connection, verify with its signing secret,
    parse the tapped button's value, and enqueue an interaction event."""
    provider = _provider_or_404(request, provider_name)
    if not hasattr(provider, "parse_interaction"):
        raise HTTPException(status_code=404, detail="Provider has no interactions endpoint")
    body = await request.body()
    key = (provider.interaction_route_key(body)
           if hasattr(provider, "interaction_route_key") else None)
    if key is None:
        raise HTTPException(status_code=400, detail="Cannot route interaction")
    with request.app.state.session_factory() as session:
        connection = session.execute(
            select(Connection).where(
                Connection.provider == provider.name,
                Connection.provider_resource_id == str(key),
                Connection.status.in_(["provisioning", "active"]),
            )
        ).scalars().first()
    if connection is None:
        raise HTTPException(status_code=404, detail="Unknown app")
    credentials = read_credentials(connection)
    try:
        inbound = provider.parse_interaction(body, dict(request.headers), credentials=credentials)
    except WebhookVerificationError as exc:
        log.warning("interaction verification failed for %s: %s", provider_name, exc)
        raise HTTPException(status_code=400, detail="Interaction verification failed") from exc
    ingest_inbound(request.app.state.session_factory, provider.name, inbound)
    # Slack wants a fast 200; the actual work already happened via the queue.
    return Response(status_code=200)


@router.post("/{provider_name}/webhooks/{resource_id}", status_code=204)
async def receive_scoped_webhook(
    provider_name: str, resource_id: str, request: Request
) -> Response:
    provider = _provider_or_404(request, provider_name)
    with request.app.state.session_factory() as session:
        connection = session.execute(
            select(Connection).where(
                Connection.provider == provider.name,
                Connection.provider_resource_id == resource_id,
                Connection.status.in_(["provisioning", "active"]),
            )
        ).scalars().first()
    if connection is None:
        raise HTTPException(status_code=404, detail="Unknown resource")

    body = await request.body()
    # The exact PUBLIC url this webhook was configured with (Twilio signs over it).
    # request.url is the internal proxied url, so rebuild it from public_base_url.
    base = request.app.state.settings.public_base_url or str(request.base_url).rstrip("/")
    scoped_credentials = {
        **read_credentials(connection),
        "provider_resource_id": connection.provider_resource_id,
        "_webhook_url": f"{base}{request.url.path}",
    }
    try:
        inbound = provider.parse_webhook(
            body, dict(request.headers), credentials=scoped_credentials
        )
    except WebhookVerificationError as exc:
        raise HTTPException(status_code=400, detail="Webhook verification failed") from exc
    return _ingest(request, provider, inbound)
