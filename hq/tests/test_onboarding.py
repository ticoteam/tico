"""The setup page's download count on HQ: it works with no brain and no key, keeps nothing until counts are switched on,
keeps no text when they are, survives a restart, and answers tico.team's browser (and only it)."""

import logging
import sqlite3
import time
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from hq import onboarding
from hq.app import create_app
from hq.db import Database
from hq.limits import Limiter
from hq.onboarding import Counts
from hq.releases import Latest

TODAY = date(2026, 11, 1)
STAFF = "s" * 32
PAGE = "https://tico.team"
SESSION = "3f6c1a2e-9b7d-4c58-9a10-2d4e8b7f5a63"


@pytest.fixture
def day():
    return [TODAY]


@pytest.fixture
def db(tmp_path, day):
    return Database(tmp_path / "hq.db", clock=lambda: day[0])


def make(db, on=True, limiter=None, staff_key=STAFF):
    latest = Latest(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    app = create_app(db, latest, limiter=limiter, staff_key=staff_key, watch_every=0, counts=Counts(db, on=on))
    return TestClient(app, client=("203.0.113.9", 5000))


def event(client, session=SESSION, **body):
    return client.post("/v1/onboarding/event", json={"session_id": session, "kind": "download", **body},
                       headers={"Origin": PAGE})


def rows(db):
    return [dict(r) for r in db.conn.execute("SELECT * FROM onboarding_sessions")]


def test_the_event_answers_204_and_keeps_nothing_while_counts_are_off(db, monkeypatch):
    # No model key, no cap, no brain: the route needs none of them.
    for name in ("HQ_ONBOARDING_ANTHROPIC_KEY", "HQ_ONBOARDING_DAILY_USD", "HQ_ONBOARDING_COUNTS"):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(db, Latest(transport=httpx.MockTransport(lambda r: httpx.Response(503))), watch_every=0)) as c:
        r = c.post("/v1/onboarding/event", json={"session_id": SESSION, "kind": "download"})
    assert r.status_code == 204 and r.content == b""
    assert rows(db) == []


def test_with_the_brain_off_health_says_so_to_the_page_and_the_event_still_answers(db, monkeypatch):
    # The page sends download counts only after /health answers JSON, so this must answer with no brain, key or model.
    for name in ("HQ_ONBOARDING_ANTHROPIC_KEY", "HQ_ONBOARDING_DAILY_USD"):
        monkeypatch.delenv(name, raising=False)
    with make(db) as c:
        health = c.get("/v1/onboarding/health", headers={"Origin": PAGE})
        r = event(c)
    assert health.status_code == 200 and health.json() == {"ok": False}
    assert health.headers["content-type"] == "application/json"
    assert health.headers["access-control-allow-origin"] == PAGE and health.headers["cache-control"] == "no-store"
    assert r.status_code == 204 and r.headers["access-control-allow-origin"] == PAGE


def test_counts_switch_reads_the_setting(db, monkeypatch):
    monkeypatch.setenv("HQ_ONBOARDING_COUNTS", "on")
    assert Counts(db).on
    monkeypatch.setenv("HQ_ONBOARDING_COUNTS", "0")
    assert not Counts(db).on
    monkeypatch.delenv("HQ_ONBOARDING_COUNTS")
    assert not Counts(db).on


def test_a_download_with_counts_on_keeps_one_row_with_no_text(db, caplog):
    with make(db) as c, caplog.at_level(logging.DEBUG):
        assert event(c).status_code == 204
        assert event(c).status_code == 204                      # the same session downloading twice is one row
        assert event(c, kind="step", step=3).status_code == 204
        assert event(c, kind="step", step=1).status_code == 204  # the furthest step stays
    [row] = rows(db)
    assert row["day"] == "2026-11-01" and row["downloaded"] == 1 and row["step"] == 3
    assert row["turns"] == 0 and row["cost_micro_usd"] == 0
    assert isinstance(row["session_hash"], bytes) and SESSION.encode() not in row["session_hash"]
    dump = "".join(db.conn.iterdump())
    assert SESSION not in dump and "203.0.113.9" not in dump
    assert SESSION not in caplog.text


def test_the_counts_table_has_no_text_column():
    conn = sqlite3.connect(":memory:")
    conn.executescript(onboarding.SCHEMA)
    columns = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(onboarding_sessions)")}
    assert columns == {"session_hash": "BLOB", "day": "TEXT", "step": "INTEGER", "downloaded": "INTEGER",
                       "turns": "INTEGER", "cost_micro_usd": "INTEGER"}
    # The one TEXT column is the day, which the code always writes as YYYY-MM-DD.


