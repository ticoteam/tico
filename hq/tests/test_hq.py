"""The HQ collector: it stores no address, refuses bad input, and its public numbers are right and never small."""

import logging
import sqlite3
import uuid
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from hq import __main__ as entry
from hq.app import create_app
from hq.db import Database
from hq.limits import Limiter
from hq.releases import Latest

RELEASE = {"tag_name": "v0.2.15", "html_url": "https://github.com/ticoteam/tico/releases/tag/v0.2.15",
           "published_at": "2026-10-20T10:00:00Z", "name": "Tico 0.2.15", "author": {"login": "someone"}}
TODAY = date(2026, 11, 1)


def ident():
    return str(uuid.uuid4())


@pytest.fixture
def day():
    return [TODAY]


@pytest.fixture
def db(tmp_path, day):
    return Database(tmp_path / "hq.db", clock=lambda: day[0])


@pytest.fixture
def client(db):
    latest = Latest(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=RELEASE)))
    with TestClient(create_app(db, latest), client=("203.0.113.9", 5000)) as c:
        yield c


def ping(client, install=None, version="0.2.15", people="true", bots="false", **extra):
    return client.get("/v1/latest", params={"install_id": install or ident(), "version": version,
                                            "active_people": people, "active_bots": bots, **extra})


def test_a_ping_returns_the_release_and_stores_only_the_allowed_columns(client, db, tmp_path, caplog):
    install = ident()
    with caplog.at_level(logging.DEBUG):
        r = ping(client, install, extra_field="1.2.3.4 secret@acme.example", people="true", bots="false")
    assert r.status_code == 200 and r.json()["tag_name"] == "v0.2.15" and "author" not in r.json()
    rows = [dict(x) for x in db.conn.execute("SELECT * FROM installs")]
    assert rows == [{"install_id": install, "first_seen": "2026-11-01", "last_seen": "2026-11-01",
                     "last_version": "0.2.15", "last_people": "2026-11-01", "last_bots": None,
                     "first_result": None, "result_from_start": 0}]
    # No address anywhere: not in the file, not in the log, not in a table beyond `installs`.
    dump = "".join(db.conn.iterdump())
    assert db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()[0][0] == "installs"
    assert "203.0.113.9" not in dump and "secret" not in dump and "extra_field" not in dump
    assert "203.0.113.9" not in (tmp_path / "hq.db").read_bytes().decode("latin1")
    # What the server logs (the test client's own httpx lines are not the server's).
    logged = " ".join(r.getMessage() for r in caplog.records if not r.name.startswith("httpx"))
    assert "203.0.113.9" not in logged and install not in logged


@pytest.mark.parametrize("bad", [{"install_id": "not-a-uuid"}, {"version": "1.2.3\n"}])
def test_bad_input_is_refused_and_nothing_is_stored(client, db, bad):
    good = {"install_id": ident(), "version": "0.2.15", "active_people": "true", "active_bots": "true"}
    assert client.get("/v1/latest", params={**good, **bad}).status_code == 422
    for name in good:                        # a missing field, or a repeated one, is refused as well
        assert client.get("/v1/latest", params={k: v for k, v in good.items() if k != name}).status_code == 422
    assert client.get("/v1/latest?" + "&".join(f"{k}={v}" for k, v in good.items()) + "&install_id=" + ident()).status_code == 422
    assert db.conn.execute("SELECT count(*) FROM installs").fetchone()[0] == 0


def test_a_bare_lookup_returns_the_release_and_counts_nothing(client, db):
    assert client.get("/v1/latest").json()["tag_name"] == "v0.2.15"
    assert db.conn.execute("SELECT count(*) FROM installs").fetchone()[0] == 0


def test_the_flags_remember_the_last_day_they_were_true_and_first_seen_never_moves(client, db, day):
    install = ident()
    ping(client, install, people="true", bots="true")
    day[0] = TODAY + timedelta(days=3)
    ping(client, install, version="0.2.16", people="false", bots="false")
    row = dict(db.conn.execute("SELECT * FROM installs").fetchone())
    assert row == {"install_id": install, "first_seen": "2026-11-01", "last_seen": "2026-11-04",
                   "last_version": "0.2.16", "last_people": "2026-11-01", "last_bots": "2026-11-01",
                   "first_result": None, "result_from_start": 0}


def test_the_first_result_day_is_kept_once_and_only_installs_counted_from_the_start_give_a_wait(client, db, day):
    old = ident()
    ping(client, old)                                         # a release from before the flag
    for wait, install in enumerate([ident() for _ in range(5)]):
        day[0] = TODAY
        ping(client, install, first_result="false")
        day[0] = TODAY + timedelta(days=wait)
        ping(client, install, first_result="true")
        day[0] = TODAY + timedelta(days=wait + 3)
        ping(client, install, first_result="true")            # a later true does not move the day
    ping(client, old, first_result="true")                     # it updated: reached a result, but its wait is unknown
    assert ping(client, ident(), first_result="maybe").status_code == 422
    assert client.get("/v1/latest", params={"first_result": "true"}).status_code == 422
    got = db.stats()
    assert got["first_result"] == 6 and got["first_result_median_days"] == 2
    row = dict(db.conn.execute("SELECT first_result, result_from_start FROM installs WHERE install_id=?", (old,)).fetchone())
    assert row == {"first_result": (TODAY + timedelta(days=7)).isoformat(), "result_from_start": 0}


