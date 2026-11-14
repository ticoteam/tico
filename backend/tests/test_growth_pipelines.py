"""Growth and Success: Experiments (Ideas, Building, Running) and Monitoring (Watching, Slipping)
(backend/pipeline_presets_more.py, docs/pipelines-v3.md "Growth and Success").

- An idea rests in Ideas until someone starts it: its bot owner when Building has room, or any person.
- A person's request moves on by itself only from a stage that says so (`requests_move_on`: Product's Ideas).
- Nothing wakes, chases or marks Late a task resting in Ideas or Watching.
- A kept thing moves Watching -> Slipping -> Watching at its owner's hand, and closes from either with a reason.
- An experiment snoozed to its end date in Running wakes at that date and is not Late meanwhile.
- The Go-live page is a person's OK: no bot gives it, pushes past it or loosens it.

Synthetic rows only (backend/tests/helpers/engine.py): the fictional company, where Ana owns it, and a few bots.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import answers as A
from backend import never_stuck as N
from backend import page_verdicts as V
from backend import pipeline_presets_more as M
from backend import pipeline_settings as PS
from backend import task_engine as E
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.product_engineering_presets import legacy_pair

ANA = X.ANA
OWNER, OTHER, MANAGER = "bot:channel-owner", "bot:other-owner", "bot:growth-manager"
START = datetime(2030, 1, 7, 9, 0, tzinfo=timezone.utc)
IDEAS, BUILDING, RUNNING = "growth-experiments-ideas", "growth-experiments-building", "growth-experiments-running"
WATCHING, SLIPPING = "growth-monitoring-watching", "growth-monitoring-slipping"


@pytest.fixture
def c(company, time_machine):
    time_machine.move_to(START, tick=False)
    with company.transaction() as conn:
        yield conn


@pytest.fixture
def later(c, time_machine):
    """Move the clock on and run one pass. Returns what the pass did, without the zeros."""

    def step(**delta):
        time_machine.shift(timedelta(**delta))
        done = N.check(c)
        assert N.rules_hold(c) == []
        return {key: n for key, n in done.items() if n}

    return step


def _set_up(c, software="no"):
    """Setup finished, the presets made, and the Growth pipelines managed by one bot."""
    record = {"answers": {"software_product": software}, "completed": H.now()}
    c.execute(
        "INSERT INTO registry_metadata VALUES('onboarding',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (json.dumps(record),),
    )
    for slug in ("channel-owner", "other-owner", "growth-manager"):
        X.bot(c, slug)
    made = PS.ensure_presets(c)
    c.execute("UPDATE pipelines SET manager_id=? WHERE factory_id='growth'", (MANAGER,))
    return made


def _idea(c, by, title, assignee=None, pipeline="growth-experiments"):
    return X.new_task(c, by, title, pipeline, assignee=assignee)


def _move(c, task_id, to, by, why=None):
    """Move a task as `by` would, having read it first."""
    return E.move(c, task_id, to, by, why, seen_version=E.task(c, task_id)["version"])


def _room_wakes(c):
    return c.execute("SELECT COUNT(*) FROM messages WHERE wake_key LIKE 'room:%'").fetchone()[0]


def _go_live(c, row):
    """The Go-live page attached on the task's visit to Building and passed by Jev. Returns the task and the page
    requirement's id."""
    rid = c.execute("SELECT id FROM requirements WHERE stage_id=? AND kind='page'", (BUILDING,)).fetchone()[0]
    c.execute(
        "INSERT INTO requirement_pages (id, visit_id, requirement_id, file_id, version, item_versions, attached_by_id, "
        "created_at) VALUES (?,?,?,?,?,?,?,?)",
        (H.new_id(), row["visit"]["id"], rid, "f-go-live", 1, "{}", row["owner_id"], H.now()),
    )
    found = V.owe(c, "f-go-live", 1, "")
    c.execute("UPDATE page_verdicts SET verdict='met', reasons='' WHERE id=?", (found["id"],))
    return E.task(c, row["id"]), rid


