"""A call with the Assistant from its sheet (docs/meet.md, Call the Assistant): in the person's own Assistant chat its
voice turns run as the Assistant, it gets no prep task, and its follow-ups ask for the person and run as personal
turns; a private follow-up of the Assistant's anywhere else is refused (#4781)."""

import json
import uuid

import pytest

from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.assistant import room
from backend.tests.helpers.calls import join, meet, start_call, thread  # noqa: F401 - meet installs the call doubles
from backend.tests.helpers.credentials import setup as credential_setup
from backend.tests.helpers.live_meetings import live  # noqa: F401 - the meet fixture needs the live API
from backend.tests.helpers.model_subscriptions import add, company, give, machine, on

DONE = {"outcome": "completed", "last_seq": 0}


@pytest.fixture(autouse=True)
def _header_in_time(monkeypatch):
    """The run header is built within its budget on a loaded test machine too."""
    from backend import run_header

    monkeypatch.setattr(run_header, "BUDGET_SECONDS", 30.0)


def _resident(api):
    """A runner for the Assistant that keeps a resident voice session."""
    credential_setup(api)
    company(api)
    default = get(api, "providers")["default"]
    on(api, "assistant", default["runtime"], default["model"])
    caps = ("model_subscriptions_v1", "private_lanes_v1", "voice_sessions_v1")
    worker = machine(api, signed_in=True, bot="assistant", caps=caps)
    give(api, worker, "claude", add(api)["id"])
    return worker


def _claim(api, worker):
    attempt = post(api, "jobs/claim", {"bot": "assistant"}, token=worker["token"])["attempt"]
    if attempt:
        post(api, f"attempts/{attempt['id']}/started", {"thread_id": "synthetic-call"}, token=worker["token"])
    return attempt


def _filed(api, task_id):
    with api.app.state.store.read() as c:
        row = c.execute("SELECT requester_id,owner_id FROM tasks WHERE id=?", (task_id,)).fetchone()
        creator = c.execute("SELECT actor FROM events WHERE action='task.create' AND target=?", (task_id,)).fetchone()
    return (*row, creator[0])


def test_a_call_in_your_assistant_chat_runs_as_the_assistant_and_its_follow_ups_are_your_personal_work(meet):
    worker = _resident(meet)
    rid = room(meet)["room_id"]
    call = start_call(meet, rid, bots=["assistant"])
    assert [c["id"] for c in get(meet, f"calls/live?thread={rid}")["calls"]] == [call["id"]]
    assert get(meet, f"calls/live?thread={thread(meet)}")["calls"] == []
    session = _claim(meet, worker)
    assert session["message"]["refs"]["live_meeting"]["voice_call"] == call["id"]
    assert _claim(meet, worker) is None  # no prep task in the person's own room
    with meet.app.state.store.read() as c:
        row = c.execute("SELECT * FROM attempts WHERE id=?", (session["id"],)).fetchone()
        assert meet.app.state.auth.assistant_principal(c, row) is None  # its turns are the Assistant's, never Ana's

    by_assistant = post(meet, f"calls/{call['id']}/follow-ups", {"ask": "Find the ship date"}, token=session["token"])
    by_ana = post(meet, f"calls/{call['id']}/follow-ups", {"ask": "Book the freight"})
    assert _filed(meet, by_assistant["task_id"]) == ("human:ana", "bot:assistant", "bot:assistant")
    assert _filed(meet, by_ana["task_id"]) == ("human:ana", "bot:assistant", "human:ana")
    tracked = get(meet, "tasks?requester=human:ana&status=open&exact=true&brief=true")
    assert {by_assistant["task_id"], by_ana["task_id"]} <= {t["id"] for t in tracked["tasks"]}

    post(meet, f"attempts/{session['id']}/complete", DONE, worker["token"])
    runs = [_claim(meet, worker), None]
    post(meet, f"attempts/{runs[0]['id']}/complete", DONE, worker["token"])
    runs[1] = _claim(meet, worker)
    assert {r["task"]["id"] for r in runs} == {by_assistant["task_id"], by_ana["task_id"]}
    assert all(r["personal"] is True for r in runs)  # Ana's own work, like her routines' turns


