"""Google Meet calls in threads (backend/calls.py, backend/google_meet.py, docs/meet.md): the provider a start picks,
the space it makes once, who it rings, the call view each viewer gets, the status Google's polls give, the Recorder
as the recording source, and the privacy of all of it. Google is never asked: `google_meet.meet_call` and the
Workspace token mint are replaced. Every name is synthetic (Acme, acme.example)."""

import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend import call_ring, google_meet, threads
from backend import calls as calls_module
from backend import google_workspace as W
from backend.store import H, encode
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.calls import join, meet, thread, thread_texts  # noqa: F401
from backend.tests.helpers.live_meetings import live, settle  # noqa: F401

MEET = "https://meet.googleapis.com/v2/"
URI = "https://meet.google.com/abc-mnop-xyz"


class FakeMeet:
    """Google Meet's API as the client reads it: spaces, each with an active conference or none, and the people in it."""

    def __init__(self):
        self.spaces, self.calls, self.refuse = {}, [], False
        self.fail = None  # a status for every status read: 0 (no answer), 429, 503...
        self.hang = {}  # space name -> an Event its status read waits on, as a Google that does not answer
        self.lock_fail = False

    def __call__(self, method, url, *, token, body=None):
        self.calls.append((method, url, body))
        if self.refuse:
            return 403, {"error": {"status": "PERMISSION_DENIED"}}
        path = url[len(MEET) :].split("?")[0]
        if method == "GET" and path in self.hang:
            self.hang[path].wait(10)
        if method == "GET" and self.fail is not None:
            return self.fail, {}
        if method == "PATCH" and path.startswith("spaces/"):
            if self.lock_fail:
                return 500, {}
            self.spaces[path]["access"] = body["config"]["accessType"]
            return 200, {"name": path, "meetingUri": URI, "config": body["config"]}
        if method == "POST" and path == "spaces":
            name = f"spaces/space{len(self.spaces) + 1}"
            self.spaces[name] = {"conference": None, "people": []}
            return 200, {"name": name, "meetingUri": URI, "meetingCode": "abc-mnop-xyz", "config": body["config"]}
        if method == "POST" and path.endswith(":endActiveConference"):
            self.spaces[path.split(":")[0]]["conference"] = None
            return 200, {}
        if method == "GET" and path.startswith("spaces/"):
            space = self.spaces.get(path)
            if space is None:
                return 404, {}
            answer = {"name": path, "meetingUri": URI, "meetingCode": "abc-mnop-xyz"}
            if space["conference"]:
                answer["activeConference"] = {"conferenceRecord": space["conference"]}
            return 200, answer
        if method == "GET" and path.endswith("/participants"):
            space = next(s for s in self.spaces.values() if s["conference"] == path.rsplit("/", 1)[0])
            people = [
                {"name": f"{path}/{i}", "signedinUser": {"displayName": n}} for i, n in enumerate(space["people"])
            ]
            return 200, {"participants": people} if people else {}  # Google answers {} for an empty conference
        raise AssertionError("unexpected Meet request: " + method + " " + url)

    def now(self, space, conference, *people):
        self.spaces[space]["conference"] = conference
        self.spaces[space]["people"] = list(people)


@pytest.fixture
def google(meet, monkeypatch):
    """A connected Workspace key on acme.example whose check proved the Meet scope, Meet's fake API, and names on the
    roster (Meet matches people by them)."""
    fake = FakeMeet()
    monkeypatch.setattr(google_meet, "meet_call", fake)
    minted = []

    def mint(c, mailbox, service, row=None, scopes=None):
        minted.append((mailbox, service))
        return {"access_token": "meet-token", "expiry": "", "scopes": W.MEET_SCOPES["meet"]}

    monkeypatch.setattr(W, "mint", mint)
    fake.minted = minted
    with meet.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO google_workspaces(id,domain,client_email,client_id,key_id,key_sealed,status,created_by,"
            "created_at,updated_at,authorized_scopes) VALUES('ws1','acme.example','sa@acme.example','1','k1','x',"
            "'connected','human:ana',?,?,?)",
            (H.now(), H.now(), json.dumps(W.scope_urls())),
        )
        people = [
            {"id": "ana", "name": "Ana Alvarez", "email": "ana@acme.example", "primary_for": ["*"]},
            {"id": "ben", "name": "Ben Brown", "email": "ben@acme.example", "primary_for": ["ops"]},
            {"id": "cara", "name": "Cara Chen", "email": "cara@acme.example"},
        ]
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode({"people": people}),))
    return fake


