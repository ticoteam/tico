"""A call with the Assistant from its sheet (docs/meet.md, Call the Assistant): in the person's own Assistant chat its
voice turns run as the Assistant, it gets no prep task, and its follow-ups ask for the person and run as personal
turns; a private follow-up of the Assistant's anywhere else is refused (#4781)."""

import uuid

from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.assistant import room
from backend.tests.helpers.calls import join, meet, start_call, thread  # noqa: F401 - meet installs the call doubles
from backend.tests.helpers.credentials import setup as credential_setup
from backend.tests.helpers.live_meetings import live  # noqa: F401 - the meet fixture needs the live API
from backend.tests.helpers.model_subscriptions import add, company, give, machine, on

DONE = {"outcome": "completed", "last_seq": 0}


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
    call = start_call(meet, rid, token="ben-test", bots=["ops"])
    assert call["connector"] == "human:ben" and call["bots"] == ["ops"]
    with meet.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM call_bot_notes WHERE call_id=?", (call["id"],)).fetchone()
        assert not c.execute("SELECT 1 FROM tasks WHERE title LIKE 'Prepare for %'").fetchone()
