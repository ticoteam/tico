"""The flight recorder keeps its tables in a file of its own beside the server's database (`<stem>.flight.db`): the
server's database gets no recorder write, the file is owner-only, its schema resets itself, a damaged or missing file
reads as empty instead of failing Health, a new file starts with the server database's recent recorder rows, and
backups never carry it."""

import json
import os
import sqlite3
import stat
import threading
import time
from contextlib import closing
from datetime import datetime, timezone

import pytest
from backend import backup, flight, invariants, maintenance_health, migrations, performance
from backend.config import Settings
from backend.maintenance_steps import clean_up
from backend.store import H, Store
from backend.tests.helpers.api import headers  # noqa: F401  (the api fixture)

NOW = 1_700_000_040


@pytest.fixture
def store(tmp_path):
    s = Store(Settings(db_path=tmp_path / "hub.db", registry_dir=tmp_path), flight.Recorder())
    s.initialize(seed_market=False)
    flight._READY.clear()
    return s


def hub_flight_tables(db_path):
    """The flight_* tables (and their indexes) in the server's own database: none since the drop migration."""
    with closing(sqlite3.connect(db_path)) as c:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE name LIKE 'flight%'")}


def hub_flight_rows(store):
    """Rows in the server database's old copies of the recorder's tables, which a test made as an older server had."""
    with closing(sqlite3.connect(store.settings.db_path)) as c:
        return {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in flight.TABLES}


def make_old_hub_tables(c):
    """What a server before the flight file (and before the drop migration) had in its own database."""
    for statement in flight.SCHEMA:
        c.execute(statement)


def flight_rows(db_path):
    with closing(sqlite3.connect(flight.path(db_path))) as c:
        return {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in flight.TABLES}


def test_the_flight_database_sits_beside_the_servers_and_memory_has_none(tmp_path):
    assert flight.path(tmp_path / "hub.sqlite") == tmp_path / "hub.flight.db"
    assert flight.path(tmp_path / "hub.db") == tmp_path / "hub.flight.db"
    assert flight.path(":memory:") is None and flight.path("") is None


def test_a_server_database_named_like_a_flight_file_gets_a_flight_file_of_its_own(tmp_path):
    """A company whose database is company.flight.db: the recorder never writes to, resets or replaces it."""
    hub = tmp_path / "company.flight.db"
    assert flight.path(hub) == tmp_path / "company.flight.flight.db"
    s = Store(Settings(db_path=hub, registry_dir=tmp_path), flight.Recorder())
    s.initialize(seed_market=False)
    flight._READY.clear()
    with closing(sqlite3.connect(hub)) as c:
        before = (c.execute("PRAGMA user_version").fetchone()[0], c.execute("SELECT count(*) FROM tasks").fetchone()[0])
    flight.record_event(s, "note", {})
    with closing(sqlite3.connect(hub)) as c:
        assert (
            c.execute("PRAGMA user_version").fetchone()[0],
            c.execute("SELECT count(*) FROM tasks").fetchone()[0],
        ) == before
    assert hub_flight_tables(hub) == set()  # no recorder table made in it
    with s.read() as c:
        assert [e for e in flight.view(c, minutes=5)["events"]["recent"] if e["kind"] == "note"]


def test_a_symlinked_server_database_has_one_flight_file_for_writers_and_readers(tmp_path):
    real = tmp_path / "data"
    real.mkdir()
    s = Store(Settings(db_path=real / "hub.db", registry_dir=tmp_path), flight.Recorder())
    s.initialize(seed_market=False)
    link = tmp_path / "hub.db"
    link.symlink_to(real / "hub.db")
    linked = Store(Settings(db_path=link, registry_dir=tmp_path), flight.Recorder())
    flight._READY.clear()
    assert flight.path(link) == real / "hub.flight.db"
    flight.record_event(linked, "note", {"via": "link"})  # the writer holds the link's path
    assert not (tmp_path / "hub.flight.db").exists() and (real / "hub.flight.db").exists()
    with linked.read() as c:  # SQLite names the real file in PRAGMA database_list: the reader finds the same one
        assert flight.state(c) == "ok"
        assert [e["via"] for e in flight.view(c, minutes=5)["events"]["recent"] if e["kind"] == "note"] == ["link"]


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

    assert hub_flight_tables(store.settings.db_path) == set()
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


