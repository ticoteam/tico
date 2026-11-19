"""Calendar on the server: the Google calendar watcher (backend/gcal_watch.py) announcing Google's own changes as
`calendar.event.*`, the appointment routes read live and booked in the request (backend/connectors.py), and the token
door (backend/google_tokens.py). Google is a fake behind `gcal_live.gcal_call`; nothing here reaches the network."""

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from backend import gcal_live, gcal_watch, google_tokens
from backend import google_workspace as W
from backend.store import Problem
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.automation_api import NOTE_STEP

ANA, BEN = "ana@acme.example", "ben@acme.example"
SECRET = "ya29.calendar-secret-token-never-shown"
UTC = timezone.utc


def stamp(when):
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


SOON = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=2)


class FakeCalendar:
    """Google Calendar as gcal_live reaches it: events per calendar, a change log behind sync tokens `t<version>`,
    tokens Google has forgotten (`expired`, answered 410), and `down` (no answer at all). The Workspace key covers
    `addresses` (its mint is faked); `own_grant` puts one of them on its person's own Connect Google grant instead,
    which the real token door refreshes (Google's token endpoint faked)."""

    def __init__(self, monkeypatch, *addresses):
        self.addresses, self.events, self.log, self.version = set(addresses), {a: {} for a in addresses}, [], 0
        self.expired, self.down, self.refuse, self.calls, self.minted = set(), False, False, [], []
        self.page_size, self.bad_pages, self.personal = 1000, set(), set()
        self.monkeypatch = monkeypatch
        authority, connected = W.authority, gcal_live.connected

        def covered(c, address):
            if address in self.personal:
                return authority(c, address)
            return "workspace" if address in self.addresses else authority(c, address)

        monkeypatch.setattr(W, "authority", covered)
        monkeypatch.setattr(
            gcal_live,
            "connected",
            lambda c, address: connected(c, address) if address in self.personal else address in self.addresses,
        )
        monkeypatch.setattr(gcal_live, "gcal_call", self.call)
        monkeypatch.setattr(gcal_watch, "CHECK_SECONDS", 0)

        def mint(c, mailbox, service, row=None, scopes=None):
            self.minted.append((mailbox, service, tuple(scopes or ())))
            return {"access_token": SECRET}

        monkeypatch.setattr(W, "mint", mint)

    def own_grant(self, api, person, mailbox, scopes):
        """`mailbox` is connected with `person`'s own Connect Google grant holding `scopes`, held on the server."""
        from backend import google_connect
        from backend.tests.helpers.google_connect import CLIENT, FakeGoogle
        from backend.tests.helpers.google_workspace import personal

        settings = api.app.state.store.settings
        self.monkeypatch.setattr(settings, "google_oauth_client_id", CLIENT)
        self.monkeypatch.setattr(settings, "google_oauth_client_secret", "tico-secret")
        personal(api, person, mailbox, client=CLIENT, scopes=json.dumps(scopes))
        self.refreshes = FakeGoogle({"access_token": SECRET, "expires_in": 3599, "scope": ""})
        self.monkeypatch.setattr(google_connect, "post_token", self.refreshes)
        self.personal.add(mailbox)

    def put(self, cal, eid, *, start=SOON, sequence=0, status="confirmed", **more):
        self.version += 1
        event = {
            "id": eid,
            "status": status,
            "sequence": sequence,
            "summary": "Planning " + eid,
            "iCalUID": eid + "@google.com",
            "start": {"dateTime": stamp(start)},
            "end": {"dateTime": stamp(start + timedelta(minutes=30))},
            "organizer": {"email": cal},
            "attendees": [{"email": cal, "self": True, "responseStatus": "accepted"}, {"email": BEN}],
            **more,
        }
        self.events[cal][eid] = event
        self.log.append((self.version, cal, eid))
        return event

    def cancel(self, cal, eid):
        self.version += 1
        self.events[cal][eid] = {"id": eid, "status": "cancelled", **self._series(cal, eid)}
        self.log.append((self.version, cal, eid))

    def _series(self, cal, eid):
        found = self.events[cal].get(eid) or {}
        return {k: found[k] for k in ("recurringEventId", "originalStartTime") if k in found}

    def call(self, url, *, token, method="GET", data=None):
        parts = urlsplit(url)
        path = unquote(parts.path.removeprefix("/calendar/v3/"))
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.calls.append((method, path, query, data))
        assert token == SECRET
        if self.down:
            return 0, {}
        if self.refuse:
            return 403, {"reason": "forbidden"}
        cal = path.split("/")[1]
        if method == "GET" and path.endswith("/events"):
            if "syncToken" in query:
                if query["syncToken"] in self.expired:
                    return 410, {"reason": "fullSyncRequired"}
                since = int(query["syncToken"][1:])
                ids = dict.fromkeys(e for v, c, e in self.log if v > since and c == cal)
                items = [self.events[cal][e] for e in ids]
            else:
                # As Google lists without deleted events: the cancelled instances of a live series still come back.
                items = [
                    e for e in self.events[cal].values() if e["status"] != "cancelled" or e.get("recurringEventId")
                ]
            if query.get("pageToken") in self.bad_pages:
                return 400, {"reason": "invalid"}
            offset = int(query.get("pageToken", "p0")[1:])
            answer = {"items": items[offset : offset + self.page_size], "accessRole": "owner"}
            if offset + self.page_size < len(items):
                answer["nextPageToken"] = f"p{offset + self.page_size}"
            else:
                answer["nextSyncToken"] = f"t{self.version}"
            return 200, answer
        if method == "POST" and path.endswith("/events"):
            eid = data.get("id") or f"made{self.version}"
            more = {"hangoutLink": "https://meet.google.com/abc-defg-hij"} if data.get("conferenceData") else {}
            event = self.put(cal, eid, **more)
            event.update(
                summary=data["summary"],
                start=data["start"],
                end=data["end"],
                attendees=data.get("attendees") or [],
                extendedProperties=data.get("extendedProperties") or {},
            )
            return 200, event
        if method == "GET" and "/events/" in path:
            found = self.events[cal].get(path.rsplit("/", 1)[1])
            return (200, found) if found else (404, {})
        return 404, {}

    def posts(self):
        return [call for call in self.calls if call[0] == "POST"]


