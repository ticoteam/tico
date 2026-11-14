"""One record for a pipeline, its stages and requirements (backend/pipeline_settings.py, docs/pipelines-v3.md "Stage
settings", "Default pipelines", S11).

Synthetic rows only (backend/tests/helpers/engine.py): the fictional company, where Ana owns it, Ben is an Admin and
Cara is a member, and a few bots.
"""

import copy

import pytest

from backend import pipeline_presets_more as more
from backend import pipeline_settings as PS
from backend import task_engine as E
from backend import task_requirements as R
from backend.store import H
from backend.tests.helpers import engine as X


def _made(c):
    """What ensure_presets made, leaving out the Growth and Success pipelines (test_default_pipelines_more.py)."""
    return [p for p in PS.ensure_presets(c) if p not in more.PRESETS]


ANA, BEN, CARA = X.ANA, "human:ben", "human:cara"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        for slug in ("builder", "reviewer", "manager"):
            X.bot(conn, slug)
        # A company that builds no software: General is its only preset (Product and Engineering are in
        # backend/tests/test_product_engineering_presets.py).
        conn.execute(
            "UPDATE registry_metadata SET value=? WHERE key='onboarding'",
            ('{"answers": {"software_product": "no"}, "completed": "2026-01-01T00:00:00Z"}',),
        )
        yield conn


def _refused(rule, words=""):
    return pytest.raises(H.Refused, match=rule if not words else f"(?s){rule}.*{words}|{words}.*{rule}")


def _ops(**fields):
    """A two-stage pipeline record with every field set, as a caller sends it."""
    record = {
        "name": "Operations",
        "goal": "Keep the office running",
        "prefix": "OP",
        "manager_id": "bot:manager",
        "owner_id": CARA,
        "private": False,
        "factory_id": None,
        "preset": None,
        "keep_warm_hours": 8,
        "stages": [
            {
                "name": "Intake",
                "purpose": "Sort what came in",
                "owner": "bot:builder",
                "by_hand": False,
                "requests_move_on": False,
                "task_limit": None,
                "expected_minutes": 60,
                "expected_bots_only": True,
                "on_enter": {},
                "keep_warm_hours": 2,
                "requirements": [
                    {
                        "kind": "owner_done",
                        "source": None,
                        "condition": None,
                        "settings": {},
                        "can_push_past": True,
                        "who_oks": "none",
                        "person_id": None,
                        "line": "Say it is sorted",
                        "choices": None,
                    }
                ],
            },
            {
                "name": "Sign off",
                "purpose": "",
                "owner": "assignee",
                "by_hand": False,
                "requests_move_on": False,
                "task_limit": 2,
                "expected_minutes": None,
                "expected_bots_only": False,
                "on_enter": {},
                "keep_warm_hours": None,
                "requirements": [
                    {
                        "kind": "ok",
                        "source": None,
                        "condition": None,
                        "settings": {},
                        "can_push_past": True,
                        "who_oks": "people_only",
                        "person_id": BEN,
                        "line": "Ben OKs it",
                        "choices": None,
                    }
                ],
            },
        ],
    }
    record.update(fields)
    return record


def _without_ids(value):
    if isinstance(value, dict):
        return {k: _without_ids(v) for k, v in value.items() if k != "id"}
    if isinstance(value, list):
        return [_without_ids(v) for v in value]
    return value


def test_general_from_its_preset_has_the_fixed_ids_and_a_task_goes_from_to_do_to_done(c):
    # The first start made General before the company had a bot. Once the General Manager exists it manages it.
    assert PS.read(c, "general")["manager_id"] is None
    X.bot(c, "general-manager")
    assert _made(c) == []
    general = PS.read(c, "general")
    assert (general["name"], general["preset"], general["manager_id"]) == ("General", "general", "bot:general-manager")
    assert [s["id"] for s in general["stages"]] == ["general-todo"]
    assert (general["done"]["id"], general["closed"]["id"]) == ("general-done", "general-closed")
    todo = general["stages"][0]
    assert (todo["name"], todo["owner"], todo["task_limit"]) == ("To do", "assignee", None)
    assert (todo["expected_minutes"], todo["expected_bots_only"]) == (1440, True)
    assert [r["kind"] for r in todo["requirements"]] == ["owner_done"]
    row = X.new_task(c, ANA, "Write the launch note", assignee="bot:builder")
    assert (row["stage_id"], row["owner_id"]) == ("general-todo", "bot:builder")
    X.say_done(c, row)
    assert E.advance(c, row["id"])["stage_id"] == "general-done"


