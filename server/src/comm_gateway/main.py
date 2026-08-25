import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import select

from .analytics import configure_analytics
from .auth import hash_key
from .config import Settings
from .crypto import configure_cipher
from .db import ensure_columns, ensure_rls, make_engine, make_session_factory
from .ids import new_id
from .jobs import run_pending_jobs
from .models import ApiKey, Base, Project
from .providers import build_providers
from .routes import (
    api,
    billing,
    device,
    domains,
    guides,
    legal,
    oauth,
    onboarding,
    skill,
    usage,
    webhooks,
)
from .secrets_store import resolve_credentials_key, resolve_stripe_keys

log = logging.getLogger("comm.gateway")


def ensure_bootstrap(session_factory, settings: Settings) -> None:
    if not settings.bootstrap_api_key:
        return
    with session_factory() as session:
        key_hash = hash_key(settings.bootstrap_api_key)
        existing = session.execute(
            select(ApiKey).where(ApiKey.key_hash == key_hash)
        ).scalar_one_or_none()
        if existing is not None:
            return
        project = Project(id=new_id("proj"), name="default")
        session.add(project)
        session.flush()  # insert the project before its FK child (Postgres enforces FKs)
        session.add(ApiKey(id=new_id("key"), project_id=project.id, key_hash=key_hash))
        session.commit()
        log.info("bootstrapped project %s from COMM_BOOTSTRAP_API_KEY", project.id)


def _start_inline_worker(session_factory, providers) -> threading.Event:
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                handled = run_pending_jobs(session_factory, providers)
            except Exception:
                log.exception("inline worker iteration failed")
                handled = 0
            if handled == 0:
                stop.wait(0.5)

    thread = threading.Thread(target=loop, name="comm-inline-worker", daemon=True)
    thread.start()
    return stop


def create_app(
    settings: Settings | None = None,
    providers: dict | None = None,
) -> FastAPI:
    settings = settings or Settings()
    configure_cipher(resolve_credentials_key(settings))
    resolve_stripe_keys(settings)
    configure_analytics(
        settings.posthog_key if settings.telemetry else "", settings.posthog_host
    )
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    ensure_columns(engine, Base.metadata)
    ensure_rls(engine, Base.metadata)
    session_factory = make_session_factory(engine)
    providers = providers or build_providers(settings)
    ensure_bootstrap(session_factory, settings)

    # Build the MCP sub-app up front so its session-manager lifespan can be
    # entered inside the gateway lifespan (mounted sub-apps do not get their
    # lifespan run automatically by Starlette).
    mcp_app = None
    try:
        from comm_gateway.mcp import build_mcp

        mcp_app = build_mcp(
            base_url=f"http://{settings.host}:{settings.port}",
            public_url=settings.public_base_url,
        )
    except Exception as exc:  # noqa: BLE001 - MCP is additive; never fail boot
        import logging

        logging.getLogger("comm.mcp").warning("MCP not built: %s", exc)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = None
        listener_stop = None
        if settings.inline_worker:
            stop = _start_inline_worker(session_factory, providers)
            if any(name in providers for name in ("discord", "x", "bluesky", "slack")):
                from .listeners import run_listeners

                listener_stop = run_listeners(session_factory, settings)
        if mcp_app is not None:
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
        else:
            yield
        if stop is not None:
            stop.set()
        if listener_stop is not None:
            listener_stop.set()

    app = FastAPI(title="Communication Gateway", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.providers = providers
    app.include_router(api.router)
    app.include_router(billing.router)
    app.include_router(domains.router)
    app.include_router(oauth.router)
    app.include_router(onboarding.router)
    app.include_router(webhooks.router)
    app.include_router(skill.router)
    app.include_router(usage.router)
    app.include_router(device.router)
    app.include_router(guides.router)
    app.include_router(legal.router)

    # Mount the MCP server (built above). Same process, same Caddy, one deploy.
    if mcp_app is not None:
        app.mount("/mcp", mcp_app)

    return app


def run() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
