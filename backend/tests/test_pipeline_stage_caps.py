"""A stage's card cap (D68, D69): the pipeline's owner sets it per stage through the stage route, a blank cap is no cap, and a
cap-0 stage is "On ice". A bot or automation cannot move a card into a full stage (409 stage_full) or out of On ice; a
person can, and the answer says over_cap. A link or a spawn into a full stage leaves the card where it is, and the
pipeline manager lists it as waiting for room."""

import uuid
from datetime import datetime, timezone

import pytest

from backend.tests.test_api import api, get, headers  # noqa: F401  (api is a fixture)
from backend import hubdb as H
from backend import pipeline_engine as E
from backend import pipeline_exceptions as X
from backend import pipeline_links as L
from backend import pipeline_manager as PM
from backend import pipeline_requirements as R
from backend.auth import Identity
from backend.store import Problem
from backend.store import refused as answer_refusal
from backend.tests.test_pipeline_foundation import ANA, step_id
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

    assert X.waiting_room(c, H.now())[task]["stage_id"] == onboarding
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