@pytest.fixture
def google(monkeypatch, api):
    with api.app.state.store.transaction() as c:
        for pid in ("ana", "ben"):
            c.execute(
                "INSERT OR IGNORE INTO humans(id,name,email) VALUES(?,?,?)", (pid, pid.title(), f"{pid}@acme.example")
            )
    from backend import connectors

    connectors.forget_live()  # the 60 s read cache is per process: no test sees another's calendar
    google_tokens.forget()
    return FakeCalendar(monkeypatch, ANA)


def tick(api):
    return gcal_watch.tick(api.app.state.store)


def announced(api):
    with api.app.state.store.read() as c:
        rows = c.execute(
            "SELECT action,target,detail FROM events WHERE action LIKE 'calendar.event.%' ORDER BY rowid"
        ).fetchall()
    return [(r["action"].rsplit(".", 1)[1], r["target"], json.loads(r["detail"])) for r in rows]


def actions(api):
    return [(action, target) for action, target, _ in announced(api)]


def watch_row(api):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM calendar_watches WHERE calendar_id=?", (ANA,)).fetchone())


# ---------------------------------------------------------------- the watcher


def test_the_first_check_learns_quietly_then_each_google_change_is_announced_once(api, google):
    google.put(ANA, "e1")
    with api.app.state.store.read() as c:
        assert gcal_watch.watched(c) == [ANA]
    assert tick(api) == 1
    assert announced(api) == []  # what was already there is not news
    assert watch_row(api)["sync_token"] and watch_row(api)["watching_since_at"]
    google.put(ANA, "e2")
    tick(api)
    [(action, target, detail)] = announced(api)
    assert (action, target) == ("created", "e2")
    assert detail == {"calendar_id": "google:" + ANA, "home": "google", "occurrence_id": "google:" + ANA + ":e2"}
    tick(api)  # a repeat pass: nothing new
    google.put(ANA, "e1", start=SOON + timedelta(hours=1), sequence=1)  # moved in Google
    tick(api)
    google.cancel(ANA, "e2")
    tick(api)
    tick(api)
    assert actions(api) == [("created", "e2"), ("changed", "e1"), ("cancelled", "e2")]
    google.put(ANA, "e1", sequence=1, description="notes only")  # same sequence: not a new change
    tick(api)
    assert len(announced(api)) == 3
    assert [c for c in google.calls if "syncToken" in c[2]], "later checks ask only what changed"


