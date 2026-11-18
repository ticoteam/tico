"""Scheduled calls (docs/meet.md, Scheduled calls): booking a call writes one calendar event and its link, the prep
sweep follows the calendar and gives each bot one prep task, and the first join starts the call. Google is a fake here
(gcal_live's calls are replaced) and LiveKit is FakeMedia; every name is synthetic (Acme, acme.example)."""

from datetime import datetime, timedelta, timezone

import pytest

from backend import call_prep, calls, gcal_live
from backend.store import H, Problem
from backend.tests.helpers.api import get, headers, post  # noqa: F401
from backend.tests.helpers.calls import join, meet, thread  # noqa: F401
from backend.tests.helpers.live_meetings import live  # noqa: F401


def _iso(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


class Google:
    """gcal_live for one connected mailbox: the events written, by id, and whether reading them fails."""

    def __init__(self, monkeypatch):
        self.connected, self.events, self.writes = set(), {}, []
        self.refuse_writes = self.refuse_reads = False
        monkeypatch.setattr(gcal_live, "connected", lambda c, address: address in self.connected)
        monkeypatch.setattr(gcal_live, "insert", self.insert)
        monkeypatch.setattr(gcal_live, "event", self.event)
        monkeypatch.setattr(gcal_live, "delete", lambda c, a, cal, eid, **k: self.writes.append(("delete", eid)))

    def insert(self, c, as_mailbox, cal, body, *, send_updates="all", event_id=None):
        if self.refuse_writes:
            raise Problem("gcal_refused", "Google Calendar refused this change", 403)
        self.writes.append(("insert", as_mailbox, cal, body, event_id))
        occ = {
            "occurrence_id": f"google:{cal}:{event_id}",
            "event_id": event_id,
            "ical_uid": event_id + "@google.com",
            "start": body["start"],
            "end": body["end"],
        }
        self.events[event_id] = dict(occ)
        return occ

    def event(self, c, as_mailbox, cal, eid, **kw):
        if self.refuse_reads:
            raise Problem("gcal_unavailable", "Google Calendar did not answer; try again", 502, retryable=True)
        if eid not in self.events:
            raise Problem("not_found", "Google Calendar could not find this calendar or event", 404)
        return dict(self.events[eid])

    def move(self, call_id, minutes, length=30):
        self.events[call_id.replace("-", "")].update(start=_iso(minutes), end=_iso(minutes + length))


@pytest.fixture
def google(monkeypatch):
    return Google(monkeypatch)


def schedule(api, cid, minutes=120, length=30, token="ana-test", expected=200, **fields):
    body = {"title": "Acme planning", "starts_at": _iso(minutes), "ends_at": _iso(minutes + length), **fields}
    response = api.post(f"/api/v2/threads/{cid}/calls", json=body, headers=headers(token))
    assert response.status_code == expected, response.text
    return response.json()


def _row(api, call_id):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone())


def _links(api, call_id):
    with api.app.state.store.read() as c:
        return [
            dict(r)
            for r in c.execute("SELECT * FROM calendar_event_links WHERE link_type='call' AND link_id=?", (call_id,))
        ]


def _prep_tasks(api):
    with api.app.state.store.read() as c:
        return [
            (r["title"], r["owner_id"])
            for r in c.execute("SELECT title,owner_id FROM tasks WHERE title LIKE 'Prepare for %' ORDER BY created_at")
        ]


def sweep(api):
    return api.app.state.call_prep.sweep()


