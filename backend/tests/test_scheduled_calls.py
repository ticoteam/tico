"""Scheduled calls (docs/meet.md, Scheduled calls): booking a call writes one calendar event and its link, the call
follows that event (the sweep, the watcher, the first join), a change or cancel made in Tico is written to the
calendar first, and each bot gets one prep task. Google is a fake here (gcal_live's calls are replaced) and LiveKit is
FakeMedia; every name is synthetic (Acme, acme.example)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend import calendar_api, calendar_store, call_prep, calls, gcal_live, gcal_watch
from backend.store import H, Problem
from backend.tests.helpers.api import get, headers, post  # noqa: F401
from backend.tests.helpers.calls import join, meet, thread  # noqa: F401
from backend.tests.helpers.live_meetings import live  # noqa: F401

ANA = "ana@acme.example"


def _iso(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).replace(second=0, microsecond=0).isoformat()


def _eid(call_id):
    return call_id.replace("-", "")


class Google:
    """gcal_live for one connected mailbox: the events written, by id, and whether reading or writing them fails."""

    def __init__(self, monkeypatch):
        self.connected, self.events, self.writes = set(), {}, []
        self.refuse_writes = self.refuse_reads = self.lose_answer = False
        self.delete_fails = None  # a Problem the next deletes raise
        monkeypatch.setattr(gcal_live, "connected", lambda c, address: address in self.connected)
        monkeypatch.setattr(gcal_live, "insert", self.insert)
        monkeypatch.setattr(gcal_live, "event", self.event)
        monkeypatch.setattr(gcal_live, "own_event", self.own_event)
        monkeypatch.setattr(gcal_live, "patch", self.patch)
        monkeypatch.setattr(gcal_live, "delete", self.delete)

    def insert(self, c, as_mailbox, cal, body, *, send_updates="all", event_id=None):
        if self.refuse_writes:
            raise Problem("gcal_refused", "Google Calendar refused this change", 403)
        self.writes.append(("insert", as_mailbox, cal, body, event_id))
        if event_id in self.events:
            raise Problem("conflict", "Google Calendar already has this event", 409)
        attendees = [{"email": a["email"], "response": a.get("response", "needs_action")} for a in body["attendees"]]
        self.events[event_id] = {
            "occurrence_id": f"google:{cal}:{event_id}",
            "event_id": event_id,
            "ical_uid": event_id + "@google.com",
            "start": body["start"],
            "end": body["end"],
            "all_day": False,
            "attendees": attendees,
            "google_sequence": 0,
            "tico_call": body.get("call"),
        }
        if self.lose_answer:
            raise Problem("gcal_unavailable", "Google Calendar did not answer; try again", 502, retryable=True)
        return self._shown(event_id)

    def _shown(self, eid):
        return {k: v for k, v in self.events[eid].items() if k != "tico_call"}

    def event(self, c, as_mailbox, cal, eid, **kw):
        if self.refuse_reads:
            raise Problem("gcal_unavailable", "Google Calendar did not answer; try again", 502, retryable=True)
        if eid not in self.events:
            raise Problem("not_found", "Google Calendar could not find this calendar or event", 404)
        return self._shown(eid)

    def own_event(self, c, as_mailbox, cal, eid):
        return dict(self.events[eid]) if eid in self.events else None

    def patch(self, c, as_mailbox, cal, eid, body, *, send_updates="all"):
        if self.refuse_writes:
            raise Problem("gcal_refused", "Google Calendar refused this change", 403)
        self.writes.append(("patch", eid, body, send_updates))
        event = self.events[eid]
        event.update(start=body["start"], end=body["end"], google_sequence=event["google_sequence"] + 1)
        if "attendees" in body:
            event["attendees"] = [{"email": a["email"], "response": a.get("response")} for a in body["attendees"]]
        return self._shown(eid)

    def delete(self, c, as_mailbox, cal, eid, *, send_updates="all"):
        if self.delete_fails is not None:
            raise self.delete_fails
        self.writes.append(("delete", eid, send_updates))
        self.events.pop(eid, None)

    def move(self, call_id, minutes, length=30):
        self.events[_eid(call_id)].update(start=_iso(minutes), end=_iso(minutes + length))


@pytest.fixture
def google(monkeypatch):
    return Google(monkeypatch)


def schedule(api, cid, minutes=120, length=30, token="ana-test", expected=200, key=None, **fields):
    body = {"title": "Acme planning", "starts_at": _iso(minutes), "ends_at": _iso(minutes + length), **fields}
    response = api.post(f"/api/v2/threads/{cid}/calls", json=body, headers=headers(token, key))
    assert response.status_code == expected, response.text
    return response.json()


def change(api, call_id, minutes, length=30, token="ana-test", expected=200):
    body = {"starts_at": _iso(minutes), "ends_at": _iso(minutes + length)}
    response = api.patch(f"/api/v2/calls/{call_id}", json=body, headers=headers(token))
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


def _tico_event(api, call_id):
    [link] = _links(api, call_id)
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM calendar_events WHERE id=?", (link["tico_event_id"],)).fetchone())


def _prep_tasks(api):
    with api.app.state.store.read() as c:
        return [
            (r["title"], r["owner_id"], r["private"])
            for r in c.execute(
                "SELECT title,owner_id,private FROM tasks WHERE title LIKE 'Prepare for %' ORDER BY created_at"
            )
        ]


def sweep(api):
    return api.app.state.call_prep.sweep()


def _ops(api):
    with api.app.state.store.read() as c:
        return "bot:" + H.resolve_bot(c, "ops")["id"]


def test_scheduling_writes_one_private_google_event_and_its_link_and_keeps_the_call_off_the_live_lists(meet, google):
    google.connected.add(ANA)
    cid = thread(meet)
    call = schedule(meet, cid, bots=["ops"])
    assert call["state"] == "scheduled" and call["bots"] == ["ops"] and call["participants"] == []
    assert call["scheduled_start"] and call["scheduled_end"] > call["scheduled_start"]

    [(kind, mailbox, calendar_id, body, event_id)] = google.writes
    assert (kind, mailbox, calendar_id) == ("insert", ANA, ANA)
    assert event_id == _eid(call["id"]) and body["call"] == call["id"] and body["visibility"] == "private"
    assert f"/#/call/{call['id']}" in body["location"] and f"/#/call/{call['id']}" in body["notes"]
    assert {a["email"] for a in body["attendees"]} == {ANA, "ben@acme.example"}
    google_body = gcal_live.google_body({**body, "start": _iso(5), "end": _iso(35)})
    assert google_body["extendedProperties"]["private"]["tico_call"] == call["id"]
    assert google_body["visibility"] == "private"
    [link] = _links(meet, call["id"])
    assert (link["google_calendar_id"], link["ical_uid"]) == (ANA, event_id + "@google.com")

    # Not live: no ringing, no live call in the thread, no live meeting listed, and a call may start beside it.
    assert get(meet, "calls/ringing", token="ben-test")["calls"] == []
    assert get(meet, "live-meetings")["meetings"] == []
    assert meet.app.state.calls.media.recorders == {}
    started = post(meet, f"threads/{cid}/calls", {"title": "Acme now"})
    assert started["state"] == "live"


def test_only_people_who_can_read_the_thread_are_invited(meet, google, monkeypatch):
    google.connected.add(ANA)
    cid = thread(meet)
    reads = calls.readable
    monkeypatch.setattr(calls, "readable", lambda c, actor, tid: actor != "human:ben" and reads(c, actor, tid))
    schedule(meet, cid)
    [(_, _, _, body, _)] = google.writes
    assert [a["email"] for a in body["attendees"]] == [ANA]


def test_a_calendar_that_refuses_the_event_leaves_no_call(meet, google):
    google.connected.add(ANA)
    google.refuse_writes = True
    cid = thread(meet)
    refused = schedule(meet, cid, expected=403)
    assert refused["error"]["code"] == "calendar_write_failed"
    with meet.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM calendar_event_links").fetchone()[0] == 0


def test_a_bot_the_booker_cannot_add_is_refused_before_google_is_written(meet, google):
    google.connected.add(ANA)
    cid = thread(meet)
    schedule(meet, cid, bots=["no-such-bot"], expected=404)
    assert google.writes == []


def test_a_failed_write_takes_back_the_google_event(meet, google, monkeypatch):
    google.connected.add(ANA)
    cid = thread(meet)

    def refuse(*args):
        raise Problem("calendar_write_failed", "The store refused", 502)

    monkeypatch.setattr(calendar_api, "record_call_event", refuse)
    schedule(meet, cid, expected=502)
    assert [w[0] for w in google.writes] == ["insert", "delete"]
    with meet.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0


def test_a_retried_booking_makes_no_second_event(meet, google):
    google.connected.add(ANA)
    cid = thread(meet)
    # The first try's answer is lost after Google wrote the event: the event is read back by its id and taken.
    google.lose_answer = True
    call = schedule(meet, cid, key="acme-booking-1")
    assert len(google.events) == 1 and _links(meet, call["id"])

    # A first try that stopped before its call was stored: the retry's insert finds the event (409) and takes it.
    google.lose_answer = False
    who = SimpleNamespace(actor="human:ana")
    call_id = calls.Calls.schedule_id(who, cid, "acme-booking-2")
    google.events[_eid(call_id)] = {**google.events[_eid(call["id"])], "tico_call": call_id}
    again = schedule(meet, cid, key="acme-booking-2")
    assert again["id"] == call_id and len(google.events) == 2
    assert [w[0] for w in google.writes] == ["insert", "insert"]

    # An event with that id that is not this call's is never taken.
    other = calls.Calls.schedule_id(who, cid, "acme-booking-3")
    google.events[_eid(other)] = {**google.events[_eid(call["id"])], "tico_call": "another-call"}
    assert schedule(meet, cid, key="acme-booking-3", expected=409)["error"]["code"] == "calendar_write_failed"


def test_a_retry_is_answered_from_its_receipt_after_its_time_has_passed(meet, google, monkeypatch):
    cid = thread(meet)
    body = {"title": "Acme planning", "starts_at": _iso(1), "ends_at": _iso(31)}
    first = meet.post(f"/api/v2/threads/{cid}/calls", json=body, headers=headers(key="acme-retry"))
    assert first.status_code == 200

    def too_late(*args):
        raise Problem("validation", "A call is scheduled for a time still to come", 422)

    monkeypatch.setattr(calls, "booking", too_late)
    again = meet.post(f"/api/v2/threads/{cid}/calls", json=body, headers=headers(key="acme-retry"))
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]


def test_a_call_started_now_hashes_as_it_did_before_booking_existed():
    assert calls.StartCall(title="Acme").model_dump() == {
        "title": "Acme",
        "speak_replies": None,
        "provider": "auto",
        "bots": [],
    }
    booked = calls.StartCall(title="Acme", starts_at=_iso(60), ends_at=_iso(90)).model_dump()
    assert booked["starts_at"] and booked["ends_at"]


def test_without_google_the_private_event_goes_on_the_bookers_tico_calendar(meet, google):
    cid = thread(meet)
    call = schedule(meet, cid)
    assert google.writes == []
    event = _tico_event(meet, call["id"])
    assert event["title"] == "Acme planning" and f"/#/call/{call['id']}" in event["location"]
    assert event["visibility"] == "private"


def test_the_sweep_preps_the_exact_bot_once_and_at_once_inside_thirty_minutes(meet, google):
    google.connected.add(ANA)
    cid = thread(meet)
    later = schedule(meet, cid, minutes=120, bots=["ops"])
    soon = schedule(meet, cid, minutes=10, bots=["ops"])
    assert sweep(meet) == {"prepped": 1}
    # The thread is a private one (a chat between Ana, Ben and Ops): the task is private too.
    assert _prep_tasks(meet) == [("Prepare for Acme planning", _ops(meet), 1)]
    assert _row(meet, soon["id"])["prepped_at"] and not _row(meet, later["id"])["prepped_at"]
    with meet.app.state.store.read() as c:
        task = c.execute("SELECT id FROM tasks WHERE title LIKE 'Prepare for %'").fetchone()
        assert call_prep.SKILL in H.task_words(c, task["id"])

    assert sweep(meet) == {}  # prepped: nothing more, however often the sweep runs
    assert len(_prep_tasks(meet)) == 1


def test_a_company_threads_prep_task_is_public(meet, google):
    made = post(meet, "threads", {"title": "Acme all hands", "audience": "company"})
    schedule(meet, made["id"], minutes=10, bots=["ops"])
    assert sweep(meet) == {"prepped": 1}
    assert _prep_tasks(meet) == [("Prepare for Acme planning", _ops(meet), 0)]


def test_a_booker_who_cannot_read_the_thread_any_more_gives_no_prep(meet, google, monkeypatch):
    cid = thread(meet)
    call = schedule(meet, cid, minutes=10, bots=["ops"])
    reads = calls.readable
    monkeypatch.setattr(calls, "readable", lambda c, actor, tid: actor != "human:ana" and reads(c, actor, tid))
    assert sweep(meet) == {"prepped": 1}
    assert _prep_tasks(meet) == [] and _row(meet, call["id"])["prepped_at"]


def test_the_sweep_follows_a_moved_event_and_cancels_with_a_cancelled_one(meet, google):
    google.connected.add(ANA)
    cid = thread(meet)
    moved = schedule(meet, cid, minutes=20, bots=["ops"])
    gone = schedule(meet, cid, minutes=25, bots=["ops"])
    google.move(moved["id"], 180)
    del google.events[_eid(gone["id"])]
    assert sweep(meet) == {"moved": 1, "cancelled": 1}
    row = _row(meet, moved["id"])
    assert row["state"] == "scheduled" and not row["prepped_at"]
    assert row["scheduled_start"][:16] == _iso(180)[:16]
    ended = _row(meet, gone["id"])
    assert (ended["state"], ended["end_reason"]) == ("ended", "calendar_cancelled") and ended["finalized_at"]
    assert _prep_tasks(meet) == []
    assert get(meet, "calls/upcoming")["calls"][0]["id"] == moved["id"]


def test_a_move_far_out_clears_the_prep_so_the_bots_prep_again(meet, google):
    google.connected.add(ANA)
    cid = thread(meet)
    call = schedule(meet, cid, minutes=10, bots=["ops"])
    assert sweep(meet) == {"prepped": 1}
    google.move(call["id"], 180)
    assert meet.app.state.call_prep.one(call["id"]) == "moved"
    assert not _row(meet, call["id"])["prepped_at"]
    with meet.app.state.store.transaction() as c:  # the first prep is closed; an open one would still stand
        first = c.execute("SELECT id FROM tasks WHERE title LIKE 'Prepare for %'").fetchone()["id"]
        H.task_close(c, "human:ana", first, "Prepped for the old time.")
    google.move(call["id"], 20)  # as the watcher hears it: moved back inside the lead, it preps at once
    assert meet.app.state.call_prep.one(call["id"]) == "prepped"
    assert len(_prep_tasks(meet)) == 2


def test_an_event_made_all_day_is_not_followed(meet, google):
    google.connected.add(ANA)
    cid = thread(meet)
    call = schedule(meet, cid, minutes=10)
    before = _row(meet, call["id"])
    google.events[_eid(call["id"])].update(all_day=True, start=_iso(60 * 24), end=_iso(60 * 48))
    assert sweep(meet) == {"prepped": 1}
    after = _row(meet, call["id"])
    assert (after["scheduled_start"], after["scheduled_end"]) == (before["scheduled_start"], before["scheduled_end"])


def test_an_unreadable_event_is_read_again_then_the_call_preps_but_never_ends_missed(meet, google):
    google.connected.add(ANA)
    cid = thread(meet)
    call = schedule(meet, cid, minutes=10, bots=["ops"])
    google.refuse_reads = True
    assert sweep(meet) == {"waiting": 1}
    assert sweep(meet) == {"waiting": 1}
    assert sweep(meet) == {"prepped": 1}
    assert _row(meet, call["id"])["prepped_at"]

    stale = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with meet.app.state.store.transaction() as c:
        c.execute("UPDATE calls SET scheduled_start=?,scheduled_end=? WHERE id=?", (stale, stale, call["id"]))
    for _ in range(call_prep.READ_TRIES + 1):
        sweep(meet)
    assert _row(meet, call["id"])["state"] == "scheduled"


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


def test_the_sweep_stops_at_its_budget_and_the_rest_wait_for_the_next_pass(meet, google, monkeypatch):
    cid = thread(meet)
    schedule(meet, cid, minutes=10)
    schedule(meet, cid, minutes=12)
    monkeypatch.setattr(call_prep, "SWEEP_BUDGET", -1.0)
    assert sweep(meet) == {"prepped": 1, "deferred": 1}
    assert sweep(meet) == {"prepped": 1}


def test_a_change_made_in_google_is_followed_at_once(meet, google):
    google.connected.add(ANA)
    cid = thread(meet)
    call = schedule(meet, cid, minutes=120)
    google.move(call["id"], 240)
    with meet.app.state.store.transaction() as c:
        gcal_watch._announce(c, ANA, ANA, "changed", _eid(call["id"]), None)
    meet.app.state.call_prep.settle()
    assert _row(meet, call["id"])["scheduled_start"][:16] == _iso(240)[:16]

    del google.events[_eid(call["id"])]
    with meet.app.state.store.transaction() as c:
        gcal_watch._announce(c, ANA, ANA, "cancelled", _eid(call["id"]), None)
    meet.app.state.call_prep.settle()
    assert _row(meet, call["id"])["end_reason"] == "calendar_cancelled"


def test_a_change_to_the_tico_event_is_followed_at_once(meet, google):
    cid = thread(meet)
    call = schedule(meet, cid, minutes=120)
    event = _tico_event(meet, call["id"])
    with meet.app.state.store.transaction() as c:
        calendar_store.update_event(c, event["id"], {"start": _iso(300), "end": _iso(330)}, "human:ana")
    meet.app.state.call_prep.settle()
    assert _row(meet, call["id"])["scheduled_start"][:16] == _iso(300)[:16]


def test_change_time_writes_google_first_and_a_refusal_changes_nothing(meet, google):
    google.connected.add(ANA)
    cid = thread(meet)
    call = schedule(meet, cid, minutes=10, bots=["ops"])
    assert sweep(meet) == {"prepped": 1}

    google.refuse_writes = True
    refused = change(meet, call["id"], 180, expected=403)
    assert refused["error"]["code"] == "calendar_write_failed"
    assert _row(meet, call["id"])["scheduled_start"][:16] == _iso(10)[:16]

    google.refuse_writes = False
    moved = change(meet, call["id"], 180, length=45)
    [(kind, eid, body, send_updates)] = [w for w in google.writes if w[0] == "patch"]
    assert (kind, eid, send_updates) == ("patch", _eid(call["id"]), "all") and "attendees" not in body
    assert moved["scheduled_start"][:16] == _iso(180)[:16] and moved["scheduled_end"][:16] == _iso(225)[:16]
    row = _row(meet, call["id"])
    assert row["prepped_at"] is None  # moved far out: the bots prep again for the new time
    assert google.events[_eid(call["id"])]["start"] == _iso(180).replace("+00:00", "Z")

    # Who may change it: whoever booked it or manages the thread; not another person who reads it.
    change(meet, call["id"], 200, token="cara-test", expected=404)
    company = post(meet, "threads", {"title": "Acme all hands", "audience": "company"})
    shared = schedule(meet, company["id"])
    assert change(meet, shared["id"], 200, token="cara-test", expected=403)["error"]["code"] == "forbidden"
    change(meet, shared["id"], 200, token="ben-test")  # an admin manages every thread
    nexts = {
        t: get(meet, f"threads/{company['id']}/calls", token=t)["next_scheduled"]["can_edit"]
        for t in ("ana-test", "ben-test", "cara-test")
    }
    assert nexts == {"ana-test": True, "ben-test": True, "cara-test": False}
    group = post(meet, "threads", {"title": "Acme crew", "audience": "members", "members": ["human:cara", "human:ben"]})
    crew = schedule(meet, group["id"], minutes=400)
    edits = {
        t: {r["id"]: r["can_edit"] for r in get(meet, "calls/upcoming", token=t)["calls"]}[crew["id"]]
        for t in ("ana-test", "ben-test", "cara-test")
    }
    assert edits == {"ana-test": True, "ben-test": True, "cara-test": False}


def test_change_time_on_a_tico_event_changes_it_in_the_same_write(meet, google):
    cid = thread(meet)
    call = schedule(meet, cid, minutes=60, token="ben-test")
    change(meet, call["id"], 90)  # Ana manages the thread
    event = _tico_event(meet, call["id"])
    assert event["starts_at"][:16] == _iso(90).replace("+00:00", "")[:16]
    assert _row(meet, call["id"])["scheduled_start"][:16] == _iso(90)[:16]


def test_cancel_deletes_the_event_first(meet, google):
    google.connected.add(ANA)
    cid = thread(meet)
    call = schedule(meet, cid)
    google.delete_fails = Problem("gcal_unavailable", "Google Calendar did not answer; try again", 502, retryable=True)
    response = meet.post(f"/api/v2/calls/{call['id']}/end", json={}, headers=headers())
    assert response.status_code == 502 and _row(meet, call["id"])["state"] == "scheduled"

    google.delete_fails = None
    ended = post(meet, f"calls/{call['id']}/end", {})
    assert (ended["state"], ended["end_reason"]) == ("ended", "cancelled")
    assert ("delete", _eid(call["id"]), "all") in google.writes and not google.events

    tico = thread(meet, subject="Acme other")
    google.connected.clear()
    booked = schedule(meet, tico)
    post(meet, f"calls/{booked['id']}/end", {})
    assert _tico_event(meet, booked["id"])["status"] == "cancelled"


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


def test_the_first_join_reads_the_event_live(meet, google):
    google.connected.add(ANA)
    cid = thread(meet)
    gone = schedule(meet, cid)
    del google.events[_eid(gone["id"])]
    refused = meet.post(f"/api/v2/calls/{gone['id']}/join", json={}, headers=headers())
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "call_cancelled"
    assert _row(meet, gone["id"])["end_reason"] == "calendar_cancelled"

    moved = schedule(meet, cid)
    google.move(moved["id"], 5)
    assert join(meet, moved["id"])["call"]["scheduled_start"][:16] == _iso(5)[:16]
    post(meet, f"calls/{moved['id']}/end", {})

    unread = schedule(meet, cid)
    google.refuse_reads = True
    assert join(meet, unread["id"])["call"]["state"] == "live"


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


def test_upcoming_keeps_only_the_viewers_calls_before_its_limit(meet, google, monkeypatch):
    monkeypatch.setattr(calls, "UPCOMING_LIMIT", 1)
    with meet.app.state.store.transaction() as c:
        anas = H.open_conversation(c, "human:ana", ["bot:ops"], kind="chat", subject="Acme solo")["id"]
    for minutes in (30, 40, 50):
        schedule(meet, anas, minutes=minutes)
    bens = schedule(meet, thread(meet), minutes=90)
    [row] = get(meet, "calls/upcoming", token="ben-test")["calls"]
    assert row["id"] == bens["id"] and set(row) == {
        "id",
        "title",
        "thread_id",
        "scheduled_start",
        "scheduled_end",
        "people",
        "can_edit",
    }
    assert row["people"] == ["human:ana", "human:ben"]


def test_the_thread_list_names_its_next_scheduled_call(meet, google):
    cid = thread(meet)
    later = schedule(meet, cid, minutes=300)
    sooner = schedule(meet, cid, minutes=60)
    for _ in range(6):
        post(meet, f"calls/{post(meet, f'threads/{cid}/calls', {'title': 'Acme now'})['id']}/end", {})
    listed = get(meet, f"threads/{cid}/calls?limit=2")
    assert listed["next_scheduled"]["id"] == sooner["id"] and later["id"] not in [c["id"] for c in listed["calls"]]
    post(meet, f"calls/{sooner['id']}/end", {})
    assert get(meet, f"threads/{cid}/calls")["next_scheduled"]["id"] == later["id"]


def test_a_booking_needs_both_times_still_to_come_on_the_whole_minute(meet, google):
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
    start = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(second=42, microsecond=7)
    stored = calls.booking(start.isoformat(), (start + timedelta(minutes=30)).isoformat())
    assert stored[0].endswith(":00.000000Z") and stored[1].endswith(":00.000000Z")


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
    conforming("changeCall", change(meet, call["id"], 180))