def test_an_expired_sync_token_lists_again_and_announces_only_the_differences(api, google):
    for eid in ("e1", "e2", "e3"):
        google.put(ANA, eid)
    tick(api)
    google.put(ANA, "e2", sequence=2)
    google.put(ANA, "e4")
    del google.events[ANA]["e3"]  # gone from the list, as a deleted event is once Google forgets the token
    google.expired.add(watch_row(api)["sync_token"])
    tick(api)
    assert sorted(actions(api)) == [("cancelled", "e3"), ("changed", "e2"), ("created", "e4")]
    google.expired.add(watch_row(api)["sync_token"])  # and again: the list now matches what was seen
    tick(api)
    assert len(announced(api)) == 3


def test_a_series_instance_changed_or_cancelled_is_news_of_its_series(api, google):
    google.put(ANA, "s1", recurrence=["RRULE:FREQ=WEEKLY"])
    tick(api)
    first = {"dateTime": stamp(SOON + timedelta(days=7))}
    google.put(ANA, "s1_x", sequence=1, recurringEventId="s1", originalStartTime=first)
    google.cancel(ANA, "s1_x")
    google.version += 1
    google.events[ANA]["s1_y"] = {"id": "s1_y", "status": "cancelled", "recurringEventId": "s1"}
    google.log.append((google.version, ANA, "s1_y"))
    tick(api)
    assert actions(api) == [("cancelled", "s1"), ("cancelled", "s1")]
    assert {d["occurrence_id"] for _, _, d in announced(api)} == {"google:" + ANA + ":s1_x", "google:" + ANA + ":s1_y"}


def test_google_down_keeps_the_token_and_a_refusal_reaches_reconnects(api, google):
    google.put(ANA, "e1")
    tick(api)
    token = watch_row(api)["sync_token"]
    google.down = True
    google.put(ANA, "e2")
    tick(api)
    row = watch_row(api)
    assert (row["sync_token"], row["check_error"]) == (token, "gcal_unavailable")
    google.down, google.refuse = False, True
    tick(api)
    with api.app.state.store.read() as c:
        refusals = c.execute("SELECT account FROM google_signin_refusals WHERE service='calendar'").fetchall()
    assert [r["account"] for r in refusals] == [ANA] and watch_row(api)["check_error"] == "gcal_refused"
    google.refuse = False
    tick(api)
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM google_signin_refusals WHERE service='calendar'").fetchone()
    assert watch_row(api)["check_error"] is None and actions(api) == [("created", "e2")]  # caught up, once


class OnePage:
    """A drain budget with room for `pages` pages of one listing: the pass asks once before the calendar, then once
    after each page."""

    def __init__(self, pages=1):
        self.pages, self.asked = pages, 0

    def left(self):
        self.asked += 1
        return self.asked <= self.pages


def test_a_google_edit_after_ticos_own_write_is_announced_and_the_write_never_is(api, google):
    tick(api)
    booked = post(api, "calendar/appointments", {**BOOKING, "attendees": []})["action"]
    eid = booked["result"]["event_id"]
    google.put(ANA, eid, sequence=1)  # moved in Google before the watcher's next look
    tick(api)
    assert actions(api) == [("created", eid), ("changed", eid)]
    with api.app.state.store.transaction() as c:  # long after: Tico's own version is still not news
        c.execute("UPDATE calendar_watch_events SET updated_at='2000-01-01T00:00:00Z'")
    google.expired.add(watch_row(api)["sync_token"])
    tick(api)
    tick(api)
    assert actions(api) == [("created", eid), ("changed", eid)]


def test_ticos_write_to_one_instance_is_its_own_not_its_series(api, google):
    from types import SimpleNamespace

    from backend import calendar_api

    google.put(ANA, "s1", recurrence=["RRULE:FREQ=WEEKLY"])
    tick(api)
    first = {"dateTime": stamp(SOON + timedelta(days=7))}
    raw = google.put(ANA, "s1_x", sequence=1, recurringEventId="s1", originalStartTime=first)
    written = gcal_live._written(raw, ANA, ANA)  # what gcal_live.patch answers for that instance
    assert written["event_id"] == "s1" and written["google_sequence"] == 1
    with api.app.state.store.transaction() as c:
        caller = {"actor": "bot:ops", "kind": "bot", "id": "ops"}
        calendar_api._record_google(c, SimpleNamespace(actor="bot:ops"), caller, "changed", written, ANA, None)
    tick(api)
    assert actions(api) == [("changed", "s1")]  # Tico's own announcement, once; the watcher adds nothing
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT event_id,sequence FROM calendar_watch_events ORDER BY event_id").fetchall()
    assert [(r["event_id"], r["sequence"]) for r in rows] == [("s1", 0), ("s1_x", 1)]