def test_presets_run_on_every_boot_make_general_once_and_never_change_it_again(c):
    assert [row[0] for row in c.execute("SELECT id FROM pipelines")] == ["general"]  # the first start made it
    assert _made(c) == []
    c.execute("DELETE FROM requirements WHERE stage_id IN (SELECT id FROM stages WHERE pipeline_id='general')")
    c.execute("DELETE FROM stages WHERE pipeline_id='general'")
    c.execute("DELETE FROM pipelines WHERE id='general'")
    assert _made(c) == ["general"]  # a database without it gets it at the next start
    assert _made(c) == []
    changed = PS.read(c, "general")
    changed["goal"] = "Everything that has no other home"
    changed["stages"][0]["expected_minutes"] = 600
    PS.save(c, ANA, changed)
    X.bot(c, "general-manager")  # a General Manager that arrives later does not change General either
    assert _made(c) == []
    after = PS.read(c, "general")
    assert (after["goal"], after["stages"][0]["expected_minutes"], after["manager_id"]) == (
        "Everything that has no other home",
        600,
        None,
    )
    assert c.execute("SELECT COUNT(*) FROM pipelines").fetchone()[0] == 1 + len(more.PRESETS) - len(
        more.PRIVATE
    )  # Legal and Finance wait for their bots


def test_a_saved_pipeline_reads_back_as_it_was_saved_with_done_and_closed_after_its_stages(c):
    sent = _ops()
    saved = PS.save(c, ANA, copy.deepcopy(sent))
    assert saved == PS.read(c, saved["id"])
    assert saved["settings_version"] == 1 and saved["retired_at"] is None
    got = _without_ids(
        {k: v for k, v in saved.items() if k not in ("settings_version", "retired_at", "done", "closed")}
    )
    assert got == _without_ids(sent)
    assert [s["name"] for s in E.stages(c, saved["id"])] == ["Intake", "Sign off", "Done", "Closed"]
    assert (saved["done"]["name"], saved["closed"]["name"]) == ("Done", "Closed")
    assert [s["id"] for s in saved["stages"]] == [f"{saved['id']}-intake", f"{saved['id']}-sign-off"]


def test_keep_warm_is_set_on_the_pipeline_and_a_stage_and_a_save_that_leaves_it_out_keeps_it(c):
    """ "Keep warm for up to N hours" (backend/keep_warm.py): 8 on a new pipeline, 0 off; a stage's own, or None for the
    pipeline's. A caller that never names it (an older editor) changes neither."""
    record = _ops()
    del record["keep_warm_hours"]
    for stage in record["stages"]:
        del stage["keep_warm_hours"]
    saved = PS.save(c, ANA, record)
    assert saved["keep_warm_hours"] == PS.KEEP_WARM_DEFAULT
    assert [s["keep_warm_hours"] for s in saved["stages"]] == [None, None]
    saved["keep_warm_hours"] = 0
    saved["stages"][1]["keep_warm_hours"] = 3
    saved = PS.save(c, ANA, saved)
    assert saved["keep_warm_hours"] == 0 and [s["keep_warm_hours"] for s in saved["stages"]] == [None, 3]
    left_out = copy.deepcopy(saved)
    del left_out["keep_warm_hours"]
    del left_out["stages"][1]["keep_warm_hours"]
    left_out["goal"] = "Keep the office running well"
    saved = PS.save(c, ANA, left_out)
    assert saved["keep_warm_hours"] == 0 and saved["stages"][1]["keep_warm_hours"] == 3
    saved["stages"][1]["keep_warm_hours"] = None  # back to the pipeline's
    assert PS.save(c, ANA, saved)["stages"][1]["keep_warm_hours"] is None
    for bad in (-1, 25, 1.5, True, "8"):
        assert PS.check(c, _ops(keep_warm_hours=bad))[0] == "Keep warm is a whole number of hours from 0 to 24."


