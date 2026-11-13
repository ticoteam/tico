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

from backend import flight, github, message_bots, start_jobs
from backend.app import create_app
from backend.observability import Observability
from backend.onboarding import Onboarding
from backend.store import Store
from backend.tests.helpers.api import api_settings


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
    monkeypatch.setattr(Onboarding, "ensure_general_manager", job("general_manager"))
    monkeypatch.setattr(Onboarding, "ensure_counsel", job("counsel"))
    monkeypatch.setattr(Onboarding, "ensure_brand_owner", job("brand_owner"))
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
            # Database initialization belongs to startup, before the background-job deadline. Waiting for the
            # serving client first still fails if an inline job blocks lifespan, without timing migrations under load.
            client = clients.get(timeout=60)
            # The job must actually be waiting before the health request starts. Its wait cannot expire and
            # get swallowed as a job failure; the request's deadline belongs to the test thread instead.
            assert entered.wait(10), captured
            assert requests.submit(client.get, "/healthz").result(10).status_code == 200
            assert ran == []
        finally:
            release.set()
            server.result(10)
    # Stopping waited for them, and they ran in their old order, off the event loop's thread.
    assert ran == [
        "librarian",
        "goal_manager",
        "general_manager",
        "counsel",
        "brand_owner",
        "message_bots",
        "ship_deployed",
    ]
    assert threading.get_ident() not in threads and len(threads) == 1
    assert captured == []


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("synthetic"), sqlite3.OperationalError("database is locked")],
    ids=["job-failure", "job-busy"],
)
def test_a_failed_start_job_is_captured_and_the_next_one_still_runs(tmp_path, monkeypatch, failure):
    monkeypatch.delenv("TICO_START_JOBS", raising=False)
    ran, captured = [], []

    def broken(*args, **kwargs):
        raise failure

    monkeypatch.setattr(Onboarding, "ensure_librarian", broken)
    monkeypatch.setattr(message_bots, "backfill", lambda *a, **k: ran.append("message_bots"))
    monkeypatch.setattr(github, "ship_deployed", lambda *a, **k: ran.append("ship_deployed"))
    monkeypatch.setattr(Observability, "capture", lambda self, source, exc: captured.append((source, exc)))
    app = create_app(api_settings(tmp_path))
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
    assert ran == ["message_bots", "ship_deployed"]
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

    completes = job("complete_company")
    onboarding = SimpleNamespace(complete_company=lambda c, failed=None: completes(c))
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
    expected = [
        "complete_company",
        "message_bots",
        "ship_deployed",
    ]
    assert startup_jobs.ran == expected
    assert startup_jobs.captured == []
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM registry_metadata WHERE key LIKE 'start-test.%'").fetchone()[0] == len(
            expected
        )


def test_a_permanent_admission_failure_is_captured_and_the_next_job_still_runs(startup_jobs, monkeypatch):
    store = startup_jobs.store
    take = store._take
    failure = sqlite3.OperationalError("synthetic connection failure")

    def unavailable():
        monkeypatch.setattr(store, "_take", take)
        raise failure

    monkeypatch.setattr(store, "_take", unavailable)
    start_jobs.run(store, startup_jobs.onboarding, startup_jobs.telemetry)
    assert startup_jobs.ran == ["message_bots", "ship_deployed"]
    assert startup_jobs.captured == [("complete_company", failure)]


def test_a_database_that_stays_busy_ends_the_wait_and_the_job_is_captured(startup_jobs, monkeypatch):
    """Shutdown waits for the start jobs, so the admission retry has a deadline."""
    store = startup_jobs.store
    take, busy = store._take, sqlite3.OperationalError("database is locked")

    def locked_once_past_the_deadline():
        monkeypatch.setattr(store, "_take", take)
        monkeypatch.setattr(start_jobs, "ADMISSION_WAIT", 0)
        raise busy

    monkeypatch.setattr(start_jobs, "ADMISSION_WAIT", 0)
    monkeypatch.setattr(store, "_take", locked_once_past_the_deadline)
    start_jobs.run(store, startup_jobs.onboarding, startup_jobs.telemetry)
    assert startup_jobs.ran == ["message_bots", "ship_deployed"]
    assert startup_jobs.captured == [("complete_company", busy)]


def test_each_start_checks_the_dependencies_that_wait_for_the_release_it_runs(startup_jobs, monkeypatch):
    """A task that depends on another until released goes on when the release this server runs holds that task's merged
    PRs: every start-up asks task_depends.on_release, with the running version and the repository it was built from."""
    from backend import releases, task_depends

    seen = []
    monkeypatch.setattr(releases, "version", lambda: "0.6.0-rc.10")
    monkeypatch.setattr(task_depends, "on_release", lambda c, version, repo: seen.append((version, repo)) or [])
    start_jobs.run(startup_jobs.store, startup_jobs.onboarding, startup_jobs.telemetry)
    assert seen == [("0.6.0-rc.10", startup_jobs.store.settings.release_repo)]
    assert startup_jobs.captured == []
