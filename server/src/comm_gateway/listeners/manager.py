"""Listener manager: one persistent connection per listener-channel connection.

Runs its own asyncio loop in a background thread. Periodically reconciles the
live set of connections that need a persistent listener (Discord bot
connections) against the running clients — starting new ones, stopping
removed ones — and also runs a simple poll loop per connection for channels
without a push transport (X DMs, Bluesky notifications, Reddit private
messages). Each client/poller feeds inbound into `ingest_inbound`, the same
pipeline the webhook route uses.
"""

import asyncio
import logging
import threading

from sqlalchemy import select

from ..crypto import read_credentials, write_credentials
from ..jobs import ingest_inbound
from ..models import Connection
from .discord_gateway import DiscordGatewayClient
from .slack_socket import SlackSocketClient

log = logging.getLogger("comm.listener")

RECONCILE_INTERVAL = 15.0


def _active_discord_bots(session_factory) -> dict[str, str]:
    """Map app_id -> bot_token for every active Discord *bot* connection.

    Webhook-identity Discord connections have no bot_token and receive nothing,
    so they are skipped.
    """
    out: dict[str, str] = {}
    with session_factory() as session:
        rows = session.execute(
            select(Connection).where(
                Connection.provider == "discord",
                Connection.status == "active",
            )
        ).scalars().all()
        for conn in rows:
            creds = read_credentials(conn)
            token = creds.get("bot_token")
            if token and conn.provider_resource_id:
                out[conn.provider_resource_id] = token
    return out


def _active_slack_socket_connections(session_factory) -> dict[str, str]:
    """conn_id -> app_token for active BYO-token (Socket Mode) Slack connections.

    OAuth Slack connections have no app_token and receive over the webhook, so
    they're skipped — only bring-your-own-token connections need a held socket.
    """
    out: dict[str, str] = {}
    with session_factory() as session:
        rows = session.execute(
            select(Connection).where(
                Connection.provider == "slack",
                Connection.status == "active",
            )
        ).scalars().all()
        for conn in rows:
            app_token = read_credentials(conn).get("app_token")
            if app_token:
                out[conn.id] = app_token
    return out


async def _slack_socket_loop(session_factory, settings, stop_event: threading.Event) -> None:
    """Reconcile a held Socket Mode WebSocket per active BYO-token Slack connection
    (same shape as the Discord gateway reconciler)."""
    clients: dict[str, tuple] = {}  # conn_id -> (client, task)

    def sink(inbound) -> None:
        ingest_inbound(session_factory, "slack", inbound)

    while not stop_event.is_set():
        try:
            wanted = _active_slack_socket_connections(session_factory)
        except Exception:
            log.exception("slack socket reconcile failed to read connections")
            wanted = {cid: c._app_token for cid, (c, _) in clients.items()}  # keep current

        for conn_id, app_token in wanted.items():
            if conn_id not in clients:
                client = SlackSocketClient(app_token, conn_id, sink)
                task = asyncio.create_task(client.run())
                clients[conn_id] = (client, task)
                log.info("started slack socket listener for connection %s", conn_id)
        for conn_id in list(clients):
            if conn_id not in wanted:
                client, task = clients.pop(conn_id)
                client.stop()
                task.cancel()
                log.info("stopped slack socket listener for connection %s", conn_id)

        await asyncio.sleep(RECONCILE_INTERVAL)

    for client, task in clients.values():
        client.stop()
        task.cancel()