def test_defaults_fill_in_the_switches_by_kind(c):
    record = _ops()
    record["stages"][0]["requirements"] = [{"kind": "owner_done"}]
    record["stages"][1]["requirements"] = [{"kind": "ok", "person_id": BEN}]
    saved = PS.save(c, ANA, record)
    done, ok = saved["stages"][0]["requirements"][0], saved["stages"][1]["requirements"][0]
    assert (done["can_push_past"], done["who_oks"]) == (True, "none")
    assert (ok["can_push_past"], ok["who_oks"]) == (True, "people_with_exceptions")
    merged = _ops(name="Releases", prefix="R")
    merged["stages"][1]["requirements"] = [PS.github("pr_merged")]
    saved = PS.save(c, ANA, merged)
    assert _req(saved, 1)["can_push_past"] is False  # its switch is off


def _stage(record, n=0):
    return record["stages"][n]


def _req(record, n=0):
    return record["stages"][n]["requirements"][0]


CASES = {
    "no name": (lambda r: r.update(name=" "), "A pipeline needs a name."),
    "a name in use": (lambda r: r.update(name="general"), "Another pipeline is called general."),
    "a prefix in use": (lambda r: r.update(prefix="ga"), "Another pipeline uses the prefix GA."),
    "a long prefix": (lambda r: r.update(prefix="OPS"), "A prefix is one or two letters."),
    "no stages": (lambda r: r.update(stages=[]), "A pipeline needs at least one stage."),
    "two stages, one name": (lambda r: _stage(r, 1).update(name="intake"), "Two stages are called intake."),
    "a stage called Done": (lambda r: _stage(r, 1).update(name="Done"), "Every pipeline ends in Done and Closed"),
    "an owner nobody is": (lambda r: _stage(r).update(owner="bot:nobody"), "must be a bot, a person or the task's"),
    "an owner word": (lambda r: _stage(r).update(owner="whoever"), "must be a bot, a person or the task's"),
    "a limit on the first stage": (
        lambda r: _stage(r).update(task_limit=3),
        "The first stage of a pipeline has no limit (Intake).",
    ),
    "a limit of zero": (lambda r: _stage(r, 1).update(task_limit=0), "The limit on Sign off is a whole number"),
    "a part of a minute": (lambda r: _stage(r).update(expected_minutes=1.5), "a whole number of minutes above zero"),
    "a setting a kind lacks": (
        lambda r: _req(r).update(PS.github("checks_pass", rubric="green")),
        "Checks pass has no setting rubric (on Intake).",
    ),
    "an answer with no name": (
        lambda r: _req(r).update(kind="answer", settings={"type": "text"}),
        "Say it is sorted on Intake needs a name",
    ),
    "an answer of no type": (
        lambda r: _req(r).update(kind="answer", settings={"name": "sorted", "type": "date"}),
        "Say it is sorted on Intake is text, a number or a choice.",
    ),
    "a number's bounds the wrong way": (
        lambda r: _req(r).update(kind="answer", settings={"name": "sorted", "type": "number", "min": 5, "max": 1}),
        "The min of Say it is sorted on Intake is more than its max.",
    ),
    "a choice with no choices": (
        lambda r: _req(r).update(kind="answer", settings={"name": "sorted", "type": "choice"}),
        "Say it is sorted on Intake needs its choices.",
    ),
    "choices on owner done": (
        lambda r: _req(r).update(choices=["Yes", "No"]),
        "Only an answer has choices, not Owner says done (on Intake).",
    ),
    "only when": (
        lambda r: _req(r, 1).update(settings={"only_when": {"answer": "risky", "in": ["Yes"]}}),
        "Only when and Met when are not ready yet. Remove only_when from OK from someone on Sign off.",
    ),
    "met when": (
        lambda r: _req(r).update(kind="answer", settings={"name": "sorted", "met_when": {"min": 1}}),
        "Remove met_when from Answer from someone on Intake.",
    ),
    "two answers, one name": (
        lambda r: (
            _req(r).update(kind="answer", settings={"name": "sorted"}),
            _req(r, 1).update(kind="answer", person_id=BEN, settings={"name": "sorted"}),
        ),
        "Two answers are named sorted.",
    ),
    "a kind that is not one": (lambda r: _req(r).update(kind="vibes"), "vibes is not a requirement kind"),
    "a person on owner done": (
        lambda r: _req(r).update(person_id=BEN),
        "Only a requirement a person can meet with a button can name a person (C29)",
    ),
    "nobody named on an OK": (
        lambda r: _req(r, 1).update(person_id="bot:nobody"),
        "bot:nobody is not a person or a bot",
    ),
    "an OK naming nobody": (lambda r: _req(r, 1).update(person_id=None), "names the person or bot who OKs it"),
    "who OKs on owner done": (
        lambda r: _req(r).update(who_oks="people_only"),
        "Who OKs is only for an OK or a page",
    ),
    "a merge pushed past": (
        lambda r: _req(r).update(PS.github("pr_merged"), can_push_past=True),
        "can't be pushed past (C3)",
    ),
    "another action": (lambda r: _stage(r).update(on_enter={"send_email": True}), "On enter on Intake is nothing"),
    "a handoff with no linked work": (
        lambda r: _stage(r).update(on_enter={"file_into": "general"}),
        "A stage that files a task always has Linked work done among its requirements",
    ),
    "a handoff to nowhere": (
        lambda r: _stage(r).update(on_enter={"file_into": "nowhere"}, requirements=[{"kind": "linked_done"}]),
        "Intake files into a pipeline that does not exist",
    ),
    "a manager nobody is": (lambda r: r.update(manager_id="bot:nobody"), "The manager must be a bot or a person"),
}