def test_a_new_file_is_owner_only_before_anything_is_written_to_it(store, monkeypatch):
    """Made owner-only when it is made, not chmod-ed afterwards: no moment where others can read it."""
    seen, schema = [], flight._schema
    monkeypatch.setattr(
        flight, "_schema", lambda target, *a: (seen.append(stat.S_IMODE(os.stat(target).st_mode)), schema(target, *a))
    )
    old = os.umask(0o022)
    try:
        flight.record_event(store, "note", {})
    finally:
        os.umask(old)
    assert seen == [0o600]


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
    assert hub_flight_tables(store.settings.db_path) == set()  # the reset never makes a table in the server's file


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


def test_a_database_without_the_recorders_tables_reads_as_unreadable_and_empty(store):
    """SQLite opens it, but the tables are not there (another program's file): every reader answers empty."""
    with closing(sqlite3.connect(flight.path(store.settings.db_path))) as c:
        c.execute("CREATE TABLE other(x)")
    with store.read() as c:
        assert flight.state(c) == "unreadable" and flight.last_start(c) is None
        assert flight.start_times(c) == [] and flight.latest_db_sample(c) is None
        assert flight.view(c, minutes=5)["flight_db"] == "unreadable"


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


def test_a_damaged_file_is_replaced_only_after_its_connection_is_closed(store, monkeypatch, tmp_path):
    opened, at_replace = [], []
    connect = flight.connect

    def tracked(*args, **kwargs):
        opened.append(connect(*args, **kwargs))
        return opened[-1]

    def closed(c):
        try:
            c.execute("SELECT 1")
        except sqlite3.ProgrammingError:
            return True
        return False

    replace = flight._replace
    monkeypatch.setattr(flight, "connect", tracked)
    monkeypatch.setattr(
        flight, "_replace", lambda target, *a: (at_replace.append([closed(c) for c in opened]), replace(target, *a))
    )
    sampler = flight.Flight(store.recorder, store, store.settings, folder=tmp_path / "flight")
    assert sampler.write_db(lambda c: flight._event(c, "first", {}, NOW))
    target = flight.path(store.settings.db_path)
    target.write_bytes(b"\0" * 100 + b"garbage" * 1000)
    assert sampler.write_db(lambda c: flight._event(c, "second", {}, NOW), NOW) is False
    target.write_bytes(b"\0" * 100 + b"garbage" * 1000)
    flight._READY.add(str(target))
    with pytest.raises(sqlite3.DatabaseError), flight.tables(store, write=True) as c:
        c.execute("SELECT count(*) FROM flight_events")
    sampler.file.close()
    assert at_replace == [[True, True], [True, True, True]]  # every connection to the file was closed first


def test_a_damaged_file_that_cannot_be_removed_fails_the_write_and_never_raises(store, monkeypatch, tmp_path):
    sampler = flight.Flight(store.recorder, store, store.settings, folder=tmp_path / "flight")
    assert sampler.write_db(lambda c: flight._event(c, "first", {}, NOW))
    flight.path(store.settings.db_path).write_bytes(b"\0" * 100 + b"garbage" * 1000)

    def refuse(name):
        raise PermissionError(name)

    monkeypatch.setattr(flight.os, "remove", refuse)
    assert sampler.write_db(lambda c: flight._event(c, "second", {}, NOW), NOW) is False  # damaged at the write
    assert sampler.write_db(lambda c: flight._event(c, "third", {}, NOW), NOW + 60) is False  # damaged at the open
    sampler.file.close()
    assert sampler.db_failed == 2 and len(sampler.pending) == 2


def test_a_connection_never_makes_a_file_without_tables(store, monkeypatch):
    """A file replaced between the schema check and the open is an error for that one write, not an empty file
    without tables that every later write would fail on."""
    flight.record_event(store, "note", {})
    target = flight.path(store.settings.db_path)
    monkeypatch.setattr(flight, "_ensure", lambda *a: None)  # it was ready a moment ago
    flight._replace(target)
    with pytest.raises(sqlite3.OperationalError):
        flight.connect(store.settings.db_path)
    assert not target.exists()


