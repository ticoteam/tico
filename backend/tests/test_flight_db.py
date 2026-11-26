"""The flight recorder keeps its tables in a file of its own beside the server's database (`<stem>.flight.db`): the
server's database gets no recorder write, the file is owner-only, its schema resets itself, a damaged or missing file
reads as empty instead of failing Health, a new file starts with the server database's recent recorder rows, and
backups never carry it."""

import json
import os
import sqlite3
import stat
import time
from contextlib import closing
from datetime import datetime, timezone

import pytest
from backend import backup, flight, invariants, maintenance_health
from backend.config import Settings
from backend.store import Store
from backend.tests.helpers.api import headers  # noqa: F401  (the api fixture)

NOW = 1_700_000_040


@pytest.fixture
def store(tmp_path):
    s = Store(Settings(db_path=tmp_path / "hub.db", registry_dir=tmp_path), flight.Recorder())
    s.initialize(seed_market=False)
    flight._READY.clear()
    return s


def hub_flight_rows(store):
    with closing(sqlite3.connect(store.settings.db_path)) as c:
        return {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in flight.TABLES}


def flight_rows(db_path):
    with closing(sqlite3.connect(flight.path(db_path))) as c:
        return {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in flight.TABLES}


def test_the_flight_database_sits_beside_the_servers_and_memory_has_none(tmp_path):
    assert flight.path(tmp_path / "hub.sqlite") == tmp_path / "hub.flight.db"
    assert flight.path(tmp_path / "hub.db") == tmp_path / "hub.flight.db"
    assert flight.path(tmp_path / "hub.flight.db") == tmp_path / "hub.flight.db"
    assert flight.path(":memory:") is None and flight.path("") is None


def test_every_recorder_write_goes_to_its_own_file_and_none_to_the_servers_database(store, tmp_path):
    rec = store.recorder
    sampler = flight.Flight(rec, store, store.settings, folder=tmp_path / "flight")
    rec.request("GET /api/v2/tasks", "bot", 200, 1500, 100, "bot:finance", [30, 80, 12])  # slow: a flight_slow row
    rec.web_timing("lcp", "/tasks", 900)
    rec.event("catch_up_unchecked", {"n": 1})
    minute = int(time.time()) // 60 * 60
    rec.drain(minute)
    sampler.record_start()
    sampler.flush(minute)
    sampler.size_db(time.time())
    flight.record_event(store, "note", {"x": 1})
    flight.sweep(store)
    sampler.file.close()

    assert hub_flight_rows(store) == dict.fromkeys(flight.TABLES, 0)
    got = flight_rows(store.settings.db_path)
    assert got["flight_requests"] and got["flight_slow"] and got["flight_process"] and got["flight_web"]
    assert got["flight_db"] == 1 and got["flight_events"] >= 3  # start, the request's event, the note
    with store.read() as c:  # the readers Health uses, on the server's connection, find the rows
        assert flight.start_times(c) and flight.latest_db_sample(c)["bytes"] > 0
        out = flight.view(c, minutes=5, now=minute + 60)
    assert out["requests"]["n"] == 1 and out["events"]["start"] and "flight_db" not in out


def test_the_file_and_its_wal_and_shm_are_owner_only(store):
    old = os.umask(0o022)
    try:
        flight.record_event(store, "note", {})
        target = flight.path(store.settings.db_path)
        with closing(sqlite3.connect(target)) as c:  # an open connection keeps the -wal and -shm on disk
            c.execute("INSERT INTO flight_events(created_at,kind,detail) VALUES(1,'x','{}')")
            c.commit()
            modes = {name: stat.S_IMODE(os.stat(f"{target}{name}").st_mode) for name in ("", "-wal", "-shm")}
    finally:
        os.umask(old)
    assert modes == {"": 0o600, "-wal": 0o600, "-shm": 0o600}
    with closing(sqlite3.connect(target)) as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_a_file_made_readable_by_others_is_made_owner_only_again(store):
    flight.record_event(store, "note", {})
    target = flight.path(store.settings.db_path)
    os.chmod(target, 0o644)
    flight._READY.clear()  # the next process
    flight.record_event(store, "note", {})
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


def test_another_schema_version_drops_and_remakes_the_tables_in_place(store, monkeypatch):
    flight.record_event(store, "note", {})
    target = flight.path(store.settings.db_path)
    with closing(sqlite3.connect(target)) as c:
        c.execute("CREATE TABLE flight_old(x)")  # a table an older recorder made stays: only the seven are remade
        c.commit()
    monkeypatch.setattr(flight, "SCHEMA_VERSION", flight.SCHEMA_VERSION + 1)
    flight._READY.clear()
    with flight.tables(store) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == flight.SCHEMA_VERSION
        assert c.execute("SELECT count(*) FROM flight_events").fetchone()[0] == 0
    assert hub_flight_rows(store) == dict.fromkeys(flight.TABLES, 0)  # the reset never touches the server's file


def test_a_file_that_is_not_a_database_is_replaced_by_the_next_write(store, tmp_path):
    target = flight.path(store.settings.db_path)
    target.write_bytes(b"not a database, " * 512)
    with store.read() as c:  # readers first: empty, never an error
        assert flight.view(c, minutes=5)["flight_db"] == "unreadable"
        assert flight.long_hold_count(c, 0) == 0 and flight.start_times(c) == []
    sampler = flight.Flight(store.recorder, store, store.settings, folder=tmp_path / "flight")
    sampler.record_start()
    sampler.file.close()
    with closing(sqlite3.connect(target)) as c:
        assert c.execute("SELECT kind FROM flight_events").fetchall() == [("start",)]
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


