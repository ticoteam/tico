"""The setup page's counts on HQ: they work with no brain and no key, keep nothing until switched on, keep no text or
anything derived from a key when on, survive a restart, answer tico.team's browser (and only it), and add up to the
main measure."""

import hashlib
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
# Invented, key-shaped strings: never real credentials.
KEY_SHAPED = ["sk-" + "a1b2c3d4e5" * 4, "ghp_" + "Z9y8X7w6V5" * 3, "AKIA" + "ABCDEFGHIJ234567", "q7" * 20,
              "xoxb-123456-abcdef"]


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


def event(client, session=SESSION, origin=PAGE, **body):
    return client.post("/v1/onboarding/event", json={"session_id": session, "kind": "download", **body},
                       headers={"Origin": origin})


def rows(db):
    return [dict(r) for r in db.conn.execute("SELECT * FROM onboarding_sessions")]


def test_with_the_brain_off_health_and_event_answer_and_counts_off_keeps_nothing(db, monkeypatch):
    # No model key, no cap, no brain: neither route needs them. The page sends counts only after /health answers JSON.
    for name in ("HQ_ONBOARDING_ANTHROPIC_KEY", "HQ_ONBOARDING_DAILY_USD", "HQ_ONBOARDING_COUNTS"):
        monkeypatch.delenv(name, raising=False)
    latest = Latest(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with TestClient(create_app(db, latest, watch_every=0)) as c:
        health = c.get("/v1/onboarding/health", headers={"Origin": PAGE})
        r = event(c)
    assert health.status_code == 200 and health.json() == {"ok": False}
    assert health.headers["access-control-allow-origin"] == PAGE and health.headers["cache-control"] == "no-store"
    assert r.status_code == 204 and r.content == b"" and r.headers["access-control-allow-origin"] == PAGE
    assert rows(db) == []


def test_counts_on_keep_one_row_of_numbers_and_no_text(db, caplog):
    with make(db) as c, caplog.at_level(logging.DEBUG):
        for body in ({}, {}, {"kind": "step", "step": 3}, {"kind": "step", "step": 1}, {"kind": "message"},
                     {"kind": "message"}):
            assert event(c, **body).status_code == 204
    [row] = rows(db)
    assert {k: row[k] for k in ("day", "downloaded", "step", "messages", "turns", "cost_micro_usd")} == \
        {"day": "2026-11-01", "downloaded": 1, "step": 3, "messages": 2, "turns": 0, "cost_micro_usd": 0}
    dump = "".join(db.conn.iterdump())
    assert SESSION not in dump and "203.0.113.9" not in dump and SESSION not in caplog.text
    # The table has no column a message, an address or the session id could go in; the one TEXT column is the day.
    conn = sqlite3.connect(":memory:")
    conn.executescript(onboarding.SCHEMA)
    assert {r[1]: r[2] for r in conn.execute("PRAGMA table_info(onboarding_sessions)")} == {
        "session_hash": "BLOB", "day": "TEXT", "step": "INTEGER", "downloaded": "INTEGER", "messages": "INTEGER",
        "turns": "INTEGER", "cost_micro_usd": "INTEGER"}


def test_a_key_shaped_session_id_is_refused_before_it_is_hashed(db, caplog, monkeypatch):
    hashed = []
    real = hashlib.blake2b
    monkeypatch.setattr(onboarding.hashlib, "blake2b", lambda data, **kw: hashed.append(data) or real(data, **kw))
    counts = Counts(db, on=True)
    with make(db) as c, caplog.at_level(logging.DEBUG):
        for value in KEY_SHAPED:
            for body in ({}, {"kind": "message"}, {"kind": "step", "step": 2}):
                r = event(c, session=value, **body)
                assert r.status_code == 422 and value not in r.text
            assert counts.event(value, "download") is False           # a direct write is refused as well
            with pytest.raises(ValueError):
                counts.key(value)
        assert event(c).status_code == 204                            # an ordinary UUID still counts
    assert all(value.encode() not in hashed for value in KEY_SHAPED)
    assert len(rows(db)) == 1 and all(value not in caplog.text for value in KEY_SHAPED)


def test_a_restart_keeps_the_session_row(tmp_path, day):
    first = Database(tmp_path / "hq.db", clock=lambda: day[0])
    Counts(first, on=True).event(SESSION, "step", 2)
    first.conn.close()
    again = Database(tmp_path / "hq.db", clock=lambda: day[0])
    Counts(again, on=True).event(SESSION, "download")
    [row] = rows(again)
    assert row["step"] == 2 and row["downloaded"] == 1


def test_bad_input_is_refused_and_keeps_nothing(db):
    bodies = [{"session_id": SESSION, "kind": "message", "text": "hello"}, {"session_id": SESSION, "kind": "turn"},
              {"session_id": "short", "kind": "download"}, {"session_id": SESSION, "kind": "download", "step": 1},
              {"session_id": SESSION, "kind": "step", "step": 10}, {"session_id": SESSION, "kind": "step", "step": True},
              [SESSION, "download"]]
    with make(db) as c:
        answers = [c.post("/v1/onboarding/event", json=body) for body in bodies]
        answers.append(c.post("/v1/onboarding/event", content=b'{"session_id": "' + b"a" * 600 + b'", "kind": "download"}'))
        beacon = c.post("/v1/onboarding/event", content=('{"session_id": "%s", "kind": "download"}' % SESSION).encode(),
                        headers={"Content-Type": "text/plain;charset=UTF-8"})
    assert [(a.status_code, a.json()) for a in answers] == [(422, {"error": "invalid"})] * len(answers)
    assert beacon.status_code == 204 and len(rows(db)) == 1          # a sendBeacon body is read the same way


def test_only_the_page_origin_gets_cors_and_preflights_skip_the_limit(db):
    with make(db, limiter=Limiter(limit=1)) as c:
        pre = [c.options("/v1/onboarding/event", headers={"Origin": PAGE, "Access-Control-Request-Method": "POST",
                                                          "Access-Control-Request-Headers": "content-type"})
               for _ in range(3)]
        refused = [c.options("/v1/onboarding/event", headers=h) for h in (
            {"Origin": "https://tico.team.evil.example", "Access-Control-Request-Method": "POST"},
            {"Origin": "http://tico.team", "Access-Control-Request-Method": "POST"},
            {"Origin": PAGE, "Access-Control-Request-Method": "DELETE"},
            {"Origin": PAGE, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"})]
        other = event(c, origin="https://evil.example")
        assert c.get("/v1/onboarding/health", headers={"Origin": PAGE}).status_code == 200   # the shared limit's one
        limited = c.get("/v1/onboarding/health", headers={"Origin": PAGE})
        staff = c.get("/v1/staff/onboarding", headers={"Origin": PAGE, "Authorization": f"Bearer {STAFF}"})
    assert all(p.status_code == 204 and p.headers["access-control-allow-origin"] == PAGE for p in pre)
    assert pre[0].headers["access-control-allow-methods"] == "GET, POST" and pre[0].headers["vary"] == "Origin"
    assert all(r.status_code == 204 and "access-control-allow-origin" not in r.headers for r in refused)
    assert other.status_code == 204 and "access-control-allow-origin" not in other.headers
    assert limited.status_code == 429 and limited.headers["access-control-allow-origin"] == PAGE   # readable as busy
    assert "access-control-allow-origin" not in staff.headers


def test_events_have_their_own_budget_so_a_long_chat_still_counts_its_download(db, monkeypatch):
    monkeypatch.setattr(onboarding, "EVENTS_PER_HOUR", 8)
    with make(db, limiter=Limiter(limit=2)) as c:                     # HQ's shared limit: health takes one of two
        assert c.get("/v1/onboarding/health").status_code == 200
        answers = [event(c, kind="message").status_code for _ in range(7)] + [event(c).status_code]
        answers.append(event(c).status_code)                          # past the events' own limit
    assert answers == [204] * 8 + [429]
    [row] = rows(db)
    assert row["messages"] == 7 and row["downloaded"] == 1


def test_new_sessions_past_the_daily_flood_cap_are_not_counted(db, monkeypatch):
    monkeypatch.setattr(onboarding, "NEW_SESSIONS_PER_DAY", 2)
    counts = Counts(db, on=True)
    assert counts.event("session-one", "download") and counts.event("session-two", "download")
    assert not counts.event("session-three", "download")
    assert counts.event("session-one", "step", 4)                     # a session already counted still updates
    assert len(rows(db)) == 2


def test_rows_go_after_13_months_and_the_daily_purge_runs_at_start(db, day):
    counts = Counts(db, on=True)
    counts.event("old-session", "download")
    day[0] = TODAY + timedelta(days=onboarding.RETENTION_DAYS)
    assert counts.purge() == 0
    day[0] = TODAY + timedelta(days=onboarding.RETENTION_DAYS + 1)
    with make(db):
        for _ in range(200):
            if not rows(db):
                break
            time.sleep(0.01)
    assert rows(db) == []


def test_staff_totals_give_the_main_measure_and_only_staff_read_them(db, day):
    def daily(sessions):
        counts = Counts(db, on=True)
        db.conn.execute("DELETE FROM onboarding_sessions")
        for name, messages, downloaded, turns in sessions:
            for _ in range(messages):
                counts.event(name, "message")
            if downloaded:
                counts.event(name, "download")
            db.conn.execute("UPDATE onboarding_sessions SET turns=?, cost_micro_usd=? WHERE session_hash=?",
                            (turns, 10_000 * turns, counts.key(name)))
        return counts.daily(1)[0]

    # The same marginal totals (2 sessions, one with 2+ messages, one download), different overlap.
    overlap = daily([("talked-and-downloaded", 2, True, 0), ("quiet-session", 1, False, 5)])
    apart = daily([("talked-only", 2, False, 0), ("quiet-downloader", 1, True, 5)])
    assert (overlap["sessions"], overlap["sessions_2plus_messages"], overlap["downloads"]) == \
        (apart["sessions"], apart["sessions_2plus_messages"], apart["downloads"]) == (2, 1, 1)
    assert overlap["sessions_2plus_messages_downloaded"] == 1 and apart["sessions_2plus_messages_downloaded"] == 0
    # Brain turns do not stand in for visitor messages: five turns and one message is not "2 or more messages".
    assert apart["cost_usd"] == 0.05

    with make(db) as c:
        assert c.get("/v1/staff/onboarding").status_code == 401
        r = c.get("/v1/staff/onboarding?days=2", headers={"Authorization": f"Bearer {STAFF}"})
        bad = [c.get(f"/v1/staff/onboarding?days={d}", headers={"Authorization": f"Bearer {STAFF}"}).status_code
               for d in ("0", "91", "abc")]
        default = c.get("/v1/staff/onboarding", headers={"Authorization": f"Bearer {STAFF}"}).json()
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.json() == {"counting": True, "days": [
        {"day": "2026-10-31", "sessions": 0, "sessions_2plus_messages": 0, "sessions_2plus_messages_downloaded": 0,
         "downloads": 0, "cost_usd": 0.0},
        {"day": "2026-11-01", **{k: apart[k] for k in apart if k != "day"}}]}
    assert bad == [422, 422, 422] and len(default["days"]) == 14
    with make(db, staff_key="") as c:
        assert c.get("/v1/staff/onboarding", headers={"Authorization": f"Bearer {STAFF}"}).status_code == 404