def test_a_restart_keeps_the_session_row(tmp_path, day):
    first = Database(tmp_path / "hq.db", clock=lambda: day[0])
    Counts(first, on=True).event(SESSION, "step", 2)
    first.conn.close()
    again = Database(tmp_path / "hq.db", clock=lambda: day[0])
    Counts(again, on=True).event(SESSION, "download")
    [row] = rows(again)
    assert row["step"] == 2 and row["downloaded"] == 1


@pytest.mark.parametrize("body", [
    {"session_id": SESSION, "kind": "download", "extra": "x"},
    {"session_id": SESSION, "kind": "message"},
    {"session_id": SESSION},
    {"kind": "download"},
    {"session_id": "short", "kind": "download"},
    {"session_id": "x" * 65, "kind": "download"},
    {"session_id": "has space in it", "kind": "download"},
    {"session_id": 12345678, "kind": "download"},
    {"session_id": SESSION, "kind": "download", "step": 1},
    {"session_id": SESSION, "kind": "step"},
    {"session_id": SESSION, "kind": "step", "step": 10},
    {"session_id": SESSION, "kind": "step", "step": -1},
    {"session_id": SESSION, "kind": "step", "step": True},
    {"session_id": SESSION, "kind": "step", "step": "2"},
    [SESSION, "download"],
])
def test_bad_input_is_refused_and_keeps_nothing(db, body):
    with make(db) as c:
        r = c.post("/v1/onboarding/event", json=body)
    assert r.status_code == 422 and r.json() == {"error": "invalid"}
    assert rows(db) == []


def test_a_long_or_broken_body_is_refused(db):
    with make(db) as c:
        long = c.post("/v1/onboarding/event", content=b'{"session_id": "' + b"a" * 600 + b'", "kind": "download"}')
        broken = c.post("/v1/onboarding/event", content=b"{not json")
    assert long.status_code == 422 and broken.status_code == 422
    assert rows(db) == []


def test_a_beacon_sent_as_text_plain_is_counted(db):
    # navigator.sendBeacon sends text/plain, which needs no preflight; the body is read the same way.
    with make(db) as c:
        r = c.post("/v1/onboarding/event", content=b'{"session_id": "%s", "kind": "download"}' % SESSION.encode(),
                   headers={"Content-Type": "text/plain;charset=UTF-8", "Origin": PAGE})
    assert r.status_code == 204 and rows(db)[0]["downloaded"] == 1


