"""The start jobs no request depends on run beside a serving server, not before it (backend/start_jobs.py).

conftest.py runs them before serving for every other test (TICO_START_JOBS=inline); this one checks the served way.
"""
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from types import SimpleNamespace

import pytest

from fastapi.testclient import TestClient

from backend import flight, github, listening, message_bots, start_jobs
from backend.app import create_app
from backend.observability import Observability
from backend.onboarding import Onboarding
from backend.store import Store
from backend.tests.test_api import api_settings


def test_a_server_answers_while_its_start_jobs_run_and_waits_for_them_to_stop(tmp_path, monkeypatch):
    monkeypatch.delenv("TICO_START_JOBS", raising=False)
    entered, release, ran, threads, captured = threading.Event(), threading.Event(), [], set(), []

    def job(name, wait=False):
        def run(*args, **kwargs):
            threads.add(threading.get_ident())
            if wait:
                entered.set()
                release.wait()
            ran.append(name)
        return run

    monkeypatch.setattr(Onboarding, "ensure_librarian", job("librarian", wait=True))
    monkeypatch.setattr(Onboarding, "ensure_goal_manager", job("goal_manager"))
    monkeypatch.setattr(Onboarding, "ensure_task_manager", job("task_manager"))
    monkeypatch.setattr(Onboarding, "ensure_counsel", job("counsel"))
    monkeypatch.setattr(listening, "seed_market", job("market"))
    monkeypatch.setattr(message_bots, "backfill", job("message_bots"))
    monkeypatch.setattr(github, "ship_deployed", job("ship_deployed"))
    monkeypatch.setattr(Observability, "capture", lambda self, source, exc: captured.append((source, exc)))
    app = create_app(api_settings(tmp_path))
    clients = Queue()

    def serve():
        with TestClient(app) as client:
            clients.put(client)
            release.wait()

    with ThreadPoolExecutor(max_workers=2) as requests:
        server = requests.submit(serve)
        try:
            # The job must actually be waiting before the health request starts. Its wait cannot expire and
            # get swallowed as a job failure; the request's deadline belongs to the test thread instead.
            assert entered.wait(10), captured
            client = clients.get(timeout=10)
            assert requests.submit(client.get, "/healthz").result(10).status_code == 200
            assert ran == []
        finally:
            release.set()
            server.result(10)
    # Stopping waited for them, and they ran in their old order, off the event loop's thread.
    assert ran == ["librarian", "goal_manager", "task_manager", "counsel", "market", "message_bots", "ship_deployed"]
    assert threading.get_ident() not in threads and len(threads) == 1
    assert captured == []


@pytest.mark.parametrize("failure", [RuntimeError("synthetic"), sqlite3.OperationalError("database is locked")],
                         ids=["job-failure", "job-busy"])
def test_a_failed_start_job_is_captured_and_the_next_one_still_runs(tmp_path, monkeypatch, failure):
    monkeypatch.delenv("TICO_START_JOBS", raising=False)
    ran, captured = [], []

    def broken(*args, **kwargs):
        raise failure

    monkeypatch.setattr(Onboarding, "ensure_librarian", broken)
    monkeypatch.setattr(listening, "seed_market", lambda *a, **k: ran.append("market"))
    monkeypatch.setattr(message_bots, "backfill", lambda *a, **k: ran.append("message_bots"))
    monkeypatch.setattr(github, "ship_deployed", lambda *a, **k: ran.append("ship_deployed"))
    monkeypatch.setattr(Observability, "capture", lambda self, source, exc: captured.append((source, exc)))
    app = create_app(api_settings(tmp_path))
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
    assert ran == ["market", "message_bots", "ship_deployed"]
    assert captured == [("librarian", failure)]


@pytest.fixture
def startup_jobs(tmp_path, monkeypatch):
    store = Store(api_settings(tmp_path))
    store.initialize()
    ran, captured = [], []

    def job(name):
        def run(c, *args):
            c.execute("INSERT INTO registry_metadata(key,value) VALUES(?, 'true')", ("start-test." + name,))
            ran.append(name)
        return run

    onboarding = SimpleNamespace(**{name: job(name) for name in (
        "ensure_librarian", "ensure_goal_manager", "ensure_task_manager", "ensure_counsel")})
    monkeypatch.setattr(listening, "seed_market", job("market"))
    monkeypatch.setattr(message_bots, "backfill", job("message_bots"))
    monkeypatch.setattr(github, "ship_deployed", job("ship_deployed"))
    telemetry = SimpleNamespace(capture=lambda source, exc: captured.append((source, exc)))
    return SimpleNamespace(store=store, onboarding=onboarding, telemetry=telemetry, ran=ran, captured=captured)


@pytest.mark.parametrize("lock", ["sqlite", "gate"])
def test_start_jobs_wait_for_write_admission_instead_of_skipping_jobs(startup_jobs, monkeypatch, lock):
    store = startup_jobs.store
    blocked, failures = threading.Event(), []
    begin = flight.begin

    def observed_begin(*args, **kwargs):
        try:
            return begin(*args, **kwargs)
        except sqlite3.OperationalError as exc:
            failures.append(exc)
            blocked.set()
            raise

    monkeypatch.setattr(flight, "begin", observed_begin)
    if lock == "sqlite":
        # A real SQLite writer outside the Store's gate, with its timeout removed to avoid a long timer.
        holder = sqlite3.connect(store.settings.db_path, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        take = store._take

        def no_wait():
            c = take()
            c.execute("PRAGMA busy_timeout=0")
            return c

        monkeypatch.setattr(store, "_take", no_wait)
        unlock = holder.close
    else:
        store.writer.acquire()
        store.writer.timeout = 0
        unlock = store.writer.release
    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(start_jobs.run, store, startup_jobs.onboarding, startup_jobs.telemetry)
        try:
            assert blocked.wait(10)
            assert startup_jobs.ran == []
        finally:
            unlock()
        pending.result(10)
    if lock == "sqlite":
        assert failures[0].sqlite_errorcode == sqlite3.SQLITE_BUSY
    else:
        assert str(failures[0]) == "database is locked"
    expected = ["ensure_librarian", "ensure_goal_manager", "ensure_task_manager", "ensure_counsel",
                "market", "message_bots", "ship_deployed"]
    assert startup_jobs.ran == expected
    assert startup_jobs.captured == []
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM registry_metadata WHERE key LIKE 'start-test.%'").fetchone()[0] == len(expected)


def test_a_permanent_admission_failure_is_captured_and_the_next_job_still_runs(startup_jobs, monkeypatch):
    store = startup_jobs.store
    take = store._take
    failure = sqlite3.OperationalError("synthetic connection failure")

    def unavailable():
        monkeypatch.setattr(store, "_take", take)
        raise failure

    monkeypatch.setattr(store, "_take", unavailable)
    start_jobs.run(store, startup_jobs.onboarding, startup_jobs.telemetry)
    assert startup_jobs.ran == ["market", "message_bots", "ship_deployed"]
    assert startup_jobs.captured == [("librarian", failure)]