async def _reconcile_loop(session_factory, settings, stop_event: threading.Event) -> None:
    clients: dict[str, tuple] = {}  # app_id -> (client, task)
    api_base = f"{settings.discord_base_url}"

    def sink(inbound) -> None:
        ingest_inbound(session_factory, "discord", inbound)

    # The shared "Caspian" bot: ONE Gateway connection for the whole deployment,
    # in every server developers installed it into. It routes each message by
    # guild id to that developer's connection. Start it once, up front.
    shared_task = None
    if settings.discord_bot_token:
        shared = DiscordGatewayClient(
            settings.discord_bot_token, "shared", sink, api_base, route_by_guild=True
        )
        shared_task = asyncio.create_task(shared.run())
        log.info("started shared discord bot listener")

    while not stop_event.is_set():
        try:
            wanted = _active_discord_bots(session_factory)
        except Exception:
            log.exception("listener reconcile failed to read connections")
            wanted = {app: c for app, (c, _) in clients.items()}  # keep current

        # start new
        for app_id, token in wanted.items():
            if app_id not in clients:
                client = DiscordGatewayClient(token, app_id, sink, api_base)
                task = asyncio.create_task(client.run())
                clients[app_id] = (client, task)
                log.info("started discord listener for app %s", app_id)
        # stop removed
        for app_id in list(clients):
            if app_id not in wanted:
                client, task = clients.pop(app_id)
                client.stop()
                task.cancel()
                log.info("stopped discord listener for app %s", app_id)

        await asyncio.sleep(RECONCILE_INTERVAL)

    for client, task in clients.values():
        client.stop()
        task.cancel()
    if shared_task is not None:
        shared_task.cancel()


def _active_x_connections(session_factory) -> list[tuple[str, dict]]:
    """(connection_id, decrypted credentials) for every active X connection."""
    out: list[tuple[str, dict]] = []
    with session_factory() as session:
        rows = session.execute(
            select(Connection).where(
                Connection.provider == "x",
                Connection.status == "active",
            )
        ).scalars().all()
        for conn in rows:
            out.append((conn.id, read_credentials(conn)))
    return out

def _active_bluesky_connections(session_factory) -> list[tuple[str, dict]]:
    """(connection_id, decrypted credentials) for every active Bluesky connection."""
    out: list[tuple[str, dict]] = []
    with session_factory() as session:
        rows = session.execute(
            select(Connection).where(
                Connection.provider == "bluesky",
                Connection.status == "active",
            )
        ).scalars().all()

        for conn in rows:
            out.append((conn.id, read_credentials(conn)))

    return out


def _save_bluesky_cursor(session_factory, conn_id: str, cursor: str) -> None:
    """Persist the newest-seen Bluesky notification cursor."""
    with session_factory() as session:
        conn = session.get(Connection, conn_id)
        if conn is None:
            return

        creds = read_credentials(conn)
        creds["bluesky_cursor"] = cursor
        write_credentials(conn, creds)
        session.commit()


def _active_reddit_connections(session_factory) -> list[tuple[str, dict]]:
    """(connection_id, decrypted credentials) for every active Reddit connection."""
    out: list[tuple[str, dict]] = []
    with session_factory() as session:
        rows = session.execute(
            select(Connection).where(
                Connection.provider == "reddit",
                Connection.status == "active",
            )
        ).scalars().all()

        for conn in rows:
            out.append((conn.id, read_credentials(conn)))

    return out


def _save_reddit_cursor(session_factory, conn_id: str, cursor: str) -> None:
    """Persist the newest-seen Reddit inbox cursor (created_utc)."""
    with session_factory() as session:
        conn = session.get(Connection, conn_id)
        if conn is None:
            return

        creds = read_credentials(conn)
        creds["reddit_cursor"] = cursor
        write_credentials(conn, creds)
        session.commit()


def _save_dm_cursor(session_factory, conn_id: str, cursor: str) -> None:
    """Persist the newest-seen dm_event id on the connection so the next poll
    only picks up messages after it (survives restarts)."""
    with session_factory() as session:
        conn = session.get(Connection, conn_id)
        if conn is None:
            return
        creds = read_credentials(conn)
        creds["dm_cursor"] = cursor
        write_credentials(conn, creds)
        session.commit()


async def _x_dm_poll_loop(session_factory, settings, stop_event: threading.Event) -> None:
    """Poll each active X connection's DMs on an interval and ingest new ones.

    Replaces the Account Activity webhook (which caps subscriptions per app and
    needs enterprise access) with per-connection polling of GET /2/dm_events.
    """
    from ..providers.registry import _build_one

    provider = _build_one("x", settings)
    interval = getattr(settings, "x_dm_poll_interval", 10.0)
    log.info("started x DM poller (interval=%ss)", interval)

    while not stop_event.is_set():
        try:
            conns = _active_x_connections(session_factory)
        except Exception:
            log.exception("x poller failed to read connections")
            conns = []
        for conn_id, creds in conns:
            try:
                cursor = creds.get("dm_cursor")
                msgs, new_cursor = await asyncio.to_thread(provider.poll_dms, creds, cursor)
                if msgs:
                    ingest_inbound(session_factory, "x", msgs)
                if new_cursor != cursor:
                    _save_dm_cursor(session_factory, conn_id, new_cursor)
            except Exception:
                log.exception("x DM poll failed for connection %s", conn_id)
        await asyncio.sleep(interval)