def test_check_names_each_problem_in_a_sentence_and_save_refuses_it(c):
    PS.save(c, ANA, dict(PS.preset(c, "general"), prefix="GA"))
    assert PS.check(c, _ops()) == []
    for case, (change, words) in CASES.items():
        record = _ops()
        change(record)
        problems = PS.check(c, record)
        assert any(words in p for p in problems), (case, problems)
        with _refused("task_settings"):
            PS.save(c, ANA, record)
    assert c.execute("SELECT COUNT(*) FROM pipelines").fetchone()[0] == 1


def test_a_requirement_id_is_listed_once_in_the_whole_record(c):
    record = _ops()
    _req(record, 0)["id"] = "r1"
    _req(record, 1)["id"] = "r1"  # two new stages: neither row is in the table yet
    assert "Requirement r1 is listed twice." in PS.check(c, record)
    with _refused("task_settings", "Requirement r1 is listed twice"):
        PS.save(c, ANA, record)
    assert c.execute("SELECT COUNT(*) FROM requirements WHERE id='r1'").fetchone()[0] == 0


def test_an_ok_on_a_stage_names_a_person_who_is_here(c):
    X.left(c, BEN)
    with _refused("task_settings", f"{BEN} is not a person or a bot here"):
        PS.save(c, ANA, _ops())  # Sign off names Ben
    record = _ops()
    _req(record, 1).update(person_id="human:missing")
    assert any("human:missing is not a person or a bot here" in p for p in PS.check(c, record))


def test_an_ok_on_a_stage_may_name_a_bot(c):
    record = _ops()
    _req(record, 1).update(person_id="bot:reviewer")
    saved = PS.save(c, ANA, record)
    assert _req(saved, 1)["person_id"] == "bot:reviewer"


def test_a_by_hand_stage_may_have_requirements_or_none(c):
    record = _ops()
    _stage(record).update(by_hand=True)
    assert PS.check(c, record) == []
    _stage(record).update(requirements=[])
    assert PS.check(c, record) == []


def test_a_stage_that_files_into_a_pipeline_saves_and_moving_into_it_files_one_task(c):
    PS.ensure_presets(c)
    record = _ops()
    _stage(record, 1).update(on_enter={"file_into": "general"}, requirements=[{"kind": "linked_done"}])
    assert PS.check(c, record) == []
    saved = PS.save(c, ANA, record)
    second = saved["stages"][1]["id"]
    row = X.new_task(c, ANA, "Order the chairs", saved["id"])
    X.say_done(c, row, ANA)
    moved = E.move(c, row["id"], E.NEXT, ANA)
    assert moved["stage_id"] == second
    filed = c.execute("SELECT to_task_id, kind FROM links WHERE from_task_id=?", (row["id"],)).fetchall()
    assert [r[1] for r in filed] == ["handoff"]
    assert E.task(c, filed[0][0])["pipeline_id"] == "general"