def test_the_assistant_keeps_no_private_follow_up_from_any_other_call(meet):
    worker = _resident(meet)
    call = start_call(meet, thread(meet), bots=["assistant"])
    voice = _claim(meet, worker)
    assert voice["message"]["refs"]["live_meeting"]["voice_call"] == call["id"]
    for token in (voice["token"], "ana-test"):
        refused = post(meet, f"calls/{call['id']}/follow-ups", {"ask": "Find it"}, token=token, expected=422)
        assert refused["error"]["code"] == "private"
    with meet.app.state.store.read() as c:
        assert not c.execute(
            "SELECT 1 FROM tasks WHERE owner_id=? AND private=1", (H.bot_actor("assistant"),)
        ).fetchone()


def test_only_the_owner_directly_or_the_assistant_in_a_lease_from_that_call_files_the_owners_private_work(meet):
    worker = _resident(meet)
    meet.app.state.store.settings.test_identities["ana-via-test"] = Identity(
        "human:ana", "human", "ana@acme.example", via="assistant"
    )
    mine = start_call(meet, room(meet)["room_id"], bots=["assistant"])
    post(meet, f"attempts/{_claim(meet, worker)['id']}/complete", DONE, worker["token"])
    other = start_call(meet, thread(meet), bots=["assistant"])
    lease = _claim(meet, worker)
    assert lease["message"]["refs"]["live_meeting"]["voice_call"] == other["id"]
    # The Assistant in another call's lease, and Ana through her Assistant, file no private work for Ana.
    refused = post(meet, f"calls/{mine['id']}/follow-ups", {"ask": "Find it"}, token=lease["token"], expected=422)
    assert refused["error"]["code"] == "private"
    via = meet.post(f"/api/v2/calls/{mine['id']}/follow-ups", json={"ask": "Find it"}, headers=headers("ana-via-test"))
    assert via.status_code >= 400
    with meet.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM call_follow_ups WHERE call_id=?", (mine["id"],)).fetchone()


def test_a_call_in_a_persons_own_room_gets_no_prep_whoever_starts_it_with_whichever_agent(meet):
    rid = room(meet)["room_id"]
    with meet.app.state.store.transaction() as c:  # Ben is a member of Ana's room, as a migrated room may grant
        c.execute("UPDATE conversations SET audience='personal' WHERE id=?", (rid,))
        c.execute(
            "INSERT INTO thread_members(id,thread_id,actor,role,added_by,created_at,updated_at) "
            "VALUES(?,?,'human:ben','member','human:ana',?,?)",
            (str(uuid.uuid4()), rid, H.now(), H.now()),
        )
    # Only the Assistant joins a call there (#4861); one Ben starts with it gets no prep either.
    refused = post(meet, f"threads/{rid}/calls", {"title": "Call", "bots": ["ops"]}, token="ben-test", expected=409)
    assert refused["error"]["code"] == "assistant_alone"
    call = start_call(meet, rid, token="ben-test", bots=["assistant"])
    assert call["connector"] == "human:ben" and call["bots"] == ["assistant"]
    with meet.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM call_bot_notes WHERE call_id=?", (call["id"],)).fetchone()
        assert not c.execute("SELECT 1 FROM tasks WHERE title LIKE 'Prepare for %'").fetchone()


# --- A private call in a person's own room reads as them, as their own chat does (#4861): its own voice session only.


def _private_call(api):
    """A call in Ana's Assistant chat and its resident voice session, claimed: (call, session, worker)."""
    worker = _resident(api)
    call = start_call(api, room(api)["room_id"], bots=["assistant"])
    session = _claim(api, worker)
    assert session["message"]["refs"]["live_meeting"]["voice_call"] == call["id"]
    return call, session, worker


def _scope(api, attempt_id):
    from backend import rooms

    with api.app.state.store.read() as c:
        return rooms._run_scope(c, attempt_id)


def _anas_errand(api):
    """A private task of Ana's own, which only she (and whatever reads as her) may read."""
    with api.app.state.store.transaction() as c:
        return H.task_create(c, "human:ana", "Ana's private errand", "Hers alone.", "human:ana", private=True)["id"]


