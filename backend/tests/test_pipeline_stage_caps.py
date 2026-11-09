"""A stage's card cap (D68, D69): the pipeline's owner sets it per stage through the stage route, a blank cap is no cap, and a
cap-0 stage is "On ice". A bot or automation cannot move a card into a full stage (409 stage_full) or out of On ice; a
person can, and the answer says over_cap. A link or a spawn into a full stage leaves the card where it is, and the
pipeline manager lists it as waiting for room."""

import uuid
from datetime import datetime, timezone

import pytest

from backend.tests.helpers.api import get, headers
from backend import assigned_start as A
from backend import delivery as DL
from backend import hubdb as H
from backend import pipeline_engine as E
from backend import pipeline_exceptions as X
from backend import pipeline_links as L
from backend import pipeline_manager as PM
from backend import invariants as I
from backend import pipeline_requirements as R
from backend import task_check_backs as CB
from backend import rules as RU
from backend import task_roundup as TR
from backend.auth import Identity
from backend.store import Problem
from backend.store import refused as answer_refusal
from backend.tests.test_pipeline_foundation import ANA, boot, hub, step_id
from backend.tests.helpers.pipeline_links import two
from backend.tests.test_pipeline_members_only import make_pipeline

LINE = [{"name": "Backlog", "status": "open"}, {"name": "Doing", "status": "doing"}, {"name": "Done", "status": "done"}]
ON_ICE_LINE = [{"name": "On ice", "status": "open"}, *LINE]


def _stage_cap(api, typ, name):
    steps = get(api, f"task-types/{typ}")["type"]["steps"]
    return next(s for s in steps if s["name"] == name)["card_cap"]


def make_line(api, name, steps=LINE):
    """A company-wide pipeline with these stages, owned by ana."""
    with api.app.state.store.transaction() as c:
        typ = H.type_create(c, H.KEEPER, name, steps)
        c.execute(
            "UPDATE task_types SET pipeline_owner_id=?, pipeline_visibility='company' WHERE id=?", (ANA, typ["id"])
        )
    return typ["id"], {s["name"]: s["id"] for s in typ["steps"]}


def set_cap(api, stage_id, cap):
    r = api.patch("/api/v2/pipeline-stages/" + stage_id, json={"card_cap": cap}, headers=headers("ana-test"))
    assert r.status_code == 200, r.text


def card_stage(api, task_id):
    with api.app.state.store.read() as c:
        return c.execute("SELECT step_id FROM tasks WHERE id=?", (task_id,)).fetchone()[0]


def bot_move(api, task_id, stage_name, actor="bot:coo"):
    """A bot's move through the write path a request takes. Returns what store.refused answers the caller with, or None."""
    with api.app.state.store.transaction() as c:
        c.execute("SAVEPOINT domain_write")
        try:
            H.task_update(c, actor, task_id, step=stage_name)
        except H.Refused as exc:
            return answer_refusal(c, Identity(actor, "bot"), exc)
        c.execute("RELEASE domain_write")
    return None


def person_move(api, task_id, stage_name, token="ana-test"):
    with api.app.state.store.read() as c:
        version = H.task(c, task_id)["version"]
    return api.post("/api/v2/tasks/" + task_id, json={"step": stage_name, "version": version}, headers=headers(token))


def test_the_owner_sets_a_stage_cap_through_the_stage_route_and_a_blank_cap_is_no_cap(api):
    typ, steps = make_pipeline(api, "Caps" + uuid.uuid4().hex[:8])
    stage = "/api/v2/pipeline-stages/" + steps["Doing"]

    assert _stage_cap(api, typ, "Doing") is None  # a new stage has no cap

    r = api.patch(stage, json={"card_cap": 3}, headers=headers("ana-test"))
    assert r.status_code == 200, r.text
    assert _stage_cap(api, typ, "Doing") == 3

    api.patch(stage, json={"limit_minutes": 60}, headers=headers("ana-test"))  # the Warn after leaves the cap alone
    assert _stage_cap(api, typ, "Doing") == 3

    api.patch(stage, json={"card_cap": 0}, headers=headers("ana-test"))  # 0 is "On ice", not no cap
    assert _stage_cap(api, typ, "Doing") == 0

    api.patch(stage, json={"card_cap": None}, headers=headers("ana-test"))  # blank in the editor sends null
    assert _stage_cap(api, typ, "Doing") is None


def test_a_cap_is_only_for_the_pipelines_owner_and_admins_and_must_be_a_whole_number_from_zero(api):
    typ, steps = make_pipeline(api, "Caps" + uuid.uuid4().hex[:8])
    stage = "/api/v2/pipeline-stages/" + steps["Doing"]

    assert api.patch(stage, json={"card_cap": 2}, headers=headers("cara-test")).status_code == 403
    assert _stage_cap(api, typ, "Doing") is None

    assert api.patch(stage, json={"card_cap": -1}, headers=headers("ana-test")).status_code == 422
    assert api.patch(stage, json={"card_cap": 2.5}, headers=headers("ana-test")).status_code == 422
    assert _stage_cap(api, typ, "Doing") is None


def test_a_bot_cannot_move_a_card_into_a_full_stage_and_the_card_stays_and_a_person_can_with_over_cap(api):
    typ, steps = make_line(api, "Line" + uuid.uuid4().hex[:8])
    set_cap(api, steps["Doing"], 1)
    with api.app.state.store.transaction() as c:
        H.task_create(c, ANA, "Already doing", "", ANA, type=typ, step="Doing", lint=False)
        waiting = H.task_create(c, ANA, "Waiting", "", "bot:coo", type=typ, step="Backlog", lint=False)["id"]

    problem = bot_move(api, waiting, "Doing")
    assert isinstance(problem, Problem)
    assert (problem.status, problem.code, problem.detail) == (409, "stage_full", "Doing is full (1 of 1)")
    assert card_stage(api, waiting) == steps["Backlog"]

    person = person_move(api, waiting, "Doing")
    assert person.status_code == 200, person.text
    assert person.json()["over_cap"] is True
    assert card_stage(api, waiting) == steps["Doing"]


def test_a_move_with_room_carries_no_flag_and_a_refused_bot_is_not_quarantined(api):
    typ, steps = make_line(api, "Room" + uuid.uuid4().hex[:8])
    set_cap(api, steps["Doing"], 2)
    with api.app.state.store.transaction() as c:
        cards = [
            H.task_create(c, ANA, f"Card {n}", "", "bot:coo", type=typ, step="Backlog", lint=False)["id"]
            for n in range(3)
        ]
    for card in cards[:2]:
        assert bot_move(api, card, "Doing") is None  # room for two
    assert card_stage(api, cards[1]) == steps["Doing"]
    assert person_move(api, cards[0], "Backlog").json().get("over_cap") is None  # a stage with no cap never flags
    assert bot_move(api, cards[0], "Doing") is None  # and the room is the bot's again

    for _ in range(12):  # a full stage is capacity waiting, never a sign the bot reaches too far (rule 8)
        assert bot_move(api, cards[2], "Doing").code == "stage_full"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM events WHERE action LIKE 'quarantine%'").fetchone()[0] == 0