def test_the_page_origin_gets_its_preflight_and_answers(db):
    with make(db) as c:
        pre = c.options("/v1/onboarding/event", headers={
            "Origin": PAGE, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
        post = event(c)
        health = c.options("/v1/onboarding/health", headers={"Origin": PAGE, "Access-Control-Request-Method": "GET"})
    assert pre.status_code == 204
    assert pre.headers["access-control-allow-origin"] == PAGE
    assert pre.headers["access-control-allow-methods"] == "GET, POST"
    assert pre.headers["access-control-allow-headers"].lower() == "content-type"
    assert pre.headers["vary"] == "Origin"
    assert post.headers["access-control-allow-origin"] == PAGE and post.headers["vary"] == "Origin"
    # The brain's routes share the rule before they exist.
    assert health.headers["access-control-allow-origin"] == PAGE


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    {"Origin": "https://tico.team.evil.example", "Access-Control-Request-Method": "POST"},
    {"Origin": "http://tico.team", "Access-Control-Request-Method": "POST"},
    {"Origin": PAGE, "Access-Control-Request-Method": "DELETE"},
    {"Origin": PAGE, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"},
])
def test_other_origins_methods_and_headers_get_no_allowance(db, headers):
    with make(db) as c:
        pre = c.options("/v1/onboarding/event", headers=headers)
    assert pre.status_code == 204 and "access-control-allow-origin" not in pre.headers


def test_another_origin_gets_no_allowance_on_the_answer_and_other_routes_get_none(db):
    with make(db) as c:
        r = c.post("/v1/onboarding/event", json={"session_id": SESSION, "kind": "download"},
                   headers={"Origin": "https://evil.example"})
        stats = c.get("/v1/staff/onboarding", headers={"Origin": PAGE, "Authorization": f"Bearer {STAFF}"})
    assert r.status_code == 204 and "access-control-allow-origin" not in r.headers
    assert "access-control-allow-origin" not in stats.headers


def test_origins_come_from_the_setting(db, monkeypatch):
    monkeypatch.setenv("HQ_ONBOARDING_ORIGINS", "https://tico.team, https://preview.tico.team/")
    with make(db) as c:
        r = event(c)
        other = c.post("/v1/onboarding/event", json={"session_id": SESSION, "kind": "download"},
                       headers={"Origin": "https://preview.tico.team"})
    assert r.headers["access-control-allow-origin"] == PAGE
    assert other.headers["access-control-allow-origin"] == "https://preview.tico.team"


def test_preflights_are_not_counted_and_the_limit_answer_is_readable(db):
    with make(db, limiter=Limiter(limit=1)) as c:
        for _ in range(3):
            pre = c.options("/v1/onboarding/event", headers={"Origin": PAGE, "Access-Control-Request-Method": "POST"})
            assert pre.status_code == 204
        assert event(c).status_code == 204
        limited = event(c, session="another-session-id")
    assert limited.status_code == 429
    assert limited.headers["access-control-allow-origin"] == PAGE     # so the page can read it as "busy"
    assert len(rows(db)) == 1


def test_new_sessions_past_the_daily_flood_cap_are_not_counted(db, monkeypatch):
    monkeypatch.setattr(onboarding, "NEW_SESSIONS_PER_DAY", 2)
    counts = Counts(db, on=True)
    assert counts.event("session-one", "download") and counts.event("session-two", "download")
    assert not counts.event("session-three", "download")
    assert counts.event("session-one", "step", 4)                     # a session already counted still updates
    assert len(rows(db)) == 2


def test_rows_go_after_13_months(db, day):
    counts = Counts(db, on=True)
    counts.event("old-session", "download")
    day[0] = TODAY + timedelta(days=onboarding.RETENTION_DAYS)
    counts.event("new-session", "download")
    assert counts.purge() == 0
    day[0] = TODAY + timedelta(days=onboarding.RETENTION_DAYS + 1)
    assert counts.purge() == 1
    assert [r["day"] for r in rows(db)] == [(TODAY + timedelta(days=onboarding.RETENTION_DAYS)).isoformat()]


def test_the_daily_purge_also_purges_the_counts(db, day):
    Counts(db, on=True).event("old-session", "download")
    day[0] = TODAY + timedelta(days=onboarding.RETENTION_DAYS + 1)
    with make(db):                                                     # the purge runs once at start
        for _ in range(200):
            if not rows(db):
                break
            time.sleep(0.01)
    assert rows(db) == []


def test_staff_read_the_daily_totals_and_no_one_else_does(db, day):
    counts = Counts(db, on=True)
    counts.event("session-one", "download")
    counts.event("session-two", "step", 1)
    db.conn.execute("UPDATE onboarding_sessions SET turns=3, cost_micro_usd=25000 WHERE downloaded=1")
    day[0] = TODAY + timedelta(days=1)
    counts.event("session-three", "download")
    with make(db) as c:
        assert c.get("/v1/staff/onboarding").status_code == 401
        assert c.get("/v1/staff/onboarding", headers={"Authorization": "Bearer " + "x" * 32}).status_code == 401
        r = c.get("/v1/staff/onboarding?days=3", headers={"Authorization": f"Bearer {STAFF}"})
        bad = [c.get(f"/v1/staff/onboarding?days={d}", headers={"Authorization": f"Bearer {STAFF}"}).status_code
               for d in ("0", "91", "abc", "-1")]
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.json() == {"counting": True, "days": [
        {"day": "2026-10-31", "sessions": 0, "sessions_2plus_turns": 0, "downloads": 0, "cost_usd": 0.0},
        {"day": "2026-11-01", "sessions": 2, "sessions_2plus_turns": 1, "downloads": 1, "cost_usd": 0.025},
        {"day": "2026-11-02", "sessions": 1, "sessions_2plus_turns": 0, "downloads": 1, "cost_usd": 0.0}]}
    assert bad == [422, 422, 422, 422]
    with make(db, staff_key="") as c:
        assert c.get("/v1/staff/onboarding", headers={"Authorization": f"Bearer {STAFF}"}).status_code == 404


def test_the_staff_read_defaults_to_14_days(db):
    with make(db, on=False) as c:
        r = c.get("/v1/staff/onboarding", headers={"Authorization": f"Bearer {STAFF}"})
    assert r.json()["counting"] is False and len(r.json()["days"]) == 14
    assert r.json()["days"][-1]["day"] == TODAY.isoformat()