def test_the_private_calls_voice_session_reads_your_private_tasks_as_your_own_chat_does(meet):
    call, session, _ = _private_call(meet)
    errand = _anas_errand(meet)
    # Its claim's run header has the snapshot the person's own chat gets (run_header.snapshot, read as Ana).
    assert session["personal"] is True and "Needs you (" in session["run_header"]
    assert _scope(meet, session["id"]) == ("person", "human:ana")
    assert get(meet, f"tasks/{errand}", session["token"])["task"]["id"] == errand
    # A personal lease gets no git credential.
    post(meet, "github/token", {"bot": "assistant"}, token=session["token"], expected=403)


def test_a_call_that_is_no_longer_private_reads_nothing_of_yours_and_its_lease_stays_personal(meet):
    call, session, _ = _private_call(meet)
    errand = _anas_errand(meet)
    store = meet.app.state.store
    for joined in (
        (
            "INSERT INTO live_meeting_bots(meeting_id,bot,joined_by,joined_at) VALUES(?,?,?,?)",
            (call["meeting_id"], "ops", "human:ana", H.now()),
        ),
        (
            "INSERT INTO call_participants(call_id,actor,joined_at,in_call) VALUES(?,?,?,1)",
            (call["id"], "human:ben", H.now()),
        ),
    ):
        with store.transaction() as c:  # another agent or person got in some other way
            c.execute(*joined)
        assert _scope(meet, session["id"]) == ("", "")
        get(meet, f"tasks/{errand}", session["token"], expected=404)
        post(meet, "github/token", {"bot": "assistant"}, token=session["token"], expected=403)
        with store.transaction() as c:
            c.execute("DELETE FROM live_meeting_bots WHERE meeting_id=? AND bot='ops'", (call["meeting_id"],))
            c.execute("DELETE FROM call_participants WHERE call_id=? AND actor='human:ben'", (call["id"],))
    assert _scope(meet, session["id"]) == ("person", "human:ana")


def test_a_forged_live_meeting_ref_gets_no_person(meet):
    from backend import rooms

    call, session, worker = _private_call(meet)
    post(meet, f"attempts/{session['id']}/complete", DONE, worker["token"])
    with meet.app.state.store.transaction() as c:  # a message elsewhere that names the call
        conv = H.open_conversation(c, "human:ana", ["human:ben", "bot:assistant"], kind="chat", subject="Forged")
        ref = {"meeting_id": call["meeting_id"], "voice_call": call["id"], "notes_stamp": ""}
        forged = H.feed(c, "bot:assistant", "Reply exactly READY", conv, refs={"live_meeting": ref})
    with meet.app.state.store.read() as c:
        job = c.execute("SELECT id FROM jobs WHERE message_id=?", (forged["id"],)).fetchone()[0]
        # Only the call's own voice job names a person, whatever a message's refs say.
        assert rooms.call_person(c, job) == "" and rooms.call_person(c, session["job_id"]) == "human:ana"
    later = _claim(meet, worker)
    assert later is None or not later.get("personal")


def test_nobody_joins_your_assistant_chat_or_its_call_while_the_call_is_on(meet):
    call, _session, _worker = _private_call(meet)
    rid = room(meet)["room_id"]
    with meet.app.state.store.read() as c:
        assert json.loads(c.execute("SELECT ring FROM calls WHERE id=?", (call["id"],)).fetchone()[0]) == []
    with meet.app.state.store.transaction() as c:
        c.execute("UPDATE conversations SET audience='personal' WHERE id=?", (rid,))  # a room that could add members
    widened = post(meet, f"threads/{rid}/members", {"actor": "human:ben"}, expected=409)
    assert widened["error"]["code"] == "call_private"
    # A second agent is refused by either attach route.
    for path in (f"calls/{call['id']}/bots", f"live-meetings/{call['meeting_id']}/bots"):
        refused = post(meet, path, {"bots": ["ops"]}, expected=409)
        assert refused["error"]["code"] == "assistant_alone"
    # Nor is the chat linked to Slack.
    linked = post(meet, "slack/links", {"thread_id": rid, "channel": "#ops"}, expected=409)
    assert linked["error"]["code"] == "call_private"
    # Nor does any agent replace the Assistant once it leaves: the call is the Assistant's alone for its life.
    left = meet.request("DELETE", f"/api/v2/calls/{call['id']}/bots/assistant", json={}, headers=headers("ana-test"))
    assert left.status_code == 200, left.text
    for path in (f"calls/{call['id']}/bots", f"live-meetings/{call['meeting_id']}/bots"):
        refused = post(meet, path, {"bots": ["ops"]}, expected=409)
        assert refused["error"]["code"] == "assistant_alone"


