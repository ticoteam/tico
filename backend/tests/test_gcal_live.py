"""Live Google Calendar reads and writes: the viewer's token, Google's sharing, the contract's occurrence shape."""

from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from backend import gcal_live as G
from backend import google_workspace as W
from backend.store import Problem

ANA, BEN = "ana@acme.example", "ben@acme.example"
START = datetime(2026, 11, 9, tzinfo=timezone.utc)
END = datetime(2026, 11, 16, tzinfo=timezone.utc)


@pytest.fixture
def minted(monkeypatch):
    calls = []

    def mint(c, mailbox, service, row=None, scopes=None):
        calls.append((mailbox, service, scopes))
        return {"access_token": "write-token" if scopes == W.WORKSPACE_SCOPES["calendar"] else "read-token"}

    monkeypatch.setattr(W, "mint", mint)
    return calls


@pytest.fixture
def google(monkeypatch, minted):
    """Google as the module reaches it: `answers` maps (method, path) to a list of (status, body), used in turn
    (the last one repeats); every call is recorded as (method, path, query, data, token)."""
    state = {"calls": [], "answers": {}}

    def call(url, *, token, method="GET", data=None):
        parts = urlsplit(url)
        path = unquote(parts.path.removeprefix("/calendar/v3/"))
        state["calls"].append((method, path, parse_qs(parts.query), data, token))
        queue = state["answers"].get((method, path)) or [(404, {})]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(G, "gcal_call", call)
    return state


def timed(eid="e1", **extra):
    return {
        "id": eid,
        "status": "confirmed",
        "summary": "Product sync",
        "htmlLink": "https://www.google.com/calendar/event?eid=abc",
        "iCalUID": eid + "@google.com",
        "start": {"dateTime": "2026-11-10T09:00:00-08:00", "timeZone": "America/Los_Angeles"},
        "end": {"dateTime": "2026-11-10T09:30:00-08:00", "timeZone": "America/Los_Angeles"},
        "organizer": {"email": BEN},
        "attendees": [
            {"email": BEN, "organizer": True, "responseStatus": "accepted", "displayName": "Ben"},
            {"email": ANA, "self": True, "responseStatus": "tentative"},
            {"email": "guest@other.example", "responseStatus": "needsAction"},
        ],
        "hangoutLink": "https://meet.google.com/abc-defg-hij",
        **extra,
    }


def page(items, role="owner", **extra):
    return {"accessRole": role, "timeZone": "America/Los_Angeles", "items": items, **extra}


def test_a_timed_event_becomes_one_occurrence_with_the_viewers_reply_and_authuser_links(google, minted):
    google["answers"][("GET", "calendars/" + ANA + "/events")] = [(200, page([timed()]))]
    people = {BEN: {"type": "human", "id": "h-ben", "name": "Ben Ng"}}
    [row] = G.events(None, ANA, ANA, START, END, people=people)
    assert row["occurrence_id"] == "google:" + ANA + ":e1" and row["event_id"] == "e1"
    assert row["home"] == "google" and row["kind"] == "event" and row["calendar"]["ref"] == "google:" + ANA
    assert (row["start"], row["end"], row["all_day"]) == ("2026-11-10T17:00:00Z", "2026-11-10T17:30:00Z", False)
    assert row["time_zone"] == "America/Los_Angeles" and row["recurring"] is False and row["busy_only"] is False
    assert row["response"] == "tentative"
    assert row["attendees"][0] == {
        "type": "human",
        "id": "h-ben",
        "email": BEN,
        "name": "Ben Ng",
        "response": "accepted",
    }
    assert row["attendees"][2]["type"] == "external" and row["attendees"][2]["response"] == "needs_action"
    assert row["meeting_url"] == "https://meet.google.com/abc-defg-hij?authuser=ana%40acme.example"
    assert row["open_url"] == "https://www.google.com/calendar/event?eid=abc&authuser=ana%40acme.example"
    assert row["can"] == {"join": True, "edit": True} and row["ical_uid"] == "e1@google.com"
    method, path, query, _, token = google["calls"][0]
    assert (method, token) == ("GET", "read-token")
    assert query["singleEvents"] == ["true"] and query["orderBy"] == ["startTime"]
    assert query["timeMin"] == ["2026-11-09T00:00:00Z"] and query["timeMax"] == ["2026-11-16T00:00:00Z"]
    assert "q" not in query
    assert minted == [(ANA, "calendar", W.READ_ONLY_SCOPES["calendar"])]