async def _bluesky_poll_loop(session_factory, settings, stop_event: threading.Event) -> None:
    """Poll each active Bluesky connection for mentions and replies."""

    from ..providers.registry import _build_one

    provider = _build_one("bluesky", settings)

    interval = getattr(settings, "bluesky_poll_interval", 10.0)

    log.info("started Bluesky poller (interval=%ss)", interval)

    while not stop_event.is_set():
        try:
            conns = _active_bluesky_connections(session_factory)
        except Exception:
            log.exception("bluesky poller failed to read connections")
            conns = []

        for conn_id, creds in conns:
            try:
                cursor = creds.get("bluesky_cursor")

                msgs, new_cursor = await asyncio.to_thread(
                    provider.poll_notifications,
                    creds,
                    cursor,
                )

                if msgs:
                    ingest_inbound(session_factory, "bluesky", msgs)

                if new_cursor != cursor:
                    _save_bluesky_cursor(
                        session_factory,
                        conn_id,
                        new_cursor,
                    )

            except Exception:
                log.exception(
                    "bluesky poll failed for connection %s",
                    conn_id,
                )

        await asyncio.sleep(interval)

async def _reddit_poll_loop(session_factory, settings, stop_event: threading.Event) -> None:
    """Poll each active Reddit connection's inbox for new private messages."""

    from ..providers.registry import _build_one

    provider = _build_one("reddit", settings)

    interval = getattr(settings, "reddit_poll_interval", 15.0)

    log.info("started Reddit poller (interval=%ss)", interval)

    while not stop_event.is_set():
        try:
            conns = _active_reddit_connections(session_factory)
        except Exception:
            log.exception("reddit poller failed to read connections")
            conns = []

        for conn_id, creds in conns:
            try:
                cursor = creds.get("reddit_cursor")

                msgs, new_cursor = await asyncio.to_thread(
                    provider.poll_messages,
                    creds,
                    cursor,
                )

                if msgs:
                    ingest_inbound(session_factory, "reddit", msgs)

                if new_cursor != cursor:
                    _save_reddit_cursor(
                        session_factory,
                        conn_id,
                        new_cursor,
                    )

            except Exception:
                log.exception(
                    "reddit poll failed for connection %s",
                    conn_id,
                )

        await asyncio.sleep(interval)

async def _run_all(session_factory, settings, stop_event: threading.Event) -> None:
    names = [n.strip() for n in (settings.providers or settings.provider).split(",")]
    tasks = [asyncio.create_task(_reconcile_loop(session_factory, settings, stop_event))]
    if "x" in names:
        tasks.append(
        asyncio.create_task(
            _x_dm_poll_loop(session_factory, settings, stop_event)
        )
    )

    if "bluesky" in names:
        tasks.append(
            asyncio.create_task(
                _bluesky_poll_loop(session_factory, settings, stop_event)
            )
        )

    if "reddit" in names:
        tasks.append(
            asyncio.create_task(
                _reddit_poll_loop(session_factory, settings, stop_event)
            )
        )

    if "slack" in names:
        tasks.append(
            asyncio.create_task(
                _slack_socket_loop(session_factory, settings, stop_event)
            )
        )
    await asyncio.gather(*tasks)


def run_listeners(session_factory, settings) -> threading.Event:
    """Start the listener manager in a background thread. Returns a stop Event.

    No-op-safe: if the channels that need listeners aren't configured, the loop
    simply finds nothing to run. Runs the Discord Gateway reconciler and (when
    `x` is configured) the X DM poller in one asyncio loop.
    """
    stop_event = threading.Event()

    def _thread() -> None:
        try:
            asyncio.run(_run_all(session_factory, settings, stop_event))
        except Exception:
            log.exception("listener manager crashed")

    thread = threading.Thread(target=_thread, name="comm-listeners", daemon=True)
    thread.start()
    return stop_event