def test_a_chat_another_member_already_reads_is_never_private(meet):
    worker = _resident(meet)
    rid = room(meet)["room_id"]
    for member in ("bot:ops", "human:ben"):
        with meet.app.state.store.transaction() as c:  # a migrated room with a member besides Ana and the Assistant
            c.execute("UPDATE conversations SET audience='personal' WHERE id=?", (rid,))
            c.execute("DELETE FROM thread_members WHERE thread_id=? AND actor NOT IN ('human:ana')", (rid,))
            c.execute(
                "INSERT INTO thread_members(id,thread_id,actor,role,added_by,created_at,updated_at) "
                "VALUES(?,?,?,'member','human:ana',?,?)",
                (str(uuid.uuid4()), rid, member, H.now(), H.now()),
            )
        call = start_call(meet, rid, bots=["assistant"])
        session = _claim(meet, worker)
        assert session["message"]["refs"]["live_meeting"]["voice_call"] == call["id"]
        assert not session.get("personal") and _scope(meet, session["id"]) == ("", ""), member
        post(meet, f"attempts/{session['id']}/complete", DONE, worker["token"])
        meet.request("DELETE", f"/api/v2/calls/{call['id']}/bots/assistant", json={}, headers=headers("ana-test"))
        post(meet, f"calls/{call['id']}/end", {})


def test_no_outside_link_while_the_call_is_on_and_a_person_who_cannot_sign_in_is_read_as_nobody(meet):
    from backend import threads
    from backend.store import Problem, encode

    call, session, _ = _private_call(meet)
    rid = room(meet)["room_id"]
    with meet.app.state.store.transaction() as c:
        try:
            threads.add_outside_delivery(c, rid, "slack:C123")
            raise AssertionError("an outside link was added during the call")
        except Problem as exc:
            assert exc.status == 409 and exc.code == "call_private"
    with meet.app.state.store.transaction() as c:  # Ana's sign-in is turned off mid-call
        roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        for person in roster["people"]:
            if person["id"] == "ana":
                person["sign_in"] = False
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(roster),))
    assert _scope(meet, session["id"])[0] == "gone"


def test_a_call_booked_in_your_assistant_chat_invites_nobody_and_takes_no_other_agent(meet):
    calls = meet.app.state.calls
    rid = room(meet)["room_id"]
    with meet.app.state.store.transaction() as c:
        c.execute("UPDATE conversations SET audience='personal' WHERE id=?", (rid,))
        c.execute(
            "INSERT INTO thread_members(id,thread_id,actor,role,added_by,created_at,updated_at) "
            "VALUES(?,?,'human:ben','member','human:ana',?,?)",
            (str(uuid.uuid4()), rid, H.now(), H.now()),
        )
    with meet.app.state.store.read() as c:
        assert calls._invited(c, "human:ana", rid) == []
        ana = meet.app.state.auth.identity_for_actor(c, "human:ana")
        when = ("2030-01-01T10:00:00Z", "2030-01-01T10:30:00Z")
        try:
            calls.schedule_plan(c, ana, rid, "Call", when, ["ops"], "k1")
            raise AssertionError("a booking with another agent was planned")
        except Exception as exc:  # refused before any calendar event is planned
            assert getattr(exc, "code", "") == "assistant_alone"


def test_a_chat_with_a_stored_participant_besides_you_and_the_assistant_is_never_private(meet):
    worker = _resident(meet)
    rid = room(meet)["room_id"]
    with meet.app.state.store.transaction() as c:
        c.execute(
            "UPDATE conversations SET participants=? WHERE id=?", (json.dumps(["bot:assistant", "human:ben"]), rid)
        )
    call = start_call(meet, rid, bots=["assistant"])
    session = _claim(meet, worker)
    assert session["message"]["refs"]["live_meeting"]["voice_call"] == call["id"]
    assert not session.get("personal") and _scope(meet, session["id"]) == ("", "")