def test_all_day_recurring_cancelled_and_out_of_office(google):
    items = [
        {"id": "gone", "status": "cancelled"},
        {"id": "where", "eventType": "workingLocation", "start": {"date": "2026-11-10"}, "end": {"date": "2026-11-11"}},
        {"id": "offsite", "summary": "Offsite", "start": {"date": "2026-11-11"}, "end": {"date": "2026-11-13"}},
        timed("series_20261112T170000Z", recurringEventId="series", summary="Standup"),
        {
            "id": "ooo",
            "eventType": "outOfOffice",
            "summary": "Out of office",
            "start": {"dateTime": "2026-11-13T00:00:00Z"},
            "end": {"dateTime": "2026-11-14T00:00:00Z"},
        },
    ]
    google["answers"][("GET", "calendars/" + ANA + "/events")] = [(200, page(items))]
    rows = {row["event_id"]: row for row in G.events(None, ANA, ANA, START, END)}
    assert set(rows) == {"offsite", "series", "ooo"}
    offsite = rows["offsite"]
    assert (offsite["start"], offsite["end"], offsite["all_day"]) == ("2026-11-11", "2026-11-13", True)
    standup = rows["series"]
    assert standup["recurring"] and standup["occurrence_id"] == "google:" + ANA + ":series_20261112T170000Z"
    assert rows["ooo"]["kind"] == "away" and rows["ooo"]["meeting_url"] is None


def test_private_or_time_only_events_show_as_busy_without_details(google):
    private = {
        "id": "p1",
        "visibility": "private",
        "start": {"dateTime": "2026-11-10T18:00:00Z"},
        "end": {"dateTime": "2026-11-10T19:00:00Z"},
    }
    google["answers"][("GET", "calendars/" + BEN + "/events")] = [(200, page([private, timed("e2")], role="reader"))]
    hidden, shown = G.events(None, ANA, BEN, START, END)
    assert hidden["busy_only"] and hidden["title"] == "Busy"
    assert hidden["attendees"] == [] and hidden["meeting_url"] is None and hidden["open_url"] is None
    assert hidden["can"] == {"join": False, "edit": False}
    assert not shown["busy_only"] and shown["title"] == "Product sync" and shown["can"]["edit"] is False

    google["answers"][("GET", "calendars/" + BEN + "/events")] = [(200, page([timed("e3")], role="freeBusyReader"))]
    [row] = G.events(None, ANA, BEN, START, END)
    assert row["busy_only"] and row["title"] == "Busy" and row["response"] is None and row["ical_uid"] is None


@pytest.mark.parametrize("status", [403, 404])
def test_a_coworker_calendar_that_refuses_its_events_answers_busy_blocks(google, status):
    google["answers"][("GET", "calendars/" + BEN + "/events")] = [(status, {})]
    google["answers"][("POST", "freeBusy")] = [
        (200, {"calendars": {BEN: {"busy": [{"start": "2026-11-10T17:00:00Z", "end": "2026-11-10T18:00:00Z"}]}}})
    ]
    [row] = G.events(None, ANA, BEN, START, END)
    assert row["busy_only"] and row["title"] == "Busy" and row["event_id"] is None
    assert (row["start"], row["end"]) == ("2026-11-10T17:00:00Z", "2026-11-10T18:00:00Z")
    fb = google["calls"][-1]
    assert fb[0] == "POST" and fb[3]["items"] == [{"id": BEN}] and fb[4] == "read-token"


def test_a_calendar_free_busy_cannot_check_is_a_refusal_not_an_empty_week(google):
    google["answers"][("GET", "calendars/" + BEN + "/events")] = [(404, {})]
    google["answers"][("POST", "freeBusy")] = [(200, {"calendars": {BEN: {"errors": [{"reason": "notFound"}]}}})]
    with pytest.raises(Problem) as caught:
        G.events(None, ANA, BEN, START, END)
    assert caught.value.status == 403
    assert G.freebusy(None, ANA, [BEN], START, END) == {}


