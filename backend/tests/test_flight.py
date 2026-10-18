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
        rec.request("GET /api/v2/tasks", "bot", 200, ms, 100, "bot:finance")
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
        slow = c.execute("SELECT route,caller,actor,status FROM flight_slow").fetchall()
        shapes = {r[0]: r[1] for r in c.execute("SELECT fingerprint,n FROM flight_sql")}
        process = c.execute("SELECT requests,txns FROM flight_process").fetchone()
        rows_ms = c.execute("SELECT total_ms FROM flight_sql WHERE fingerprint LIKE 'WITH RECURSIVE%'").fetchone()[0]
    bot = rows["bot"]
    assert (bot["n"], bot["errors"], bot["bytes"], bot["max_ms"]) == (20, 0, 2000, 1500)
    assert 10 <= bot["p50"] <= 12.5 and 900 <= bot["p95"] <= 1500
    assert rows["human"]["errors"] == 1
    assert [tuple(r) for r in slow] == [("GET /api/v2/tasks", "bot", "bot:finance", 200)]
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


def test_table_sizes_are_reused_for_a_day_even_after_a_restart(store, monkeypatch):
    with store.transaction() as c:
        c.execute("CREATE TABLE size_fixture(created INTEGER, payload BLOB)")
        # Large enough to appear in the top 40 alongside the initialized schema's tables/indexes.
        c.execute("INSERT INTO size_fixture VALUES(?, zeroblob(?))", (MINUTE, 128 * 1024))
    monkeypatch.setattr(flight, "BIG", {**flight.BIG, "size_fixture": "created"})
    scans = []
    read = store.read

    @contextmanager
    def traced_read():
        with read() as c:
            def trace(sql):
                if "FROM dbstat" in sql:
                    scans.append((sql, c.in_transaction))
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
    assert len(scans) == 1
    with store.transaction() as c:
        c.execute("INSERT INTO size_fixture VALUES(?, zeroblob(?))", (MINUTE + 1, 256 * 1024))
    sampler.size_db(MINUTE + 3600)
    # A new recorder must use the persisted sample rather than repeat the scan on each restart.
    sampler = flight.Flight(store.recorder, store, store.settings)
    sampler.size_db(MINUTE + 7200)
    with store.read() as c:
        reused = flight._db(c, MINUTE + 7200)
    assert len(scans) == 1
    assert reused["objects"] == first["objects"] and reused["objects_at"] == MINUTE
    assert reused["at"] == (MINUTE + 7200) // 3600 * 3600
    assert reused["rows"]["size_fixture"] == 2 and reused["oldest"]["size_fixture"] == MINUTE

    sampler.size_db(MINUTE + 86400)
    with store.read() as c:
        refreshed = flight._db(c, MINUTE + 86400)
        assert [r[1] for r in c.execute("PRAGMA table_info(flight_db)")] == [
            "ts", "bytes", "wal_bytes", "free_bytes", "detail_json"]
    assert len(scans) == 2
    assert refreshed["objects_at"] == MINUTE + 86400
    assert refreshed["objects"]["size_fixture"] > first["objects"]["size_fixture"]
    assert all("aggregate=TRUE" in sql and not in_transaction for sql, in_transaction in scans)


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
            c.execute("INSERT INTO agents(bot,harness,token_hash,created,created_by) VALUES('qa','hermes','x',?,'human:ana')",
                      (H.now(),))

        def get(token, path="/api/v2/system/metrics"):
            return client.get(path, headers={"Authorization": "Bearer " + token})
        assert get("ana-test", "/api/v2/health").status_code == 200
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
            c.execute("INSERT INTO flight_slow(ts,route,caller,actor,ms,bytes,status) VALUES(?,?,?,?,?,?,?)",
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
    (registry / "hub-access.yaml").write_text(yaml.safe_dump({"owner": "ana@acme.example"}))
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
        c.execute("INSERT INTO flight_slow(ts,route) VALUES(?,?)", (now - 15 * 86400, "GET /x"))
        c.execute("INSERT INTO flight_sql VALUES(?,?,?,?,?,?,?)", (now - 91 * 86400, "SELECT N", 1, 1, 1, 1, "{}"))
        c.execute("INSERT INTO flight_process(ts) VALUES(?)", (now - 60,))
    flight.sweep(store, now)
    with store.read() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM flight_requests")]
        assert c.execute("SELECT count(*) FROM flight_slow").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM flight_sql").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM flight_process").fetchone()[0] == 1
    assert len(rows) == 1
    row = rows[0]
    assert (row["ts"], row["span"], row["n"], row["bytes"], row["max_ms"]) == (hour, 3600, 3, 15, 4000)
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