def test_threads_finding_a_damaged_file_together_make_one_new_file(store):
    flight.record_event(store, "note", {})
    target = flight.path(store.settings.db_path)
    target.write_bytes(b"\0" * 100 + b"garbage" * 1000)
    flight._READY.clear()
    errors, go = [], threading.Barrier(8)

    def write(i):
        go.wait()
        try:
            flight.record_event(store, "note", {"i": i})
        except Exception as exc:  # noqa: BLE001  the test counts them
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert errors == []
    with closing(sqlite3.connect(target)) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == flight.SCHEMA_VERSION
        assert sorted(
            json.loads(r[0])["i"] for r in c.execute("SELECT detail FROM flight_events WHERE kind='note'")
        ) == list(range(8))
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


def test_a_folder_that_cannot_take_the_file_counts_failed_writes_and_keeps_a_capped_backlog(tmp_path, monkeypatch):
    folder = tmp_path / "data"
    folder.mkdir()
    s = Store(Settings(db_path=folder / "hub.db", registry_dir=tmp_path), flight.Recorder())
    s.initialize(seed_market=False)
    flight._READY.clear()
    monkeypatch.setattr(flight, "PENDING_MAX", 3)
    sampler = flight.Flight(s.recorder, s, s.settings, folder=tmp_path / "flight")
    os.chmod(folder, 0o500)
    try:
        for minute in range(5):
            assert sampler.write_db(lambda c: None, NOW + minute * 60) is False
    finally:
        os.chmod(folder, 0o700)
        sampler.file.close()
    assert sampler.db_failed == 5 and len(sampler.pending) == 3 and sampler.lost == [NOW, NOW + 60]
    assert not flight.path(s.settings.db_path).exists()


def test_last_start_reads_the_flight_file_from_the_servers_connection(store):
    """Health's release-image check holds the server's connection: it finds the start there, and None (never an
    error) when the file is missing or unreadable."""
    with store.read() as c:
        assert flight.last_start(c) is None  # missing
    flight.record_event(store, "start", {"version": "0.2.0", "provenance": "modified"}, NOW)
    with store.read() as c:
        assert flight.last_start(c) == {"at": NOW, "version": "0.2.0", "provenance": "modified"}
    flight.path(store.settings.db_path).write_bytes(b"not a database, " * 512)
    with store.read() as c:
        assert flight.state(c) == "unreadable" and flight.last_start(c) is None


def test_a_row_with_damaged_json_reads_as_empty_detail_not_an_error(store):
    flight.record_event(store, "note", {})
    with flight.tables(store, write=True) as c:
        c.execute("INSERT INTO flight_events(created_at,kind,detail) VALUES(?,'start','{not json')", (NOW,))
        c.execute("INSERT INTO flight_slow(created_at,route,ms,top,parts) VALUES(?,'GET /x',1500,'[x','{y')", (NOW,))
        c.execute("INSERT INTO flight_db VALUES(?,1,0,0,'nope')", (NOW,))
    with store.read() as c:
        assert flight.last_start(c) == {"at": NOW}
        out = flight.view(c, minutes=5, now=NOW + 60)
    assert "flight_db" not in out and out["events"]["start"] == {"at": NOW}
    assert [(r["top"], r["parts"]) for r in out["slow"]] == [([], {})]


def test_cleanup_finishes_its_other_sweeps_when_the_flight_file_fails(store, monkeypatch):
    swept = []

    def broken(store, now=None):
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(flight, "sweep", broken)
    monkeypatch.setattr(performance, "sweep", lambda store, now=None: swept.append(now))
    out = clean_up.run(None, {"store": store, "now": H.now()})
    assert out["status"] == "ran" and out["details"] == {"flight": "not swept: DatabaseError"} and len(swept) == 1


def test_a_file_at_the_current_version_missing_a_table_is_remade_at_the_next_start(store):
    flight.record_event(store, "note", {})
    target = flight.path(store.settings.db_path)
    with closing(sqlite3.connect(target)) as c:
        c.execute("DROP TABLE flight_web")
    with store.read() as c:
        assert flight.state(c) == "unreadable"  # every table is checked, not only flight_requests
    flight._READY.clear()  # the next start
    flight.record_event(store, "note", {})
    with store.read() as c:
        assert flight.state(c) == "ok"