def start(api, cid, token="ana-test", key=None, expected=200, **body):
    r = api.post(f"/api/v2/threads/{cid}/calls", json=body, headers=headers(token, key))
    assert r.status_code == expected, r.text
    return r.json()


def row(api, call_id):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone())


def updates(api, call_id):
    with api.app.state.store.read() as c:
        return c.execute(
            "SELECT COUNT(*) FROM changes WHERE kind='call.updated' AND subject_id=?", (call_id,)
        ).fetchone()[0]


def reads(fake, space):
    """How many times the space's status was read."""
    return len([c for c in fake.calls if c[0] == "GET" and c[1] == MEET + space])


def locked(fake, space, wait=2.0):
    """Whether the space was locked, waiting a little for a lock the sweep left to the poll pool."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if fake.spaces[space].get("access") == "RESTRICTED":
            return True
        time.sleep(0.02)
    return False


def backdate(api, call_id, seconds, *columns):
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with api.app.state.store.transaction() as c:
        for column in columns:
            c.execute(f"UPDATE calls SET {column}=? WHERE id=?", (stamp, call_id))


# ----------------------------------------------------------------------------- start


def test_auto_starts_a_google_meet_with_its_card_and_a_retry_makes_no_second_space(google, meet):
    cid = thread(meet)
    key = str(uuid.uuid4())
    call = start(meet, cid, key=key, title="Acme sync")
    again = start(meet, cid, key=key, title="Acme sync")
    assert again["id"] == call["id"]
    creates = [c for c in google.calls if c[0] == "POST" and c[1] == MEET + "spaces"]
    assert len(creates) == 1 and creates[0][2] == {"config": {"accessType": "TRUSTED"}}
    assert call["provider"] == "google_meet" and call["meet_uri"] == URI
    assert call["join_url"] == URI + "?authuser=ana%40acme.example"
    assert google.minted[0] == ("ana@acme.example", "meet")
    assert meet.app.state.call_media.recorders == {}  # no LiveKit for a Meet
    with meet.app.state.store.read() as c:
        card = H.message(c, call["card_message_id"])
    assert card["body"] == "Call" and card["refs"]["call"] == {
        "id": call["id"],
        "meeting_id": call["meeting_id"],
        "card": True,
    }
    assert threads.quiet_notice(card)  # the card wakes no bot in the thread
    # Ben reads the same call with his own address in the link.
    assert get(meet, f"calls/{call['id']}", token="ben-test")["join_url"] == URI + "?authuser=ben%40acme.example"


def test_auto_falls_back_to_tico_meet_without_meet_and_503_with_neither(google, meet):
    cid = thread(meet)
    with meet.app.state.store.transaction() as c:
        c.execute("UPDATE google_workspaces SET authorized_scopes=?", (json.dumps(W.LEGACY_SCOPES),))
    assert get(meet, f"threads/{cid}/calls")["providers"] == ["tico"]
    call = start(meet, cid)
    assert call["provider"] == "tico" and call["join_url"] is None
    assert not [c for c in google.calls if c[0] == "POST"]
    post(meet, f"calls/{call['id']}/end", {})
    meet.app.state.call_media = None
    listed = get(meet, f"threads/{cid}/calls")
    assert listed["providers"] == [] and listed["available"] is False
    assert start(meet, cid, expected=503)["error"]["code"] == "meet_unavailable"
    assert start(meet, cid, expected=503, provider="google_meet")["error"]["code"] == "meet_unavailable"


def test_google_refusing_the_space_falls_back_on_auto_and_refuses_when_named(google, meet):
    cid = thread(meet)
    google.refuse = True
    assert start(meet, cid, expected=503, provider="google_meet")["error"]["code"] == "meet_unavailable"
    assert start(meet, cid)["provider"] == "tico"


# ----------------------------------------------------------------------------- ringing


def test_a_dm_rings_the_other_person_never_a_bot_and_a_big_thread_rings_nobody(google, meet, monkeypatch):
    rung = []
    monkeypatch.setattr(call_ring, "HOOKS", [lambda app, call_id, actors, payload: rung.append((actors, payload))])
    cid = thread(meet)  # Ana, Ben and the Ops bot
    call = start(meet, cid)
    assert json.loads(row(meet, call["id"])["ring"]) == ["human:ben"]
    assert rung[0][0] == ["human:ben"]
    assert rung[0][1]["tag"] == "call-" + call["id"] and rung[0][1]["url"].endswith("?call=" + call["id"])
    assert call["ring"] is False and get(meet, f"calls/{call['id']}", token="ben-test")["ring"] is True
    assert [c["id"] for c in get(meet, "calls/ringing", token="ben-test")["calls"]] == [call["id"]]
    join(meet, call["id"], "ben-test")  # opening it from Tico answers the ring
    assert get(meet, "calls/ringing", token="ben-test")["calls"] == []
    post(meet, f"calls/{call['id']}/end", {})

    big = thread(meet, subject="Acme all hands")
    with meet.app.state.store.transaction() as c:
        for n in range(9):
            c.execute(
                "INSERT INTO thread_members(id,thread_id,actor,role,added_by,created_at,updated_at) "
                "VALUES(?,?,?,'member','human:ana',?,?)",
                (str(uuid.uuid4()), big, f"human:guest{n}", H.now(), H.now()),
            )
    quiet = start(meet, big)
    assert json.loads(row(meet, quiet["id"])["ring"]) == [] and len(rung) == 1


# ----------------------------------------------------------------------------- privacy


def test_a_non_reader_cannot_see_ring_claim_or_report_on_a_call_or_its_mark(google, meet):
    cid = thread(meet)
    call = start(meet, cid)
    for method, path in (
        ("get", f"calls/{call['id']}"),
        ("post", f"calls/{call['id']}/recorder"),
        ("post", f"calls/{call['id']}/presence"),
        ("post", f"calls/{call['id']}/join"),
    ):
        body = {"names": ["Cara Chen"]} if path.endswith("presence") else {}
        r = getattr(meet, method)(
            "/api/v2/" + path, **({} if method == "get" else {"json": body}), headers=headers("cara-test")
        )
        assert r.status_code == 404, (path, r.text)
    assert get(meet, "calls/ringing", token="cara-test")["calls"] == []
    rail = get(meet, "threads/rail", token="cara-test")
    assert all(t["id"] != cid for t in rail["pinned"] + rail["threads"])
    assert meet.get(f"/api/v2/threads/{cid}", headers=headers("cara-test")).status_code == 404
    shown = get(meet, f"threads/{cid}", token="ben-test")
    assert shown["live_call"] == {"id": call["id"], "live_count": 0}


def test_a_meet_call_has_no_token(google, meet):
    call = start(meet, thread(meet))
    answer = join(meet, call["id"], "ben-test")
    assert answer["url"] == URI + "?authuser=ben%40acme.example" and "token" not in answer
    r = meet.post(f"/api/v2/calls/{call['id']}/token", headers=headers("ben-test"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_tico_meet"
    r = meet.post(f"/api/v2/calls/{call['id']}/token", headers=headers("cara-test"))
    assert r.status_code == 404


# ----------------------------------------------------------------------------- status from Google


def test_polls_set_who_is_in_the_call_and_write_one_update_per_change(google, meet):
    cid = thread(meet)
    call = start(meet, cid)
    space = row(meet, call["id"])["meet_space"]
    calls = meet.app.state.calls
    google.now(space, "conferenceRecords/c1")  # the pre-join page opens a conference with nobody in it
    calls.poll(call["id"])
    assert row(meet, call["id"])["meet_seen_at"] is None and row(meet, call["id"])["status_source"] == "google"
    google.now(space, "conferenceRecords/c1", "Ana Alvarez", "ben brown", "A Guest")
    calls.poll(call["id"])
    seen = updates(meet, call["id"])
    calls.poll(call["id"])  # nothing moved: no second update
    assert updates(meet, call["id"]) == seen
    stored = row(meet, call["id"])
    assert stored["live_count"] == 3 and stored["meet_seen_at"] and stored["meet_conference"] == "conferenceRecords/c1"
    view = get(meet, f"calls/{call['id']}", token="ben-test")
    assert view["live_count"] == 3 and view["status_source"] == "google" and view["seen_at"]
    assert {p["actor"]: p["in_call"] for p in view["participants"]} == {"human:ana": True, "human:ben": True}
    assert view["ring"] is False  # seen in the Meet: answered
    rail = get(meet, "threads/rail", token="ben-test")
    assert [t["live_call"] for t in rail["pinned"] + rail["threads"] if t["id"] == cid] == [
        {"id": call["id"], "live_count": 3}
    ]
    shown = get(meet, f"threads/{cid}", token="ben-test")
    assert shown["live_call"] == {"id": call["id"], "live_count": 3}
    google.now(space, "conferenceRecords/c1", "Ana Alvarez")
    calls.poll(call["id"])
    assert updates(meet, call["id"]) == seen + 1
    view = get(meet, f"calls/{call['id']}")
    assert {p["actor"]: p["in_call"] for p in view["participants"]} == {"human:ana": True, "human:ben": False}


def test_a_drop_and_rejoin_within_a_minute_keeps_the_call_and_a_minute_empty_ends_it(google, meet):
    cid = thread(meet)
    call = start(meet, cid)
    space = row(meet, call["id"])["meet_space"]
    calls = meet.app.state.calls
    google.now(space, "conferenceRecords/c1", "Ana Alvarez")
    calls.poll(call["id"])
    google.now(space, None)  # everyone left: Google ends the conference at once
    calls.poll(call["id"])
    assert row(meet, call["id"])["state"] == "live"
    google.now(space, "conferenceRecords/c2", "Ana Alvarez")  # a rejoin makes a new conference on the same space
    calls.poll(call["id"])
    assert row(meet, call["id"])["meet_conference"] == "conferenceRecords/c2"
    google.now(space, None)
    calls.poll(call["id"])
    assert row(meet, call["id"])["meet_empty_at"]  # the first read that showed nobody
    backdate(meet, call["id"], 600, "meet_seen_at")
    backdate(meet, call["id"], 90, "meet_active_at")
    backdate(meet, call["id"], 61, "meet_empty_at")
    calls.poll(call["id"])
    ended = row(meet, call["id"])
    assert ended["state"] == "ended" and ended["end_reason"] == "meet_ended" and ended["live_count"] == 0
    view = get(meet, f"calls/{call['id']}")
    assert 505 <= view["duration_s"] <= 515  # first seen to last seen, across conferences: not the empty minute
    assert locked(google, space)  # the sweep's end locks the space too
    texts = thread_texts(meet, cid)
    assert not any(text.startswith("Call transcript") for text in texts)  # no Recorder wrote one


def test_nobody_seen_in_two_minutes_is_no_answer_even_with_a_conference_open(google, meet):
    call = start(meet, thread(meet))
    space = row(meet, call["id"])["meet_space"]
    google.now(space, "conferenceRecords/c1")
    meet.app.state.calls.poll(call["id"])
    assert row(meet, call["id"])["state"] == "live"
    backdate(meet, call["id"], 121, "started_at")
    meet.app.state.calls.poll(call["id"])
    assert row(meet, call["id"])["end_reason"] == "no_answer"


def test_google_refusing_leaves_no_status_and_eight_hours_on_the_call_is_stale(google, meet):
    call = start(meet, thread(meet))
    space = row(meet, call["id"])["meet_space"]
    google.now(space, "conferenceRecords/c1", "Ana Alvarez")
    meet.app.state.calls.poll(call["id"])
    google.refuse = True
    meet.app.state.calls.poll(call["id"])
    stored = row(meet, call["id"])
    assert stored["state"] == "live" and stored["status_source"] is None and stored["live_count"] == 0
    backdate(meet, call["id"], 8 * 3600 + 5, "started_at")
    meet.app.state.calls.sweep()
    assert row(meet, call["id"])["end_reason"] == "stale"


def test_the_connector_s_end_ends_google_s_conference(google, meet):
    call = start(meet, thread(meet))
    space = row(meet, call["id"])["meet_space"]
    google.now(space, "conferenceRecords/c1", "Ana Alvarez")
    r = meet.post(f"/api/v2/calls/{call['id']}/end", json={}, headers=headers("ben-test"))
    assert r.status_code == 403
    ended = post(meet, f"calls/{call['id']}/end", {})
    assert ended["state"] == "ended" and ended["end_reason"] == "ended_by_connector"
    ended_at = google.calls.index(("POST", MEET + space + ":endActiveConference", {}))
    lock = ("PATCH", MEET + space + "?updateMask=config.accessType", {"config": {"accessType": "RESTRICTED"}})
    assert google.calls.index(lock) > ended_at  # then the space is locked, so its link is spent
    assert meet.app.state.call_media.ended == []  # no LiveKit room to close


def test_a_space_google_will_not_lock_still_ends_the_call(google, meet, caplog):
    call = start(meet, thread(meet))
    google.lock_fail = True
    ended = post(meet, f"calls/{call['id']}/end", {})
    assert ended["state"] == "ended" and ended["end_reason"] == "ended_by_connector"
    assert any(c[0] == "PATCH" for c in google.calls)
    assert "was not locked" in caplog.text


# ----------------------------------------------------------------------------- the Recorder


def test_the_first_recorder_claim_wins_and_only_its_holder_writes_the_transcript(google, meet):
    cid = thread(meet)
    call = start(meet, cid)
    meeting = call["meeting_id"]
    claim = post(meet, f"calls/{call['id']}/recorder", {}, token="ben-test")
    assert claim == {
        "source": True,
        "meeting_id": meeting,
        "title": call["title"],
        "source_actor": "human:ben",
        "has_transcript": False,
    }
    assert post(meet, f"calls/{call['id']}/recorder", {}, token="ben-test")["source"] is True  # kept by its holder
    second = post(meet, f"calls/{call['id']}/recorder", {})
    assert second["source"] is False and second["source_actor"] == "human:ben"
    assert get(meet, f"live-meetings/{meeting}", token="ben-test")["state"] == "live"  # the Recorder's end check
    chunk = {"chunks": [{"seq": 1, "speaker": "Ben", "start_ms": 0, "end_ms": 900, "text": "We ship Friday"}]}
    post(meet, f"live-meetings/{meeting}/chunks", chunk, token="ben-test")
    correction = {"revision": 2, "speaker": "Ben", "start_ms": 0, "end_ms": 900, "text": "We ship on Friday"}
    post(meet, f"live-meetings/{meeting}/chunks/1/corrections", correction, token="ben-test")
    r = meet.post(f"/api/v2/live-meetings/{meeting}/control", json={"action": "pause"}, headers=headers("ben-test"))
    assert r.status_code == 403  # the source writes the transcript and controls nothing else
    assert post(meet, f"calls/{call['id']}/recorder", {})["has_transcript"] is True
    r = meet.request("DELETE", f"/api/v2/calls/{call['id']}/recorder", json={}, headers=headers("ana-test"))
    assert r.status_code == 403
    released = meet.request("DELETE", f"/api/v2/calls/{call['id']}/recorder", json={}, headers=headers("ben-test"))
    assert released.status_code == 200 and released.json()["source"] is False
    assert row(meet, call["id"])["recorder_actor"] is None
    r = meet.post(f"/api/v2/live-meetings/{meeting}/chunks", json=chunk, headers=headers("ben-test"))
    assert r.status_code == 403  # no longer the source
    post(meet, f"calls/{call['id']}/end", {})
    texts = thread_texts(meet, cid)
    transcripts = [text for text in texts if text.startswith("Call transcript")]
    assert len(transcripts) == 1 and "We ship on Friday" in transcripts[0]
    assert get(meet, f"calls/{call['id']}")["transcript_message_id"]


def test_presence_counts_while_google_is_silent_and_is_ignored_once_google_answers(google, meet):
    call = start(meet, thread(meet))
    space = row(meet, call["id"])["meet_space"]
    names = {"names": ["Ana Alvarez", "Ben Brown", "A Guest"]}
    r = meet.post(f"/api/v2/calls/{call['id']}/presence", json=names, headers=headers())
    assert r.status_code == 403  # only the recording source reports
    post(meet, f"calls/{call['id']}/recorder", {})
    google.refuse = True
    meet.app.state.calls.poll(call["id"])
    shown = post(meet, f"calls/{call['id']}/presence", names)
    assert shown["status_source"] == "recorder" and shown["live_count"] == 3 and shown["seen_at"]
    google.refuse = False
    google.now(space, "conferenceRecords/c1", "Ana Alvarez")
    meet.app.state.calls.poll(call["id"])
    shown = post(meet, f"calls/{call['id']}/presence", names)
    assert shown["status_source"] == "google" and shown["live_count"] == 1


# ----------------------------------------------------------------------------- the Workspace key's Meet scope


def test_the_key_check_proves_the_meet_scope_without_changing_what_tico_may_do(meet, monkeypatch):
    with meet.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO google_workspaces(id,domain,client_email,client_id,key_id,key_sealed,status,created_by,"
            "created_at,updated_at,authorized_scopes) VALUES('ws1','acme.example','sa@acme.example','1','k1','x',"
            "'connected','human:ana',?,?,?)",
            (H.now(), H.now(), json.dumps(sorted(set(W.scope_urls()) - {W.MEET_SCOPE}))),
        )
        row_ = W.active(c)
        assert W.narrowable(row_) and not W.meet_ready(c)
    answers = {"meet": None}

    def mint(c, mailbox, service, row=None, scopes=None):
        if service == "meet" and answers["meet"] is None:
            from backend.store import Problem

            raise Problem("not_connected", "refused", 403, extra={"kind": "unauthorized_client"})
        return {"access_token": "t", "expiry": "", "scopes": scopes or []}

    def call(url, *, data=None, token=None):
        return (answers["meet"] or 200, {}) if url == W.MEET_PROBE else (200, {})

    monkeypatch.setattr(W, "mint", mint)
    monkeypatch.setattr(W, "google_call", call)
    assert W._probe(meet.app.state.store, row_, "ana@acme.example")[3] == sorted(set(W.scope_urls()) - {W.MEET_SCOPE})
    answers["meet"] = 403  # the Meet API is off in the key's project
    assert W.MEET_SCOPE not in W._probe(meet.app.state.store, row_, "ana@acme.example")[3]
    answers["meet"] = 404  # a space that cannot exist, answered as missing: the scope and the API both work
    ok, _, _, proven = W._probe(meet.app.state.store, row_, "ana@acme.example")
    assert ok and proven == W.scope_urls()


def test_the_declared_meet_call_answers_match_the_live_ones(google, meet):
    from backend.tests.helpers.openapi_v2 import conforms

    document = meet.get("/api/v2/openapi.json", headers=headers("ana-test")).json()
    by_id = {op["operationId"]: op for ops in document["paths"].values() for op in ops.values()}

    def send(method, path, operation, body=None, token="ana-test"):
        response = meet.request(method, path, json=body, headers=headers(token))
        assert response.status_code == 200, (path, response.text)
        schema = by_id[operation]["responses"]["200"]["content"]["application/json"]["schema"]
        assert conforms(response.json(), schema, document) is None, (operation, response.text[:300])
        return response.json()

    cid = thread(meet)
    call_id = send("POST", f"/api/v2/threads/{cid}/calls", "startCall", {"title": "Acme standup"})["id"]
    assert send("GET", f"/api/v2/threads/{cid}/calls", "listCalls")["calls"]
    send("GET", f"/api/v2/calls/{call_id}", "getCall")
    send("GET", "/api/v2/calls/ringing", "listRingingCalls", token="ben-test")
    send("GET", f"/api/v2/threads/{cid}", "getThread", token="ben-test")
    send("POST", f"/api/v2/calls/{call_id}/join", "joinCall", {}, token="ben-test")
    send("POST", f"/api/v2/calls/{call_id}/recorder", "claimCallRecorder", {})
    send("POST", f"/api/v2/calls/{call_id}/presence", "reportCallPresence", {"names": ["Ana Alvarez"]})
    send("DELETE", f"/api/v2/calls/{call_id}/recorder", "releaseCallRecorder", {})
    send("POST", f"/api/v2/calls/{call_id}/end", "endCall", {})


# ----------------------------------------------------------------------------- polling that Google cannot stall


def test_a_hanging_google_does_not_hold_up_the_sweep_or_the_other_calls(google, meet, monkeypatch):
    monkeypatch.setattr(calls_module, "POLL_BUDGET_SECONDS", 0.3)
    calls = meet.app.state.calls
    slow, quick = start(meet, thread(meet, subject="Acme ops")), start(meet, thread(meet, subject="Acme sales"))
    slow_space, quick_space = row(meet, slow["id"])["meet_space"], row(meet, quick["id"])["meet_space"]
    google.now(slow_space, "conferenceRecords/c1", "Ana Alvarez")
    google.now(quick_space, "conferenceRecords/c2", "Ana Alvarez", "Ben Brown")
    answer = threading.Event()
    google.hang[slow_space] = answer
    try:
        began = time.monotonic()
        calls.sweep()
        assert time.monotonic() - began < 3  # the pass waits its budget, not Google
        assert row(meet, quick["id"])["live_count"] == 2 and row(meet, slow["id"])["status_source"] is None
        participants = [c for c in google.calls if c[1].startswith(MEET + "conferenceRecords/c2/participants")]
        assert len(participants) == 1  # each call's people read once per pass
        calls.sweep()  # the slow read is still out: it is not asked again on top of itself
        assert reads(google, slow_space) == 1 and reads(google, quick_space) == 2
    finally:
        answer.set()
    calls.polling[slow["id"]].result(timeout=5)
    calls.sweep()
    assert row(meet, slow["id"])["live_count"] == 1 and row(meet, slow["id"])["status_source"] == "google"


def test_short_google_failures_keep_the_status_and_never_count_as_nobody(google, meet):
    calls = meet.app.state.calls
    call = start(meet, thread(meet))
    space = row(meet, call["id"])["meet_space"]
    google.now(space, "conferenceRecords/c1", "Ana Alvarez", "Ben Brown")
    calls.poll(call["id"])
    seen = updates(meet, call["id"])
    for status in (503, 429, 0):  # Google's own error, slow down, no answer
        google.fail = status
        calls.poll(call["id"])
        stored = row(meet, call["id"])
        assert (stored["state"], stored["status_source"], stored["live_count"]) == ("live", "google", 2)
    assert updates(meet, call["id"]) == seen
    view = get(meet, f"calls/{call['id']}")
    assert {p["actor"]: p["in_call"] for p in view["participants"]} == {"human:ana": True, "human:ben": True}
    # Google was out for five minutes, then answers that everyone has gone: the empty minute starts at that answer.
    backdate(meet, call["id"], 400, "meet_seen_at")
    backdate(meet, call["id"], 300, "meet_active_at", "meet_polled_at")
    google.fail = None
    google.now(space, None)
    calls.poll(call["id"])
    assert row(meet, call["id"])["state"] == "live"
    backdate(meet, call["id"], 61, "meet_empty_at")
    calls.poll(call["id"])
    ended = row(meet, call["id"])
    assert ended["end_reason"] == "meet_ended"
    assert 95 <= get(meet, f"calls/{call['id']}")["duration_s"] <= 105  # first seen to last seen


def test_a_failing_read_eight_hours_on_ends_stale_only_once_google_has_been_quiet(google, meet):
    calls = meet.app.state.calls
    call = start(meet, thread(meet))
    space = row(meet, call["id"])["meet_space"]
    google.now(space, "conferenceRecords/c1", "Ana Alvarez")
    calls.poll(call["id"])
    backdate(meet, call["id"], 8 * 3600 + 5, "started_at")
    google.fail = 503
    calls.poll(call["id"])
    assert row(meet, call["id"])["state"] == "live"  # a good read moments ago: one failure is not 8 hours of none
    backdate(meet, call["id"], calls_module.QUIET_SECONDS + 5, "meet_polled_at")
    calls.poll(call["id"])
    assert row(meet, call["id"])["end_reason"] == "stale"


def test_a_failing_read_backs_off_1_2_4_passes_and_a_good_one_resets_it(google, meet):
    calls = meet.app.state.calls
    call = start(meet, thread(meet))
    space = row(meet, call["id"])["meet_space"]
    google.fail = 503
    pattern = []
    for _ in range(8):
        before = reads(google, space)
        calls.poll_live([call["id"]])
        pattern.append(reads(google, space) - before)
    assert pattern == [1, 0, 1, 0, 0, 1, 0, 0]
    google.fail = None
    for _ in range(3):
        calls.poll_live([call["id"]])  # the last skip passes, then a good read
    assert call["id"] not in calls.backoff
    before = reads(google, space)
    calls.poll_live([call["id"]])
    assert reads(google, space) == before + 1


def test_a_call_nobody_was_seen_in_has_no_duration_and_keeps_who_watched_it(google, meet):
    call = start(meet, thread(meet))
    backdate(meet, call["id"], 121, "started_at")
    meet.app.state.calls.poll(call["id"])  # Google answers: nobody in it
    view = get(meet, f"calls/{call['id']}")
    assert view["end_reason"] == "no_answer" and view["duration_s"] is None
    assert view["status_source"] == "google"  # watched and nobody came: a missed call
    # Nothing watched this one (Google refused every read): it ends with no source, a plain ended call.
    unwatched = start(meet, thread(meet, subject="Acme ops"))
    google.refuse = True
    meet.app.state.calls.poll(unwatched["id"])
    google.refuse = False
    post(meet, f"calls/{unwatched['id']}/end", {})
    view = get(meet, f"calls/{unwatched['id']}")
    assert view["state"] == "ended" and view["duration_s"] is None and view["status_source"] is None
    # A Recorder that watched a call and lets go after its end leaves the source in place.
    watched = start(meet, thread(meet, subject="Acme sales"))
    post(meet, f"calls/{watched['id']}/recorder", {})
    google.refuse = True
    meet.app.state.calls.poll(watched["id"])
    post(meet, f"calls/{watched['id']}/presence", {"names": ["Ana Alvarez"]})
    post(meet, f"calls/{watched['id']}/end", {})
    meet.request("DELETE", f"/api/v2/calls/{watched['id']}/recorder", json={}, headers=headers("ana-test"))
    view = get(meet, f"calls/{watched['id']}")
    assert view["status_source"] == "recorder" and view["duration_s"] == 0


# ----------------------------------------------------------------------------- the Recorder's tail and who may claim


def test_the_recorder_s_last_chunks_are_taken_for_90_seconds_after_the_call_ends(google, meet):
    cid = thread(meet)
    call = start(meet, cid)
    meeting = call["meeting_id"]
    post(meet, f"calls/{call['id']}/recorder", {}, token="ben-test")

    def chunk(seq, text):
        return {
            "chunks": [{"seq": seq, "speaker": "Ben", "start_ms": seq * 1000, "end_ms": seq * 1000 + 900, "text": text}]
        }

    post(meet, f"live-meetings/{meeting}/chunks", chunk(1, "We ship Friday"), token="ben-test")
    post(meet, f"calls/{call['id']}/end", {})
    assert not [t for t in thread_texts(meet, cid) if t.startswith("Call transcript")]  # it waits for the tail
    post(meet, f"live-meetings/{meeting}/chunks", chunk(2, "Ben owns the rollout"), token="ben-test")
    fix = {"revision": 2, "speaker": "Ben", "start_ms": 1000, "end_ms": 1900, "text": "We ship on Friday"}
    post(meet, f"live-meetings/{meeting}/chunks/1/corrections", fix, token="ben-test")
    backdate(meet, call["id"], 91, "ended_at")
    late = meet.post(f"/api/v2/live-meetings/{meeting}/chunks", json=chunk(3, "Late"), headers=headers("ben-test"))
    assert late.status_code == 403
    fix = {**fix, "revision": 3, "text": "Too late"}
    late = meet.post(f"/api/v2/live-meetings/{meeting}/chunks/1/corrections", json=fix, headers=headers("ben-test"))
    assert late.status_code in (403, 409)
    meet.app.state.calls.sweep()
    transcripts = [t for t in thread_texts(meet, cid) if t.startswith("Call transcript")]
    assert len(transcripts) == 1 and "We ship on Friday" in transcripts[0] and "Ben owns the rollout" in transcripts[0]
    assert "Late" not in transcripts[0]


def test_only_someone_in_the_call_may_claim_its_recording(google, meet):
    with meet.app.state.store.transaction() as c:
        cid = threads.create(c, "human:ana", title="Acme all hands", audience="company")["id"]
    call = start(meet, cid)
    assert get(meet, f"calls/{call['id']}", token="cara-test")["id"] == call["id"]  # Cara reads the thread
    r = meet.post(f"/api/v2/calls/{call['id']}/recorder", json={}, headers=headers("cara-test"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_in_call"
    assert row(meet, call["id"])["recorder_actor"] is None
    join(meet, call["id"], "cara-test")  # she opens the call from Tico: now she is in it
    assert post(meet, f"calls/{call['id']}/recorder", {}, token="cara-test")["source"] is True