def test_a_database_from_before_the_flag_gains_its_columns(tmp_path, day):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE installs(install_id TEXT PRIMARY KEY, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, "
                 "last_version TEXT NOT NULL, last_people TEXT, last_bots TEXT)")
    conn.execute("INSERT INTO installs VALUES('a','2026-10-01','2026-10-02','0.2.15','2026-10-02',NULL)")
    conn.commit()
    conn.close()
    db = Database(path, clock=lambda: day[0])
    assert db.stats()["first_result"] == 0
    db.record("a", "0.3.52", True, True, True)
    assert db.stats()["first_result"] == 1 and db.stats()["first_result_median_days"] is None


def test_the_rate_limit_is_per_address_and_kept_in_memory(db):
    now = [0.0]
    latest = Latest(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=RELEASE)))
    limiter = Limiter(limit=3, window=3600, clock=lambda: now[0])
    with TestClient(create_app(db, latest, limiter, client_ip_header="X-Forwarded-For")) as c:
        one = {"X-Forwarded-For": "198.51.100.1, 203.0.113.7"}
        assert [c.get("/v1/stats", headers=one).status_code for _ in range(4)] == [200, 200, 200, 429]
        assert c.get("/v1/stats", headers={"X-Forwarded-For": "203.0.113.8"}).status_code == 200
        now[0] += 3601
        assert c.get("/v1/stats", headers=one).status_code == 200
    assert all("203.0.113" not in str(k) for k in limiter.hits)    # only salted hashes, held in memory


def test_stats_math_tried_active_and_retained(db, day):
    def seen(days_ago_first, days_ago_last, people, bots, version="0.2.15"):
        install = ident()
        day[0] = TODAY - timedelta(days=days_ago_first)
        db.record(install, version, people, bots)
        day[0] = TODAY - timedelta(days=days_ago_last)
        db.record(install, version, people, bots)
    seen(40, 1, True, True)      # tried, active, retained
    seen(40, 20, True, True)     # tried, but not heard from this week: not active, not retained
    seen(40, 1, True, False)     # active, never a bot: not tried, not retained
    seen(5, 1, True, True)       # tried and active, too new to be retained
    seen(100, 2, False, True)    # a bot ran, nobody signed in: not tried; retained
    day[0] = TODAY
    assert db.stats() == {"tried": 3, "active_7d": 4, "retained_30d": 2, "first_result": 0,
                          "first_result_median_days": None, "by_version": {}, "as_of": "2026-11-01"}


def test_small_version_counts_are_suppressed_and_the_rest_rounded_into_other(db, day):
    for _ in range(6):
        db.record(ident(), "0.2.15", True, True)
    for _ in range(3):
        db.record(ident(), "0.2.14", True, False)
    for _ in range(2):
        db.record(ident(), "0.1.9", True, False)
    for _ in range(2):
        db.record(ident(), "1.0.0", True, False)
    got = db.stats()
    # 0.2.15 and 0.2.14 share the "0.2" bucket (9); 0.1 and 1 are two each, together four: under five, left out.
    assert got["by_version"] == {"0.2": 9} and got["active_7d"] == 13
    db.record(ident(), "2.0.0", True, False)
    assert db.stats()["by_version"] == {"0.2": 9, "other": 5}


def test_release_counts_are_exact_per_release_and_only_for_the_last_week(db, day):
    db.record(ident(), "0.2.24", True, True)
    db.record(ident(), "0.2.24", True, False)
    db.record(ident(), "0.2.9", True, False)
    old = ident()
    day[0] = TODAY - timedelta(days=20)
    db.record(old, "0.2.1", True, True)
    day[0] = TODAY
    assert list(db.release_counts().items()) == [("0.2.9", 1), ("0.2.24", 2)]
    assert db.stats()["by_version"] == {}          # the public view still hides every count under 5


def test_installs_not_heard_from_in_thirteen_months_are_deleted(db, day):
    day[0] = TODAY - timedelta(days=400)
    db.record(ident(), "0.2.15", True, True)
    day[0] = TODAY - timedelta(days=300)
    db.record(ident(), "0.2.15", True, True)
    day[0] = TODAY
    assert db.purge() == 1 and db.conn.execute("SELECT count(*) FROM installs").fetchone()[0] == 1


def test_it_will_not_start_without_its_key(monkeypatch):
    monkeypatch.delenv("TICO_HQ_KEY", raising=False)
    with pytest.raises(SystemExit) as refused:
        entry.main()
    assert "TICO_HQ_KEY" in str(refused.value)
    monkeypatch.setenv("TICO_HQ_KEY", "too-short")
    with pytest.raises(SystemExit):
        entry.main()


def test_the_app_serves_the_org_builders_suggestions(client):
    # hq/recruit.py is installed on the collector's app: its strict input check answers, not a 404.
    assert client.post("/v1/recruit", json={"department": "sales", "extra": 1}).status_code == 422