def test_a_future_cancelled_instance_is_kept_so_a_later_list_does_not_announce_it_again(api, google):
    google.put(ANA, "s1", recurrence=["RRULE:FREQ=WEEKLY"])
    tick(api)
    google.version += 1
    later = {"dateTime": stamp(SOON + timedelta(days=60))}
    google.events[ANA]["s1_y"] = {
        "id": "s1_y",
        "status": "cancelled",
        "recurringEventId": "s1",
        "originalStartTime": later,
    }
    google.log.append((google.version, ANA, "s1_y"))
    tick(api)
    assert actions(api) == [("cancelled", "s1")]
    with api.app.state.store.transaction() as c:  # long after the cancellation, before its time
        c.execute("UPDATE calendar_watch_events SET updated_at='2000-01-01T00:00:00Z'")
    tick(api)
    google.expired.add(watch_row(api)["sync_token"])
    tick(api)
    assert actions(api) == [("cancelled", "s1")]


def test_a_long_listing_goes_on_where_the_budget_stopped_and_tells_what_went_at_its_end(api, google):
    google.page_size = 2
    for n in range(5):
        google.put(ANA, f"e{n}")
    gcal_watch.tick(api.app.state.store, budget=OnePage())
    row = watch_row(api)
    assert row["page_token"] == "p2" and row["list_mode"] == "seed" and not row["watching_since_at"]
    gcal_watch.tick(api.app.state.store, budget=OnePage())
    gcal_watch.tick(api.app.state.store, budget=OnePage())
    row = watch_row(api)
    assert row["page_token"] is None and row["sync_token"] and row["watching_since_at"]
    starts = [c for c in google.calls if c[0] == "GET" and c[1].endswith("/events") and "pageToken" not in c[2]]
    assert len(starts) == 1  # one listing, never started again
    del google.events[ANA]["e4"]
    google.put(ANA, "e5")
    google.expired.add(row["sync_token"])
    gcal_watch.tick(api.app.state.store, budget=OnePage())
    assert actions(api) == []  # a cancellation is told only once the whole list is in
    google.down = True
    tick(api)  # a failure keeps the place
    google.down = False
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE calendar_watches SET checked_at='2000-01-01T00:00:00Z'")
    tick(api)
    assert sorted(actions(api)) == [("cancelled", "e4"), ("created", "e5")]
    relists = [c for c in google.calls if c[1].endswith("/events") and not {"pageToken", "syncToken"} & set(c[2])]
    assert len(relists) == 2  # the seed and the relist, each started once


def test_a_page_token_google_refuses_starts_that_listing_again(api, google):
    google.page_size = 2
    for n in range(3):
        google.put(ANA, f"e{n}")
    gcal_watch.tick(api.app.state.store, budget=OnePage())
    google.bad_pages.add(watch_row(api)["page_token"])
    google.page_size = 10
    tick(api)
    row = watch_row(api)
    assert row["page_token"] is None and row["sync_token"] and actions(api) == []


def test_a_calendar_no_longer_on_google_stops_being_watched(api, google):
    google.put(ANA, "e1")
    tick(api)
    google.addresses.clear()
    tick(api)
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM calendar_watches").fetchone()
        assert not c.execute("SELECT 1 FROM calendar_watch_events").fetchone()


def test_an_automation_hears_a_google_change_only_when_its_owner_reads_that_calendar(api, google):
    trigger = {"type": "event", "name": "calendar.event.created"}
    mine = post(api, "automations", {"name": "Ana's", "owner": "person:ana", "trigger": trigger, "steps": [NOTE_STEP]})
    theirs = post(
        api, "automations", {"name": "Ben's", "owner": "person:ana", "trigger": trigger, "steps": [NOTE_STEP]}
    )
    with api.app.state.store.transaction() as c:  # Ben holds no level on Ana's mailbox
        c.execute("UPDATE automations SET owner_id='person:ben' WHERE id=?", (theirs["id"],))
    tick(api)
    google.put(ANA, "e1")
    tick(api)
    with api.app.state.store.read() as c:
        runs = {
            aid: c.execute("SELECT count(*) FROM automation_runs WHERE automation_id=?", (aid,)).fetchone()[0]
            for aid in (mine["id"], theirs["id"])
        }
    assert runs == {mine["id"]: 1, theirs["id"]: 0}
    from backend import mailbox_access

    with api.app.state.store.transaction() as c:
        row = mailbox_access.ensure_mailbox(c, ANA)
        mailbox_access.grant(c, "person:ana", row["id"], "person", "ben", "read")
    google.put(ANA, "e2")
    tick(api)
    with api.app.state.store.read() as c:
        assert (
            c.execute("SELECT count(*) FROM automation_runs WHERE automation_id=?", (theirs["id"],)).fetchone()[0] == 1
        )


