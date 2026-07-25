import logging

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

log = logging.getLogger("comm.db")


def make_engine(database_url: str) -> Engine:
    kwargs: dict = {}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if database_url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
    else:
        # Postgres (e.g. Supabase session pooler): recycle idle connections the
        # pooler may drop, and verify liveness before use.
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = 1800
    return create_engine(database_url, **kwargs)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def ensure_columns(engine: Engine, metadata) -> None:
    """Add columns that exist in the models but not in the live database.

    create_all only creates missing tables. This closes the gap for simple
    additive column changes until a real migration tool is warranted.
    """
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table in metadata.sorted_tables:
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                column_type = column.type.compile(engine.dialect)
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column_type}"
                if column.default is not None and getattr(column.default, "is_scalar", False):
                    ddl += f" DEFAULT {column.default.arg!r}"
                log.info("adding missing column %s.%s", table.name, column.name)
                connection.execute(text(ddl))


def ensure_rls(engine: Engine, metadata) -> None:
    """Enable Row Level Security on every table (Postgres only).

    Supabase exposes the ``public`` schema through its REST API using the public
    anon key, so a table created by ``create_all`` with no RLS is world-readable
    via that API. With RLS enabled and no policies, the ``anon``/``authenticated``
    roles are denied; the gateway connects as ``postgres`` (which has BYPASSRLS),
    so it is unaffected. Runs every startup so a newly-added table can never
    silently reopen the hole.
    """
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        for table in metadata.sorted_tables:
            connection.execute(
                text(f'ALTER TABLE public."{table.name}" ENABLE ROW LEVEL SECURITY')
            )
    log.info("row level security enabled on %d tables", len(metadata.sorted_tables))