def test_nobody_is_rung_into_a_private_call_nor_given_its_meeting(meet):
    call, _session, _ = _private_call(meet)
    rid = room(meet)["room_id"]
    # Refused before any target is looked at: one who cannot read the chat, and one who can.
    for actor in ("human:ben", "human:ana"):
        refused = post(meet, f"calls/{call['id']}/ring", {"actor": actor}, expected=409)
        assert refused["error"]["code"] == "call_private"
    with meet.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM thread_members WHERE thread_id=? AND actor='human:ben'", (rid,)).fetchone()
        assert json.loads(c.execute("SELECT ring FROM calls WHERE id=?", (call["id"],)).fetchone()[0]) == []
    for widen in ({"add": ["ben"]}, {"visibility": "team"}):
        shared = post(meet, f"live-meetings/{call['meeting_id']}/access", widen, expected=409)
        assert shared["error"]["code"] == "call_private"
    with meet.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM live_meeting_access WHERE meeting_id=?", (call["meeting_id"],)).fetchone()


def test_a_pinned_lease_reads_nothing_while_its_person_may_not_sign_in_even_once_the_call_no_longer_qualifies(meet):
    from backend.store import encode

    call, session, _ = _private_call(meet)
    with meet.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO call_participants(call_id,actor,joined_at,in_call) VALUES(?,?,?,1)",
            (call["id"], "human:ben", H.now()),
        )
        roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        for person in roster["people"]:
            if person["id"] == "ana":
                person["sign_in"] = False
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(roster),))
    assert _scope(meet, session["id"]) == ("gone", "")


def test_a_booked_call_in_your_assistant_chat_shares_its_meeting_with_nobody_before_its_first_join(meet):
    from datetime import datetime, timedelta, timezone

    rid = room(meet)["room_id"]
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=2)
    body = {"title": "Call", "starts_at": start.isoformat(), "ends_at": (start + timedelta(minutes=30)).isoformat()}
    booked = post(meet, f"threads/{rid}/calls", {**body, "bots": ["assistant"]})
    assert booked["state"] == "scheduled"
    for widen in ({"add": ["ben"]}, {"visibility": "team"}):
        shared = post(meet, f"live-meetings/{booked['meeting_id']}/access", widen, expected=409)
        assert shared["error"]["code"] == "call_private"


def test_the_private_calls_lease_reads_threads_as_you_and_none_once_you_cannot_sign_in(meet):
    from backend.store import encode

    call, session, _ = _private_call(meet)
    from backend import threads

    with meet.app.state.store.transaction() as c:  # a thread of Ana's and Ben's, which the Assistant alone may not read
        ana = threads.viewer(meet.app.state.auth, meet.app.state.auth.identity_for_actor(c, "human:ana"))
        dm = threads.create(c, ana, title="Plans", audience="members", members=["human:ben"])["id"]
    assert get(meet, f"threads/{dm}", session["token"])["id"] == dm
    listed = get(meet, "threads?limit=200", session["token"])
    assert dm in json.dumps(listed)
    with meet.app.state.store.transaction() as c:  # Ana's sign-in is turned off
        roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        for person in roster["people"]:
            if person["id"] == "ana":
                person["sign_in"] = False
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (encode(roster),))
    get(meet, f"threads/{dm}", session["token"], expected=404)
    get(meet, f"threads/{call['thread_id']}", session["token"], expected=404)
    assert dm not in json.dumps(get(meet, "threads?limit=200", session["token"]))


def test_a_call_whose_meeting_names_anyone_else_gets_no_snapshot(meet):
    worker = _resident(meet)
    rid = room(meet)["room_id"]
    for widen in (
        "INSERT INTO live_meeting_access VALUES(?,'human:ben','human:ana',?)",
        "INSERT INTO live_meeting_humans VALUES(?,'human:ben',?)",
        "UPDATE live_meetings SET visibility='team' WHERE id=? AND ?<>''",
    ):
        call = start_call(meet, rid, bots=["assistant"])
        with meet.app.state.store.transaction() as c:  # written before the voice session is claimed
            c.execute(widen, (call["meeting_id"], H.now()))
        session = _claim(meet, worker)
        assert session["message"]["refs"]["live_meeting"]["voice_call"] == call["id"]
        assert "Needs you" not in (session.get("run_header") or "") and not session.get("personal"), widen
        post(meet, f"attempts/{session['id']}/complete", DONE, worker["token"])
        meet.request("DELETE", f"/api/v2/calls/{call['id']}/bots/assistant", json={}, headers=headers("ana-test"))
        post(meet, f"calls/{call['id']}/end", {})
