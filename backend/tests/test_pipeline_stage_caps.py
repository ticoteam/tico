"""A stage's card cap (D68, D69): the pipeline's owner sets it per stage through the stage route, a blank cap is no cap, and a
cap-0 stage is "On ice". A bot or automation cannot move a card into a full stage (409 stage_full) or out of On ice; a
person can, and the answer says over_cap. A link or a spawn into a full stage leaves the card where it is, and the
pipeline manager lists it as waiting for room."""

import uuid
from datetime import datetime, timezone

import pytest

from backend.tests.test_api import api, get, headers  # noqa: F401  (api is a fixture)
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
from backend.tests.test_pipeline_links import two
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