def test_scheduling_writes_one_google_event_and_its_link_and_keeps_the_call_off_the_live_lists(meet, google):
    google.connected.add("ana@acme.example")
    cid = thread(meet)
    call = schedule(meet, cid, bots=["ops"])
    assert call["state"] == "scheduled" and call["bots"] == ["ops"] and call["participants"] == []
    assert call["scheduled_start"] and call["scheduled_end"] > call["scheduled_start"]

    [(kind, mailbox, calendar_id, body, event_id)] = google.writes
    assert (kind, mailbox, calendar_id) == ("insert", "ana@acme.example", "ana@acme.example")
    assert event_id == call["id"].replace("-", "") and body["call"] == call["id"]
    assert f"/#/call/{call['id']}" in body["location"] and f"/#/call/{call['id']}" in body["notes"]
    assert {a["email"] for a in body["attendees"]} == {"ana@acme.example", "ben@acme.example"}
    assert (
        gcal_live.google_body({**body, "start": _iso(5), "end": _iso(35)})["extendedProperties"]["private"]["tico_call"]
        == call["id"]
    )
    [link] = _links(meet, call["id"])
    assert (link["google_calendar_id"], link["ical_uid"]) == ("ana@acme.example", event_id + "@google.com")

    # Not live: no ringing, no live call in the thread, no live meeting listed, and a call may start beside it.
    assert get(meet, "calls/ringing", token="ben-test")["calls"] == []
    assert get(meet, "live-meetings")["meetings"] == []
    assert meet.app.state.calls.media.recorders == {}
    started = post(meet, f"threads/{cid}/calls", {"title": "Acme now"})
    assert started["state"] == "live"


def test_a_calendar_that_refuses_the_event_leaves_no_call(meet, google):
    google.connected.add("ana@acme.example")
    google.refuse_writes = True
    cid = thread(meet)
    refused = schedule(meet, cid, expected=403)
    assert refused["error"]["code"] == "calendar_write_failed"
    with meet.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM calendar_event_links").fetchone()[0] == 0


def test_a_failed_write_takes_back_the_google_event(meet, google):
    google.connected.add("ana@acme.example")
    cid = thread(meet)
    schedule(meet, cid, bots=["no-such-bot"], expected=404)
    assert [w[0] for w in google.writes] == ["insert", "delete"]
    with meet.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0


def test_without_google_the_event_goes_on_the_schedulers_tico_calendar(meet, google):
    cid = thread(meet)
    call = schedule(meet, cid)
    assert google.writes == []
    [link] = _links(meet, call["id"])
    with meet.app.state.store.read() as c:
        event = dict(c.execute("SELECT * FROM calendar_events WHERE id=?", (link["tico_event_id"],)).fetchone())
    assert event["title"] == "Acme planning" and f"/#/call/{call['id']}" in event["location"]


def test_the_sweep_preps_each_bot_once_and_at_once_inside_thirty_minutes(meet, google):
    google.connected.add("ana@acme.example")
    cid = thread(meet)
    later = schedule(meet, cid, minutes=120, bots=["ops"])
    soon = schedule(meet, cid, minutes=10, bots=["ops"])
    assert sweep(meet) == {"prepped": 1}
    assert _prep_tasks(meet) == [("Prepare for Acme planning", "bot:ops")]
    assert _row(meet, soon["id"])["prepped_at"] and not _row(meet, later["id"])["prepped_at"]
    with meet.app.state.store.read() as c:
        task = c.execute("SELECT id FROM tasks WHERE title LIKE 'Prepare for %'").fetchone()
        assert call_prep.SKILL in H.task_words(c, task["id"])

    assert sweep(meet) == {}  # prepped: nothing more, however often the sweep runs
    assert len(_prep_tasks(meet)) == 1


def test_the_sweep_follows_a_moved_event_and_cancels_with_a_cancelled_one(meet, google):
    google.connected.add("ana@acme.example")
    cid = thread(meet)
    moved = schedule(meet, cid, minutes=20, bots=["ops"])
    gone = schedule(meet, cid, minutes=25, bots=["ops"])
    google.move(moved["id"], 180)
    del google.events[gone["id"].replace("-", "")]
    assert sweep(meet) == {"moved": 1, "cancelled": 1}
    row = _row(meet, moved["id"])
    assert row["state"] == "scheduled" and not row["prepped_at"]
    assert row["scheduled_start"][:16] == _iso(180)[:16]
    ended = _row(meet, gone["id"])
    assert (ended["state"], ended["end_reason"]) == ("ended", "calendar_cancelled") and ended["finalized_at"]
    assert _prep_tasks(meet) == []
    assert get(meet, "calls/upcoming")["calls"][0]["id"] == moved["id"]


def test_an_unreadable_event_is_read_again_then_the_call_preps_anyway(meet, google):
    google.connected.add("ana@acme.example")
    cid = thread(meet)
    call = schedule(meet, cid, minutes=10, bots=["ops"])
    google.refuse_reads = True
    assert sweep(meet) == {"waiting": 1}
    assert sweep(meet) == {"waiting": 1}
    assert sweep(meet) == {"prepped": 1}
    assert _row(meet, call["id"])["prepped_at"]