def test_tico_calendars_wake_the_same_automations_as_google_ones(api, google):
    from backend import calendar_store

    trigger = {"type": "event", "name": "calendar.event.*"}
    aid = post(api, "automations", {"name": "Any", "owner": "person:ana", "trigger": trigger, "steps": [NOTE_STEP]})[
        "id"
    ]
    with api.app.state.store.transaction() as c:
        calendar = calendar_store.calendar_for(c, "human", "ben")
        fields = {"title": "Standup", "start": stamp(SOON), "end": stamp(SOON + timedelta(minutes=15))}
        made = calendar_store.create_event(c, calendar["id"], fields, "human:ben")
    [(action, target, detail)] = announced(api)
    assert (action, target, detail) == ("created", made["event_id"], {"calendar_id": calendar["id"]})
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM automation_runs WHERE automation_id=?", (aid,)).fetchone()[0] == 1


# ---------------------------------------------------------------- the appointment routes


def test_the_appointments_read_gives_todays_meetings_live_with_no_snapshot(api, google):
    google.put(ANA, "m1", hangoutLink="https://meet.google.com/abc-defg-hij")
    google.put(ANA, "hold", attendees=[])  # a hold with nobody else and no link is no meeting
    read = get(api, "calendar/appointments?calendar=" + ANA)
    assert read["stale"] is False and read["updated_at"] and read["calendar"] == ANA
    [event] = read["events"]
    assert event["event_id"] == "m1" and event["title"] == "Planning m1" and event["attendees"] == [ANA, BEN]
    assert event["meeting_url"].startswith("https://meet.google.com/abc-defg-hij")
    assert set(event) == {"occurrence_id", "event_id", "title", "start", "end", "attendees", "meeting_url"}
    lists = len([c for c in google.calls if c[1].endswith("/events")])
    assert get(api, "calendar/upcoming")["events"] == read["events"]  # the native view, same 60 s read
    assert len([c for c in google.calls if c[1].endswith("/events")]) == lists


def test_a_google_failure_answers_stale_not_an_error(api, google):
    google.down = True
    read = get(api, "calendar/appointments?calendar=" + ANA)
    assert (read["events"], read["stale"], read["error"]) == ([], True, "gcal_unavailable")


BOOKING = {
    "calendar": ANA,
    "title": "Intro call",
    "start": stamp(SOON),
    "end": stamp(SOON + timedelta(minutes=30)),
    "attendees": [BEN],
    "description": "Agenda",
}


def test_a_booking_is_made_once_with_its_meet_link_and_its_invitation_queued(api, google):
    tick(api)  # the calendar is watched before the booking
    action = post(api, "calendar/appointments", BOOKING, key="book-1")["action"]
    assert action["status"] == "succeeded" and action["error"] == ""
    assert action["result"]["meeting_url"].startswith("https://meet.google.com/")
    [(method, path, query, body)] = google.posts()
    assert query["sendUpdates"] == "none" and query["conferenceDataVersion"] == "1"
    assert body["id"] == action["result"]["event_id"] and body["conferenceData"]["createRequest"]["requestId"]
    assert body["extendedProperties"]["private"]["tico_actor"] == "human:ana"
    again = post(api, "calendar/appointments", BOOKING, key="book-1")["action"]
    assert again["id"] == action["id"] and again["status"] == "succeeded" and len(google.posts()) == 1
    with api.app.state.store.read() as c:
        send = c.execute("SELECT * FROM email_sends WHERE calendar_action_id=?", (action["id"],)).fetchone()
        assert send["id"] == action["result"]["invitation_send_id"] and send["mailbox"] == ANA
    tick(api)  # the watcher sees Tico's own event and does not announce it again
    assert actions(api) == [("created", action["result"]["event_id"])]
    assert get(api, "calendar/actions/" + action["id"])["action"]["status"] == "succeeded"


