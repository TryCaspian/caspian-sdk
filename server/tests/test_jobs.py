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
from comm_gateway.models import Base, OutboxJob


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