def test_handoffs_that_form_a_loop_are_refused_across_pipelines(c):
    PS.ensure_presets(c)
    product = _ops(name="Product", prefix="P")
    _stage(product).update(requirements=[{"kind": "linked_done"}])
    _stage(product).update(on_enter={"file_into": "general"})
    product = PS.save(c, ANA, product)
    general = PS.read(c, "general")
    _stage(general).update(on_enter={"file_into": product["id"]}, requirements=[{"kind": "linked_done"}])
    assert any("would loop" in p for p in PS.check(c, general))
    with _refused("task_settings"):
        PS.save(c, ANA, general)
    assert PS.read(c, "general")["stages"][0]["on_enter"] == {}
    _stage(general).update(on_enter={"file_into": "general"})
    assert any("would loop" in p for p in PS.check(c, general))  # into itself is a loop too
    with _refused("task_settings", "files tasks into General"):
        PS.retire(c, ANA, "general")


def test_a_bot_changes_a_pipeline_only_from_the_version_it_read(c):
    saved = PS.save(c, ANA, _ops())
    saved["goal"] = "Keep the office running well"
    with _refused("task_changed"):
        PS.save(c, "bot:manager", saved)  # a bot says which version it saw
    with _refused("task_changed"):
        PS.save(c, "bot:manager", saved, seen_version=0)
    again = PS.save(c, "bot:manager", saved, seen_version=1)
    assert (again["goal"], again["settings_version"]) == ("Keep the office running well", 2)
    with _refused("task_changed"):
        PS.save(c, ANA, again, seen_version=1)  # a person who gives one is held to it
    assert PS.save(c, ANA, again)["settings_version"] == 3  # a person may leave it out


def test_stage_and_requirement_ids_stay_the_same_across_saves_and_the_change_is_logged(c):
    saved = PS.save(c, ANA, _ops())
    intake, sign = saved["stages"]
    intake["name"] = "Triage"
    edited = dict(saved, stages=[sign, intake, {"name": "Intake", "requirements": [{"kind": "owner_done"}]}])
    sign["task_limit"] = None  # it is the first stage now
    after = PS.save(c, ANA, edited)
    assert [s["id"] for s in after["stages"][:2]] == [sign["id"], intake["id"]]
    assert after["stages"][1]["requirements"][0]["id"] == intake["requirements"][0]["id"]
    assert after["stages"][2]["id"] == f"{saved['id']}-intake-2"  # the old Intake keeps its id under its new name
    assert (after["done"], after["closed"]) == (saved["done"], saved["closed"])
    logged = H._one(c, "SELECT * FROM events WHERE action='pipeline.save' ORDER BY created_at DESC, id DESC LIMIT 1")
    data = H._json(logged["detail"])
    assert (logged["actor"], logged["target"], data["version"]) == (ANA, saved["id"], 2)
    assert data["changes"][f"stages.{intake['id']}.name"] == ["Intake", "Triage"]


def test_a_stage_with_open_tasks_cannot_be_removed_and_an_empty_one_is_retired(c):
    saved = PS.save(c, ANA, dict(_ops(), owner_id=None))
    intake, sign = saved["stages"]
    row = X.new_task(c, ANA, "Order more paper", saved["id"])
    with _refused("task_settings", "Move its 1 task first"):
        PS.save(c, ANA, dict(saved, stages=[dict(sign, task_limit=None)]))
    after = PS.save(c, ANA, dict(saved, stages=[intake]))
    assert [s["id"] for s in E.stages(c, saved["id"])] == [intake["id"], saved["done"]["id"], saved["closed"]["id"]]
    assert E.stage(c, sign["id"])["retired_at"]
    assert R.get(c, sign["requirements"][0]["id"])["withdrawn_at"] is None  # its rows stay as they were
    assert after["stages"] == [intake]
    X.say_done(c, E.task(c, row["id"]))
    assert E.advance(c, row["id"])["stage_id"] == saved["done"]["id"]  # Intake is the last stage now


