"""The Calendar's routes (backend/calendar_api.py, docs/dev/calendar-contract.md HTTP): the merge of Google, Tico and
derived calendars for one viewer, who may write where, and the live topic. Google is a fake in this file: gcal_live's
calls are replaced, so nothing here reaches Google or a browser."""

import time
from datetime import datetime, timedelta, timezone

import pytest

from backend import calendar_api, calendar_store, gcal_live
from backend.store import H, Problem
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.automation_api import as_bot

UTC = timezone.utc
DAY = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def at(hours, day=DAY):
    return (day + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


WINDOW = f"from={DAY.date().isoformat()}&to={(DAY + timedelta(days=1)).date().isoformat()}"


def g_occ(cal, eid, title, start, end, *, calendar=None, uid=None, attendees=()):
    """An occurrence as gcal_live answers one."""
    return {
        "occurrence_id": f"google:{cal}:{eid}",
        "event_id": eid,
        "ical_uid": uid or eid + "@google.com",
        "calendar": calendar or {"ref": "google:" + cal, "type": "google", "id": cal, "name": cal},
        "home": "google",
        "kind": "event",
        "title": title,
        "start": start,
        "end": end,
        "all_day": False,
        "time_zone": "UTC",
        "attendees": list(attendees),
        "response": None,
        "busy_only": False,
        "recurring": False,
        "status": "confirmed",
        "meeting_url": None,
        "open_url": "https://calendar.google.com/x",
        "links": {},
        "can": {"join": False, "edit": True},
        "original_starts_at": None,
    }


class FakeGoogle:
    """gcal_live, recorded: `connected` addresses, `cals` {calendar_id: [occurrences]}, failing calendar ids, and
    every write as (call, as_mailbox, calendar_id, body)."""

    def __init__(self, monkeypatch):
        self.connected, self.cals, self.failing, self.slow, self.writes, self.reads = set(), {}, set(), {}, [], []
        self.listed = []
        monkeypatch.setattr(gcal_live, "connected", lambda c, address: address in self.connected)
        monkeypatch.setattr(gcal_live, "events", self.events)
        monkeypatch.setattr(gcal_live, "event", self.event)
        monkeypatch.setattr(gcal_live, "freebusy", self.freebusy)
        monkeypatch.setattr(gcal_live, "time_zone", lambda c, address: "UTC")
        monkeypatch.setattr(gcal_live, "calendar_list", lambda c, address: list(self.listed))
        monkeypatch.setattr(gcal_live, "insert", self.insert)
        monkeypatch.setattr(gcal_live, "patch", self.patch)
        monkeypatch.setattr(
            gcal_live, "delete", lambda c, a, cal, eid, **k: self.writes.append(("delete", a, cal, eid))
        )
        calendar_api._ZONES.clear()

    def events(self, c, as_mailbox, cal, start, end, *, q=None, limit=250, calendar=None, people=None, bot=None):
        self.reads.append((as_mailbox, cal, q, bot))
        if cal in self.slow:
            time.sleep(self.slow[cal])
        if cal in self.failing:
            raise Problem("gcal_refused", "Google Calendar refused this calendar", 403)
        found = [dict(o) for o in self.cals.get(cal, []) if not q or q.lower() in o["title"].lower()]
        if bot:
            found = [o for o in found if o.get("bot") == bot]
        return [{**o, "calendar": calendar or o["calendar"]} for o in found]

    def event(self, c, as_mailbox, cal, eid, *, calendar=None, people=None):
        for o in self.cals.get(cal, []):
            if o["event_id"] == eid:
                return dict(o)
        raise Problem("not_found", "No such Google event", 404)

    def freebusy(self, c, as_mailbox, ids, start, end):
        return {i: [] for i in ids if i not in self.failing}

    def insert(self, c, as_mailbox, cal, body, *, send_updates="all"):
        self.writes.append(("insert", as_mailbox, cal, body))
        n = len(self.writes)
        occ = g_occ(cal, f"new{n}", body["title"], body["start"], body["end"], uid=f"uid{n}@google.com")
        occ["attendees"] = body.get("attendees") or []
        return occ

    def patch(self, c, as_mailbox, cal, eid, body, *, send_updates="all"):
        self.writes.append(("patch", as_mailbox, cal, eid, body))
        return g_occ(cal, eid, body.get("title") or "Changed", at(9), at(10))


@pytest.fixture
def google(monkeypatch):
    return FakeGoogle(monkeypatch)


def people(api):
    with api.app.state.store.transaction() as c:
        for pid in ("ana", "ben", "cara", "eve"):
            c.execute(
                "INSERT OR IGNORE INTO humans(id,name,email) VALUES(?,?,?)", (pid, pid.title(), f"{pid}@acme.example")
            )


def bot(api, slug="planner", reports_to=None):
    token = as_bot(api, slug)
    with api.app.state.store.transaction() as c:
        bot_id = H.resolve_bot(c, slug)["id"]
        if reports_to:
            c.execute("UPDATE bot_config SET reports_to=? WHERE bot=?", (reports_to, bot_id))
    return token, bot_id


def events(api, query, token="ana-test", expected=200):
    r = api.get("/api/v2/calendar/events?" + query, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def create(api, body, token, expected=200):
    r = api.post("/api/v2/calendar/events", json=body, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def add_run(api, bot_id, start, end, rid=None):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO attempts(id,bot_id,created_at,started_at,finished_at,settled_at,trigger) VALUES(?,?,?,?,?,?,?)",
            (rid or H.new_id(), bot_id, start, start, end, end, "message"),
        )


def test_who_defaults_to_the_caller_and_a_person_without_google_writes_their_own_tico_calendar(api, google):
    people(api)
    answer = events(api, WINDOW)
    assert [c["ref"] for c in answer["calendars"]] == ["human:ana"]
    assert answer["calendars"][0]["home"] == "tico" and answer["calendars"][0]["can_write"] is True
    assert answer["errors"] == []
    made = create(api, {"title": "Dentist", "start": at(9), "end": at(10)}, "ana-test")
    assert made["home"] == "tico" and made["can"]["edit"] is True
    shown = events(api, WINDOW)["occurrences"]
    assert [o["title"] for o in shown] == ["Dentist"]
    assert shown[0]["calendar"]["ref"] == "human:ana"


def test_google_tico_and_runs_merge_into_one_list(api, google):
    people(api)
    google.connected |= {"ana@acme.example", "ben@acme.example"}
    google.cals["ben@acme.example"] = [g_occ("ben@acme.example", "e1", "Ben's standup", at(9), at(9.5))]
    token, bot_id = bot(api)
    create(api, {"calendar": "me", "title": "Inbox sweep", "start": at(11), "end": at(12)}, token)
    add_run(api, bot_id, at(13), at(13.25))
    answer = events(api, f"who=me,human:ben,bot:{bot_id}&{WINDOW}")
    assert [c["ref"] for c in answer["calendars"]] == ["human:ana", "human:ben", "bot:" + bot_id]
    assert ("ana@acme.example", "ben@acme.example", None, None) in google.reads  # read as the viewer
    kinds = {(o["home"], o["kind"], o["title"]) for o in answer["occurrences"]}
    assert ("google", "event", "Ben's standup") in kinds
    assert ("tico", "event", "Inbox sweep") in kinds
    assert any(o["kind"] == "run" for o in answer["occurrences"])
    assert answer["errors"] == []
    google_one = next(o for o in answer["occurrences"] if o["home"] == "google")
    assert google_one["calendar"]["ref"] == "human:ben" and google_one["can"]["edit"] is False  # people view


def test_a_persons_calendar_leaves_out_the_runs_of_bots_that_report_to_them_until_one_is_added(api, google):
    people(api)
    _, bot_id = bot(api, "scheduler", reports_to="human:ana")
    add_run(api, bot_id, at(9), at(9.25))
    own = events(api, f"who=me&{WINDOW}")["occurrences"]
    assert [o for o in own if o["kind"] == "run"] == []  # only you: the manager's own calendar has no bot runs
    added = events(api, f"who=me,bot:{bot_id}&{WINDOW}")["occurrences"]
    assert [(o["kind"], o["calendar"]["ref"]) for o in added] == [("run", "bot:" + bot_id)]


def test_a_failing_google_calendar_is_an_error_and_the_others_still_answer(api, google):
    people(api)
    google.connected |= {"ana@acme.example", "ben@acme.example", "cara@acme.example"}
    google.cals["cara@acme.example"] = [g_occ("cara@acme.example", "c1", "Cara's review", at(9), at(10))]
    google.failing.add("ben@acme.example")
    answer = events(api, f"who=human:ben,human:cara&{WINDOW}")
    assert answer["errors"] == [{"ref": "human:ben", "reason": "Google Calendar refused this calendar"}]
    assert [o["title"] for o in answer["occurrences"]] == ["Cara's review"]
    unknown = events(api, f"who=human:nobody&{WINDOW}")
    assert unknown["errors"][0]["ref"] == "human:nobody"


def test_a_private_tico_event_is_busy_to_a_non_attendee(api, google):
    people(api)
    token, bot_id = bot(api)
    create(
        api,
        {"title": "Salary talk", "start": at(9), "end": at(10), "visibility": "private", "attendees": ["human:cara"]},
        token,
    )
    seen_by_ben = events(api, f"who=bot:{bot_id}&{WINDOW}", "ben-test")["occurrences"]
    assert [(o["title"], o["busy_only"], o["attendees"]) for o in seen_by_ben] == [("Busy", True, [])]
    seen_by_cara = events(api, f"who=bot:{bot_id}&{WINDOW}", "cara-test")["occurrences"]
    assert seen_by_cara[0]["title"] == "Salary talk" and seen_by_cara[0]["response"] == "needs_action"
    on_caras_own = events(api, WINDOW, "cara-test")["occurrences"]
    assert [o["title"] for o in on_caras_own] == ["Salary talk"]  # an invitation shows on the attendee's calendar


def test_a_window_over_62_days_is_refused(api, google):
    r = api.get("/api/v2/calendar/events?from=2026-01-01&to=2026-03-10", headers=headers())
    assert r.status_code == 422 and r.json()["error"]["code"] == "range"


def test_a_window_over_7_days_answers_runs_as_month_counts(api, google):
    people(api)
    _, bot_id = bot(api)
    add_run(api, bot_id, at(9), at(9.1))
    add_run(api, bot_id, at(10), at(10.1))
    start, end = DAY - timedelta(days=10), DAY + timedelta(days=20)
    answer = events(api, f"who=bot:{bot_id}&from={start.date().isoformat()}&to={end.date().isoformat()}")
    assert {"bot_id": bot_id, "date": DAY.date().isoformat(), "count": 2} in answer["month_counts"]
    assert not [o for o in answer["occurrences"] if o["kind"] == "run"]
    short = events(api, f"who=bot:{bot_id}&{WINDOW}")
    assert "month_counts" not in short and len([o for o in short["occurrences"] if o["kind"] == "run"]) == 2


def test_a_bot_writing_to_a_connected_person_writes_google_as_that_person(api, google):
    people(api)
    google.connected.add("ben@acme.example")
    token, bot_id = bot(api)
    made = create(api, {"calendar": "human:ben", "title": "1:1 prep", "start": at(9), "end": at(10)}, token)
    call, as_mailbox, cal, body = google.writes[-1]
    assert (call, as_mailbox, cal) == ("insert", "ben@acme.example", "ben@acme.example")
    assert body["bot"] == bot_id and body["actor"] == "bot:" + bot_id
    assert made["home"] == "google" and made["calendar"]["ref"] == "human:ben" and made["can"]["edit"] is True
    with api.app.state.store.read() as c:
        link = c.execute("SELECT * FROM calendar_event_links WHERE link_type='bot'").fetchone()
        assert (link["link_id"], link["google_calendar_id"], link["ical_uid"]) == (
            bot_id,
            "ben@acme.example",
            made["ical_uid"],
        )
        change = c.execute("SELECT * FROM changes WHERE topic='calendar' ORDER BY seq DESC LIMIT 1").fetchone()
        assert (change["kind"], change["ref"]) == ("google", "google:ben@acme.example")


def test_with_two_connected_attendees_the_bots_manager_hosts_else_the_first(api, google):
    people(api)
    google.connected |= {"ben@acme.example", "cara@acme.example"}
    token, _ = bot(api, "scheduler", reports_to="human:cara")
    both = ["ben@acme.example", "cara@acme.example"]
    create(api, {"calendar": "me", "title": "Sync", "start": at(9), "end": at(10), "attendees": both}, token)
    call, as_mailbox, cal, body = google.writes[-1]
    assert (as_mailbox, cal) == ("cara@acme.example", "cara@acme.example")
    assert [a["email"] for a in body["attendees"]] == ["cara@acme.example", "ben@acme.example"]
    other, _ = bot(api, "helper2", reports_to="human:eve")
    create(api, {"calendar": "me", "title": "Sync 2", "start": at(11), "end": at(12), "attendees": both}, other)
    assert google.writes[-1][1] == "ben@acme.example"


def test_a_bot_write_is_refused_by_book_rule_none(api, google):
    people(api)
    token, bot_id = bot(api)
    r = api.put(f"/api/v2/reach/settings/bot:{bot_id}", json={"book": "none"}, headers=headers())
    assert r.status_code == 200, r.text
    refused = create(
        api, {"title": "Pairing", "start": at(9), "end": at(10), "attendees": ["human:ben"]}, token, expected=403
    )
    assert refused["error"]["code"] == "book_rule" and "Nothing was booked" in refused["error"]["detail"]
    assert create(api, {"title": "Alone", "start": at(9), "end": at(10)}, token)["home"] == "tico"  # itself: fine
    free = api.get("/api/v2/calendar/free?who=human:ben&minutes=30", headers=headers(token))
    assert free.status_code == 403 and free.json()["error"]["code"] == "book_rule"


def test_a_person_writing_someone_elses_calendar_is_view_only(api, google):
    people(api)
    _, bot_id = bot(api)
    for calendar in ("human:ben", "bot:" + bot_id):
        refused = create(api, {"calendar": calendar, "title": "X", "start": at(9), "end": at(10)}, "ana-test", 403)
        assert refused["error"]["code"] == "view_only"
    google.connected.add("ana@acme.example")
    refused = create(api, {"title": "Mine on Google", "start": at(9), "end": at(10)}, "ana-test", 403)
    assert refused["error"]["code"] == "view_only" and google.writes == []


def test_a_bot_changes_and_cancels_only_the_event_it_added_to_a_persons_calendar(api, google):
    people(api)
    token, _ = bot(api, "planner")
    other, _ = bot(api, "helper")
    made = create(api, {"calendar": "human:ben", "title": "1:1", "start": at(9), "end": at(10)}, token)
    path = "/api/v2/calendar/events/" + made["occurrence_id"]
    seen = {t: events(api, WINDOW + "&who=human:ben", t)["occurrences"][0]["can"]["edit"] for t in (token, other)}
    assert seen == {token: True, other: False}
    moved = api.patch(path, json={"title": "1:1 moved"}, headers=headers(other))
    assert moved.status_code == 403 and moved.json()["error"]["code"] == "forbidden", moved.text
    assert "Only the bot that added it" in moved.json()["error"]["detail"]
    cancelled = api.delete(path, headers=headers(other))
    assert cancelled.status_code == 403 and cancelled.json()["error"]["code"] == "forbidden", cancelled.text
    assert [o["title"] for o in events(api, WINDOW, "ben-test")["occurrences"]] == ["1:1"]
    assert api.patch(path, json={"title": "1:1 moved"}, headers=headers(token)).status_code == 200
    assert api.delete(path, headers=headers(token)).status_code == 200
    assert events(api, WINDOW, "ben-test")["occurrences"] == []


def test_a_bot_cannot_move_or_cancel_a_persons_own_event_on_their_tico_calendar(api, google):
    people(api)
    token, _ = bot(api, "planner")
    made = create(api, {"title": "Dentist", "start": at(9), "end": at(10)}, "ana-test")
    path = "/api/v2/calendar/events/" + made["occurrence_id"]
    moved = api.patch(path, json={"title": "Moved"}, headers=headers(token))
    assert moved.status_code == 403 and moved.json()["error"]["code"] == "forbidden", moved.text
    cancelled = api.delete(path, headers=headers(token))
    assert cancelled.status_code == 403 and cancelled.json()["error"]["code"] == "forbidden", cancelled.text
    assert [o["title"] for o in events(api, WINDOW)["occurrences"]] == ["Dentist"]


def test_a_bot_with_read_write_on_a_persons_calendar_changes_their_events_and_read_alone_does_not(api, google):
    people(api)
    token, bot_id = bot(api, "planner")
    made = create(api, {"title": "Dentist", "start": at(9), "end": at(10)}, "ana-test")
    path = "/api/v2/calendar/events/" + made["occurrence_id"]
    with api.app.state.store.transaction() as c:
        ana_calendar = calendar_store.calendar_for(c, "human", "ana")["id"]
        calendar_store.set_access(c, ana_calendar, "bot", bot_id, "read", "human:ana")
    refused = api.patch(path, json={"title": "Moved"}, headers=headers(token))
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "forbidden", refused.text
    with api.app.state.store.transaction() as c:
        calendar_store.set_access(c, ana_calendar, "bot", bot_id, "read_write", "human:ana")
    assert api.patch(path, json={"title": "Moved"}, headers=headers(token)).status_code == 200
    assert api.delete(path, headers=headers(token)).status_code == 200
    assert events(api, WINDOW)["occurrences"] == []


def test_a_tico_write_publishes_the_calendar_topic(api, google):
    from backend import events as E

    people(api)
    token, bot_id = bot(api)
    made = create(api, {"title": "Plan", "start": at(9), "end": at(10)}, token)
    with api.app.state.store.read() as c:
        calendar_id = c.execute("SELECT calendar_id FROM calendar_events WHERE id=?", (made["event_id"],)).fetchone()[0]
        rows = c.execute("SELECT kind, subject_id, ref FROM changes WHERE topic='calendar'").fetchall()
    assert ("event", made["event_id"], calendar_id) in [tuple(r) for r in rows]
    assert "calendar" in E.TOPICS
    r = api.patch("/api/v2/calendar/events/" + made["occurrence_id"], json={"title": "Plan B"}, headers=headers(token))
    assert r.status_code == 200 and r.json()["title"] == "Plan B", r.text
    r = api.delete("/api/v2/calendar/events/" + made["occurrence_id"], headers=headers(token))
    assert r.status_code == 200, r.text
    assert events(api, f"who=bot:{bot_id}&{WINDOW}")["occurrences"] == []


def test_a_live_meeting_from_an_event_links_it_and_the_event_shows_the_meeting(api, google):
    people(api)
    made = create(api, {"title": "Standup", "start": at(9), "end": at(10)}, "ana-test")
    meeting = post(api, "live-meetings", {"title": "Standup", "calendar_event": made["occurrence_id"]})
    shown = events(api, WINDOW)["occurrences"]
    assert shown[0]["links"]["meeting"] == meeting["id"]
    with api.app.state.store.read() as c:
        link = c.execute("SELECT * FROM calendar_event_links WHERE link_type='meeting'").fetchone()
    assert (link["tico_event_id"], link["link_id"]) == (made["event_id"], meeting["id"])
    unlinked = post(api, "live-meetings", {"title": "Other", "calendar_event": "google:x:nope"})
    assert unlinked["id"]  # an occurrence that does not resolve leaves the meeting unlinked


def test_search_merges_tico_and_google_and_is_partial_when_the_budget_runs_out(api, google, monkeypatch):
    people(api)
    monkeypatch.setattr(calendar_api, "SEARCH_SECONDS", 0.3)
    google.connected.add("ana@acme.example")
    google.cals["ana@acme.example"] = [g_occ("ana@acme.example", "g1", "Budget sync", at(9), at(10))]
    google.cals["team-cal"] = [g_occ("team-cal", "g2", "Budget offsite", at(11), at(12))]
    google.listed = [{"id": "ana@acme.example", "primary": True}, {"id": "team-cal", "name": "Team"}]
    google.slow["team-cal"] = 1.5
    token, bot_id = bot(api)
    create(api, {"title": "Budget draft", "start": at(13), "end": at(14)}, token)
    r = api.get(f"/api/v2/calendar/search?q=budget&who=me,bot:{bot_id}", headers=headers())
    assert r.status_code == 200, r.text
    found = r.json()
    titles = {o["title"] for o in found["occurrences"]}
    assert {"Budget sync", "Budget draft"} <= titles and "Budget offsite" not in titles
    assert found["partial"] is True
    with api.app.state.store.read() as c:
        logged = c.execute("SELECT detail FROM events WHERE action='calendar.search'").fetchone()
    assert logged is not None and '"partial": true' in logged["detail"]


def one(api, occurrence_id, token="ana-test", expected=200):
    r = api.get("/api/v2/calendar/events/" + occurrence_id, headers=headers(token))
    assert r.status_code == expected, r.text
    return r.json()


def test_one_occurrence_reads_as_the_caller_sees_it_and_the_writes_on_its_path_still_work(api, google):
    people(api)
    made = create(api, {"title": "Standup", "start": at(9), "end": at(10)}, "ana-test")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE calendar_events SET meeting_url=? WHERE id=?", ("https://meet.example/abc", made["event_id"]))
    shown = one(api, made["occurrence_id"])
    assert (shown["occurrence_id"], shown["title"], shown["can"]["join"]) == (made["occurrence_id"], "Standup", True)
    assert one(api, made["occurrence_id"], "ben-test")["can"]["join"] is True  # a Tico calendar is the company's
    # One instance of a repeating event, by its original start.
    daily = create(api, {"title": "Daily", "start": at(11), "end": at(11.5), "repeat": "daily"}, "ana-test")
    second = f"tico:{daily['event_id']}:{at(35)}"
    assert one(api, second)["start"] == at(35)
    one(api, f"tico:{daily['event_id']}:{at(35.25)}", expected=404)  # not an instance
    one(api, f"tico:{daily['event_id']}", expected=404)  # a series is not one occurrence
    for missing in ("tico:nope", "google:x:busy:" + at(9), "run:abc", "nonsense", "google:only"):
        one(api, missing, expected=404)
    # PATCH, DELETE and POST …/response on the same path are not shadowed by the GET.
    r = api.patch("/api/v2/calendar/events/" + made["occurrence_id"], json={"title": "Sync"}, headers=headers())
    assert r.status_code == 200 and r.json()["title"] == "Sync", r.text
    r = api.post(
        "/api/v2/calendar/events/" + made["occurrence_id"] + "/response",
        json={"response": "accepted"},
        headers=headers(),
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_attendee", r.text  # its own handler answered
    r = api.delete("/api/v2/calendar/events/" + made["occurrence_id"], headers=headers())
    assert r.status_code == 200, r.text
    one(api, made["occurrence_id"], expected=404)  # cancelled


def test_one_private_tico_occurrence_is_busy_to_a_non_attendee_who_cannot_join_it(api, google):
    people(api)
    token, _ = bot(api)
    made = create(
        api,
        {"title": "Salary talk", "start": at(9), "end": at(10), "visibility": "private", "attendees": ["human:cara"]},
        token,
    )
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE calendar_events SET meeting_url=? WHERE id=?", ("https://meet.example/x", made["event_id"]))
    hidden = one(api, made["occurrence_id"], "ben-test")
    assert (hidden["title"], hidden["busy_only"], hidden["meeting_url"], hidden["can"]["join"]) == (
        "Busy",
        True,
        None,
        False,
    )
    attending = one(api, made["occurrence_id"], "cara-test")
    assert (attending["title"], attending["can"]["join"]) == ("Salary talk", True)


def test_one_google_occurrence_is_read_as_the_viewer_and_404_when_they_cannot_see_it(api, google):
    people(api)
    google.connected.add("ana@acme.example")
    standup = g_occ("team-cal", "g1", "Standup", at(9), at(10))
    standup["meeting_url"] = "https://meet.google.com/abc-defg-hij"
    google.cals["team-cal"] = [standup]
    shown = one(api, "google:team-cal:g1")
    assert (shown["title"], shown["can"]["join"]) == ("Standup", True)
    one(api, "google:team-cal:missing", expected=404)
    one(api, "google:team-cal:g1", "ben-test", expected=404)  # no Google of his own: nothing to read it as