# ----------------------------------------------------------------------------- the shapes
def test_monitoring_is_made_only_where_its_experiments_pipeline_is(c):
    made = _set_up(c)
    assert {"growth-monitoring", "success-monitoring"} <= set(made)
    rows = c.execute("SELECT id, factory_id, prefix FROM pipelines WHERE id LIKE '%-monitoring' ORDER BY id")
    assert [tuple(r) for r in rows] == [("growth-monitoring", "growth", "GM"), ("success-monitoring", "success", "SM")]
    # A company that retired Success Experiments and never had Success Monitoring does not get it.
    c.execute("UPDATE pipelines SET retired_at=? WHERE id='success-experiments'", (H.now(),))
    c.execute(
        "DELETE FROM requirements WHERE stage_id IN (SELECT id FROM stages WHERE pipeline_id='success-monitoring')"
    )
    c.execute("DELETE FROM stages WHERE pipeline_id='success-monitoring'")
    c.execute("DELETE FROM pipelines WHERE id='success-monitoring'")
    assert "success-monitoring" not in PS.ensure_presets(c)


# ----------------------------------------------------------------------------- the setting
def test_a_persons_request_moves_on_by_itself_only_from_a_by_hand_stage_that_says_so(c):
    _set_up(c, software="yes")
    product = PS.read(c, "product")
    assert not any(s["requests_move_on"] for s in product["stages"])  # Product's Ideas is not by hand (idea_checks)
    growth = PS.read(c, "growth-experiments")
    assert not any(s["requests_move_on"] for s in growth["stages"])
    record = PS.read(c, "growth-experiments")
    record["stages"][1]["requests_move_on"] = True  # Building: not by hand
    with pytest.raises(H.Refused, match="only from a stage tasks leave by hand"):
        PS.save(c, ANA, record, seen_version=record["settings_version"])
    record["stages"][1]["requests_move_on"] = "yes"
    with pytest.raises(H.Refused):
        PS.save(c, ANA, record, seen_version=record["settings_version"])
    record = PS.read(c, "growth-experiments")
    record["stages"][0]["requests_move_on"] = True
    saved = PS.save(c, ANA, record, seen_version=record["settings_version"])
    assert saved["stages"][0]["requests_move_on"] is True
    # Left out of a save, it is off: a new stage, and a record from before the setting.
    record = PS.read(c, "growth-experiments")
    del record["stages"][0]["requests_move_on"]
    assert PS.save(c, ANA, record, seen_version=record["settings_version"])["stages"][0]["requests_move_on"] is False


# ----------------------------------------------------------------------------- (a) starting an idea
def test_a_bot_starts_its_own_idea_when_building_has_room_and_is_told_plainly_when_it_is_full(c):
    _set_up(c)
    ideas = [_idea(c, OWNER, f"Idea {n}") for n in range(8)]
    assert {E.task(c, i["id"])["owner_id"] for i in ideas} == {OWNER}
    for idea in ideas[:6]:
        moved = _move(c, idea["id"], BUILDING, OWNER)
        assert (moved["stage_id"], moved["owner_id"]) == (BUILDING, OWNER)
    with pytest.raises(
        H.Refused,
        match=r"^task_stage_full: No room in Building\. It holds 6 a bot is working on: move it again when one leaves\.$",
    ):
        _move(c, ideas[6]["id"], BUILDING, OWNER)
    assert E.task(c, ideas[6]["id"])["stage_id"] == IDEAS
    # A slot frees: the bot starts the next one itself. Nothing moved it there in the meantime.
    _move(c, ideas[0]["id"], E.CLOSED, OWNER, "Not worth it after all")
    N.check(c)
    assert E.task(c, ideas[6]["id"])["stage_id"] == IDEAS
    assert _move(c, ideas[6]["id"], BUILDING, OWNER)["stage_id"] == BUILDING
    # A person can start any idea, a bot's included, when there is room.
    _move(c, ideas[1]["id"], E.CLOSED, OWNER, "Dropped")
    moved = _move(c, ideas[7]["id"], BUILDING, ANA)
    assert (moved["stage_id"], moved["owner_id"]) == (BUILDING, OWNER)