def test_the_viewers_own_calendar_refusing_is_an_error_not_busy_blocks(google):
    google["answers"][("GET", "calendars/" + ANA + "/events")] = [(403, {})]
    with pytest.raises(Problem) as caught:
        G.events(None, ANA, ANA, START, END)
    assert caught.value.code == "gcal_refused"
    assert all(call[1] != "freeBusy" for call in google["calls"])


@pytest.mark.parametrize(
    "status, body, code, expected",
    [
        (0, {}, "gcal_unavailable", 502),
        (400, {}, "gcal_query", 422),
        (401, {}, "gcal_refused", 403),
        (403, {}, "gcal_refused", 403),
        (403, {"reason": "rateLimitExceeded"}, "gcal_unavailable", 503),
        (404, {}, "not_found", 404),
        (429, {}, "gcal_unavailable", 503),
        (500, {}, "gcal_unavailable", 502),
        (503, {}, "gcal_unavailable", 502),
    ],
)
def test_google_errors_are_clear_refusals(google, status, body, code, expected):
    google["answers"][("GET", "calendars/" + ANA + "/events")] = [(status, body)]
    with pytest.raises(Problem) as caught:
        G.events(None, ANA, ANA, START, END)
    assert (caught.value.code, caught.value.status) == (code, expected)
    assert caught.value.retryable == (code == "gcal_unavailable")


def test_search_passes_q_and_a_bots_events_pass_its_tag(google):
    google["answers"][("GET", "calendars/" + ANA + "/events")] = [(200, page([]))]
    G.events(None, ANA, ANA, START, END, q="board review")
    G.events(None, ANA, ANA, START, END, bot="b-1")
    assert google["calls"][0][2]["q"] == ["board review"]
    assert google["calls"][1][2]["privateExtendedProperty"] == ["tico_bot=b-1"]


def test_paging_follows_tokens_but_stops_at_the_page_bound_and_the_limit(google):
    endless = [(200, page([timed("e" + str(n))], nextPageToken="t" + str(n))) for n in range(40)]
    google["answers"][("GET", "calendars/" + ANA + "/events")] = endless
    rows = G.events(None, ANA, ANA, START, END)
    assert len(google["calls"]) == G.MAX_PAGES and len(rows) == G.MAX_PAGES
    assert google["calls"][1][2]["pageToken"] == ["t0"] and "pageToken" not in google["calls"][0][2]

    google["calls"].clear()
    rows = G.events(None, ANA, ANA, START, END, limit=2)
    assert len(rows) == 2 and len(google["calls"]) == 2
    assert google["calls"][0][2]["maxResults"] == ["2"]


def test_insert_builds_googles_body_with_the_write_scope(google, minted):
    created = timed("new1", summary="Kickoff")
    google["answers"][("POST", "calendars/" + ANA + "/events")] = [(200, created)]
    fields = {
        "title": "Kickoff",
        "start": "2026-11-12T10:00:00",
        "end": "2026-11-12T10:30:00",
        "time_zone": "America/Los_Angeles",
        "attendees": [BEN, {"email": "Guest@Other.example"}, BEN],
        "notes": "Agenda",
        "location": "Room 1",
        "repeat": "RRULE:FREQ=WEEKLY;BYDAY=TH",
        "add_meet": True,
        "visibility": "private",
        "actor": "bot:b-1",
        "bot": "b-1",
    }
    row = G.insert(None, ANA, ANA, fields, send_updates="none")
    assert row["occurrence_id"] == "google:" + ANA + ":new1"
    method, _, query, body, token = google["calls"][0]
    assert method == "POST" and token == "write-token"
    assert query["sendUpdates"] == ["none"] and query["conferenceDataVersion"] == ["1"]
    assert body["summary"] == "Kickoff" and body["description"] == "Agenda" and body["location"] == "Room 1"
    assert body["start"] == {"dateTime": "2026-11-12T10:00:00", "timeZone": "America/Los_Angeles"}
    assert body["attendees"] == [{"email": BEN}, {"email": "guest@other.example"}]
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=TH"] and body["visibility"] == "private"
    assert body["conferenceData"]["createRequest"]["conferenceSolutionKey"] == {"type": "hangoutsMeet"}
    assert body["extendedProperties"] == {"private": {"tico_actor": "bot:b-1", "tico_bot": "b-1"}}
    assert minted == [(ANA, "calendar", W.WORKSPACE_SCOPES["calendar"])]