def test_a_file_damaged_after_it_was_opened_is_replaced_and_the_minute_kept(store, tmp_path):
    sampler = flight.Flight(store.recorder, store, store.settings, folder=tmp_path / "flight")
    assert sampler.write_db(lambda c: flight._event(c, "first", {}, NOW))
    target = flight.path(store.settings.db_path)
    target.write_bytes(b"\0" * 100 + b"garbage" * 1000)  # _READY still names it: the write finds out
    assert sampler.write_db(lambda c: flight._event(c, "second", {}, NOW), NOW) is False
    assert sampler.write_db(lambda c: flight._event(c, "third", {}, NOW), NOW + 60)
    sampler.file.close()
    with closing(sqlite3.connect(target)) as c:
        assert [r[0] for r in c.execute("SELECT kind FROM flight_events ORDER BY id")] == ["second", "third"]


def test_health_invariants_and_metrics_read_empty_without_a_flight_file(store, tmp_path):
    with store.read() as c:
        assert flight.state(c) == "missing"
        assert (
            flight.start_times(c) == [] and flight.long_hold_count(c, 0) == 0 and flight.long_hold_sites(c, 0, 5) == []
        )
        assert flight.route_totals(c, 0, ("GET /x",)) == [] and flight.route_minutes(c, 0, ("GET /x",)) is None
        assert (
            dict(flight.drop_totals(c, 0)) == {"dropped": 0, "requests": 0} and flight.worst_drop_minutes(c, 0, 5) == []
        )
        assert flight.latest_db_sample(c) is None and flight.speed(c, NOW - 600, NOW - 300, NOW) == []
        out = flight.view(c, minutes=5, now=NOW)
        assert out["flight_db"] == "missing" and out["requests"]["n"] == 0 and out["slow"] == [] and out["db"] == {}
        ctx = invariants.Ctx(at=datetime.fromtimestamp(NOW, timezone.utc), now="")
        assert (
            invariants.long_holds(c, ctx) == invariants.runner_errors(c, ctx) == invariants.recorder_drops(c, ctx) == []
        )
        assert maintenance_health._server_starts(c)  # this process's own start
    assert not flight.path(store.settings.db_path).exists()  # a read never makes the file


def test_a_new_file_takes_the_servers_recent_recorder_rows_once(store, monkeypatch):
    monkeypatch.setattr(flight, "_now", lambda: NOW)
    with store.transaction() as c:  # what an older server left in its own database
        for at in (NOW - 600, NOW - flight.SEED_SECONDS - 60):
            c.execute(
                "INSERT INTO flight_requests VALUES(?,60,'GET /api/v2/tasks','human',3,0,30,30.0,10,10,10,'{}')", (at,)
            )
        c.execute(
            "INSERT INTO flight_events(created_at,kind,detail) VALUES(?,'start','{\"version\":\"0.1.0\"}')",
            (NOW - 30 * 86400,),
        )
        c.execute("INSERT INTO flight_events(created_at,kind,detail) VALUES(?,'stall','{}')", (NOW - 30 * 86400,))
        c.execute(
            "INSERT INTO flight_slow(created_at,route,ms,parts) VALUES(?,'GET /x',1500,'{\"walk\":3}')", (NOW - 60,)
        )
        c.execute("INSERT INTO flight_db VALUES(?,1,0,0,'{}')", (NOW - 86400,))  # sizes go back further, for 24h growth
    before = hub_flight_rows(store)

    flight.record_event(store, "note", {})

    with flight.tables(store) as c:
        assert [r[0] for r in c.execute("SELECT created_at FROM flight_requests")] == [NOW - 600]
        assert c.execute("SELECT parts FROM flight_slow").fetchone()[0] == '{"walk":3}'
        kinds = [r[0] for r in c.execute("SELECT kind FROM flight_events ORDER BY id")]
        seeded = json.loads(c.execute("SELECT detail FROM flight_events WHERE kind='seeded'").fetchone()[0])
    assert kinds == ["start", "seeded", "note"]  # every start, nothing older than SEED_SECONDS
    assert seeded["rows"] == {"flight_db": 1, "flight_events": 1, "flight_requests": 1, "flight_slow": 1}
    assert hub_flight_rows(store) == before  # only read

    flight._READY.clear()  # the next start: the file is not new, so nothing is copied again
    flight.record_event(store, "note", {})
    with flight.tables(store) as c:
        assert c.execute("SELECT count(*) FROM flight_requests").fetchone()[0] == 1


def test_the_release_comparison_names_an_empty_baseline(api, monkeypatch):
    monkeypatch.setenv("TICO_VERSION", "0.2.0")
    history = [
        {"version": "0.1.0", "since": "2026-01-01T00:00:00Z"},
        {"version": "0.2.0", "since": "2026-01-02T00:00:00Z"},
    ]
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('release-history',?)", (json.dumps(history),))
    got = api.get(flight.SPEED_PATH, headers=headers("ana-test")).json()
    assert (got["base"], got["routes"], got["baseline"]) == ("0.1.0", [], "empty")
    assert got.get("flight_db") in ("missing", None)  # the app's own recorder may have made the file by now


def test_a_backup_snapshot_carries_the_servers_database_and_not_the_flight_file(store, tmp_path):
    flight.record_event(store, "note", {})
    for c in store.pool:
        c.close()
    store.pool.clear()
    out = tmp_path / "out"
    out.mkdir()
    backup.snapshot(store.settings.db_path, out / "hub.db")
    assert sorted(p.name for p in out.iterdir() if not p.name.endswith(("-wal", "-shm"))) == ["hub.db"]
