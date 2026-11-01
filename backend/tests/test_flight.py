"""The flight recorder (backend/flight.py): what it aggregates is right, only owners and admins read it, it forgets on
schedule, it catches a stalled event loop in the act, the start record holds no secret, and a caller that hangs up is no
server error."""
import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager

import httpx
import pytest
import yaml
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend import code_hash, diagnostics, flight, flight_file, releases
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.store import H, Store

MINUTE = 1_700_000_040


@pytest.fixture
def store(tmp_path):
    s = Store(Settings(db_path=tmp_path / "hub.db", registry_dir=tmp_path), flight.Recorder())
    s.initialize(seed_market=False)
    return s


def test_requests_and_sql_are_aggregated_per_minute_route_and_caller(store):
    rec = store.recorder
    for ms in [10] * 18 + [900, 1500]:
        rec.request("GET /api/v2/tasks", "bot", 200, ms, 100, "bot:finance", [30, 80, 12])
    rec.request("GET /api/v2/tasks", "human", 503, 5, 10, "human:ana")
    with store.transaction() as c:
        c.execute("SELECT * FROM tasks WHERE id='x1'").fetchall()
        c.execute("SELECT * FROM tasks WHERE id='y22'").fetchone()
        # Rows fetched after the first count toward the statement's one sample, however they are read.
        many = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<?) SELECT x FROM n"
        assert sum(x for (x,) in c.execute(many, (200_000,))) == 200_000 * 200_001 // 2
        c.execute(many, (3,)).fetchmany(2)
    rec.drain(MINUTE)
    taken = rec.take(MINUTE)
    with store.transaction() as c:
        flight.write(c, taken)
        rows = {r["caller"]: dict(r) for r in c.execute("SELECT * FROM flight_requests")}
        slow = c.execute("SELECT route,caller,actor,status,sql_ms,wait_ms,queries FROM flight_slow").fetchall()
        shapes = {r[0]: r[1] for r in c.execute("SELECT fingerprint,n FROM flight_sql")}
        process = c.execute("SELECT requests,txns FROM flight_process").fetchone()
        rows_ms = c.execute("SELECT total_ms FROM flight_sql WHERE fingerprint LIKE 'WITH RECURSIVE%'").fetchone()[0]
    bot = rows["bot"]
    assert (bot["n"], bot["errors"], bot["bytes"], bot["max_ms"]) == (20, 0, 2000, 1500)
    assert 10 <= bot["p50"] <= 12.5 and 900 <= bot["p95"] <= 1500
    assert rows["human"]["errors"] == 1
    assert [tuple(r) for r in slow] == [("GET /api/v2/tasks", "bot", "bot:finance", 200, 30, 80, 12)]
    # Literals are folded: two lookups are one query shape, and no value is kept.
    assert shapes["SELECT * FROM tasks WHERE id='?'"] == 2 and not any("x1" in fp for fp in shapes)
    assert shapes[flight.fingerprint(many)] == 2 and rows_ms > 5
    assert process["requests"] == 21 and process["txns"] >= 1


