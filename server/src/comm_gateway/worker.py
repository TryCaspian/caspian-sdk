import logging
import time

from .config import Settings
from .crypto import configure_cipher
from .db import ensure_columns, make_engine, make_session_factory
from .jobs import run_pending_jobs
from .listeners import run_listeners
from .models import Base
from .providers import build_providers
from .secrets_store import resolve_credentials_key

log = logging.getLogger("comm.worker")


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings(inline_worker=False)
    configure_cipher(resolve_credentials_key(settings))
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    ensure_columns(engine, Base.metadata)
    session_factory = make_session_factory(engine)
    providers = build_providers(settings)
    if "discord" in providers or "x" in providers:
        run_listeners(session_factory, settings)  # Discord Gateway + X DM poller
    log.info("worker started (providers=%s)", ", ".join(providers))
    while True:
        try:
            handled = run_pending_jobs(session_factory, providers)
        except Exception:
            log.exception("worker iteration failed")
            handled = 0
        if handled == 0:
            time.sleep(0.5)