def test_a_table_dropped_while_running_keeps_the_work_for_the_next_write(store, tmp_path):
    sampler = flight.Flight(store.recorder, store, store.settings, folder=tmp_path / "flight")
    assert sampler.write_db(lambda c: flight._event(c, "first", {}, NOW))
    with closing(sqlite3.connect(flight.path(store.settings.db_path))) as c:
        c.execute("DROP TABLE flight_events")
    assert sampler.write_db(lambda c: flight._event(c, "second", {}, NOW), NOW) is False
    assert sampler.lost == [] and len(sampler.pending) == 1  # not dropped as bad work
    assert sampler.write_db(lambda c: flight._event(c, "third", {}, NOW), NOW + 60)
    sampler.file.close()
    with flight.tables(store) as c:
        assert [r[0] for r in c.execute("SELECT kind FROM flight_events ORDER BY id")] == ["second", "third"]


def test_a_late_repair_never_removes_the_file_another_connection_already_made(store):
    """Two connections find the same damage: the first replaces the file and a write lands in the new one; the
    second's repair then finds another file at the path and leaves it."""
    flight.record_event(store, "note", {})
    stale = flight.connect(store.settings.db_path)
    stale.close()
    flight._replace(flight.path(store.settings.db_path), stale.file)  # the first repair
    flight.record_event(store, "kept", {})  # a new file, with a row
    flight._replace(flight.path(store.settings.db_path), stale.file)  # the second, late
    with flight.tables(store) as c:
        assert [r[0] for r in c.execute("SELECT kind FROM flight_events WHERE kind='kept'")] == ["kept"]


def test_the_first_connect_waits_no_longer_than_its_timeout_for_the_files_lock(store, monkeypatch):
    """The schema check, the WAL switch and the open all wait the recorder's own budget, not a fixed 30 s."""
    flight.record_event(store, "note", {})
    waits, connect, wal_mode = [], sqlite3.connect, flight.hubdb.wal_mode
    monkeypatch.setattr(
        flight.sqlite3, "connect", lambda *a, **k: (waits.append(k.get("timeout")), connect(*a, **k))[1]
    )
    monkeypatch.setattr(flight.hubdb, "wal_mode", lambda c, wait=30.0: (waits.append(wait), wal_mode(c, wait))[1])
    with closing(connect(flight.path(store.settings.db_path), isolation_level=None)) as holder:
        holder.execute("BEGIN IMMEDIATE")
        flight._READY.clear()  # a new process: its first connect checks the schema under the lock
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            flight.connect(store.settings.db_path, timeout=0.2)
    assert waits == [0.2, 0.2]  # the schema check's open and its WAL switch; it fails there, so no other open


def test_stored_values_of_the_wrong_shape_read_as_empty_everywhere(store):
    flight.record_event(store, "note", {})
    with flight.tables(store, write=True) as c:
        c.execute("INSERT INTO flight_events(created_at,kind,detail) VALUES(?,'start','[1,2]')", (NOW,))
        c.execute("INSERT INTO flight_db VALUES(?,1,0,0,'[3]')", (NOW,))
        c.execute(
            "INSERT INTO flight_requests VALUES(?,60,'GET /x','human',3,0,30,30.0,10,10,10,'[4]')", (NOW - 3 * 86400,)
        )
    with store.read() as c:
        assert flight.last_start(c) == {"at": NOW}  # Health's release-image check reads this
        assert flight.view(c, minutes=5, now=NOW + 60)["events"]["start"] == {"at": NOW}
    flight.sweep(store, NOW)  # the old minute becomes an hour without its histogram, not a failed sweep


def test_health_names_an_unreadable_recorder_file_and_not_a_missing_one(store):
    with store.read() as c:
        assert [f["key"] for f in maintenance_health._server_checks(c)] == []  # a new server: no file yet
    flight.path(store.settings.db_path).write_bytes(b"not a database, " * 512)
    with store.read() as c:
        found = maintenance_health._server_checks(c)
    assert [f["key"] for f in found] == ["flight_recorder"]
    assert "flight_recorder" in maintenance_health.MEANING