def test_a_removed_requirement_lets_a_waiting_task_move_in_the_same_step(c):
    record = _ops()
    _stage(record)["requirements"].append({"kind": "ok", "person_id": BEN, "line": "Ben looks first"})
    saved = PS.save(c, ANA, record)
    looks = _stage(saved)["requirements"][1]["id"]
    row = X.new_task(c, ANA, "Order more paper", saved["id"])
    X.say_done(c, row)
    X.answer(c, row, looks, "no", BEN, "Which paper?")
    assert E.advance(c, row["id"]) is None  # it waits on Ben
    _stage(saved)["requirements"] = _stage(saved)["requirements"][:1]
    PS.save(c, ANA, saved)
    assert E.task(c, row["id"])["stage"]["name"] == "Sign off"
    assert R.get(c, looks)["withdrawn_at"]  # withdrawn, not deleted
    notes = c.execute("SELECT notes FROM answers WHERE answer='no' AND visit_id=?", (row["visit"]["id"],)).fetchone()
    assert notes[0] == "Which paper?"  # the answer given to it stays readable


def test_a_raised_limit_lets_the_oldest_waiting_task_in(c):
    record = _ops()
    # Sign off's owner has work there too: a task waiting only on Ben's OK would hold no slot (task_engine.has_room).
    _stage(record, 1)["requirements"].append(_stage(record)["requirements"][0])
    _stage(record)["requirements"] = []
    _stage(record, 1)["task_limit"] = 1
    saved = PS.save(c, ANA, record)
    first = X.new_task(c, ANA, "Order more paper", saved["id"])
    second = X.new_task(c, ANA, "Order more pens", saved["id"])
    E.fill_open_slots(c)
    assert E.task(c, second["id"])["stage"]["name"] == "Intake"
    _stage(saved, 1)["task_limit"] = 2
    PS.save(c, ANA, saved)
    assert [E.task(c, t["id"])["stage"]["name"] for t in (first, second)] == ["Sign off", "Sign off"]


def test_who_may_make_change_and_retire_a_pipeline(c):
    with _refused("task_settings", "Only the company's owner and Admins can make a pipeline"):
        PS.save(c, CARA, _ops())
    with _refused("task_settings", "Only the company's owner and Admins"):
        PS.save(c, "bot:manager", _ops())
    saved = PS.save(c, BEN, _ops())  # an Admin makes one; Cara owns it, the manager bot manages it
    saved["goal"] = "Cara's goal"
    saved = PS.save(c, CARA, saved)
    saved = PS.save(c, "bot:manager", dict(saved, goal="The manager's goal"), seen_version=saved["settings_version"])
    with _refused("task_settings", "Only the pipeline's owner, its manager and Admins can change it"):
        PS.save(c, "bot:reviewer", saved, seen_version=saved["settings_version"])
    with _refused("task_settings", "Only the pipeline's owner"):
        PS.save(c, "human:nobody", saved)
    row = X.new_task(c, ANA, "Order more paper", saved["id"])
    with _refused("task_settings", "Move its 1 task first"):
        PS.retire(c, CARA, saved["id"])
    E.move(c, row["id"], E.CLOSED, ANA)
    with _refused("task_settings", "Only the pipeline's owner"):
        PS.retire(c, "bot:reviewer", saved["id"], seen_version=saved["settings_version"])
    gone = PS.retire(c, CARA, saved["id"])
    assert gone["retired_at"] and gone["settings_version"] == saved["settings_version"] + 1
    with _refused("task_no_such_stage"):
        X.new_task(c, ANA, "Order more pens", saved["id"])
    assert PS.check(c, _ops()) == []  # its name and prefix are free again


def test_a_pipelines_manager_owner_and_stage_owner_are_people_who_can_act(c):
    X.left(c, BEN)
    X.no_sign_in(c, CARA)
    for change, words in (
        (lambda r: r.update(manager_id=BEN), f"The manager must be a bot or a person here, not {BEN}"),
        (lambda r: r.update(owner_id=CARA), f"The owner must be a bot or a person here, not {CARA}"),
        (lambda r: _stage(r).update(owner=CARA), "The owner of Intake must be a bot, a person or the task's assignee"),
    ):
        record = _ops(owner_id=ANA)
        _req(record, 1).update(person_id=ANA)  # Sign off names Ben, who left
        assert PS.check(c, record) == []
        change(record)
        assert any(words in p for p in PS.check(c, record)), PS.check(c, record)
        with _refused("task_settings"):
            PS.save(c, ANA, record)
