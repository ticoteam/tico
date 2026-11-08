"""Revision 8 provenance: disposable synthetic meetings; no bot jobs or provider calls."""
import json
import sqlite3

import pytest

from backend import hubdb as H
from backend.tests.test_api import get, post, headers
from backend.tests.test_live_meetings import api, live, chunk  # noqa: F401


def test_client_validation_and_legacy_idempotency(live):
    old = post(live, "live-meetings", {"client_id": "legacy"})
    assert old["client"] is None and old["visibility"] == "team"
    replay = post(live, "live-meetings", {"client_id": "legacy", "client": "tico-recorder"})
    assert replay["id"] == old["id"] and replay["client"] is None
    post(live, "live-meetings", {"client": "something-else"}, expected=422)
    assert post(live, "live-meetings", {"client": None})["client"] is None


def test_recorder_private_pause_end_finalize_approve_and_count(live):
    body = {"client_id": "recorder", "client": "tico-recorder", "visibility": "private"}
    meeting = post(live, "live-meetings", body)
    mid = meeting["id"]
    assert post(live, "live-meetings", body)["id"] == mid
    assert get(live, f"live-meetings/{mid}")["client"] == "tico-recorder"
    assert get(live, "live-meetings")["meetings"][0]["client"] == "tico-recorder"
    assert mid not in {m["id"] for m in get(live, "live-meetings", token="ben-test")["meetings"]}
    get(live, f"live-meetings/{mid}", token="ben-test", expected=404)
    chunk(live, mid, 1, 0, 1000, "Synthetic transcript without bots")
    post(live, f"live-meetings/{mid}/control", {"action": "pause"})
    post(live, f"live-meetings/{mid}/control", {"action": "end"})
    filed = post(live, f"live-meetings/{mid}/finalize", {})["meeting_id"]
    assert post(live, f"live-meetings/{mid}/finalize", {})["meeting_id"] == filed
    post(live, f"meetings/{filed}/review", {"action": "approve", "private": True})
    assert live.get(f"/api/meetings/{filed}", headers=headers()).json()["client"] == "tico-recorder"
    assert live.get(f"/api/meetings/{filed}", headers=headers("ben-test")).status_code == 403
    with live.app.state.store.read() as c:
        row = c.execute("SELECT review_state,metadata FROM meetings WHERE id=?", (filed,)).fetchone()
        assert row["review_state"] == "live" and json.loads(row["metadata"])["private"]
        assert json.loads(row["metadata"])["client"] == "tico-recorder"
        assert not c.execute("SELECT 1 FROM live_meeting_bots WHERE meeting_id=?", (mid,)).fetchone()
        query = """SELECT client, COUNT(*) AS shared, SUM(EXISTS(
            SELECT 1 FROM live_meeting_events e WHERE e.meeting_id=m.id
            AND e.type='meeting.bot_reply')) AS replied
            FROM live_meetings m GROUP BY client"""
        assert tuple(c.execute(query).fetchone()) == ("tico-recorder", 1, 0)
    # A synthetic event proves the reporting predicate; this never attaches or runs a bot.
    with live.app.state.store.transaction() as c:
        c.execute("INSERT INTO live_meeting_events(meeting_id,event_id,type,payload,created_at) VALUES(?,?,?,?,?)",
                  (mid, 100, "meeting.bot_reply", "{}", H.now()))
        assert tuple(c.execute(query).fetchone()) == ("tico-recorder", 1, 1)


def test_existing_meetings_upgrade_to_null_client():
    with sqlite3.connect(":memory:") as c:
        c.execute("CREATE TABLE live_meetings(id TEXT PRIMARY KEY)")
        c.execute("INSERT INTO live_meetings VALUES('old')")
        c.executescript(H.LIVE_MEETINGS_CLIENT_SCHEMA)
        assert c.execute("SELECT client FROM live_meetings").fetchone() == (None,)
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE live_meetings SET client='unknown'")