def test_a_call_nobody_joined_ends_missed_after_its_end(meet, google):
    cid = thread(meet)
    call = schedule(meet, cid, minutes=10)
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with meet.app.state.store.transaction() as c:
        event_id = c.execute(
            "SELECT tico_event_id FROM calendar_event_links WHERE link_id=?", (call["id"],)
        ).fetchone()[0]
        c.execute("UPDATE calendar_events SET starts_at=?,ends_at=? WHERE id=?", (stale, stale, event_id))
    assert sweep(meet) == {"missed": 1}
    assert _row(meet, call["id"])["end_reason"] == "missed"


def test_the_first_join_starts_a_scheduled_call_with_its_bots(meet, google):
    cid = thread(meet)
    call = schedule(meet, cid, bots=["ops"])
    assert [c["id"] for c in get(meet, "calls/upcoming", token="ben-test")["calls"]] == [call["id"]]
    joined = join(meet, call["id"], "ben-test")
    assert joined["call"]["state"] == "live" and joined["token"]
    assert joined["call"]["bots"] == ["ops"]
    assert call["id"] in meet.app.state.calls.media.recorders
    [ringing] = get(meet, "calls/ringing")["calls"]  # Ben started it: it rings Ana
    assert ringing["id"] == call["id"]
    assert get(meet, "calls/upcoming")["calls"] == []
    again = join(meet, call["id"], "ana-test")
    assert again["call"]["state"] == "live"
    with meet.app.state.store.read() as c:
        assert c.execute("SELECT state FROM live_meetings WHERE id=?", (call["meeting_id"],)).fetchone()[0] == "live"


def test_a_scheduled_call_waits_while_another_is_live_and_its_end_cancels_it(meet, google):
    cid = thread(meet)
    call = schedule(meet, cid)
    live_call = post(meet, f"threads/{cid}/calls", {"title": "Acme now"})
    response = meet.post(f"/api/v2/calls/{call['id']}/join", json={}, headers=headers("ana-test"))
    assert response.status_code == 409 and response.json()["error"]["code"] == "call_in_progress"
    assert _row(meet, call["id"])["state"] == "scheduled"
    post(meet, f"calls/{live_call['id']}/end", {})
    ended = post(meet, f"calls/{call['id']}/end", {})
    assert (ended["state"], ended["end_reason"]) == ("ended", "cancelled")


def test_a_booking_needs_both_times_still_to_come(meet, google):
    cid = thread(meet)
    for body in (
        {"starts_at": _iso(60)},
        {"starts_at": _iso(-60), "ends_at": _iso(-30)},
        {"starts_at": _iso(60), "ends_at": _iso(30)},
        {"starts_at": _iso(60), "ends_at": _iso(60 + 9 * 60)},
        {"starts_at": "soon", "ends_at": _iso(60)},
        {"starts_at": "2026-12-01T10:00:00", "ends_at": _iso(60)},
    ):
        response = meet.post(
            f"/api/v2/threads/{cid}/calls", json={"title": "Acme", **body}, headers=headers("ana-test")
        )
        assert response.status_code == 422, body
    assert calls.booking(None, None) is None


def test_the_declared_answers_match_a_scheduled_call(meet, google):
    from backend.tests.helpers.openapi_v2 import conforms

    document = meet.get("/api/v2/openapi.json", headers=headers("ana-test")).json()
    by_id = {op["operationId"]: op for ops in document["paths"].values() for op in ops.values()}

    def conforming(operation, answer):
        schema = by_id[operation]["responses"]["200"]["content"]["application/json"]["schema"]
        assert conforms(answer, schema, document) is None, (operation, str(answer)[:300])

    cid = thread(meet)
    call = schedule(meet, cid, bots=["ops"])
    conforming("startCall", call)
    conforming("listUpcomingCalls", get(meet, "calls/upcoming"))
    conforming("getCall", get(meet, f"calls/{call['id']}"))
    conforming("listCalls", get(meet, f"threads/{cid}/calls"))