def test_the_load_gate_reads_the_recorder_through_the_servers_database(store, tmp_path):
    from bench.load_gate import gate

    read = gate.db_flight(store.settings.db_path)
    assert read(NOW, NOW + 60)["flight_db"] == "missing"
    rec = store.recorder
    sampler = flight.Flight(rec, store, store.settings, folder=tmp_path / "flight")
    rec.request("GET /api/v2/tasks", "bot", 200, 15, 100, "bot:finance", [3, 1, 1])
    minute = int(time.time()) // 60 * 60
    rec.drain(minute)
    sampler.flush(minute)
    sampler.file.close()
    got = read(minute, minute + 60)
    assert "flight_db" not in got and got["requests"]["n"] == 1


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
        make_old_hub_tables(c)
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


def test_a_new_server_database_has_no_flight_tables_and_the_flight_file_still_works(store, monkeypatch):
    """The drop migration: hub.sqlite holds none of the seven tables (nor their index), the runner perf tables stay,
    and the first write still makes hub.flight.db, seeds nothing from a server database that has no tables, and reads."""
    assert hub_flight_tables(store.settings.db_path) == set()
    with closing(sqlite3.connect(store.settings.db_path)) as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"perf_points", "perf_usage", "perf_overloads"} <= names
    warned, seeded, seed = [], [], flight._seed
    monkeypatch.setattr(flight.LOG, "warning", lambda *a, **k: warned.append(a))
    monkeypatch.setattr(flight, "_seed", lambda *a, **k: seeded.append(seed(*a, **k)) or seeded[-1])

    flight.record_event(store, "note", {"x": 1})

    assert seeded == [{}]  # the one-time seed ran on the new file, found no tables to read and copied nothing

    with flight.tables(store) as c:
        assert [r[0] for r in c.execute("SELECT kind FROM flight_events")] == ["note"]  # no "seeded" event
    with store.read() as c:
        assert flight.state(c) == "ok"
        assert [e["kind"] for e in flight.view(c, minutes=5, now=time.time() + 60)["events"]["recent"]] == ["note"]
    assert warned == [] and hub_flight_tables(store.settings.db_path) == set()


def test_an_upgraded_server_database_drops_its_old_flight_tables_and_keeps_the_perf_rows(store):
    """A company that ran the releases with the flight file still has the old copies: the migration takes them out,
    with their rows and index, and leaves the runner perf rows."""
    version = next(m for m in migrations.files() if m.version.endswith("_drop_hub_flight_tables")).version
    with closing(sqlite3.connect(store.settings.db_path, isolation_level=None)) as c:
        make_old_hub_tables(c)
        c.execute("INSERT INTO flight_events(created_at,kind,detail) VALUES(1,'start','{}')")
        c.execute("INSERT INTO perf_points(runner_id,span,created_at,n,cpu,cpu_max) VALUES('r1',60,1,1,0.5,0.5)")
        c.execute("DELETE FROM schema_migrations WHERE version=?", (version,))
        assert len(hub_flight_tables(store.settings.db_path)) == len(flight.TABLES) + 1  # and flight_events_kind
        migrations.apply(c)
        assert c.execute("SELECT count(*) FROM perf_points").fetchone()[0] == 1
        assert c.execute("SELECT 1 FROM schema_migrations WHERE version=?", (version,)).fetchone()
    assert hub_flight_tables(store.settings.db_path) == set()


def old_server_database(store, monkeypatch):
    """The store's database as a release before the flight file left it: the drop migration not run, the seven tables
    in it with recent rows, and no hub.flight.db beside it."""
    monkeypatch.setattr(flight, "_now", lambda: NOW)
    version = next(m for m in migrations.files() if m.version.endswith("_drop_hub_flight_tables")).version
    with closing(sqlite3.connect(store.settings.db_path, isolation_level=None)) as c:
        make_old_hub_tables(c)
        c.execute(
            "INSERT INTO flight_requests VALUES(?,60,'GET /api/v2/tasks','human',3,0,30,30.0,10,10,10,'{}')",
            (NOW - 600,),
        )
        c.execute(
            "INSERT INTO flight_events(created_at,kind,detail) VALUES(?,'start','{\"version\":\"0.6.0\"}')",
            (NOW - 86400,),
        )
        c.execute("DELETE FROM schema_migrations WHERE version=?", (version,))
    flight._READY.clear()
    return flight.path(store.settings.db_path)


