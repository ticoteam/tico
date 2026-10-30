"""The start jobs no request depends on run beside a serving server, not before it (backend/app.py start_jobs).

conftest.py runs them before serving for every other test (TICO_START_JOBS=inline); this one checks the served way.
"""
import threading

from fastapi.testclient import TestClient

from backend import github, listening, message_bots
from backend.app import create_app
from backend.onboarding import Onboarding
from backend.tests.test_api import api_settings


def test_a_server_answers_while_its_start_jobs_run_and_waits_for_them_to_stop(tmp_path, monkeypatch):
    monkeypatch.delenv("TICO_START_JOBS", raising=False)
    release, ran, threads = threading.Event(), [], set()

    def job(name, wait=False):
        def run(*args, **kwargs):
            threads.add(threading.get_ident())
            if wait:
                assert release.wait(10)
            ran.append(name)
        return run

    monkeypatch.setattr(Onboarding, "ensure_librarian", job("librarian", wait=True))
    monkeypatch.setattr(Onboarding, "ensure_goal_manager", job("goal_manager"))
    monkeypatch.setattr(Onboarding, "ensure_task_manager", job("task_manager"))
    monkeypatch.setattr(listening, "seed_market", job("market"))
    monkeypatch.setattr(message_bots, "backfill", job("message_bots"))
    monkeypatch.setattr(github, "ship_deployed", job("ship_deployed"))
    app = create_app(api_settings(tmp_path))
    with TestClient(app) as client:
        # Started and answering while the first job still waits.
        assert client.get("/healthz").status_code == 200
        assert ran == []
        release.set()
    # Stopping waited for them, and they ran in their old order, off the event loop's thread.
    assert ran == ["librarian", "goal_manager", "task_manager", "market", "message_bots", "ship_deployed"]
    assert threading.get_ident() not in threads and len(threads) == 1


def test_a_failed_start_job_is_captured_and_the_next_one_still_runs(tmp_path, monkeypatch):
    monkeypatch.delenv("TICO_START_JOBS", raising=False)
    ran = []

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(Onboarding, "ensure_librarian", broken)
    monkeypatch.setattr(listening, "seed_market", lambda *a, **k: ran.append("market"))
    monkeypatch.setattr(message_bots, "backfill", lambda *a, **k: ran.append("message_bots"))
    monkeypatch.setattr(github, "ship_deployed", lambda *a, **k: ran.append("ship_deployed"))
    app = create_app(api_settings(tmp_path))
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
    assert ran == ["market", "message_bots", "ship_deployed"]
