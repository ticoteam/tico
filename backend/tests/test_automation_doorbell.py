"""A request drains only its own store's automations, without taking another store's wakeup."""

from datetime import datetime, timezone

import time_machine
from fastapi.testclient import TestClient

from backend import demo_seed
from backend.app import create_app
from backend.tests.test_api import api, api_settings, get  # noqa: F401
from backend.tests.test_automation_runs import make, queued, run_row


def test_another_apps_request_does_not_take_a_pending_automation_wakeup(api, tmp_path):
    aid = make(api, [{"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "Ready"}}])
    # Work queued outside a request waits for this app's next request, even if a different app serves first.
    with time_machine.travel(datetime(2030, 1, 1, tzinfo=timezone.utc), tick=False):
        rid = queued(api, aid)
    directory = tmp_path / "other"
    directory.mkdir()
    with TestClient(create_app(api_settings(directory))) as other:
        clock = demo_seed.Clock(datetime(2026, 11, 6, tzinfo=timezone.utc))
        before = clock.at
        with demo_seed.fixed(clock):
            assert other.get("/healthz").status_code == 200
        assert clock.at == before, "another store's work must not consume a demo clock call"
        assert run_row(api, rid)["status"] == "queued"
        get(api, "config")
        assert run_row(api, rid)["status"] == "ok"