def test_a_stall_behind_the_write_lock_reaches_the_file_and_the_summary(store, capsys):
    """The write lock is held elsewhere, a writer waits for it, the event loop stops waking: the stall is in the file
    (class `lock`, with the waiting thread's stack) although the recorder's own table write could not get the lock,
    and the summary reads it from the file alone."""
    folder = store.settings.db_path.parent / "flight"
    sampler = flight.Flight(store.recorder, store, store.settings, folder=folder, stall_s=0.05, db_wait=0.05)
    sampler.file.start()
    holder = sqlite3.connect(str(store.settings.db_path), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    def write():
        with store.transaction():
            pass
    waiter = threading.Thread(target=write, name="tico-writer", daemon=True)
    try:
        waiter.start()
        deadline = time.monotonic() + 5
        while not store.recorder.waiting and time.monotonic() < deadline:
            time.sleep(0.01)
        sampler.step()
        time.sleep(0.1)
        store.recorder.beat = time.monotonic() - 1
        stall = sampler.step()
        assert stall["class"] == "lock" and stall["threads"]["busy"] and sampler.db_failed == 1
        store.recorder.beat = time.monotonic()
        sampler.step()
        sampler.file.close()
    finally:
        holder.execute("ROLLBACK")
        holder.close()
        waiter.join(timeout=5)
    out = flight_file.summary(folder, 600)
    found = out["stalls"][0]
    assert found["class"] == "lock" and not found["ongoing"] and found["usage"]["lock_ms"] > 0
    assert any("tico-writer" == t["thread"] and any("begin" in f for f in t["stack"]) for t in found["threads"])
    assert flight.main(["summary", "--since", "10m", "--dir", str(folder)]) == 0
    assert "class=lock" in capsys.readouterr().out


def test_a_full_buffer_counts_what_it_drops(monkeypatch):
    monkeypatch.setattr(flight, "BUFFER", 3)
    rec = flight.Recorder()
    for _ in range(5):
        rec.query("SELECT 1", 0.1)
    rec.drain(MINUTE)
    assert rec.take(MINUTE)["process"][-1] == 2 and rec.take(MINUTE)["process"][-1] == 0


def test_slow_request_upgrade_keeps_history_and_adds_nullable_attribution(tmp_path):
    path = tmp_path / 'synthetic.sqlite'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE flight_slow(id INTEGER PRIMARY KEY,created_at INTEGER,route TEXT,caller TEXT,actor TEXT,ms REAL,bytes INTEGER,status INTEGER)')
        c.execute("INSERT INTO flight_slow(created_at,route,ms) VALUES(?,?,?)", (MINUTE, 'GET /api/v2/tasks/{tid}', 1500))
    store = Store(Settings(db_path=path, registry_dir=tmp_path), flight.Recorder())
    store.initialize(seed_market=False)
    store.initialize(seed_market=False)
    with store.read() as c:
        row = dict(c.execute('SELECT * FROM flight_slow').fetchone())
    assert row['ms'] == 1500 and row['sql_ms'] is None and row['wait_ms'] is None and row['queries'] is None


def test_table_sizes_are_reused_for_a_day_even_after_a_restart(store, monkeypatch):
    with store.transaction() as c:
        c.execute("CREATE TABLE size_fixture(created_at INTEGER, payload BLOB)")
        # Large enough to appear in the top 40 alongside the initialized schema's tables/indexes.
        c.execute("INSERT INTO size_fixture VALUES(?, zeroblob(?))", (MINUTE, 128 * 1024))
    monkeypatch.setattr(flight, "BIG", {**flight.BIG, "size_fixture": "created_at"})
    sizing, dbstat = [], []                      # the size pass's statements, and any that walk pages with dbstat
    read = store.read

    @contextmanager
    def traced_read():
        with read() as c:
            def trace(sql):
                if "dbstat" in sql:
                    dbstat.append(sql)
                if sql.startswith(("PRAGMA table_info", "PRAGMA index_info")):
                    sizing.append((sql, c.in_transaction))
            c.set_trace_callback(trace)
            try:
                yield c
            finally:
                c.set_trace_callback(None)
    monkeypatch.setattr(store, "read", traced_read)

    sampler = flight.Flight(store.recorder, store, store.settings)
    sampler.size_db(MINUTE)
    with store.read() as c:
        first = flight._db(c, MINUTE)
    assert first["objects"]["size_fixture"] > 0
    one_pass = len(sizing)                       # one short read per table and its indexes, so many statements make one pass
    assert one_pass > 1
    with store.transaction() as c:
        c.execute("INSERT INTO size_fixture VALUES(?, zeroblob(?))", (MINUTE + 1, 256 * 1024))
    sampler.size_db(MINUTE + 3600)
    # A new recorder must use the persisted sample rather than repeat the scan on each restart.
    sampler = flight.Flight(store.recorder, store, store.settings)
    sampler.size_db(MINUTE + 7200)
    with store.read() as c:
        reused = flight._db(c, MINUTE + 7200)
    assert len(sizing) == one_pass
    assert reused["objects"] == first["objects"] and reused["objects_at"] == MINUTE
    assert reused["at"] == (MINUTE + 7200) // 3600 * 3600
    assert reused["rows"]["size_fixture"] == 2 and reused["oldest"]["size_fixture"] == MINUTE

    sampler.size_db(MINUTE + 86400)
    with store.read() as c:
        refreshed = flight._db(c, MINUTE + 86400)
        assert [r[0] for r in c.execute("SELECT name FROM pragma_table_info('flight_db')")] == [
            "ts", "bytes", "wal_bytes", "free_bytes", "detail"]
    assert len(sizing) == 2 * one_pass
    assert refreshed["objects_at"] == MINUTE + 86400
    assert refreshed["objects"]["size_fixture"] > first["objects"]["size_fixture"]
    assert not dbstat and all(not in_transaction for _, in_transaction in sizing)


def test_owners_admins_and_bots_read_the_metrics(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "hub-access.yaml").write_text(yaml.safe_dump({"owner": "ana@acme.example",
                                                              "bot_admins": ["ben@acme.example"]}))
    app = create_app(Settings(db_path=tmp_path / "hub.db", registry_dir=registry, flight_recorder=True, test_identities={
        "ana-test": Identity("human:ana", "owner", "ana@acme.example"),
        "ben-test": Identity("human:ben", "human", "ben@acme.example"),
        "cara-test": Identity("human:cara", "human", "cara@acme.example"),
        "qa-test": Identity("bot:qa", "bot", agent="hermes"), "runner-test": Identity("runner:r1", "runner", runner_id="r1")}))
    with TestClient(app) as client:
        with app.state.store.transaction() as c:
            H.sync_registry(c, {"qa": {"name": "QA", "status": "active"}},
                            {"people": [{"id": p, "email": p + "@acme.example"} for p in ("ana", "ben", "cara")]})
            c.execute("INSERT INTO agents(bot,harness,token_hash,created_at,created_by) VALUES('qa','hermes','x',?,'human:ana')",
                      (H.now(),))

        def get(token, path="/api/v2/system/metrics"):
            return client.get(path, headers={"Authorization": "Bearer " + token})
        response = get("ana-test", "/api/v2/health")
        assert response.status_code == 200
        assert 'sql;dur=' in response.headers['server-timing'] and 'wait;dur=' in response.headers['server-timing']
        rec = app.state.flight
        rec.drain()
        taken = rec.take(int(time.time() // 60 * 60) - 120)
        with app.state.store.transaction() as c:
            flight.write(c, taken)
        # A request's SQL time reaches its route's row through the threads it ran on (the file's per-route copy).
        health = [r for r in taken["file"][1]["routes"] if r["route"] == "GET /api/v2/health"]
        assert health and health[0]["sql_ms"] > 0 and health[0]["bytes_max"] > 0
        # The start event comes from the recorder's thread; on a busy machine it lands after the first requests.
        deadline = time.time() + 10
        while time.time() < deadline:
            with app.state.store.transaction() as c:
                if flight.last_start(c):
                    break
            time.sleep(0.05)
        assert get("cara-test").status_code == 403 and get("runner-test").status_code in (401, 403)
        with app.state.store.transaction() as c:
            c.execute("INSERT INTO flight_slow(created_at,route,caller,actor,ms,bytes,status) VALUES(?,?,?,?,?,?,?)",
                      (int(time.time()), "GET /api/v2/tasks", "human", "human:cara", 1500, 10, 200))
        slow = get("qa-test", "/api/v2/system/metrics?section=slow")
        assert slow.status_code == 200 and slow.json()["slow"][0]["ms"] == 1500
        assert "actor" not in slow.json()["slow"][0]
        assert get("ben-test").status_code == 200
        body = get("ana-test").json()
        routes = {r["route"]: r for r in body["requests"]["routes"]}
        assert routes["GET /api/v2/health"]["callers"].get("human", 0) >= 1
        assert body["sql"]["queries"] and body["events"]["start"]["version"]
        assert set(get("ana-test", "/api/v2/system/metrics?section=slow").json()) == {"since", "now", "minutes", "slow"}


def test_a_client_that_hangs_up_during_the_body_read_is_a_499_not_a_5xx(tmp_path, caplog):
    """A caller that disconnects while request_guard reads its body (v0.3.55's seven "5xx", Dev 170) ends as 499 with no
    traceback, and the 5xx count stays 0; a normal request is still a 200 and a handler that raises still a 500."""
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "hub-access.yaml").write_text(yaml.safe_dump({"owner_id": "ana@acme.example"}))
    app = create_app(Settings(db_path=tmp_path / "hub.db", registry_dir=registry, flight_recorder=True, test_identities={
        "ana-test": Identity("human:ana", "owner", "ana@acme.example")}))

    def boom():
        raise RuntimeError("a real handler failure")
    app.router.routes.insert(0, APIRoute("/api/v2/test-boom", boom, methods=["GET"]))
    statuses = []
    end = app.state.timing.end

    def recorded(route, auth_ms, total_ms, caller="anonymous", status=200, *rest):
        statuses.append((route, status))
        return end(route, auth_ms, total_ms, caller, status, *rest)
    app.state.timing.end = recorded

    async def hang_up():
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
                 "path": "/api/v2/tasks", "raw_path": b"/api/v2/tasks", "root_path": "", "query_string": b"",
                 "headers": [(b"host", b"testserver"), (b"authorization", b"Bearer ana-test"),
                             (b"content-type", b"application/json"), (b"content-length", b"100")],
                 "client": ("testclient", 50000), "server": ("testserver", 80), "state": {}, "extensions": {}}
        parts = iter([{"type": "http.request", "body": b'{"title": "half', "more_body": True}])
        sent = []

        async def receive():
            return next(parts, {"type": "http.disconnect"})

        async def send(message):
            sent.append(message)
        await app(scope, receive, send)
        return sent

    with TestClient(app, raise_server_exceptions=False) as client:
        with app.state.store.transaction() as c:
            H.sync_registry(c, {}, {"people": [{"id": "ana", "email": "ana@acme.example"}]})
        caplog.set_level(logging.DEBUG)
        rec = app.state.flight
        rec.drain()
        rec.take(0)
        failures = diagnostics.RING.total
        caplog.clear()
        sent = client.portal.call(hang_up)
        assert statuses == [("POST unmatched", 499)]
        assert [m["status"] for m in sent if m["type"] == "http.response.start"] == [499]
        assert diagnostics.RING.total == failures
        assert not [r for r in caplog.records if r.exc_info]
        assert [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "tico.request"] == [
            (logging.INFO, "POST request: the client disconnected before its body was read")]
        rec.drain()
        routes = {r["route"]: r for r in rec.take(0)["file"][1]["routes"]}
        assert routes["POST unmatched"]["n"] == 1 and routes["POST unmatched"]["errors"] == 0

        # Unchanged: a normal request is a 200 and a handler that raises is a 500, counted as a server error.
        statuses.clear()
        assert client.get("/api/v2/health", headers={"Authorization": "Bearer ana-test"}).status_code == 200
        assert client.get("/api/v2/test-boom", headers={"Authorization": "Bearer ana-test"}).status_code == 500
        assert statuses == [("GET /api/v2/health", 200), ("GET /api/v2/test-boom", 500)]
        rec.drain()
        routes = {r["route"]: r for r in rec.take(0)["file"][1]["routes"]}
        assert routes["GET /api/v2/health"]["errors"] == 0 and routes["GET /api/v2/test-boom"]["errors"] == 1

def test_minutes_become_hours_and_old_rows_go(store):
    now = 1_800_000_000
    old = now - 3 * 86400
    hour = old // 3600 * 3600
    with store.transaction() as c:
        for i, ms in enumerate((10, 20, 4000)):
            hist = {}
            flight._add(hist, ms)
            c.execute("INSERT INTO flight_requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (hour + 60 * i, 60, "GET /api/v2/tasks", "bot", 1, 0, 5, ms, ms, ms, ms, json.dumps(hist)))
        c.execute("INSERT INTO flight_requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (now - 15 * 86400, 3600, "GET /x", "bot", 1, 0, 0, 1, 1, 1, 1, "{}"))
        c.execute("INSERT INTO flight_slow(created_at,route) VALUES(?,?)", (now - 15 * 86400, "GET /x"))
        c.execute("INSERT INTO flight_sql VALUES(?,?,?,?,?,?,?)", (now - 91 * 86400, "SELECT N", 1, 1, 1, 1, "{}"))
        c.execute("INSERT INTO flight_process(created_at) VALUES(?)", (now - 60,))
    flight.sweep(store, now)
    with store.read() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM flight_requests")]
        assert c.execute("SELECT count(*) FROM flight_slow").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM flight_sql").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM flight_process").fetchone()[0] == 1
    assert len(rows) == 1
    row = rows[0]
    assert (row["created_at"], row["span"], row["n"], row["bytes"], row["max_ms"]) == (hour, 3600, 3, 15, 4000)
    assert row["p95"] >= 4000 and row["p50"] < 25


def test_the_start_record_keeps_no_secret_and_flags_an_image_not_built_from_its_release(tmp_path, monkeypatch):
    secrets = {"session_secret": "s3ssion-VALUE", "oidc_client_secret": "oidc-VALUE", "local_owner_token": "tok-VALUE",
               "github_webhook_secret": "hook-VALUE", "sentry_dsn": "https://abc@o1.ingest.sentry.io/1",
               "owner_email": "ana@acme.example", "access_audience": "aud-VALUE"}
    settings = Settings(db_path=tmp_path / "hub.db", registry_dir=tmp_path, release_commit="a" * 40,
                        release_repo="ticoteam/tico", **secrets)
    monkeypatch.setenv("TICO_VERSION", "v0.3.22")
    monkeypatch.setenv("TICO_UPDATE_CHECK", "on")
    monkeypatch.setattr(releases, "TRANSPORT", httpx.MockTransport(lambda request: httpx.Response(200, text="b" * 40)))
    event = flight.start_event(settings)
    text = json.dumps(event)
    assert not any(value in text for value in secrets.values())
    assert not any(name in event["config_fields"] for name in secrets)
    assert event["version"] == "0.3.22" and event["provenance"] == "mismatch"
    monkeypatch.setattr(releases, "TRANSPORT", httpx.MockTransport(lambda request: httpx.Response(200, text="a" * 40)))
    assert flight.provenance(settings, "0.3.22") == "match"
    # Files copied over a release image after it was built: `modified`, whatever the commit says. Compiled files are not code.
    image = tmp_path / "image"
    (image / "backend" / "__pycache__").mkdir(parents=True)
    (image / "backend" / "app.py").write_text("release = 1\n")
    (image / "release-manifest.json").write_text(json.dumps({"commit": "a" * 40, "code": code_hash.digest(image)}))
    (image / "backend" / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"compiled")
    assert flight.provenance(settings, "0.3.22", image) == "match"
    (image / "backend" / "app.py").write_text("release = 1  # patched\n")
    assert flight.provenance(settings, "0.3.22", image) == "modified"


def test_a_long_write_names_its_route_and_code_site_in_the_file_the_log_and_the_events(store, monkeypatch, caplog):
    """Dev 249: two writes held the lock ~100 s and nothing said whose they were. A hold over LONG_HOLD_MS is now
    recorded with the request's route (or none, for a background job) and the code that held it, also while it is
    still held."""
    monkeypatch.setattr(flight, "LONG_HOLD_MS", 30)
    rec = store.recorder
    folder = store.settings.db_path.parent / "flight"
    sampler = flight.Flight(rec, store, store.settings, folder=folder)
    sampler.file.start()
    route = type("Route", (), {"path": "/api/v2/runners/performance"})()
    cost, token = flight.request_started("POST", {"route": route})

    def slow_ingest():
        with store.transaction() as c:
            c.execute("SELECT 1").fetchone()
            time.sleep(0.05)
            return rec.open_holds()
    try:
        still = slow_ingest()
    finally:
        flight.request_finished(token)
    with store.transaction():                       # a short one is not logged
        pass

    def sweep_job():
        with store.transaction():
            time.sleep(0.05)
    sweep_job()
    assert [h["route"] for h in still] == ["POST /api/v2/runners/performance"] and still[0]["open"]
    assert "slow_ingest" in still[0]["site"]
    rec.drain(MINUTE)
    with caplog.at_level(logging.WARNING, logger="tico.flight"):
        sampler.flush(MINUTE)
    sampler.file.close()
    with store.read() as c:
        events = flight.view(c, minutes=10**6, sections=("events",), now=time.time() + 60)["events"]["recent"]
    holds = [e for e in events if e["kind"] == "long_hold"]
    by_site = {e["site"].split()[-1]: e for e in holds}
    assert set(by_site) == {"slow_ingest", "sweep_job"}
    assert by_site["slow_ingest"]["route"] == "POST /api/v2/runners/performance" and by_site["slow_ingest"]["hold_s"] >= 0
    assert by_site["sweep_job"]["route"] is None and "backend/tests/test_flight.py" in by_site["sweep_job"]["site"]
    assert any("Write lock held" in r.getMessage() and "runners/performance" in r.getMessage() for r in caplog.records)
    found = flight_file.summary(folder, 600)["long_holds"]
    assert {h["site"].split()[-1] for h in found} == {"slow_ingest", "sweep_job"}
    assert "Write lock held longest" in flight_file.render(flight_file.summary(folder, 600))


def test_a_minute_the_lock_kept_out_is_written_with_the_next_one_or_named_as_missing(store, monkeypatch):
    """Dev 249: the recorder skipped a minute's rows whenever it could not get the lock within DB_WAIT, so the metrics
    lost 06:44-06:46 of the incident. The rows now wait for the next write; past PENDING_MAX the oldest minutes go and a
    `recorder_gap` event names them."""
    rec = store.recorder
    sampler = flight.Flight(rec, store, store.settings, folder=store.settings.db_path.parent / "flight", db_wait=0.05)
    holder = sqlite3.connect(str(store.settings.db_path), isolation_level=None)

    def minutes():
        with store.read() as c:
            return [r[0] for r in c.execute("SELECT created_at FROM flight_process ORDER BY created_at")]

    def errors(minute):
        rec.request("POST /api/v2/runners/heartbeat", "runner", 503, 30_000, 89)
        rec.drain(minute)
        sampler.flush(minute)
    holder.execute("BEGIN IMMEDIATE")
    try:
        errors(MINUTE)
        errors(MINUTE + 60)
    finally:
        holder.execute("ROLLBACK")
    assert minutes() == [] and sampler.db_failed == 2
    errors(MINUTE + 120)
    assert minutes() == [MINUTE, MINUTE + 60, MINUTE + 120] and sampler.pending == []
    with store.read() as c:
        assert c.execute("SELECT sum(errors) FROM flight_requests").fetchone()[0] == 3     # every 503 counted
    monkeypatch.setattr(flight, "PENDING_MAX", 1)
    holder.execute("BEGIN IMMEDIATE")
    try:
        errors(MINUTE + 180)
        errors(MINUTE + 240)                    # only one waits: MINUTE + 180 goes
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    errors(MINUTE + 300)
    assert minutes()[-2:] == [MINUTE + 240, MINUTE + 300] and MINUTE + 180 not in minutes()
    with store.read() as c:
        gap = c.execute("SELECT detail FROM flight_events WHERE kind='recorder_gap'").fetchall()
    assert [json.loads(g[0]) for g in gap] == [{"minutes": [MINUTE + 180], "count": 1}]


def test_a_start_or_stall_the_lock_kept_out_keeps_the_time_it_happened(store, monkeypatch):
    """A start or stall event written minutes later, once the lock frees, is dated when it happened, not when it got
    in."""
    clock = [MINUTE + 5.0]
    monkeypatch.setattr(flight.time, "time", lambda: clock[0])
    monkeypatch.setattr(flight, "start_event", lambda settings, check=True: {"version": "test"})
    rec = store.recorder
    sampler = flight.Flight(rec, store, store.settings, folder=store.settings.db_path.parent / "flight", stall_s=0.05,
                            db_wait=0.05)
    holder = sqlite3.connect(str(store.settings.db_path), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        sampler.record_start()
        rec.beat = time.monotonic() - 1
        assert sampler.watch()["kind"] == "stall"
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert len(sampler.pending) == 2
    clock[0] += 180
    rec.drain(MINUTE + 180)
    sampler.flush(MINUTE + 180)
    with store.read() as c:
        when = dict(c.execute("SELECT kind,created_at FROM flight_events WHERE kind IN ('start','stall')").fetchall())
        assert when == {"start": MINUTE + 5, "stall": MINUTE + 5} and flight.last_start(c)["at"] == MINUTE + 5


def test_a_retry_after_a_failing_write_keeps_order_drops_only_the_bad_batch_and_names_it(store):
    """Busy-lock minutes replay oldest first, so an hour's cumulative SQL totals end at the newest; a batch that fails
    for another reason rolls the whole try back, goes alone and is named; append-only rows land exactly once."""
    rec = store.recorder
    sampler = flight.Flight(rec, store, store.settings, folder=store.settings.db_path.parent / "flight", db_wait=0.05)

    def minute(ts):
        rec.query("SELECT 1", 5.0)                              # the hour's SQL row grows: n = 1, 2, 3
        rec.request("POST /api/v2/runners/heartbeat", "runner", 200, 1500, 10)      # one flight_slow row a minute
        rec.drain(ts)
        return sampler.flush(ts)

    def tables(c):
        return ([r[0] for r in c.execute("SELECT created_at FROM flight_process ORDER BY created_at")],
                c.execute("SELECT count(*) FROM flight_slow").fetchone()[0],
                c.execute("SELECT max(n) FROM flight_sql WHERE fingerprint=?",
                          (flight.fingerprint("SELECT 1"),)).fetchone()[0])
    holder = sqlite3.connect(str(store.settings.db_path), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        minute(MINUTE)
        minute(MINUTE + 60)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert sampler.write_db(lambda c: c.execute("INSERT INTO no_such_table VALUES(1)"), MINUTE + 90) is False
    with store.read() as c:
        assert tables(c) == ([], 0, None)                          # the try rolled back: nothing half-written
    assert [m for m, _ in sampler.pending] == [MINUTE, MINUTE + 60] and sampler.lost == [MINUTE + 90]
    minute(MINUTE + 120)
    with store.read() as c:
        assert tables(c) == ([MINUTE, MINUTE + 60, MINUTE + 120], 3, 3)     # replayed in reverse, n would end at 1 or 2
        gap = [json.loads(r[0]) for r in c.execute("SELECT detail FROM flight_events WHERE kind='recorder_gap'")]
    assert gap == [{"minutes": [MINUTE + 90], "count": 1}] and sampler.pending == [] and sampler.lost == []


def test_a_real_request_holding_the_lock_is_named_while_held_and_after(tmp_path, monkeypatch, caplog):
    """A sync handler's write held across a recorder flush on another thread: the open hold names the route template
    (from the middleware's context, through the threadpool) and the handler's line before it ends, and the event the
    busy lock kept out lands once it does."""
    monkeypatch.setattr(flight, "LONG_HOLD_MS", 30)
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "hub-access.yaml").write_text(yaml.safe_dump({"owner_id": "ana@acme.example"}))
    app = create_app(Settings(db_path=tmp_path / "hub.db", registry_dir=registry, flight_recorder=True, test_identities={
        "ana-test": Identity("human:ana", "owner", "ana@acme.example")}))
    store, rec = app.state.store, app.state.flight
    store.initialize(seed_market=False)
    with store.transaction() as c:
        H.sync_registry(c, {}, {"people": [{"id": "ana", "email": "ana@acme.example"}]})
    entered, release = threading.Event(), threading.Event()

    def hold_the_lock(key: str):
        with store.transaction():
            entered.set()
            release.wait(10)
        return {"held": key}
    app.router.routes.insert(0, APIRoute("/api/v2/test-hold/{key}", hold_the_lock, methods=["POST"]))
    folder = tmp_path / "flight"
    sampler = flight.Flight(rec, store, store.settings, folder=folder, db_wait=0.05)
    sampler.file.start()
    client = TestClient(app)                        # no lifespan: the server's own recorder thread never flushes here
    answers = []
    request = threading.Thread(target=lambda: answers.append(client.post(
        "/api/v2/test-hold/x", headers={"Authorization": "Bearer ana-test"})), daemon=True)
    request.start()
    try:
        assert entered.wait(10), [(a.status_code, a.text[:200]) for a in answers]
        time.sleep(0.06)
        rec.drain(MINUTE)
        with caplog.at_level(logging.WARNING, logger="tico.flight"):
            sampler.flush(MINUTE)
        held = [r.getMessage() for r in caplog.records if "(still held)" in r.getMessage()]
        assert len(held) == 1 and "POST /api/v2/test-hold/{key}" in held[0]
        assert "test_flight.py" in held[0] and "hold_the_lock" in held[0]
        assert sampler.pending                      # the handler still has the lock: the event waits
    finally:
        release.set()
        request.join(10)
    assert answers[0].status_code == 200
    rec.drain(MINUTE + 60)
    sampler.flush(MINUTE + 60)
    sampler.file.close()
    with store.read() as c:
        holds = [json.loads(r[0]) for r in c.execute("SELECT detail FROM flight_events WHERE kind='long_hold'")]
    opened = [h for h in holds if h.get("open")]
    assert opened and opened[0]["route"] == "POST /api/v2/test-hold/{key}" and "hold_the_lock" in opened[0]["site"]
    assert any(not h.get("open") and h["route"] == "POST /api/v2/test-hold/{key}" for h in holds)   # and when it ended
    found = flight_file.summary(folder, 10**9)["long_holds"]
    assert any(h.get("open") and h["route"] == "POST /api/v2/test-hold/{key}" for h in found)


def test_hourly_sizes_read_a_tables_rowid_span_not_every_row(tmp_path):
    import sqlite3
    from backend.flight import _rows
    c = sqlite3.connect(tmp_path / "rows.sqlite")
    c.execute("CREATE TABLE log(id INTEGER PRIMARY KEY, v TEXT)")
    c.execute("CREATE TABLE keyed(k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID")
    assert _rows(c, "log") == 0 and _rows(c, "keyed") == 0
    c.executemany("INSERT INTO log(v) VALUES(?)", [("x",)] * 50)
    c.executemany("INSERT INTO keyed VALUES(?,?)", [(str(i), "x") for i in range(7)])
    c.execute("DELETE FROM log WHERE id<=20")              # pruned from the oldest end
    assert _rows(c, "log") == 30 and _rows(c, "keyed") == 7

    def steps():
        seen = []
        c.set_progress_handler(lambda: seen.append(1), 1)
        _rows(c, "log")
        c.set_progress_handler(None, 0)
        return len(seen)
    few = steps()
    c.executemany("INSERT INTO log(v) VALUES(?)", [("x",)] * 5000)
    assert steps() == few, "the span reads two ends of the rowid b-tree, not each row"