# ----------------------------------------------------------------------------- (b) a person's idea stays put
def test_a_persons_idea_rests_in_growth_ideas_while_in_product_it_still_moves_on(c):
    legacy_pair(c)  # Ideas is the Product of the pair Product replaced
    _set_up(c, software="yes")
    X.bot(c, "pm")
    c.execute("UPDATE stages SET owner='bot:pm' WHERE id='product-spec'")
    growth = _idea(c, ANA, "Try a referral banner", assignee=OWNER)
    request = X.new_task(c, ANA, "Saved carts", "product")
    X.settled(c)
    X.ideas_checked(c)
    N.check(c)
    N.check(c)
    assert E.task(c, growth["id"])["stage_id"] == IDEAS
    assert E.task(c, request["id"])["stage_id"] == "product-spec"
    (wait,) = W.of(c, E.task(c, growth["id"]))
    assert wait.state == W.BY_HAND


def test_a_freed_slot_in_building_wakes_nobody_even_though_ideas_is_the_assignees(c):
    """C10 skips an assignee-owned by-hand stage whose requests do not move on: Growth's Ideas wakes no idea's owner
    when Building has room, from the start or after a slot frees. Product's Ideas does wake its owner
    (test_product_engineering_presets.py)."""
    _set_up(c)
    stage = c.execute("SELECT owner, requests_move_on FROM stages WHERE id=?", (IDEAS,)).fetchone()
    assert tuple(stage) == ("assignee", 0)
    waiting = _idea(c, OWNER, "Bot's idea")
    started = _idea(c, ANA, "Ana's idea", assignee=OWNER)
    _move(c, started["id"], BUILDING, OWNER)
    _move(c, started["id"], IDEAS, ANA, "Not yet")
    X.settled(c)
    before = X.wakes(c, waiting["id"])
    N.check(c)
    N.check(c)
    assert _room_wakes(c) == 0
    assert X.wakes(c, waiting["id"]) == before
    assert E.task(c, waiting["id"])["stage_id"] == IDEAS


# ----------------------------------------------------------------------------- (c) resting
def test_nothing_wakes_chases_or_marks_late_a_task_resting_in_ideas_or_watching(c, later):
    _set_up(c)
    resting = [
        _idea(c, OWNER, "Bot's idea"),
        _idea(c, ANA, "Ana's idea", assignee=OWNER),
        _idea(c, ANA, "Ana's own idea"),
        _idea(c, OWNER, "Keep the referral banner working", pipeline="growth-monitoring"),
        _idea(c, ANA, "Keep the onboarding email working", assignee=OTHER, pipeline="growth-monitoring"),
    ]
    X.settled(c)
    before = {r["id"]: X.wakes(c, r["id"]) for r in resting}
    for _ in range(6):
        assert later(days=30) == {}
    for r in resting:
        row = E.task(c, r["id"])
        assert row["stage_id"] in (IDEAS, WATCHING) and row["status"] == "open"
        assert X.wakes(c, r["id"]) == before[r["id"]], row["title"]
        assert not row.get("late")
        (wait,) = W.of(c, row)
        assert wait.state == W.BY_HAND
    assert _room_wakes(c) == 0
    assert not c.execute("SELECT 1 FROM messages WHERE recipient_id=?", (MANAGER,)).fetchone()


