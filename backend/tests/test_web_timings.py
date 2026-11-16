"""People's browser timings (LCP, INP) reach the flight recorder as histogram buckets only, and the metrics read gives
p99 per route and people's routes on their own (P06, docs/observability.md "People's browser timings")."""

import json
import time

import pytest
import yaml
from fastapi.testclient import TestClient

from backend import flight
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.store import H, Store

PEOPLE = {
    "ana-test": Identity("human:ana", "owner", "ana@acme.example"),
    "ben-test": Identity("human:ben", "human", "ben@acme.example"),
    "qa-test": Identity("bot:qa", "bot", agent="hermes"),
    "runner-test": Identity("runner:r1", "runner", runner_id="r1"),
}


@pytest.fixture
def hub(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "hub-access.yaml").write_text(yaml.safe_dump({"owner": "ana@acme.example"}))
    app = create_app(
        Settings(db_path=tmp_path / "hub.db", registry_dir=registry, flight_recorder=True, test_identities=PEOPLE)
    )
    with TestClient(app) as client:
        with app.state.store.transaction() as c:
            H.sync_registry(
                c,
                {"qa": {"name": "QA", "status": "active"}},
                {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben")]},
            )
            c.execute(
                "INSERT INTO agents(bot,harness,token_hash,created_at,created_by) VALUES('qa','hermes','x',?,'human:ana')",
                (H.now(),),
            )
        yield app, client


def post(client, token, timings):
    return client.post("/api/v2/web-timings", json={"timings": timings}, headers={"Authorization": "Bearer " + token})


def stored(app):
    rec = app.state.flight
    with app.state.store.transaction() as c:
        flight.write(c, rec.take(int(time.time() // 60 * 60)))
    with app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM flight_web ORDER BY metric, route")]


def test_a_persons_reading_is_kept_as_its_bucket_only_and_reads_back_under_web(hub):
    app, client = hub
    response = post(client, "ben-test", [{"metric": "lcp", "route": "/tasks", "ms": 1234.5, "url": "/#/tasks/t1"}])
    assert response.status_code == 202 and response.json() == {"kept": 1, "dropped": 0}
    assert post(client, "ana-test", [{"metric": "inp", "route": "/bot/:bot", "ms": 88}]).status_code == 202
    rows = stored(app)
    # Only the minute, its span, the metric, the screen template and the buckets: no person, address or device.
    assert set(rows[0]) == {"created_at", "span", "metric", "route", "n", "hist"}
    assert [(r["metric"], r["route"], r["n"]) for r in rows] == [("inp", "/bot/:bot", 1), ("lcp", "/tasks", 1)]
    lcp = json.loads(rows[1]["hist"])
    assert list(lcp.values()) == [1] and flight.EDGES[int(next(iter(lcp)))] >= 1234.5
    assert "ben" not in json.dumps(rows) and "t1" not in json.dumps(rows)
    web = client.get("/api/v2/system/metrics?section=web", headers={"Authorization": "Bearer ana-test"}).json()["web"]
    assert web["dropped"] == 0
    assert [(r["metric"], r["route"], r["n"]) for r in web["timings"]] == [
        ("lcp", "/tasks", 1),
        ("inp", "/bot/:bot", 1),
    ]
    assert web["timings"][0]["p75"] >= 1234.5 and web["timings"][0]["p99"] == web["timings"][0]["max_ms"]


@pytest.mark.parametrize(
    "token, timings, status",
    [
        ("qa-test", [{"metric": "lcp", "route": "/tasks", "ms": 10}], 403),
        # An unenrolled runner is refused at sign-in; an enrolled one would be at the people-only check.
        ("runner-test", [{"metric": "lcp", "route": "/tasks", "ms": 10}], 401),
        ("ben-test", [{"metric": "cls", "route": "/tasks", "ms": 10}], 422),
        ("ben-test", [{"metric": "lcp", "route": "/tasks/t1", "ms": 10}], 422),
        ("ben-test", [{"metric": "lcp", "route": "/unknown", "ms": 10}], 422),
        ("ben-test", [{"metric": "lcp", "route": "/tasks", "ms": 60_001}], 422),
        ("ben-test", [{"metric": "lcp", "route": "/tasks", "ms": -1}], 422),
        ("ben-test", [{"metric": "lcp", "route": "/tasks", "ms": True}], 422),
        ("ben-test", [{"metric": "lcp", "route": "/tasks", "ms": "10"}], 422),
        # One bad reading refuses the good one beside it.
        ("ben-test", [{"metric": "lcp", "route": "/tasks", "ms": 10}, {"metric": "inp", "route": "/x", "ms": 1}], 422),
        ("ben-test", [], 422),
        ("ben-test", [{"metric": "lcp", "route": "/tasks", "ms": 10}] * (flight.WEB_BATCH + 1), 422),
    ],
)
def test_a_bot_runner_or_bad_reading_is_refused_and_kept_nowhere(hub, token, timings, status):
    app, client = hub
    assert post(client, token, timings).status_code == status
    assert stored(app) == []


def test_a_body_that_is_not_a_list_of_readings_is_refused(hub):
    app, client = hub
    auth = {"Authorization": "Bearer ben-test"}
    assert client.post("/api/v2/web-timings", content=b"not json", headers=auth).status_code == 422
    assert client.post("/api/v2/web-timings", json=[1], headers=auth).status_code == 422
    assert stored(app) == []


def test_over_a_persons_minute_allowance_readings_are_dropped_and_counted(hub):
    app, client = hub
    batch = [{"metric": "inp", "route": "/chat", "ms": 40}] * flight.WEB_BATCH
    for _ in range(flight.WEB_PER_MINUTE // flight.WEB_BATCH):
        assert post(client, "ben-test", batch).json() == {"kept": flight.WEB_BATCH, "dropped": 0}
    assert post(client, "ben-test", batch[:1]).json() == {"kept": 0, "dropped": 1}
    assert post(client, "ana-test", batch[:1]).json() == {"kept": 1, "dropped": 0}  # another person's own allowance
    rows = {r["metric"]: r for r in stored(app)}
    assert rows["inp"]["n"] == flight.WEB_PER_MINUTE + 1 and rows["dropped"]["n"] == 1
    web = client.get("/api/v2/system/metrics?section=web", headers={"Authorization": "Bearer ana-test"}).json()["web"]
    assert web["dropped"] == 1 and [r["metric"] for r in web["timings"]] == ["inp"]


def test_the_allowance_starts_again_each_minute():
    now = [600.0]
    allowance = flight.WebAllowance(per_minute=3, clock=lambda: now[0])
    assert (allowance.take("human:ben", 2), allowance.take("human:ben", 2), allowance.take("human:ben", 1)) == (2, 1, 0)
    now[0] += 60
    assert allowance.take("human:ben", 5) == 3


def _hist(*values):
    hist = {}
    for ms in values:
        flight._add(hist, ms)
    return hist


def test_people_only_gives_their_own_p99_and_ranks_by_their_requests(tmp_path):
    store = Store(Settings(db_path=tmp_path / "hub.db", registry_dir=tmp_path), flight.Recorder())
    store.initialize(seed_market=False)
    now = 1_800_000_000
    people_tasks = _hist(*([10] * 98 + [300, 900]))  # 100 calls: the 99th is the 300 ms one
    rows = [
        ("GET /api/v2/tasks/{tid}", "human", 100, people_tasks),
        ("GET /api/v2/tasks/{tid}", "bot", 1000, _hist(*([5000] * 1000))),
        ("GET /api/v2/needs-you", "human", 150, _hist(*([20] * 150))),
        ("POST /api/v2/attempts/{aid}/inputs", "runner", 9000, _hist(*([50] * 9000))),
    ]
    with store.transaction() as c:
        for route, caller, n, hist in rows:
            total = sum(flight.EDGES[i] * k for i, k in hist.items())
            c.execute(
                "INSERT INTO flight_requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (now - 600, 60, route, caller, n, 0, 0, total, 0, 0, 0, json.dumps(hist)),
            )
    with store.read() as c:
        people = flight.view(c, minutes=60, sections=("requests",), now=now, caller="human")["requests"]
        everyone = flight.view(c, minutes=60, sections=("requests",), now=now)["requests"]
    assert people["caller"] == "human" and people["n"] == 250
    assert [r["route"] for r in people["routes"]] == ["GET /api/v2/needs-you", "GET /api/v2/tasks/{tid}"]
    tasks = people["routes"][1]
    assert tasks["n"] == 100 and tasks["callers"] == {"human": 100}
    assert (
        tasks["p99"]
        == flight.quantile(people_tasks, 0.99)
        == round(flight.EDGES[flight.bisect.bisect_left(flight.EDGES, 300)], 2)
    )
    assert tasks["p50"] <= tasks["p95"] < tasks["p99"] < 400
    # Without the filter every caller counts, ranked by total time, and the bots' slow calls set the p99.
    assert "caller" not in everyone and everyone["routes"][0]["route"] == "GET /api/v2/tasks/{tid}"
    assert everyone["routes"][0]["n"] == 1100 and everyone["routes"][0]["p99"] >= 5000 and everyone["p99"] > 0


def test_the_metrics_read_refuses_an_unknown_caller(hub):
    _, client = hub
    auth = {"Authorization": "Bearer ana-test"}
    assert client.get("/api/v2/system/metrics?caller=robot", headers=auth).status_code == 422
    answer = client.get("/api/v2/system/metrics?section=requests&caller=human", headers=auth)
    assert answer.status_code == 200 and answer.json()["requests"]["caller"] == "human"


def test_browser_minutes_become_hours_and_go_after_14_days(tmp_path):
    store = Store(Settings(db_path=tmp_path / "hub.db", registry_dir=tmp_path), flight.Recorder())
    store.initialize(seed_market=False)
    now = 1_800_000_000
    hour = (now - 3 * 86400) // 3600 * 3600
    with store.transaction() as c:
        for i, ms in enumerate((100, 2000)):
            c.execute(
                "INSERT INTO flight_web VALUES(?,?,?,?,?,?)",
                (hour + 60 * i, 60, "lcp", "/tasks", 1, json.dumps(_hist(ms))),
            )
        c.execute("INSERT INTO flight_web VALUES(?,?,?,?,?,?)", (now - 15 * 86400, 3600, "inp", "/chat", 4, "{}"))
        c.execute(
            "INSERT INTO flight_web VALUES(?,?,?,?,?,?)", (now - 60, 60, "inp", "/chat", 2, json.dumps(_hist(30, 30)))
        )
    flight.sweep(store, now)
    with store.read() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM flight_web ORDER BY created_at")]
        web = flight.view(c, minutes=14 * 1440, sections=("web",), now=now)["web"]
    assert [(r["created_at"], r["span"], r["metric"], r["n"]) for r in rows] == [
        (hour, 3600, "lcp", 2),
        (now - 60, 60, "inp", 2),
    ]
    assert sum(json.loads(rows[0]["hist"]).values()) == 2
    lcp = next(r for r in web["timings"] if r["metric"] == "lcp")
    assert lcp["n"] == 2 and lcp["p50"] >= 100 and lcp["max_ms"] >= 2000


def test_only_a_person_passes_the_senders_check():
    assert flight.caller_kind(PEOPLE["ben-test"]) == flight.caller_kind(PEOPLE["ana-test"]) == "human"
    assert {flight.caller_kind(PEOPLE[t]) for t in ("qa-test", "runner-test")} == {"bot", "runner"}


def test_the_browser_and_the_server_name_the_same_screens():
    """A screen the page names but the server does not know would refuse the whole batch, silently losing it."""
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "ui" / "observability.ts").read_text()
    listed = re.search(r"const SCREENS = new Set<string \| undefined>\(\((.*?)\)\.split\(' '\)\);", source, re.S)
    assert listed, "ui/observability.ts SCREENS moved: update this test"
    screens = "".join(re.findall(r"'([^']*)'", listed.group(1))).split(" ")
    assert sorted(screens) == sorted(flight.WEB_SCREENS)
    assert {"/home", "/task/:task", "/thread/:thread", "/threads", "/bot/:bot", "/settings/:sub"} <= flight.WEB_ROUTES


def test_task_view_and_home_are_kept_and_an_id_is_not(hub):
    app, client = hub
    good = [{"metric": "lcp", "route": "/task/:task", "ms": 900}, {"metric": "inp", "route": "/home", "ms": 60}]
    assert post(client, "ben-test", good).status_code == 202
    assert post(client, "ben-test", [{"metric": "lcp", "route": "/task/abc-123", "ms": 900}]).status_code == 422
    assert [(r["metric"], r["route"]) for r in stored(app)] == [("inp", "/home"), ("lcp", "/task/:task")]


def test_people_only_lists_the_browsers_timing_sends_apart(tmp_path):
    store = Store(Settings(db_path=tmp_path / "hub.db", registry_dir=tmp_path), flight.Recorder())
    store.initialize(seed_market=False)
    now = 1_800_000_000
    with store.transaction() as c:
        for route, n in ((flight.WEB_TIMINGS_ROUTE, 500), ("GET /api/v2/tasks/{tid}", 100)):
            c.execute(
                "INSERT INTO flight_requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (now - 600, 60, route, "human", n, 0, 0, n, 1, 1, 1, json.dumps(_hist(*([1] * n)))),
            )
    with store.read() as c:
        people = flight.view(c, minutes=60, sections=("requests",), now=now, caller="human")["requests"]
        everyone = flight.view(c, minutes=60, sections=("requests",), now=now)["requests"]
    assert [r["route"] for r in people["routes"]] == ["GET /api/v2/tasks/{tid}"]
    assert people["web_timings"]["n"] == 500 and people["n"] == 600
    assert "web_timings" not in everyone and len(everyone["routes"]) == 2