def test_a_booking_google_never_answered_is_unknown_and_not_retried(api, google):
    google.down = True
    action = post(api, "calendar/appointments", {**BOOKING, "attendees": []})["action"]
    assert action["status"] == "unknown" and "check the calendar" in action["error"]
    assert len(google.posts()) == 1
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM email_sends").fetchone()
    google.down = False
    tick(api)
    assert len(google.posts()) == 1 and get(api, "calendar/actions/" + action["id"])["action"]["status"] == "unknown"


def test_a_booking_left_running_by_a_stopped_server_becomes_unknown(api, google):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO calendar_actions(id,requested_by,calendar_email,summary,start,end,attendees,description,"
            "add_meet,status,result,error,created_at,updated_at,started_at) "
            "VALUES('a1','human:ana',?,'x','s','e','[]','',1,'running','{}','',?,?,?)",
            (ANA, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
    tick(api)
    assert get(api, "calendar/actions/a1")["action"]["status"] == "unknown"


def test_change_calendar_off_refuses_a_booking_before_google(api, google):
    r = api.put(
        "/api/v2/google/permissions",
        json={"organize_mail": True, "send_mail": True, "change_calendar": False},
        headers=headers(),
    )
    assert r.status_code == 200, r.text
    refused = api.post("/api/v2/calendar/appointments", json=BOOKING, headers=headers())
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "company_off"
    assert google.posts() == []


def test_a_personal_connect_google_calendar_is_read_watched_and_booked_on_the_server(api, google):
    from backend import google_connect as GC

    google.own_grant(api, "ana", ANA, GC.READ_SCOPES + GC.ACT_SCOPES)
    with api.app.state.store.read() as c:
        assert gcal_live.connected(c, ANA) and gcal_watch.watched(c) == [ANA]
    google.put(ANA, "e1")
    tick(api)
    google.put(ANA, "e2")
    tick(api)
    assert actions(api) == [("created", "e2")]
    read = get(api, "calendar/appointments?calendar=" + ANA)
    assert read["stale"] is False and {e["event_id"] for e in read["events"]} == {"e1", "e2"}
    booked = post(api, "calendar/appointments", {**BOOKING, "attendees": []})["action"]
    assert booked["status"] == "succeeded" and len(google.posts()) == 1
    assert google.minted == []  # never the Workspace key: the person's own grant wins
    asked = {call["scope"] for call in google.refreshes.calls}
    assert asked == {GC.G + "calendar.events", " ".join(sorted(GC.SERVICE_SCOPES["calendar"]))}


def test_a_read_only_personal_grant_refuses_a_booking_before_google(api, google):
    from backend import google_connect as GC

    google.own_grant(api, "ana", ANA, GC.READ_SCOPES)
    booked = post(api, "calendar/appointments", {**BOOKING, "attendees": []})["action"]
    assert booked["status"] == "failed" and "read_only" in booked["error"]
    assert google.posts() == [] and google.minted == []
    assert all(call["scope"] != GC.G + "calendar.events" for call in google.refreshes.calls)


def test_a_calendar_the_server_holds_no_credential_for_is_refused_not_queued(api, google):
    google.addresses.clear()
    read = get(api, "calendar/appointments?calendar=" + ANA)
    assert (read["events"], read["stale"], read["error"]) == ([], True, "not_connected")
    refused = api.post("/api/v2/calendar/appointments", json=BOOKING, headers=headers())
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "not_connected"
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM calendar_actions").fetchone()


def test_no_google_token_reaches_an_answer_an_event_or_a_row(api, google):
    tick(api)
    google.put(ANA, "e1")
    answers = [
        post(api, "calendar/appointments", BOOKING),
        get(api, "calendar/appointments?calendar=" + ANA),
        get(api, "calendar/upcoming"),
    ]
    tick(api)
    google.down = True
    tick(api)
    assert SECRET not in json.dumps(answers)
    with api.app.state.store.read() as c:
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            try:
                rows = c.execute(f'SELECT * FROM "{table}"').fetchall()
            except Exception:  # noqa: BLE001 - a virtual table this build cannot read holds nothing of ours
                continue
            assert SECRET not in json.dumps([[str(v) for v in row] for row in rows]), table
