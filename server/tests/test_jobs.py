"""Outbox job queue: concurrent workers must never double-process a job.

Uses a real file-backed SQLite database (not the in-memory `sqlite://` used
elsewhere) so two threads genuinely get independent connections and can race,
the same way two `comm-worker` processes would race against a shared Postgres
database in production.
"""

import os
import tempfile
import threading
import time

from comm_gateway import jobs as jobs_mod
from comm_gateway.db import make_engine, make_session_factory
from comm_gateway.models import Base, OutboxJob, ProviderEvent
from comm_gateway.providers.base import InboundMessage
from sqlalchemy import event, select


def _file_backed_session_factory():
    path = tempfile.mktemp(suffix=".db")
    engine = make_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    return make_session_factory(engine), engine, path


def test_two_concurrent_workers_never_double_process_a_job():
    session_factory, engine, path = _file_backed_session_factory()
    try:
        run_count = {"n": 0}
        lock = threading.Lock()

        def slow_handler(session, providers, payload):
            # Simulate real work (an outbound API call) happening between the
            # job being read and it being marked done - the exact window a
            # second worker could otherwise read the same "pending" row in.
            time.sleep(0.2)
            with lock:
                run_count["n"] += 1

        jobs_mod._HANDLERS["test_concurrent_job"] = slow_handler
        try:
            with session_factory() as s:
                s.add(OutboxJob(type="test_concurrent_job", payload={}, status="pending"))
                s.commit()

            handled_counts = []

            def worker():
                handled_counts.append(
                    jobs_mod.run_pending_jobs(session_factory, providers={}, max_jobs=1)
                )

            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            # The single enqueued job must be handled exactly once in total,
            # split across the two workers (one gets it, one finds nothing).
            assert run_count["n"] == 1
            assert sorted(handled_counts) == [0, 1]
        finally:
            del jobs_mod._HANDLERS["test_concurrent_job"]
    finally:
        engine.dispose()
        os.remove(path)


def test_losing_worker_moves_on_to_the_next_job():
    """A worker that loses the claim race for job A must still pick up job B,
    not stop or error out."""
    session_factory, engine, path = _file_backed_session_factory()
    try:
        seen = []

        def handler(session, providers, payload):
            seen.append(payload["name"])

        jobs_mod._HANDLERS["test_ordered_job"] = handler
        try:
            with session_factory() as s:
                s.add(OutboxJob(type="test_ordered_job", payload={"name": "a"}, status="pending"))
                s.add(OutboxJob(type="test_ordered_job", payload={"name": "b"}, status="pending"))
                s.commit()

            handled = jobs_mod.run_pending_jobs(session_factory, providers={}, max_jobs=10)
            assert handled == 2
            assert seen == ["a", "b"]
        finally:
            del jobs_mod._HANDLERS["test_ordered_job"]
    finally:
        engine.dispose()
        os.remove(path)


def test_concurrent_duplicate_webhook_ingest_is_idempotent():
    """Two near-simultaneous deliveries of the same provider event (the exact
    shape of a Twilio/Slack/Meta/Stripe retry racing the original request)
    must not 500 - the uq_provider_event constraint should be treated as an
    idempotent no-op, with exactly one ProviderEvent landing.

    A barrier on the dedupe SELECT forces both threads past the "not seen
    yet" check before either commits its insert - otherwise the two calls
    would very likely just run back-to-back and never actually race.
    """
    session_factory, engine, path = _file_backed_session_factory()
    try:
        item = InboundMessage(
            external_event_id="evt_dup_1",
            provider_inbox_id="inbox_1",
            provider_message_id="msg_1",
            provider_thread_id="thread_1",
            text="hi",
        )

        barrier = threading.Barrier(2, timeout=5)
        released = 0
        released_lock = threading.Lock()

        def _sync_dedupe_select(conn, cursor, statement, parameters, context, executemany):
            nonlocal released
            is_select = statement.strip().upper().startswith("SELECT")
            if "provider_events" not in statement or not is_select:
                return
            with released_lock:
                if released >= 2:
                    return
                released += 1
            barrier.wait()

        event.listen(engine, "before_cursor_execute", _sync_dedupe_select)
        try:
            errors = []
            counts = []
            counts_lock = threading.Lock()

            def worker():
                try:
                    n = jobs_mod.ingest_inbound(session_factory, "test-provider", [item])
                    with counts_lock:
                        counts.append(n)
                except Exception as exc:  # noqa: BLE001 - asserting none raised, below
                    with counts_lock:
                        errors.append(exc)

            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            t1.join()
            t2.join()
        finally:
            event.remove(engine, "before_cursor_execute", _sync_dedupe_select)

        assert errors == [], f"ingest_inbound raised: {errors!r}"
        assert sorted(counts) == [0, 1]
        with session_factory() as s:
            rows = s.execute(
                select(ProviderEvent).where(ProviderEvent.external_event_id == "evt_dup_1")
            ).scalars().all()
            assert len(rows) == 1
    finally:
        engine.dispose()
        os.remove(path)