def test_an_all_day_insert_without_meet_sends_dates_and_no_conference_version(google):
    google["answers"][("POST", "calendars/" + ANA + "/events")] = [
        (200, {"id": "d1", "summary": "Away", "start": {"date": "2026-11-12"}, "end": {"date": "2026-11-13"}})
    ]
    G.insert(
        None,
        ANA,
        ANA,
        {"title": "Away", "start": "2026-11-12", "end": "2026-11-13", "all_day": True, "actor": "human:h-ana"},
    )
    _, _, query, body, _ = google["calls"][0]
    assert body["start"] == {"date": "2026-11-12"} and body["end"] == {"date": "2026-11-13"}
    assert "conferenceDataVersion" not in query and query["sendUpdates"] == ["all"] and "conferenceData" not in body
    with pytest.raises(Problem):
        G.insert(None, ANA, ANA, {"title": "No actor", "start": "2026-11-12", "end": "2026-11-13", "all_day": True})


def test_patch_sends_only_given_fields_and_keeps_the_bots_tag(google):
    path = "calendars/" + ANA + "/events/e1"
    google["answers"][("GET", path)] = [(200, timed(extendedProperties={"private": {"tico_bot": "b-1"}}))]
    google["answers"][("PATCH", path)] = [(200, timed(summary="Renamed"))]
    row = G.patch(None, ANA, ANA, "e1", {"title": "Renamed", "actor": "human:h-ana"})
    assert row["title"] == "Renamed"
    body = google["calls"][-1][3]
    assert set(body) == {"summary", "extendedProperties"}
    assert body["extendedProperties"]["private"] == {"tico_bot": "b-1", "tico_actor": "human:h-ana"}


def test_delete_treats_already_deleted_as_done_and_refuses_otherwise(google):
    path = "calendars/" + ANA + "/events/e1"
    google["answers"][("DELETE", path)] = [(204, {})]
    G.delete(None, ANA, ANA, "e1", send_updates="all")
    assert google["calls"][-1][2]["sendUpdates"] == ["all"] and google["calls"][-1][4] == "write-token"
    google["answers"][("DELETE", path)] = [(410, {})]
    G.delete(None, ANA, ANA, "e1")
    google["answers"][("DELETE", path)] = [(403, {})]
    with pytest.raises(Problem) as caught:
        G.delete(None, ANA, ANA, "e1")
    assert caught.value.code == "gcal_refused"


def test_calendar_list_and_time_zone(google):
    google["answers"][("GET", "users/me/calendarList")] = [
        (200, {"items": [{"id": ANA, "summary": ANA, "primary": True, "accessRole": "owner"}], "nextPageToken": "n"}),
        (200, {"items": [{"id": "legal@acme.example", "summary": "Legal", "summaryOverride": "Legal team"}]}),
    ]
    google["answers"][("GET", "users/me/settings/timezone")] = [(200, {"value": "Europe/Lisbon"})]
    assert G.calendar_list(None, ANA) == [
        {"id": ANA, "name": ANA, "primary": True, "access_role": "owner", "time_zone": None},
        {"id": "legal@acme.example", "name": "Legal team", "primary": False, "access_role": "", "time_zone": None},
    ]
    assert G.time_zone(None, ANA) == "Europe/Lisbon"


def test_connected_needs_the_company_key_for_this_address_and_a_calendar_scope(monkeypatch):
    row = {"authorized_scopes": None}
    monkeypatch.setattr(W, "active", lambda c: row)
    monkeypatch.setattr(
        W, "authority", lambda c, address: "workspace" if address.endswith("@acme.example") else "legacy"
    )
    assert G.connected(None, ANA)  # a key from before the read-only scopes holds the full calendar scope
    assert not G.connected(None, "someone@other.example")
    row["authorized_scopes"] = '["https://www.googleapis.com/auth/gmail.modify"]'
    assert not G.connected(None, ANA)
    monkeypatch.setattr(W, "authority", lambda c, address: "personal")
    row["authorized_scopes"] = None
    assert not G.connected(None, ANA)  # a person's own Connect Google keeps the computer path