def test_on_ice_takes_no_card_from_a_bot_and_only_a_person_moves_a_card_in_or_out(api):
    typ, steps = make_line(api, "Ice" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    with api.app.state.store.transaction() as c:
        card = H.task_create(c, ANA, "Parked", "", "bot:coo", type=typ, step="Backlog", lint=False)["id"]

    problem = bot_move(api, card, "On ice")
    assert (problem.status, problem.code) == (409, "stage_full")
    assert problem.detail == "On ice takes no cards from a bot or automation"
    assert card_stage(api, card) == steps["Backlog"]

    assert person_move(api, card, "On ice").json()["over_cap"] is True
    assert card_stage(api, card) == steps["On ice"]

    problem = bot_move(api, card, "Backlog")
    assert (problem.status, problem.code) == (409, "on_ice")
    assert card_stage(api, card) == steps["On ice"]

    assert person_move(api, card, "Backlog").status_code == 200
    assert card_stage(api, card) == steps["Backlog"]


def test_a_move_link_into_a_full_stage_leaves_the_card_and_the_manager_sees_it_waiting_for_room(tmp_path):
    c, sales, success, _ = two(tmp_path)
    L.create_link(c, ANA, step_id(c, sales["id"], "Lead"), success["id"], "move")
    onboarding = step_id(c, success["id"], "Onboarding")
    R.set_stage(c, ANA, onboarding, {"card_cap": 1})
    H.task_create(c, ANA, "Already onboarding", "", ANA, type=success["id"], step="Onboarding", lint=False)
    task = H.task_create(c, ANA, "Acme deal", "", ANA, type=sales["id"], step="Lead", lint=False)["id"]

    out = E.advance(c, H.KEEPER, task)  # a link's move is automation, not a person's
    assert not out["moved"]
    assert "Onboarding is full (1 of 1)" in out["reason"]
    assert c.execute("SELECT step_id FROM tasks WHERE id=?", (task,)).fetchone()[0] == step_id(c, sales["id"], "Lead")

    assert X.waiting_room(c)[task]["stage_id"] == onboarding
    entries = X.build(c, H.type_get(c, sales["id"]), rows=[H.task(c, task)], at=H.now(), reader=ANA)
    assert any("waiting for room in Onboarding" in str(entry) for entry in entries)

    passed = PM._pass(c, {"id": sales["id"]}, datetime.now(timezone.utc))
    assert passed["exceptions"] >= 1


def test_a_spawn_into_a_full_stage_is_refused_so_the_card_waits_at_its_stage(tmp_path):
    c, _, success, _ = two(tmp_path)
    intake = H.type_create(
        c,
        ANA,
        "Intake",
        [
            {"name": "Intake", "status": "open"},
            {"name": "Review", "status": "doing"},
            {"name": "Done", "status": "done"},
        ],
        mover=True,
    )
    c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", (ANA, intake["id"]))
    L.create_link(c, ANA, step_id(c, intake["id"], "Intake"), success["id"], "spawn")
    R.set_stage(c, ANA, step_id(c, success["id"], "Onboarding"), {"card_cap": 1})
    H.task_create(c, ANA, "Already onboarding", "", ANA, type=success["id"], step="Onboarding", lint=False)
    card = H.task_create(c, ANA, "Intake card", "", ANA, type=intake["id"], step="Intake", lint=False)["id"]

    c.execute("SAVEPOINT spawn_test")
    with pytest.raises(H.Refused) as raised:
        H.task_update(c, ANA, card, step="Review")
    c.execute("ROLLBACK TO spawn_test")
    c.execute("RELEASE spawn_test")
    assert raised.value.rule == "stage_full"
    assert "so the linked ticket waits" in raised.value.detail
    assert c.execute("SELECT step_id FROM tasks WHERE id=?", (card,)).fetchone()[0] == step_id(
        c, intake["id"], "Intake"
    )


def runs(c, slug):
    """Runs queued for a bot that no hold keeps back: what a notice to it would start."""
    return c.execute(
        "SELECT count(*) FROM jobs WHERE bot_id=? AND state='queued' AND coalesce(held,0)=0", (slug,)
    ).fetchone()[0]


def test_a_card_on_ice_starts_no_run_for_its_bot_owner_when_a_person_moves_it_in_or_news_reaches_it(api):
    typ, steps = make_line(api, "Wake" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    with api.app.state.store.transaction() as c:
        H.task_create(c, ANA, "Live", "", "bot:coo", type=typ, step="Doing", lint=False)
        assert runs(c, "coo") == 1  # the control: a card made in an ordinary stage starts its owner's run
        c.execute("DELETE FROM jobs WHERE bot_id='coo'")

        parked = H.task_create(c, ANA, "Parked", "", "bot:coo", type=typ, step="Backlog", lint=False)["id"]
        c.execute("DELETE FROM jobs WHERE bot_id='coo'")
        H.task_update(c, ANA, parked, step="On ice")  # a person moves it in: no run starts
        assert runs(c, "coo") == 0
        news = {"news": "assignment", "rule": "assignment", "trigger": "status_or_relation"}
        H._wake(c, H.task(c, parked), "bot:coo", "News about the parked card", news)
        assert runs(c, "coo") == 0


def test_a_follow_up_on_a_card_on_ice_is_never_listed_for_its_bot(api):
    typ, steps = make_line(api, "Follow" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    earlier = H.shift(H.now(), minutes=-10)
    with api.app.state.store.transaction() as c:
        live = H.task_create(c, ANA, "Live", "", "bot:coo", type=typ, step="Doing", lint=False)
        parked = H.task_create(c, ANA, "Parked", "", "bot:coo", type=typ, step="On ice", lint=False)
        for card in (live, parked):  # a person sets a follow-up that is due now
            CB.set_time(c, H.task(c, card["id"]), "5m", "Look at it", actor=ANA, at=earlier)
        listed = [item["tid"] for item in TR._items(c, "coo", H.now(), {})]
        assert live["id"] in listed  # the control: a due follow-up on an ordinary stage is listed for its owner
        assert parked["id"] not in listed


def test_a_card_on_ice_is_never_late_and_leaves_the_managers_visits(api):
    typ, steps = make_line(api, "Pass" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    with api.app.state.store.transaction() as c:
        for stage in ("On ice", "Doing"):
            R.set_stage(c, ANA, steps[stage], {"limit_minutes": 30})
        parked = H.task_create(c, ANA, "Parked", "", "bot:coo", type=typ, step="On ice", lint=False)["id"]
        live = H.task_create(c, ANA, "Live", "", "bot:coo", type=typ, step="Doing", lint=False)["id"]
        long_ago = H.shift(H.now(), hours=-3)
        c.execute("UPDATE stage_visits SET entered_at=? WHERE task_id IN (?, ?)", (long_ago, parked, live))
        c.execute(
            "UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id IN (?, ?)",
            (long_ago, parked, live),
        )
        late = PM.late_tickets(c, [H.task(c, parked), H.task(c, live)], H.now())
        assert live in late  # the control: an ordinary stage past its Warn after is late
        assert parked not in late
        assert parked not in [v["task_id"] for v in PM._visits(c, typ)]


def test_a_card_with_no_stage_named_lands_in_the_first_stage_with_room_and_never_on_ice(api):
    typ, steps = make_line(api, "Default" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    with api.app.state.store.transaction() as c:
        person = H.task_create(c, ANA, "Filed by a person", "", ANA, type=typ, lint=False)["id"]
        assert H.task(c, person)["step_id"] == steps["Backlog"]  # the first ordinary open stage, not On ice
        bot = H.task_create(c, "bot:coo", "Filed by a bot", "", "bot:coo", type=typ, lint=False)["id"]
        assert H.task(c, bot)["step_id"] == steps["Backlog"]


def test_a_bot_creation_with_a_step_is_checked_on_that_step_alone_not_on_the_default_it_passes_over(api):
    typ, steps = make_line(api, "Landing" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    set_cap(api, steps["Backlog"], 1)
    with api.app.state.store.transaction() as c:
        H.task_create(c, ANA, "Already in Backlog", "", ANA, type=typ, step="Backlog", lint=False)
        card = H.task_create(c, "bot:coo", "Straight to Doing", "", "bot:coo", type=typ, step="Doing", lint=False)["id"]
    assert card_stage(api, card) == steps["Doing"]


def test_a_bot_creation_into_a_full_step_is_refused_and_no_card_is_made(api):
    typ, steps = make_line(api, "Full" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    set_cap(api, steps["Doing"], 1)
    with api.app.state.store.transaction() as c:
        H.task_create(c, ANA, "Already doing", "", ANA, type=typ, step="Doing", lint=False)
        before = c.execute("SELECT COUNT(*) FROM tasks WHERE type_id=?", (typ,)).fetchone()[0]
        c.execute("SAVEPOINT full_create")
        with pytest.raises(H.Refused) as raised:
            H.task_create(c, "bot:coo", "Into a full Doing", "", "bot:coo", type=typ, step="Doing", lint=False)
        c.execute("ROLLBACK TO full_create")
        c.execute("RELEASE full_create")
        assert raised.value.rule == "stage_full"
        assert c.execute("SELECT COUNT(*) FROM tasks WHERE type_id=?", (typ,)).fetchone()[0] == before


def test_an_automatic_backward_move_is_held_to_the_stage_cap_as_a_forward_one_is(api):
    typ, steps = make_line(api, "Back" + uuid.uuid4().hex[:8])
    set_cap(api, steps["Backlog"], 1)
    with api.app.state.store.transaction() as c:
        H.task_create(c, ANA, "Already in Backlog", "", ANA, type=typ, step="Backlog", lint=False)
        card = H.task_create(c, ANA, "Moved back", "", ANA, type=typ, step="Doing", lint=False)["id"]
        assert H.try_auto_move(c, H.KEEPER, H.task(c, card), step=steps["Backlog"]) is False
        assert c.execute("SELECT step_id FROM tasks WHERE id=?", (card,)).fetchone()[0] == steps["Doing"]
        assert (
            c.execute("SELECT count(*) FROM history_events WHERE subject_id=? AND kind='step'", (card,)).fetchone()[0]
            == 1
        )  # only its creation's step: the refused move left no step behind


def test_an_automatic_move_refused_for_room_is_listed_as_waiting_for_room_in_the_manager(api):
    typ, steps = make_line(api, "Listed" + uuid.uuid4().hex[:8])
    set_cap(api, steps["Backlog"], 1)
    with api.app.state.store.transaction() as c:
        H.task_create(c, ANA, "Already in Backlog", "", ANA, type=typ, step="Backlog", lint=False)
        card = H.task_create(c, ANA, "Moved back", "", ANA, type=typ, step="Doing", lint=False)["id"]
        assert card not in X.waiting_room(c)  # the control: nothing has been refused yet
        H.try_auto_move(c, H.KEEPER, H.task(c, card), step=steps["Backlog"])
        assert X.waiting_room(c)[card]["stage_id"] == steps["Backlog"]
        entries = X.build(c, H.type_get(c, typ), rows=[H.task(c, card)], at=H.now(), reader=ANA)
        assert any("waiting for room in Backlog" in str(entry) for entry in entries)


def _link_refused_for_room(tmp_path):
    boot(tmp_path / "hub.sqlite")
    c = hub(tmp_path / "hub.sqlite")
    sales = H.type_create(
        c,
        ANA,
        "Sales",
        [
            {"name": "Lead", "status": "open"},
            {"name": "Qualified", "status": "doing"},
            {"name": "Won", "status": "done"},
        ],
        mover=True,
    )
    success = H.type_create(c, ANA, "Success", [{"name": "Onboarding", "status": "doing"}], mover=True)
    for t in (sales, success):
        c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", (ANA, t["id"]))
    L.create_link(c, ANA, step_id(c, sales["id"], "Lead"), success["id"], "move")
    onboarding = step_id(c, success["id"], "Onboarding")
    R.set_stage(c, ANA, onboarding, {"card_cap": 1})
    H.task_create(c, ANA, "Already onboarding", "", ANA, type=success["id"], step="Onboarding", lint=False)
    task = H.task_create(c, ANA, "Acme deal", "", ANA, type=sales["id"], step="Lead", lint=False)["id"]
    assert not E.advance(c, H.KEEPER, task)["moved"]
    return c, sales, task, onboarding


def test_a_refused_move_waits_for_room_however_long_ago_it_was_refused(tmp_path):
    c, _, task, onboarding = _link_refused_for_room(tmp_path)
    long_ago = H.shift(H.now(), hours=-48)
    c.execute("UPDATE refusals SET created_at=? WHERE rule='stage_full'", (long_ago,))
    c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?", (long_ago, task))
    assert X.waiting_room(c)[task]["stage_id"] == onboarding  # still full, and the card has never moved


def test_a_refused_move_stops_waiting_once_its_stage_has_room_or_the_card_moves(tmp_path):
    c, sales, task, onboarding = _link_refused_for_room(tmp_path)
    assert task in X.waiting_room(c)
    R.set_stage(c, ANA, onboarding, {"card_cap": 2})  # room now
    assert task not in X.waiting_room(c)
    R.set_stage(c, ANA, onboarding, {"card_cap": 1})
    assert task in X.waiting_room(c)
    H.task_update(
        c, ANA, task, step=step_id(c, sales["id"], "Qualified")
    )  # a person moves it on, to a stage of its own
    assert task not in X.waiting_room(c)


def test_a_move_a_bot_makes_for_a_person_is_held_to_the_caps_the_bot_would_face(api):
    typ, steps = make_line(api, "Via" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    set_cap(api, steps["Doing"], 1)
    with api.app.state.store.transaction() as c:
        H.task_create(c, ANA, "Already doing", "", ANA, type=typ, step="Doing", lint=False)
        waiting = H.task_create(c, ANA, "Waiting", "", ANA, type=typ, step="Backlog", lint=False)["id"]
        parked = H.task_create(c, ANA, "Parked", "", ANA, type=typ, step="On ice", lint=False)["id"]
        token = H.VIA.set("botops")  # BotOps writing as the person (app.py): automated, not a person's own move
        try:
            c.execute("SAVEPOINT via_full")
            with pytest.raises(H.Refused) as full:
                H.task_update(c, ANA, waiting, step="Doing")
            c.execute("ROLLBACK TO via_full")
            c.execute("RELEASE via_full")
            c.execute("SAVEPOINT via_parked")
            with pytest.raises(H.Refused) as out:
                H.task_update(c, ANA, parked, step="Backlog")
            c.execute("ROLLBACK TO via_parked")
            c.execute("RELEASE via_parked")
        finally:
            H.VIA.reset(token)
    assert full.value.rule == "stage_full"
    assert out.value.rule == "on_ice"
    assert card_stage(api, waiting) == steps["Backlog"]
    assert card_stage(api, parked) == steps["On ice"]


def test_a_message_about_a_card_on_ice_starts_no_run_whichever_path_writes_it(api):
    typ, steps = make_line(api, "Mail" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    with api.app.state.store.transaction() as c:
        parked = H.task_create(c, ANA, "Parked", "", "bot:coo", type=typ, step="On ice", lint=False)["id"]
        c.execute("DELETE FROM jobs WHERE bot_id='coo'")
        H.task_comment(c, ANA, parked, "@coo can you look at this?")  # a comment (threads.post)
        H.say(c, ANA, "bot:coo", "Please look at the parked card", refs={"task": parked})  # a message with its task
        RU._tell_bot(
            c, "rule-on-ice", "coo", "A rule woke you", {}, wake=True, task=H.task(c, parked)
        )  # a rule's notice
        assert runs(c, "coo") == 0
        c.execute("DELETE FROM jobs WHERE bot_id='coo'")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET delivered_at=NULL WHERE recipient_id='bot:coo'")
    api.app.state.store.enqueue_existing()  # the sweep that queues undelivered messages
    with api.app.state.store.read() as c:
        assert runs(c, "coo") == 0


def test_a_deferred_message_about_a_card_on_ice_is_not_alive_for_a_deadline_run(api):
    typ, steps = make_line(api, "Defer" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    with api.app.state.store.transaction() as c:
        parked = H.task_create(c, ANA, "Parked", "", "bot:coo", type=typ, step="On ice", lint=False)["id"]
        mid = H.say(c, ANA, "bot:coo", "Look at this", refs={"task": parked})["id"]
        c.execute("DELETE FROM jobs WHERE message_id=?", (mid,))  # the deferral's own item, with no run of its own
        assert DL._alive(c, {"kind": "message", "item": mid, "bot": "coo", "conversation_id": None}) is False


def test_a_card_on_ice_is_never_listed_as_assigned_not_started_for_its_bot(api):
    typ, steps = make_line(api, "Assign" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    earlier = H.shift(H.now(), hours=-3)
    with api.app.state.store.transaction() as c:
        live = H.task_create(c, ANA, "Live", "", "bot:coo", type=typ, step="Backlog", lint=False)["id"]
        parked = H.task_create(c, ANA, "Parked", "", "bot:coo", type=typ, step="On ice", lint=False)["id"]
        c.execute("DELETE FROM jobs WHERE bot_id='coo'")
        c.execute("UPDATE tasks SET created_at=? WHERE id IN (?, ?)", (earlier, live, parked))
        c.execute(  # the assignment (its owner row) was made the same time
            "UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id IN (?, ?)",
            (earlier, live, parked),
        )
        waiting = [row["id"] for row, *_ in A.unstarted(c, H.now(), owner="bot:coo")]
        assert live in waiting  # the control: an ordinary assignment not started is listed
        assert parked not in waiting
        listed = [item["tid"] for _, _, item in TR._assigned(c, "coo", "bot:coo", H.now())]
        assert live in listed
        assert parked not in listed


def test_a_next_run_task_on_ice_is_not_carried_into_its_bots_run(api):
    typ, steps = make_line(api, "Carry" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    with api.app.state.store.transaction() as c:
        live = H.task_create(
            c, ANA, "Live next run", "", "bot:coo", type=typ, step="Backlog", next_run=True, lint=False
        )
        parked = H.task_create(
            c, ANA, "Parked next run", "", "bot:coo", type=typ, step="On ice", next_run=True, lint=False
        )
        carried = [t["id"] for t in H.next_run_tasks(c, "coo")]
        assert live["id"] in carried  # the control: an ordinary next-run task is carried
        assert parked["id"] not in carried
        assert H.next_run_waiting(c, H.task(c, parked["id"])) is False


def test_a_bot_cannot_clear_a_cards_stage_to_slip_out_of_on_ice(api):
    typ, steps = make_line(api, "Clear" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    with api.app.state.store.transaction() as c:
        card = H.task_create(c, ANA, "Parked", "", "bot:coo", type=typ, step="On ice", lint=False)["id"]
    problem = bot_move(api, card, "")
    assert (problem.status, problem.code) == (422, "stage_required")
    assert card_stage(api, card) == steps["On ice"]
    with api.app.state.store.transaction() as c:
        H.task_update(c, ANA, card, step="")  # a person may take a card out of its stage
        assert H.task(c, card)["step_id"] is None


def test_a_card_on_ice_is_neither_stalled_nor_stuck_for_the_health_checks(api):
    typ, steps = make_line(api, "Health" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    long_ago = H.shift(H.now(), hours=-3)
    with api.app.state.store.transaction() as c:
        live = H.task_create(c, ANA, "Live", "", "bot:coo", type=typ, step="Backlog", lint=False)["id"]
        parked = H.task_create(c, ANA, "Parked", "", "bot:coo", type=typ, step="On ice", lint=False)["id"]
        c.execute("DELETE FROM jobs WHERE bot_id='coo'")
        c.execute("UPDATE tasks SET updated_at=? WHERE id IN (?, ?)", (long_ago, live, parked))
        stalled = [row["id"] for row in H.stalled_tasks(c, H.now())]
        assert live in stalled  # the control: an ordinary open bot task nothing moves is stalled
        assert parked not in stalled
    with api.app.state.store.transaction() as c:
        doing = H.task_create(c, ANA, "Doing", "", "bot:coo", type=typ, step="Doing", lint=False)["id"]
        c.execute("DELETE FROM jobs WHERE bot_id='coo'")
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (long_ago, doing))
    with api.app.state.store.read() as c:
        assert doing in I.task_stuck(c, I.Ctx(now=H.now(), at=None))  # the control: a doing task with no run is stuck
    set_cap(api, steps["Doing"], 0)  # its stage is taken off the board
    with api.app.state.store.read() as c:
        assert doing not in I.task_stuck(c, I.Ctx(now=H.now(), at=None))
        assert doing not in [row["id"] for row in H.stalled_tasks(c, H.now())]


def test_a_task_type_reports_each_stage_held_count_snoozed_cards_included_for_its_cap(api):
    """The count a capped stage shows is the one its cap is enforced against (D68): a snoozed card holds its place, and a
    finished card holds none. Without it a board showing 0 of 3 would still refuse a bot's entry as 3 of 3."""
    typ, steps = make_line(api, "Held" + uuid.uuid4().hex[:8])
    set_cap(api, steps["Doing"], 3)
    with api.app.state.store.transaction() as c:
        snoozed = [
            H.task_create(c, ANA, f"Snoozed {n}", "", ANA, type=typ, step="Doing", lint=False)["id"] for n in (1, 2, 3)
        ]
        H.task_create(c, ANA, "Shipped", "", ANA, type=typ, step="Done", lint=False)
        waiting = H.task_create(c, ANA, "Waiting", "", "bot:coo", type=typ, step="Backlog", lint=False)["id"]
    for task_id in snoozed:
        r = api.post(
            f"/api/v2/tasks/{task_id}/snooze", json={"until": H.shift(H.now(), days=3)}, headers=headers("ana-test")
        )
        assert r.status_code == 200, r.text

    held = {s["name"]: s["held"] for s in get(api, f"task-types/{typ}")["type"]["steps"]}
    assert held == {"Backlog": 1, "Doing": 3, "Done": 0}  # Backlog holds the waiting card; Done's card is finished
    listed = next(t for t in get(api, "task-types")["types"] if t["id"] == typ)
    assert {s["name"]: s["held"] for s in listed["steps"]} == held

    problem = bot_move(api, waiting, "Doing")  # the refusal the board's 3 stands for
    assert isinstance(problem, Problem)
    assert (problem.status, problem.code, problem.detail) == (409, "stage_full", "Doing is full (3 of 3)")


def test_a_queued_assignment_for_a_card_parked_before_its_claim_is_refused_at_the_claim(api):
    """A bot's assignment queued while its card sat in Backlog is not claimed once a person parks the card On ice."""
    from backend import execution

    typ, steps = make_line(api, "Queued" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs WHERE bot_id='coo'")
        card = H.task_create(c, ANA, "Assigned", "", "bot:coo", type=typ, step="Backlog", lint=False)["id"]
        queued = [dict(row) for row in c.execute("SELECT * FROM jobs WHERE bot_id='coo'").fetchall()]
        assert queued  # the assignment is queued for the bot
        assert all(execution.job_refusal(c, job, {}, {}, set(), set(), set()) is None for job in queued)  # control
        H.task_update(c, ANA, card, step="On ice")  # a person parks it before any claim
        assert all(execution.job_refusal(c, job, {}, {}, set(), set(), set()) is not None for job in queued)


def test_a_bot_cannot_clear_a_card_out_of_on_ice_by_a_type_or_a_status_with_no_stage_for_it(api):
    typ, steps = make_line(api, "ClearWay" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    with api.app.state.store.transaction() as c:
        shipped = H.type_create(
            c, H.KEEPER, "Shipped only" + uuid.uuid4().hex[:8], [{"name": "Shipped", "status": "done"}]
        )
        card = H.task_create(c, ANA, "Parked", "", "bot:coo", type=typ, step="On ice", lint=False)["id"]
    for change in ({"type": shipped["id"]}, {"status": "declined"}):
        with api.app.state.store.transaction() as c:
            with pytest.raises(H.Refused) as refused:
                H.task_update(c, "bot:coo", card, **change)
            assert refused.value.rule == "stage_required", change
        assert card_stage(api, card) == steps["On ice"], change


def test_a_card_the_merge_queue_drops_into_a_full_stage_is_listed_waiting_for_room(tmp_path):
    boot(tmp_path / "hub.sqlite")
    c = hub(tmp_path / "hub.sqlite")
    typ = H.type_create(
        c,
        ANA,
        "Ship" + uuid.uuid4().hex[:8],
        [{"name": "Doing", "status": "doing"}, {"name": "Review", "status": "review"}],
        mover=True,
    )["id"]
    c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", (ANA, typ))
    doing, review = step_id(c, typ, "Doing"), step_id(c, typ, "Review")
    R.add(c, ANA, review, {"name": "Due date", "kind": "field", "needs": {"field": "due"}, "reviewed_by": "nobody"})
    H.task_create(c, ANA, "Already in Doing", "", ANA, type=typ, step="Doing", lint=False)
    R.set_stage(c, ANA, doing, {"card_cap": 1})  # Doing is full
    card = H.task_create(c, ANA, "Dropped from the queue", "", ANA, type=typ, step="Review", lint=False)["id"]
    assert E.queue_ejected(c, card, "Dropped from the queue") is False
    assert X.waiting_room(c)[card]["stage_id"] == doing


def _private_card(c, typ, title, who, step):
    card = H.task_create(c, who, title, "", who, type=typ, step=step, lint=False)["id"]
    c.execute("UPDATE tasks SET private=1 WHERE id=?", (card,))
    return card


def test_a_stage_count_holds_only_the_cards_a_viewer_reads_and_the_pipelines_editor_sees_the_enforced_count(api):
    """A private card is not counted for a viewer who cannot read it, so its occupancy does not leak. The pipeline's
    editor, who sets its caps, gets the count the cap is enforced against, with `unseen` naming the cards it cannot
    read, so its board never shows room a bot is refused."""
    from backend import task_privacy as privacy

    typ, steps = make_line(api, "Unseen" + uuid.uuid4().hex[:8])
    set_cap(api, steps["Doing"], 2)
    with api.app.state.store.transaction() as c:
        H.task_create(c, ANA, "Open card", "", ANA, type=typ, step="Doing", lint=False)
        hidden = _private_card(c, typ, "Ben's private card", "human:ben", "Doing")
        assert not privacy.task_readable(c, Identity(ANA, "human"), H.task(c, hidden))  # the precondition
        assert not privacy.task_readable(c, Identity("human:cara", "human"), H.task(c, hidden))

    def doing(token, listed=False):
        if listed:
            found = next(t for t in get(api, "task-types", token)["types"] if t["id"] == typ)
        else:
            found = get(api, f"task-types/{typ}", token)["type"]
        return next(({"held": s["held"], "unseen": s.get("unseen", 0)} for s in found["steps"] if s["name"] == "Doing"))

    for listed in (False, True):
        assert doing("cara-test", listed) == {"held": 1, "unseen": 0}  # an outsider: only the card she reads
        assert doing("ana-test", listed) == {"held": 2, "unseen": 1}  # the editor: what the cap is enforced against
        assert doing("ben-test", listed) == {"held": 2, "unseen": 0}  # ben reads both cards


def test_a_bot_cannot_decline_or_close_a_card_out_of_on_ice(api):
    """A cancellation lands in the pipeline's closed stage without a stage move's check: a bot's decline or close is held
    to On ice all the same, so it cannot clear a parked card and reopen it into Backlog. A person can."""
    typ, steps = make_line(
        api, "IceClose" + uuid.uuid4().hex[:8], [*ON_ICE_LINE, {"name": "Closed", "status": "closed"}]
    )
    set_cap(api, steps["On ice"], 0)
    with api.app.state.store.transaction() as c:
        card = H.task_create(c, ANA, "Parked", "", "bot:coo", type=typ, step="On ice", lint=False)["id"]
        c.execute("UPDATE tasks SET requester_id='bot:coo' WHERE id=?", (card,))  # its requester may close it
    for write in (
        lambda c: H.task_update(c, "bot:coo", card, status="declined"),
        lambda c: H.task_close(c, "bot:coo", card, "Not needed"),
    ):
        with api.app.state.store.transaction() as c:
            c.execute("SAVEPOINT bot_cancel")
            with pytest.raises(H.Refused) as refused:
                write(c)
            c.execute("ROLLBACK TO bot_cancel")
            c.execute("RELEASE bot_cancel")
        assert refused.value.rule == "on_ice"
        assert card_stage(api, card) == steps["On ice"]
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:coo", card, status="waiting", note="Parked until spring")  # it stays On ice: allowed
        assert H.task(c, card)["step_id"] == steps["On ice"]
        H.task_update(c, ANA, card, status="declined")  # a person may decline it
        assert H.task(c, card)["status"] == "declined"


def test_a_private_card_refused_for_room_is_still_listed_as_waiting_for_room(api):
    """The refusal of a private card's write keeps only what was refused, but the stage it waits for is kept: without it
    the manager could never list the card as waiting for room."""
    typ, steps = make_line(api, "PrivRoom" + uuid.uuid4().hex[:8])
    set_cap(api, steps["Doing"], 1)
    with api.app.state.store.transaction() as c:
        H.task_create(c, ANA, "Already doing", "", ANA, type=typ, step="Doing", lint=False)
        card = H.task_create(c, ANA, "Private card", "", "bot:coo", type=typ, step="Backlog", lint=False)["id"]
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (card,))
    problem = bot_move(api, card, "Doing")
    assert (problem.status, problem.code) == (409, "stage_full")
    with api.app.state.store.read() as c:
        assert X.waiting_room(c)[card]["stage_id"] == steps["Doing"]
        recorded = H._json(c.execute("SELECT detail FROM refusals WHERE rule='stage_full'").fetchone()["detail"], {})
        assert recorded["detail"] == "Private task write refused"  # what was refused stays private
        assert recorded["context"] == {"what": None, "stage": steps["Doing"]}


def test_a_job_held_for_a_card_on_ice_is_an_intended_wait_for_health():
    """A queued job for a card On ice waits on purpose (execution.job_refusal "on_ice"): Health lists it as resting, with
    words of its own, never as a stuck bot or "Not started after 15 min"."""
    from backend import health

    assert "on_ice" in health.QUEUE_WAITS
    words, _ = health.HELD["on_ice"]
    assert "On ice" in words


def test_a_stage_count_leaves_out_a_card_whose_bot_the_viewer_may_not_read(api):
    """A stage's count follows the rule the task list reads with, not privacy alone: a card a bot works on whose activity
    the viewer may not read is not counted for that viewer, as its task list leaves it out."""
    typ, steps = make_line(api, "BotRead" + uuid.uuid4().hex[:8])
    set_cap(api, steps["Doing"], 2)
    with api.app.state.store.transaction() as c:
        card = H.task_create(c, "bot:coo", "Coo's own card", "", "bot:coo", type=typ, step="Doing", lint=False)["id"]
        c.execute("UPDATE tasks SET private=0 WHERE id=?", (card,))
    current = get(api, "bots/coo/access", "ana-test")
    everyone = current["see"]
    r = api.put(
        "/api/v2/bots/coo/access",
        json={"see": everyone, "read": {"people": ["ana"]}, "write": everyone, "revision": current["revision"]},
        headers=headers("ana-test"),
    )
    assert r.status_code == 200, r.text
    assert api.get("/api/v2/tasks/" + card, headers=headers("cara-test")).status_code in (403, 404)  # the precondition

    def doing(token):
        found = get(api, f"task-types/{typ}", token)["type"]
        listed = next(t for t in get(api, "task-types", token)["types"] if t["id"] == typ)
        return {s["name"]: s["held"] for s in found["steps"]}["Doing"], {s["name"]: s["held"] for s in listed["steps"]}[
            "Doing"
        ]

    assert doing("cara-test") == (0, 0)
    assert doing("ana-test") == (1, 1)


def test_an_automation_cannot_close_a_card_out_of_on_ice_and_no_sweep_expires_a_parked_ask(api):
    """An automation acts as Tico but is held as one: its close does not take a card out of On ice. A parked card is never
    late: the ask sweep passes it over instead of closing it or failing on it, and a parked question is no open question
    to send back or expire. Tico's own cleanup after a person's decision still closes its task wherever it is."""
    from backend import ask_deadlines as AD
    from backend import question_tasks as QT

    typ, steps = make_line(
        api, "IceKeeper" + uuid.uuid4().hex[:8], [*ON_ICE_LINE, {"name": "Closed", "status": "closed"}]
    )
    set_cap(api, steps["On ice"], 0)
    old = H.shift(H.now(), days=-(AD.EXPIRE_DAYS + 5))
    with api.app.state.store.transaction() as c:
        card = H.task_create(c, ANA, "Parked ask", "", ANA, type=typ, step="On ice", lint=False)["id"]
        question = H.task_create(c, ANA, "Parked question", "", ANA, type=typ, step="On ice", lint=False)["id"]
        for tid in (card, question):
            c.execute("UPDATE tasks SET created_at=?, updated_at=?, requester_id='bot:coo' WHERE id=?", (old, old, tid))
            c.execute("UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=?", (old, tid))
        c.execute("UPDATE tasks SET questions=? WHERE id=?", ('[{"text": "Which one?"}]', question))
    with api.app.state.store.transaction() as c:
        c.execute("SAVEPOINT rule_close")
        with pytest.raises(H.Refused) as refused:
            RU._close(c, card, {}, "Rule tidy")
        c.execute("ROLLBACK TO rule_close")
        c.execute("RELEASE rule_close")
    assert refused.value.rule == "on_ice"
    with api.app.state.store.transaction() as c:
        assert question not in {row["id"] for row in QT.open_rows(c)}
        done = AD.sweep(c)
        assert card not in done["closed"]
        assert not c.execute("SELECT 1 FROM service_health WHERE service LIKE ?", ("%" + card,)).fetchone()
    assert card_stage(api, card) == steps["On ice"]
    with api.app.state.store.transaction() as c:
        assert H.task(c, card)["status"] == "open"
        H.task_close(c, H.KEEPER, card, "Every hold on this task is decided.", strict=False)  # Tico's own cleanup
        assert H.task(c, card)["status"] == "closed"


def test_a_verdict_move_into_a_full_stage_is_listed_as_waiting_for_room(api):
    """A verdict's automatic move (verdict_moves.move) into a full stage leaves the card where it is, and the manager
    lists it as waiting for room."""
    from backend import verdict_moves as VM

    typ, steps = make_line(api, "Verdict" + uuid.uuid4().hex[:8])
    set_cap(api, steps["Doing"], 1)
    with api.app.state.store.transaction() as c:
        H.task_create(c, ANA, "Already doing", "", ANA, type=typ, step="Doing", lint=False)
        card = H.task_create(c, ANA, "Reviewed", "", "bot:coo", type=typ, step="Backlog", lint=False)["id"]
        assert VM.move(c, H.task(c, card), steps["Doing"], "Approved") is False
    assert card_stage(api, card) == steps["Backlog"]
    with api.app.state.store.read() as c:
        assert X.waiting_room(c)[card]["stage_id"] == steps["Doing"]


def test_a_spawn_into_a_pipeline_all_on_ice_waits_instead_of_dropping_the_ticket(api):
    """A spawn link whose target pipeline has no open stage but On ice files nothing there: the card that would have
    spawned it waits at its stage (stage_full), so the linked work is never silently dropped."""
    typ, steps = make_line(api, "SpawnSrc" + uuid.uuid4().hex[:8])
    target, tsteps = make_line(
        api,
        "SpawnIce" + uuid.uuid4().hex[:8],
        [{"name": "Intake", "status": "open"}, {"name": "Closed", "status": "closed"}],
    )
    set_cap(api, tsteps["Intake"], 0)
    with api.app.state.store.transaction() as c:
        card = H.task_create(c, ANA, "Makes follow-up", "", ANA, type=typ, step="Doing", lint=False)["id"]
        link = {
            "id": "L" + uuid.uuid4().hex[:8],
            "mode": "spawn",
            "to_pipeline_id": target,
            "from_pipeline_stage_id": steps["Doing"],
        }
        with pytest.raises(H.Refused) as refused:
            L.spawn(c, link, card)
    assert refused.value.rule == "stage_full"
    assert refused.value.context == {"stage": tsteps["Intake"], "entry": target}


def test_an_automatic_move_of_a_private_card_refused_for_room_is_recorded_as_private(api):
    """An automatic move (hubdb.try_auto_move) runs outside the private task write that redacts a refusal: the wait it
    records for the manager still keeps only the stage, never the refusal's text."""
    typ, steps = make_line(api, "PrivAuto" + uuid.uuid4().hex[:8])
    set_cap(api, steps["Doing"], 1)
    with api.app.state.store.transaction() as c:
        H.task_create(c, ANA, "Already doing", "", ANA, type=typ, step="Doing", lint=False)
        card = _private_card(c, typ, "Private automatic", ANA, "Backlog")
        assert H.try_auto_move(c, "bot:coo", H.task(c, card), step=steps["Doing"]) is False
    with api.app.state.store.read() as c:
        assert X.waiting_room(c)[card]["stage_id"] == steps["Doing"]
        refusal = c.execute("SELECT detail FROM refusals WHERE rule='stage_full' ORDER BY created_at DESC").fetchone()
        recorded = H._json(refusal["detail"], {})
        assert recorded["detail"] == "Private task write refused"
        assert recorded["context"] == {"what": None, "stage": steps["Doing"]}
        audit = c.execute(
            "SELECT detail FROM events WHERE action='refused' AND target='stage_full' ORDER BY created_at DESC"
        ).fetchone()
        assert H._json(audit["detail"], {})["detail"] == "Private task write refused"


def test_a_job_held_for_a_card_on_ice_is_never_an_aged_queue_refusal(api, monkeypatch):
    """A queued job held for a card On ice waits on purpose: the 12-hour refusal watch (queue_refusals) never records it,
    so Health raises no "needs attention" warning and Maintenance no finding for it."""
    from backend import queue_refusals as QR
    from backend import read_cache

    bot = "coo"
    monkeypatch.setattr(read_cache, "one", lambda c, sql, args: {"bot": bot, "runner_id": "r1", "display_name": "COO"})
    held = {"why": "on_ice"}
    monkeypatch.setattr(QR.E, "bot_claimable", lambda *a, **k: (held["why"], H.now(), None, "job-1"))
    with api.app.state.store.read() as c:
        assert QR.current(c, api.app.state.store.settings, bot) == {}
        held["why"] = "bot_limit"  # the control: any other refusal is watched
        assert QR.current(c, api.app.state.store.settings, bot)["reason"] == "bot_limit"


FROZEN_LINE = [{"name": "Intake", "status": "open"}, {"name": "Done", "status": "done"}]


def test_a_spawn_into_a_pipeline_whose_working_stages_are_on_ice_never_files_the_ticket_into_done(api):
    """A target whose only working stage is On ice takes no linked ticket: the spawn waits (stage_full on that stage)
    rather than file the new ticket straight into Done."""
    typ, steps = make_line(api, "SpawnSrc" + uuid.uuid4().hex[:8])
    target, tsteps = make_line(api, "SpawnFrozen" + uuid.uuid4().hex[:8], FROZEN_LINE)
    set_cap(api, tsteps["Intake"], 0)
    with api.app.state.store.transaction() as c:
        card = H.task_create(c, ANA, "Makes follow-up", "", ANA, type=typ, step="Doing", lint=False)["id"]
        link = {"id": "L" + uuid.uuid4().hex[:8], "mode": "spawn", "to_pipeline_id": target}
        link["from_pipeline_stage_id"] = steps["Doing"]
        with pytest.raises(H.Refused) as refused:
            L.spawn(c, link, card)
        assert not c.execute("SELECT 1 FROM tasks WHERE type_id=?", (target,)).fetchone()
    assert refused.value.rule == "stage_full"
    assert refused.value.context == {"stage": tsteps["Intake"], "entry": target}


def test_a_move_link_into_a_pipeline_whose_working_stages_are_on_ice_leaves_the_card_waiting_for_room(tmp_path):
    """A move link into a pipeline whose working stages are all On ice moves nothing, never into its Done stage: the card
    stays, and the manager lists it as waiting for room in the parked stage."""
    c, sales, _, _ = two(tmp_path)
    frozen = H.type_create(c, ANA, "Frozen", FROZEN_LINE, mover=True)
    c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", (ANA, frozen["id"]))
    intake = step_id(c, frozen["id"], "Intake")
    R.set_stage(c, ANA, intake, {"card_cap": 0})
    L.create_link(c, ANA, step_id(c, sales["id"], "Lead"), frozen["id"], "move")
    task = H.task_create(c, ANA, "Acme deal", "", ANA, type=sales["id"], step="Lead", lint=False)["id"]

    out = E.advance(c, H.KEEPER, task)
    assert not out["moved"]
    assert "so the ticket waits" in out["reason"]
    assert c.execute("SELECT step_id FROM tasks WHERE id=?", (task,)).fetchone()[0] == step_id(c, sales["id"], "Lead")
    assert X.waiting_room(c)[task]["stage_id"] == intake


def test_an_automation_close_leaves_a_subtask_on_ice_open(api):
    """An automation's close (rules._close) of a parent closes its other subtasks, but not one On ice: the cascade writes
    as Tico on the automation's behalf, and only a person takes a card out of On ice."""
    typ, steps = make_line(api, "IceKid" + uuid.uuid4().hex[:8], [*ON_ICE_LINE, {"name": "Closed", "status": "closed"}])
    set_cap(api, steps["On ice"], 0)
    with api.app.state.store.transaction() as c:
        parent = H.task_create(c, ANA, "Parent", "", ANA, type=typ, step="Backlog", lint=False)["id"]
        parked = H.task_create(c, ANA, "Parked kid", "", ANA, type=typ, step="On ice", parent_id=parent, lint=False)[
            "id"
        ]
        loose = H.task_create(c, ANA, "Loose kid", "", ANA, type=typ, step="Backlog", parent_id=parent, lint=False)[
            "id"
        ]
        RU._close(c, parent, {}, "Rule tidy")
        assert H.task(c, parent)["status"] == "closed"
        assert H.task(c, loose)["status"] == "closed"
        assert H.task(c, parked)["status"] == "open"
    assert card_stage(api, parked) == steps["On ice"]


def _trigger_rule(api, event, action, params, by="human:ana"):
    """A Task Trigger rule as its maker saved it: it acts as `by`, for every task."""
    import json

    with api.app.state.store.transaction() as c:
        row = {
            "id": "rule-" + H.new_id(),
            "scope_kind": "team",
            "scope_id": "",
            "event": event,
            "condition": "{}",
            "then_outcome": "action",
            "params": json.dumps({"action": action, **params}, sort_keys=True),
            "enabled": 1,
            "position": 1,
            "note": "",
            "created_by": by,
            "action_by": by,
            "created_at": H.now(),
            "updated_at": H.now(),
            "revision": 1,
        }
        c.execute(f"INSERT INTO rules({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))
    return row


def test_a_task_trigger_move_into_a_full_stage_is_held_and_listed_as_waiting_for_room(api):
    """A Task Trigger rule acts as its maker, a person, but is an automation: its move into a full stage is refused, the
    card stays, and the manager lists it as waiting for room."""
    typ, steps = make_line(api, "TrigFull" + uuid.uuid4().hex[:8])
    set_cap(api, steps["Doing"], 1)
    with api.app.state.store.transaction() as c:
        H.task_create(c, ANA, "Already doing", "", ANA, type=typ, step="Doing", lint=False)
    _trigger_rule(api, "task.created", "move_step", {"step": "Doing"})
    with api.app.state.store.transaction() as c:
        card = H.task_create(c, ANA, "Moved by a rule", "", ANA, type=typ, step="Backlog", lint=False)["id"]
    assert card_stage(api, card) == steps["Backlog"]
    with api.app.state.store.read() as c:
        assert X.waiting_room(c)[card]["stage_id"] == steps["Doing"]


def test_an_automation_writing_as_its_owner_is_held_to_the_caps_as_an_automation(api):
    """An automation writes as its human owner, but that is no person's override: a card it files or moves into a full
    stage, or into or out of On ice, is refused (automation_actions.run holds every action as an automation's)."""
    from backend import automation_actions as AA

    typ, steps = make_line(api, "OwnerAuto" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    set_cap(api, steps["Doing"], 1)
    with api.app.state.store.transaction() as c:
        H.task_create(c, ANA, "Already doing", "", ANA, type=typ, step="Doing", lint=False)
        card = H.task_create(c, ANA, "Owner's card", "", ANA, type=typ, step="Backlog", lint=False)["id"]
        parked = H.task_create(c, ANA, "Parked", "", ANA, type=typ, step="On ice", lint=False)["id"]
    seen = []

    def move(c, auto, run_id, args, values):
        seen.append(E.AUTOMATION.get())
        H.task_update(c, ANA, args["task"], step=args["step"])
        return {"ok": True, "detail": "moved"}

    AA.ACTIONS["_test_move"] = move
    try:
        with api.app.state.store.transaction() as c:
            for tid, to in ((card, "Doing"), (card, "On ice"), (parked, "Backlog")):
                out = AA.run(c, {"id": "auto-1", "flow": {}}, "run-1", "_test_move", {"task": tid, "step": to}, {})
                assert out["ok"] is False, (tid, to)
            assert E.AUTOMATION.get() is False
    finally:
        del AA.ACTIONS["_test_move"]
    assert seen == [True, True, True]
    assert card_stage(api, card) == steps["Backlog"]
    assert card_stage(api, parked) == steps["On ice"]
    assert person_move(api, card, "Doing").status_code == 200  # the person herself still may, over the cap


def test_a_task_rule_close_and_a_trigger_done_never_close_a_subtask_on_ice(api):
    """The 24-hour Task Rule close and a Task Trigger's move to Done are automations: a parked subtask stays open On ice."""
    from backend import task_rules

    typ, steps = make_line(
        api, "RuleKids" + uuid.uuid4().hex[:8], [*ON_ICE_LINE, {"name": "Closed", "status": "closed"}]
    )
    set_cap(api, steps["On ice"], 0)
    old = H.shift(H.now(), hours=-25)
    with api.app.state.store.transaction() as c:
        done = H.task_create(c, ANA, "Done parent", "", ANA, type=typ, step="Doing", lint=False)["id"]
        kept = H.task_create(c, ANA, "Parked kid", "", ANA, type=typ, step="On ice", parent_id=done, lint=False)["id"]
        c.execute(
            "UPDATE tasks SET status='done', step_id=?, done_at=?, created_at=?, updated_at=?, checkpoint_at=NULL "
            "WHERE id=?",
            (steps["Done"], old, old, old, done),
        )
        c.execute(
            "UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id IN (?, ?)",
            (old, done, kept),
        )
    failures = []
    task_rules.tick(api.app.state.store, H.parse_ts(H.now()), failures)
    assert not failures
    with api.app.state.store.read() as c:
        assert H.task(c, done)["status"] == "closed"
        assert H.task(c, kept)["status"] == "open"
    assert card_stage(api, kept) == steps["On ice"]

    with api.app.state.store.transaction() as c:
        parent = H.task_create(c, ANA, "Trigger parent", "", ANA, type=typ, step="Backlog", lint=False)["id"]
        kid = H.task_create(c, ANA, "Parked kid 2", "", ANA, type=typ, step="On ice", parent_id=parent, lint=False)[
            "id"
        ]
    _trigger_rule(api, "task.step_changed", "move_step", {"step": "Done"})
    assert person_move(api, parent, "Doing").status_code == 200
    with api.app.state.store.read() as c:
        assert H.task(c, kid)["status"] == "open"
    assert card_stage(api, kid) == steps["On ice"]


def test_an_ended_snooze_on_a_card_on_ice_is_no_missed_start(api):
    """A parked card's snooze that ended is not "nothing has picked it up": the roundup lists no missed start for it."""
    from backend import task_roundup as RD

    typ, steps = make_line(api, "IceSnooze" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    at = H.now()
    with api.app.state.store.transaction() as c:
        ids = {}
        for stage in ("Backlog", "On ice"):
            tid = H.task_create(c, ANA, "Snoozed " + stage, "", ANA, type=typ, step=stage, lint=False)["id"]
            c.execute("UPDATE tasks SET owner_id='bot:coo' WHERE id=?", (tid,))
            H._task_event(c, tid, H.KEEPER, "snoozed_until", H.shift(at, hours=-5), None, "snooze over")
            ids[stage] = tid
        listed = " ".join(str(line) for line in RD._missed_starts(c, "coo", "bot:coo", at))
    assert ids["Backlog"][:8] in listed  # the control: an ordinary card is listed
    assert ids["On ice"][:8] not in listed


def test_a_check_subtask_on_ice_is_never_a_stuck_check(api):
    """A Security check parked On ice under an ordinary ticket waits on purpose: it is not among the open checks the stuck
    test looks at (pipeline_exceptions._check_children)."""
    typ, steps = make_line(api, "IceCheck" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    with api.app.state.store.transaction() as c:
        ticket = H.task_create(c, ANA, "Ticket", "", ANA, type=typ, step="Doing", lint=False)["id"]
        live = H.task_create(c, ANA, "Security: live", "", ANA, type=typ, step="Backlog", parent_id=ticket, lint=False)
        parked = H.task_create(
            c, ANA, "Security: parked", "", ANA, type=typ, step="On ice", parent_id=ticket, lint=False
        )
        assert X._check_children(c, {ticket}) == {live["id"]: ticket}
        assert parked["id"] not in X._check_children(c, {ticket})


def test_a_wait_to_enter_a_frozen_pipeline_ends_once_any_working_stage_there_opens(tmp_path):
    """A link's wait into a pipeline all On ice names its first parked stage. Opening another working stage there lets the
    card in, so the wait ends: the manager no longer lists it as waiting for room."""
    c, sales, _, _ = two(tmp_path)
    frozen = H.type_create(
        c,
        ANA,
        "Frozen2",
        [{"name": "Parked", "status": "open"}, {"name": "Queue", "status": "open"}, {"name": "Done", "status": "done"}],
        mover=True,
    )
    c.execute("UPDATE task_types SET pipeline_owner_id=? WHERE id=?", (ANA, frozen["id"]))
    parked, queue = step_id(c, frozen["id"], "Parked"), step_id(c, frozen["id"], "Queue")
    R.set_stage(c, ANA, parked, {"card_cap": 0})
    R.set_stage(c, ANA, queue, {"card_cap": 0})
    L.create_link(c, ANA, step_id(c, sales["id"], "Lead"), frozen["id"], "move")
    task = H.task_create(c, ANA, "Acme deal", "", ANA, type=sales["id"], step="Lead", lint=False)["id"]

    assert not E.advance(c, H.KEEPER, task)["moved"]
    assert X.waiting_room(c)[task]["stage_id"] == parked
    R.set_stage(c, ANA, queue, {"card_cap": None})
    assert task not in X.waiting_room(c)


def _default_entry(c):
    """The stage a card with no type named lands at (the default type's first), emptied of other cards' weight."""
    probe = H.task_create(c, ANA, "Probe", "", ANA, lint=False, deduplicate=False)
    return probe["id"], probe["step_id"]


def test_an_automation_turn_and_an_event_rule_create_task_are_held_to_the_entry_cap(api):
    """A human-owned automation's turn (automation_actions.start_turn) and a person's event rule that files a task
    (rules create_task on a sourceless event) are automations: neither files a card into a full or On ice stage."""
    from backend import automation_actions as AA
    from backend import rules as RU2

    with api.app.state.store.transaction() as c:
        _, entry = _default_entry(c)
        c.execute("UPDATE task_steps SET card_cap=1 WHERE id=?", (entry,))
        auto = {"id": "auto-" + uuid.uuid4().hex[:8], "flow": {"owner": "person:ana", "name": "Turny"}}
        c.execute("SAVEPOINT turn")
        with pytest.raises(H.Refused) as refused:
            AA.start_turn(c, auto, "run-1", "s1", "bot:coo", "Do the thing", None)
        c.execute("ROLLBACK TO turn")
        c.execute("RELEASE turn")
        assert refused.value.rule == "stage_full"
        assert c.execute("SELECT count(*) FROM tasks WHERE step_id=?", (entry,)).fetchone()[0] == 1
        c.execute("UPDATE task_steps SET card_cap=0 WHERE id=?", (entry,))
    _trigger_rule(api, "tico.notice", "create_task", {"owner": "coo", "title": "Notice work"})
    with api.app.state.store.transaction() as c:
        RU2.judge_event(c, "tico.notice", {"trigger": "t-" + uuid.uuid4().hex[:6]})
        assert not c.execute("SELECT 1 FROM tasks WHERE title='Notice work'").fetchone()


def test_a_parked_card_wakes_no_bot_through_a_backlog_or_a_blocker(api):
    """On ice is never over its backlog (step_backlog.warnings), and a parked waiting card held by a bot's work is not
    listed on that bot's roundup (task_roundup._blocked_waits)."""
    from backend import step_backlog as SB
    from backend import task_roundup as RD
    from backend.tests.test_step_backlog import board, tickets

    with api.app.state.store.transaction() as c:
        typ = board(c, {"over": 1, "notify": ["manager"], "every_hours": 4})
        tickets(c, typ, "bot:ops", 2)
        assert SB.warnings(c), "the control: a stage over its backlog warns"
        c.execute("UPDATE task_steps SET card_cap=0 WHERE id=?", (typ["steps"][0]["id"],))
        assert SB.warnings(c) == []

    typ2, steps = make_line(api, "IceBlocked" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    with api.app.state.store.transaction() as c:
        blocker = H.task_create(c, ANA, "Finish the menu", "", "bot:ops", lint=False)["id"]
        ids = {}
        for stage in ("Backlog", "On ice"):
            tid = H.task_create(c, ANA, "Waits " + stage, "", "bot:finance", type=typ2, step=stage, lint=False)["id"]
            c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (tid,))
            c.execute(
                "INSERT INTO task_relations(from_task,to_task,kind,created_by,created_at) VALUES(?,?,'blocks',?,?)",
                (blocker, tid, ANA, H.now()),
            )
            ids[stage] = tid
        listed = {row["id"] for row, *_ in RD._blocked_waits(c, H.now()).get("ops", [])}
    assert ids["Backlog"] in listed  # the control
    assert ids["On ice"] not in listed


def test_an_automatic_move_into_another_pipelines_full_stage_keeps_waiting_while_its_entry_has_room(api):
    """A wait for a named stage in another pipeline lasts while that stage is full, though that pipeline's entry stage has
    room: only a wait to enter a pipeline that was all On ice ends when any stage there opens."""
    typ, steps = make_line(api, "XFrom" + uuid.uuid4().hex[:8])
    other, osteps = make_line(api, "XTo" + uuid.uuid4().hex[:8])
    set_cap(api, osteps["Doing"], 1)
    with api.app.state.store.transaction() as c:
        H.task_create(c, ANA, "Already doing", "", ANA, type=other, step="Doing", lint=False)
        card = H.task_create(c, ANA, "Transfer", "", ANA, type=typ, step="Backlog", lint=False)["id"]
        assert H.try_auto_move(c, "bot:coo", H.task(c, card), type=other, step=osteps["Doing"]) is False
    with api.app.state.store.read() as c:
        assert X.waiting_room(c)[card]["stage_id"] == osteps["Doing"]


def test_a_message_rule_move_into_a_full_stage_is_listed_as_waiting_for_room(api):
    """A message rule's move (rules.carry_out) into a full stage fails open, but the wait is recorded after the rollback:
    the card stays and the manager lists it as waiting for room."""
    typ, steps = make_line(api, "MsgRule" + uuid.uuid4().hex[:8])
    set_cap(api, steps["Doing"], 1)
    with api.app.state.store.transaction() as c:
        H.task_create(c, ANA, "Already doing", "", ANA, type=typ, step="Doing", lint=False)
        card = H.task_create(c, ANA, "Moved by a message", "", ANA, type=typ, step="Backlog", lint=False)["id"]
        decision = RU.Decision(RU.ACTION, "rule-msg", params={"action": "move_step", "step": "Doing"}, by=ANA)
        out = RU.carry_out(c, decision, ANA, "bot:coo", "chat", {"task": card}, "Move it", None)
        assert out.outcome == RU.WAKE
    assert card_stage(api, card) == steps["Backlog"]
    with api.app.state.store.read() as c:
        assert X.waiting_room(c)[card]["stage_id"] == steps["Doing"]


def test_no_roundup_line_and_no_task_manager_item_names_a_card_on_ice(api, monkeypatch):
    """Whichever source would name a parked card (a question sent back, a resume stop, a merged pull request), the bot's
    roundup and the Task Manager's report leave it out, so it wakes no bot."""
    from backend import task_manager as TM
    from backend import task_roundup as RD

    typ, steps = make_line(api, "IceLines" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    with api.app.state.store.transaction() as c:
        ids = {
            stage: H.task_create(c, ANA, "Line " + stage, "", "bot:coo", type=typ, step=stage, lint=False)["id"]
            for stage in ("Backlog", "On ice")
        }
    at = H.now()

    def stops(c, slug, actor, at):
        return [(RD.STOPPED, at, RD._item(tid, "No progress " + tid[:8], "stop:" + tid)) for tid in ids.values()]

    monkeypatch.setattr(RD, "_resume_stops", stops)
    with api.app.state.store.read() as c:
        named = {item["tid"] for item in RD._items(c, "coo", at, {})}
    assert ids["Backlog"] in named  # the control
    assert ids["On ice"] not in named

    def merged(c, at):
        return [TM._item("merged", H.task(c, tid), "its pull request merged", "close it") for tid in ids.values()]

    monkeypatch.setattr(TM, "_merged", merged)
    with api.app.state.store.read() as c:
        by, _ = TM.collect(c, api.app.state.store.settings, at)
    reported = {item["task"] for items in by.values() for item in items}
    assert ids["Backlog"] in reported  # the control
    assert ids["On ice"] not in reported


def test_a_card_parked_after_its_run_carried_it_is_no_quiet_unworked_warning(api):
    """Health's "handed to a run and not worked" warning (quiet_work.stale) leaves out a card parked On ice since."""
    from backend import maintenance_health as MH
    from backend import quiet_work as Q
    from backend.tests.test_quiet_work import finish, later, setup, wake

    r = setup(api)
    task = later(api)
    finish(api, r, wake(api, r))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE events SET created_at=? WHERE action=?", (H.shift(H.now(), hours=-3), Q.CARRIED))
        assert [f["key"] for f in MH._quiet_work_checks(c)] == [f"quiet_unworked:ops:{task['id']}"]  # the control
        c.execute("UPDATE task_steps SET card_cap=0 WHERE id=?", (H.task(c, task["id"])["step_id"],))
        assert MH._quiet_work_checks(c) == []


def test_a_card_on_ice_is_never_stuck_stranded_or_a_stuck_blocker(api):
    """A parked card is no stuck task (hubdb.stuck_tasks, the stuck API), no stranded work the resume sweep pushes on
    (stranded_resume.stranded), and a parked subtask never makes its parent "held by its own stuck subtasks"."""
    from backend import stranded_resume as SR2

    typ, steps = make_line(api, "IceStuck" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    set_cap(api, steps["On ice"], 0)
    old = H.shift(H.now(), hours=-26)
    with api.app.state.store.transaction() as c:
        ids = {}
        for stage in ("Backlog", "On ice"):
            tid = H.task_create(c, ANA, "Stuck " + stage, "", "bot:coo", type=typ, step=stage, lint=False)["id"]
            ids[stage] = tid
        parent = H.task_create(c, ANA, "Parent", "", "bot:coo", type=typ, step="Doing", lint=False)["id"]
        kid = H.task_create(c, ANA, "Kid", "", "bot:coo", type=typ, step="Doing", parent_id=parent, lint=False)["id"]
        c.execute(
            "INSERT INTO task_relations(from_task,to_task,kind,created_by,created_at) VALUES(?,?,'blocks',?,?)",
            (kid, parent, ANA, old),
        )
        c.execute("UPDATE tasks SET updated_at=?, next_run=0 WHERE id IN (?,?,?,?)", (old, *ids.values(), parent, kid))
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='coo'")
        stuck = {row["id"] for row in H.stuck_tasks(c)}
        assert ids["Backlog"] in stuck and ids["On ice"] not in stuck
        assert H.own_stuck_blockers(c, H.task(c, parent)) == [kid]  # the control
        c.execute("UPDATE tasks SET step_id=? WHERE id=?", (steps["On ice"], kid))
        assert H.own_stuck_blockers(c, H.task(c, parent)) == []

        doing = H.task_create(c, ANA, "Doing work", "", "bot:coo", type=typ, step="Doing", lint=False)["id"]
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='coo'")
        assert SR2.stranded(c, H.task(c, doing), H.now())  # the control
        c.execute("UPDATE tasks SET step_id=? WHERE id=?", (steps["On ice"], doing))
        assert not SR2.stranded(c, H.task(c, doing), H.now())


def test_a_follow_up_queued_for_a_card_parked_mid_turn_is_not_taken_into_the_running_turn(api):
    """A steer queued for a card's running turn waits once a person parks the card On ice: the turn's inputs pass it
    over and its job stays queued, as the claim leaves it (execution.job_refusal "on_ice")."""
    from backend.tests.helpers.api import claim, post
    from backend.tests.test_comment_delivery import inputs, job_state, world

    typ, steps = make_line(api, "IceInputs" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    machine, tid = world(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET type_id=?, step_id=? WHERE id=?", (typ, steps["Doing"], tid))
    attempt = claim(api, machine)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "plan"}, machine["token"])
    first = post(api, f"tasks/{tid}/comments", {"text": "Use the August numbers.", "delivery": "steer"})["comment"][
        "id"
    ]
    assert first in inputs(api, machine, attempt)  # the control
    second = post(api, f"tasks/{tid}/comments", {"text": "And the July ones.", "delivery": "steer"})["comment"]["id"]
    assert job_state(api, second) == "queued"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE task_steps SET card_cap=0 WHERE id=?", (steps["Doing"],))  # a person parks its stage
    assert second not in inputs(api, machine, attempt)
    assert job_state(api, second) == "queued"


def test_no_task_manager_item_names_a_card_on_ice_as_the_other_of_a_pair(api, monkeypatch):
    """A duplicate pair never names a parked card (the next open one is the first of the pair), and no item whose line
    names a parked card (a review ticket's parent, a duplicate's first) reaches the report."""
    from backend import task_manager as TM

    typ, steps = make_line(api, "IcePairs" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    with api.app.state.store.transaction() as c:
        same = []
        for n in range(3):
            tid = H.task_create(c, ANA, f"Lamps {n}", "", "bot:coo", type=typ, step="Backlog", lint=False)["id"]
            c.execute(  # the same title, as a pair made before the duplicate check would be
                "UPDATE tasks SET title='Order the lamps', private=0, created_at=? WHERE id=?",
                (H.shift(H.now(), minutes=n - 10), tid),
            )
            same.append(tid)
        first, second, third = same
        pairs = [(i["task"], i["about"]) for i in TM._duplicates(c)]
        assert pairs == [(second, [first]), (third, [first])]  # the control
        c.execute("UPDATE tasks SET step_id=? WHERE id=?", (steps["On ice"], first))
        c.execute("UPDATE task_steps SET card_cap=0 WHERE id=?", (steps["On ice"],))
        assert [(i["task"], i["about"]) for i in TM._duplicates(c)] == [(third, [second])]
    at = H.now()

    def reviews(c):
        return [
            TM._item("review_duplicate", H.task(c, mine), f"a second ticket for {other}", "close it", about=(other,))
            for mine, other in ((third, second), (second, first))
        ]

    monkeypatch.setattr(TM, "_reviews", reviews)
    monkeypatch.setattr(TM, "_duplicates", lambda c: [])
    with api.app.state.store.read() as c:
        by, _ = TM.collect(c, api.app.state.store.settings, at)
    reported = [item for items in by.values() for item in items if item["kind"] == "review_duplicate"]
    assert [(item["task"], item["about"]) for item in reported] == [(third, [second])]


def test_the_hourly_reopen_pass_leaves_a_card_on_ice_and_still_reopens_the_rest(api):
    """A bot's card waiting on nothing tracked goes back to open after the grace (task_holds.unstrand), but one a person
    parked On ice stays where it is, and the pass carries on for the others rather than failing."""
    from backend import task_holds
    from backend.tests.test_task_holds import bot_task, wait

    typ, steps = make_line(api, "IceReopen" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    with api.app.state.store.transaction() as c:
        loose, parked = (bot_task(c, f"Draft the post {n}") for n in range(2))
        for tid in (loose, parked):
            assert wait(c, tid, note="Waiting on the vendor")["blocked_kind"] == "stranded"
        c.execute("UPDATE tasks SET type_id=?, step_id=? WHERE id=?", (typ, steps["Doing"], parked))
        c.execute("UPDATE task_steps SET card_cap=0 WHERE id=?", (steps["Doing"],))  # a person parks its stage
        assert task_holds.unstrand(c, H.shift(H.now(), hours=3))["opened"] == 1
        assert H.task(c, loose)["status"] == "open"
        assert H.task(c, parked)["status"] == "waiting"


def test_a_question_sent_back_then_parked_on_ice_is_no_roundup_line_for_its_asker(api):
    """A question that went back unanswered (still_needed.send_back) and that a person then parks On ice is theirs now:
    the asker's roundup does not tell it to ask again."""
    from backend import still_needed, task_roundup as RD
    from backend.tests.test_task_holds import ask, bot_task, wait

    typ, steps = make_line(api, "IceAsk" + uuid.uuid4().hex[:8], ON_ICE_LINE)
    with api.app.state.store.transaction() as c:
        tid = bot_task(c, "Order the mail key")
        question = ask(c, tid, "Which mailbox gets the key?")
        wait(c, tid)
        still_needed.send_back(c, "bot:ops", [question])
        assert [item["tid"] for _, _, item in RD._asks_back(c, "ops", "bot:ops", H.now())] == [tid]  # the control
        c.execute(  # a person reopens the question and parks it
            "UPDATE tasks SET status='open', type_id=?, step_id=? WHERE id=?", (typ, steps["On ice"], question["id"])
        )
        c.execute("UPDATE task_steps SET card_cap=0 WHERE id=?", (steps["On ice"],))
        assert RD._asks_back(c, "ops", "bot:ops", H.now()) == []