# ----------------------------------------------------------------------------- (d) monitoring
def test_a_bot_moves_a_kept_thing_to_slipping_and_back_and_closes_it_from_either_stage(c):
    _set_up(c)
    kept = _idea(c, OWNER, "Keep the referral banner working", pipeline="growth-monitoring")
    assert (kept["stage_id"], kept["owner_id"]) == (WATCHING, OWNER)
    moved = _move(c, kept["id"], SLIPPING, OWNER)
    assert (moved["stage_id"], moved["owner_id"]) == (SLIPPING, OWNER)
    with pytest.raises(H.Refused):
        _move(c, kept["id"], WATCHING, OWNER)  # back needs a reason
    assert _move(c, kept["id"], WATCHING, OWNER, "Fixed the broken link: back in range")["stage_id"] == WATCHING
    with pytest.raises(H.Refused):
        _move(c, kept["id"], E.CLOSED, OWNER)  # closing needs a reason
    assert _move(c, kept["id"], E.CLOSED, OWNER, "Banner retired with the old pricing")["status"] == "closed"
    other = _idea(c, OWNER, "Keep the welcome email working", pipeline="growth-monitoring")
    _move(c, other["id"], SLIPPING, OWNER)
    closed = _move(c, other["id"], E.CLOSED, OWNER, "Can't fix it: filed an idea instead")
    assert closed["status"] == "closed"
    # From Slipping, saying it is done moves it on to Done.
    third = _idea(c, OWNER, "Keep the pricing page working", pipeline="growth-monitoring")
    row = _move(c, third["id"], SLIPPING, OWNER)
    X.say_done(c, row)
    assert E.advance(c, third["id"])["status"] == "done"


# ----------------------------------------------------------------------------- (e) snoozed while it runs
def test_a_running_experiment_snoozed_to_its_end_date_wakes_then_and_is_not_late_meanwhile(c, later):
    _set_up(c)
    row = _move(c, _idea(c, OWNER, "Try a referral banner")["id"], BUILDING, OWNER)
    row, rid = _go_live(c, row)
    A.answer(c, row["id"], rid, ANA, "ok")
    row = E.task(c, row["id"])
    assert (row["stage_id"], row["owner_id"]) == (RUNNING, OWNER)
    X.settled(c)
    E.snooze(c, row["id"], OWNER, "Running until its end date", H.shift(H.now(), days=14), seen_version=row["version"])
    before = X.wakes(c, row["id"])
    assert [who for _, who in before if who == MANAGER] == []  # an end date: not "on ice", the manager is not told
    for _ in range(13):
        assert later(days=1) == {}
    assert not E.task(c, row["id"]).get("late") and X.wakes(c, row["id"]) == before
    assert later(days=1) == {"snoozes_ended": 1}
    assert later(seconds=10) == {"woken": 1}
    assert X.wakes(c, row["id"])[-1][1] == OWNER
    assert not E.task(c, row["id"]).get("late")


# ----------------------------------------------------------------------------- (f) the Go-live page
def test_no_bot_oks_pushes_past_or_loosens_the_go_live_page(c):
    _set_up(c)
    row = _move(c, _idea(c, OWNER, "Try a referral banner")["id"], BUILDING, OWNER)
    row, rid = _go_live(c, row)
    page = c.execute("SELECT who_oks, person_id, can_push_past FROM requirements WHERE id=?", (rid,)).fetchone()
    assert page[0] == "people_only"
    for bot in (OWNER, MANAGER):
        with pytest.raises(H.Refused, match="task_requirement"):
            A.answer(c, row["id"], rid, bot, "ok", seen_version=E.task(c, row["id"])["version"])
        with pytest.raises(H.Refused, match="task_needs_person"):
            _move(c, row["id"], E.NEXT, bot, "It looks fine")
        with pytest.raises(H.Refused, match="needs a person's OK first"):
            H.task_create(c, bot, "Skip it", "", bot, pipeline="growth-experiments", stage=RUNNING, lint=False)
    assert E.task(c, row["id"])["stage_id"] == BUILDING
    # The manager may change the pipeline, but not loosen the person's OK.
    assert PS.may_change(c, MANAGER, E.pipeline(c, "growth-experiments"))
    record = PS.read(c, "growth-experiments")
    record["stages"][1]["requirements"][0]["who_oks"] = "people_with_exceptions"
    with pytest.raises(H.Refused, match="This change loosens the person's OK on Building. A person must make it."):
        PS.save(c, MANAGER, record, seen_version=record["settings_version"])
    record = PS.read(c, "growth-experiments")
    record["stages"][1]["requirements"] = []
    with pytest.raises(H.Refused, match="loosens the person's OK"):
        PS.save(c, MANAGER, record, seen_version=record["settings_version"])
    # A person's OK moves it on.
    A.answer(c, row["id"], rid, ANA, "ok")
    assert E.task(c, row["id"])["stage_id"] == RUNNING
