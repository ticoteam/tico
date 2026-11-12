"""The load gate (bench/load_gate) keeps working as Tico changes: a small synthetic company, a short mixed load of
people, streams and runners against an in-process server over HTTP, and a report with every field (AC-6). A sender
that cannot keep its schedule makes its level "not valid" (AC-3)."""

import socket
import threading
import time

import uvicorn
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.live import block_outside_network
from bench.load_gate import company, gate, report as R

ROUTE_FIELDS = {
    "requests",
    "errors",
    "rejected_4xx",
    "bytes",
    "sender_p50",
    "sender_p95",
    "sender_p99",
    "server_requests",
    "server_p50",
    "server_p95",
}
BUDGET = {
    "lines": [
        {"id": "every route p95", "metric": "server_p95_ms", "max": 10**9},
        {"id": "no errors at all", "metric": "error_share", "max": -1},
    ]
}


def test_a_smoke_run_reports_every_field_and_a_starved_sender_is_not_valid(tmp_path, monkeypatch):
    block_outside_network(monkeypatch, "the load gate's smoke run reaches only its own server")
    settings = company.settings(tmp_path, scheduler=False, release_commit="smoke")
    app = create_app(settings)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    target = f"http://127.0.0.1:{sock.getsockname()[1]}"
    with TestClient(app) as client:
        manifest = company.build(client, 7, company.SMOKE)
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 30
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        try:
            common = {"ramp": 1, "hold": 6, "workers": 2, "seed": 7, "folder": tmp_path, "align": False, "grace": 2}
            runs = gate.run_levels(target, manifest, [4], **common)
            starved = gate.run_levels(target, manifest, [4], starve_ms=300, **common)
        finally:
            server.should_exit = True
            thread.join(timeout=30)
            sock.close()
    # The app has stopped: its flight recorder wrote its last minute, which the report reads from the database.
    meta = {"commit": "smoke", "dirty": False, "server_host": gate.host(), "sender_host": gate.host(), "started": "-"}
    out = gate.assemble(runs + starved, gate.db_flight(settings.db_path), manifest, meta, BUDGET)

    head = out["run"]
    assert head["seed"] == 7 and head["server_commit"] == "smoke" and head["pinned"] is True
    assert head["rows"]["automations"] >= company.SMOKE.routines and head["rows"]["attempt_events"] >= 2000
    assert set(head["plan"]["person_mix"]) and head["limits"]["lateness_p95_ms"] == R.LATENESS_P95_MS

    level, slow = out["levels"]
    assert level["level"] == 4 and level["requests"] > 30
    for route, row in level["routes"].items():
        assert set(row) == ROUTE_FIELDS, route
    # People navigated, wrote and held their streams; runners claimed, ran and finished turns.
    assert any(route.startswith("GET ") for route in level["routes"])  # a person opened a page
    for route in (
        "POST /api/v2/jobs/claim",
        "POST /api/v2/attempts/{aid}/events",
        "POST /api/v2/attempts/{aid}/inputs",
    ):
        assert level["routes"][route]["requests"] >= 1, route
        assert level["routes"][route]["server_p95"] is not None, route  # the server's own time beside the sender's
    assert level["sender"]["turns_completed"] >= 1 and level["sender"]["turns_running_mean"] > 0
    assert {"deliveries", "p50_ms", "p95_ms", "max_ms"} <= set(level["live_delivery"])
    assert {"cpu_avg", "cpu_max", "rss", "lock_wait_max_ms", "lock_hold_max_ms", "lock_hold_p95_ms"} <= set(
        level["server"]
    )
    assert level["server"]["requests"] > 0 and level["server"]["cpu_max"] is not None
    assert [b["result"] for b in level["budget"]] == ["met", "missed"]

    # The starved sender fell behind its own schedule: the level says so and is not valid, whatever the server did.
    assert slow["valid"] is False
    assert any("lateness" in why for why in slow["not_valid_because"]), slow["not_valid_because"]
    assert slow["sender"]["lateness_p95_ms"] > level["sender"]["lateness_p95_ms"]

    text = R.markdown(out)
    assert "## Level 4 turns at once" in text and "NOT VALID" in text and "POST /api/v2/jobs/claim" in text


def test_compare_names_the_routes_whose_p95_moved():
    """Measures 3 and 4: two reports' routes side by side, within 15% or not."""

    def report(ms):
        row = {"requests": 2000, "server_p95": ms, "sender_p95": ms + 1}
        return {"levels": [{"level": 30, "routes": {"GET /api/v2/needs-you": row, "GET /api/v2/tasks": dict(row)}}]}

    same = R.compare(report(20.0), report(22.0))
    assert same and all(r["within"] for r in same)
    slower = report(20.0)
    slower["levels"][0]["routes"]["GET /api/v2/tasks"]["server_p95"] = 70.0
    moved = [r for r in R.compare(report(20.0), slower) if not r["within"]]
    assert [(r["route"], r["side"], r["delta_ms"]) for r in moved] == [("GET /api/v2/tasks", "server_p95", 50.0)]