def test_an_upgrade_from_before_the_flight_file_moves_the_history_into_it_before_the_drop(store, monkeypatch):
    """A company updating straight from a release before hub.flight.db: the boot's drop migration first makes the file
    from the old tables (the release comparison's last hour, every start), then takes them out of hub.sqlite."""
    target = old_server_database(store, monkeypatch)
    assert not target.exists()

    Store(Settings(db_path=store.settings.db_path, registry_dir=store.settings.registry_dir)).initialize(
        seed_market=False
    )

    assert hub_flight_tables(store.settings.db_path) == set()
    with flight.tables(store) as c:
        assert [r[0] for r in c.execute("SELECT created_at FROM flight_requests")] == [NOW - 600]
        assert [r[0] for r in c.execute("SELECT kind FROM flight_events ORDER BY id")] == ["start", "seeded"]
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


def test_an_existing_flight_file_is_left_as_it_is_by_the_drop(store, monkeypatch):
    target = old_server_database(store, monkeypatch)
    flight.record_event(store, "note", {})  # this server already writes its own file (seeded once, when it made it)
    with flight.tables(store) as c:
        before = c.execute("SELECT * FROM flight_events ORDER BY id").fetchall()
    flight._READY.clear()

    with closing(sqlite3.connect(store.settings.db_path, isolation_level=None)) as c:
        migrations.apply(c)

    with flight.tables(store) as c:
        assert c.execute("SELECT * FROM flight_events ORDER BY id").fetchall() == before  # not seeded a second time
    assert hub_flight_tables(store.settings.db_path) == set() and target.exists()


def test_a_flight_file_left_empty_by_a_crash_before_its_seed_is_seeded_by_the_drop(store, monkeypatch):
    """A boot that made the file and stopped before seeding it (the drop rolled back with it): the next boot's drop
    still copies the history in, instead of taking an empty file as seeded."""
    target = old_server_database(store, monkeypatch)
    assert flight._create(target)
    flight._schema(target)  # made, with its tables, and never seeded

    with closing(sqlite3.connect(store.settings.db_path, isolation_level=None)) as c:
        migrations.apply(c)

    assert hub_flight_tables(store.settings.db_path) == set()
    with flight.tables(store) as c:
        assert [r[0] for r in c.execute("SELECT created_at FROM flight_requests")] == [NOW - 600]
        assert [r[0] for r in c.execute("SELECT kind FROM flight_events ORDER BY id")] == ["start", "seeded"]


def test_a_flight_file_that_cannot_be_made_never_stops_the_drop(store, monkeypatch):
    """The file is disposable: a folder that cannot take it loses the old history, logged, and the server still boots."""
    old_server_database(store, monkeypatch)
    warned = []
    monkeypatch.setattr(flight.LOG, "warning", lambda *a, **k: warned.append(a))
    monkeypatch.setattr(flight, "_create", lambda target: (_ for _ in ()).throw(PermissionError("read-only folder")))

    with closing(sqlite3.connect(store.settings.db_path, isolation_level=None)) as c:
        migrations.apply(c)

    assert hub_flight_tables(store.settings.db_path) == set()
    assert [w[0] for w in warned] == ["Flight database not seeded before the drop, its history is lost: %s"]


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
    first = api.get(flight.SPEED_PATH + "?base=0.0.9", headers=headers("ana-test")).json()  # never ran here
    assert (first["base"], first["routes"], first["baseline"]) == (None, [], "empty")


def test_a_backup_snapshot_carries_the_servers_database_and_not_the_flight_file(store, tmp_path):
    flight.record_event(store, "note", {})
    for c in store.pool:
        c.close()
    store.pool.clear()
    out = tmp_path / "out"
    out.mkdir()
    backup.snapshot(store.settings.db_path, out / "hub.db")
    assert sorted(p.name for p in out.iterdir() if not p.name.endswith(("-wal", "-shm"))) == ["hub.db"]
