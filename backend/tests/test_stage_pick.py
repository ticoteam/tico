"""A stage picks its owner from several bots, "One of several" (backend/stage_pick.py; spec "A stage picks its owner
from several", acceptance criteria AC-1 to AC-11).

Synthetic rows only (backend/tests/helpers/engine.py): an Engineering-like pipeline whose Build picks from two Builders
and becomes the task's owner, and whose two Reviews pick from two Reviewers and hold only the review.
"""

import collections
import urllib.request

import pytest

from backend import never_stuck, pipeline_settings, stage_pick
from backend import task_engine as E
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
BUILDERS = ["bot:builder", "bot:builder2"]
REVIEWERS = ["bot:reviewer1", "bot:reviewer2"]
DONE = [{"kind": "owner_done"}]


def _build(**pick):
    return {"bots": BUILDERS, "how": "turns", "prefer": "anyone", "owns_task": True, **pick}


def _review(**pick):
    return {"bots": REVIEWERS, "how": "turns", "prefer": "new", "owns_task": False, **pick}


def _stages(build=None, review=None):
    return (
        {"name": "Build", "owner": "pick", "pick": build or _build(), "requirements": DONE},
        {"name": "Review 1", "owner": "pick", "pick": review or _review(), "requirements": DONE},
        {"name": "Fixes 1", "owner": "assignee", "requirements": DONE},
        {"name": "Review 2", "owner": "pick", "pick": review or _review(), "requirements": DONE},
    )


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


@pytest.fixture
def bots(c):
    for slug in ("builder", "builder2", "reviewer1", "reviewer2", "reviewer3", "manager", "filer"):
        X.bot(c, slug)
    X.pipeline(c, manager="bot:manager")


def _eng(c, build=None, review=None):
    return X.pipeline(c, "eng", "Engineering", _stages(build, review), manager="bot:manager")


def _file(c, title, by=ANA, owner=None):
    """A task filed into Engineering: named to `owner`, or with nobody named (a hand-off)."""
    made = H.task_create(c, by, title, "What to do.", owner or by, pipeline="eng", lint=False, unassigned=owner is None)
    return E.task(c, made["id"])


def _last(c, stage_id, bot):
    c.execute("INSERT OR REPLACE INTO stage_pick_turns (stage_id, bot_id, at) VALUES (?,?,?)", (stage_id, bot, H.now()))


def _next(c, stage_id):
    return stage_pick.next_line(c, E.stage(c, stage_id))


def _lines(c, task_id):
    rows = c.execute(
        "SELECT note FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='pick' ORDER BY rowid",
        (task_id,),
    ).fetchall()
    return [r[0] for r in rows]


def _through(c, row, by=None):
    """The owner says done and the task moves to the next stage."""
    X.say_done(c, row, by)
    return E.advance(c, row["id"])


def _pause(c, slug):
    c.execute("DELETE FROM assignments WHERE bot=?", (slug,))


def test_ac1_take_turns_gives_a_task_with_no_owner_to_the_next_builder_and_wakes_nobody_else(c, bots):
    eng = _eng(c)
    _last(c, eng["stages"]["Build"], "bot:builder")
    assert _next(c, eng["stages"]["Build"]).startswith("The next task goes to builder2")  # AC-10
    row = _file(c, "Fix the login page")
    assert (row["owner_id"], row["assignee_id"]) == ("bot:builder2", "bot:builder2")
    assert [who for _, who in X.wakes(c, row["id"])] == ["bot:builder2"]  # the manager is not woken
    assert _lines(c, row["id"]) == ["Build given to builder2 · took turns"]
    assert _next(c, eng["stages"]["Build"]).startswith("The next task goes to builder,")
    assert _file(c, "Fix the signup page")["owner_id"] == "bot:builder"


def test_ac2_random_spreads_two_hundred_picks_evenly(c, bots, monkeypatch):
    eng = _eng(c, build=_build(how="random"))
    stage = E.stage(c, eng["stages"]["Build"])
    pick = stage_pick.config(stage)
    monkeypatch.setattr(stage_pick, "chance", __import__("random").Random(264))
    counts = collections.Counter(stage_pick.choose(c, None, stage, pick).bot for _ in range(200))
    assert set(counts) == set(BUILDERS) and all(80 <= n <= 120 for n in counts.values()), counts
    assert _next(c, eng["stages"]["Build"]) == "The next task goes to one of builder or builder2 at random."


def test_ac3_a_bot_filing_without_an_owner_is_picked_and_naming_an_owner_always_wins(c, bots):
    _eng(c)
    filed = _file(c, "Fix the export", by="bot:filer")
    assert filed["owner_id"] in BUILDERS and filed["assignee_id"] == filed["owner_id"]
    named = _file(c, "Fix the import", by="bot:filer", owner="bot:filer")
    assert (named["owner_id"], _lines(c, named["id"])) == ("bot:filer", [])  # not on the list, and still kept
    chosen = _file(c, "Fix the banner", owner="bot:builder")
    assert chosen["owner_id"] == "bot:builder" and _lines(c, chosen["id"]) == []


def test_ac4_a_kickback_returns_to_the_same_builder_even_over_its_limit(c, bots):
    eng = _eng(c, build=_build(limit_each=1))
    _last(c, eng["stages"]["Build"], "bot:builder")
    row = _file(c, "Fix the dashboard")
    assert row["owner_id"] == "bot:builder2"
    in_review = _through(c, row)
    assert in_review["assignee_id"] == "bot:builder2"
    _file(c, "Another fix")  # builder: builder2 is not at its limit, so turns go on
    assert _file(c, "A third fix")["owner_id"] == "bot:builder2"  # builder2 holds one in Build now
    back = E.move(c, row["id"], "Build", ANA, "The tests do not cover the empty state")
    assert (back["owner_id"], back["assignee_id"]) == ("bot:builder2", "bot:builder2")
    assert stage_pick.held(c, eng["stages"]["Build"], "bot:builder2") == 2  # over its limit of 1
    fixes = E.move(c, back["id"], "Fixes 1", ANA, "Straight to fixes")
    assert fixes["owner_id"] == "bot:builder2"


def test_ac5_prefer_new_to_the_task_gives_review_2_to_the_reviewer_who_did_not_do_review_1(c, bots):
    eng = _eng(c)
    _last(c, eng["stages"]["Review 1"], "bot:reviewer2")
    row = _through(c, _file(c, "Fix the footer"))
    assert row["owner_id"] == "bot:reviewer1" and row["assignee_id"] in BUILDERS
    fixes = _through(c, row)
    assert fixes["owner_id"] == fixes["assignee_id"]  # Fixes 1 is the task's owner: its Builder
    # Review 2's turns would give it to reviewer1 first; reviewer1 worked on it, so reviewer2 does.
    assert _next(c, eng["stages"]["Review 2"]).startswith("The next task goes to reviewer1")
    review_2 = _through(c, fixes)
    assert review_2["owner_id"] == "bot:reviewer2"
    assert _lines(c, row["id"])[-1] == "Review 2 given to reviewer2 · new to this task"


def test_ac6_prefer_a_different_model_family_and_unknown_never_counts_as_different(c, bots, monkeypatch):
    families = {"bot:builder": "anthropic", "bot:reviewer1": "openai", "bot:reviewer3": "anthropic"}
    monkeypatch.setattr(stage_pick, "family", lambda conn, bot: families.get(bot, ""))
    review = _review(bots=["bot:reviewer3", "bot:reviewer1"], prefer="family")
    eng = _eng(c, build=_build(bots=["bot:builder"]), review=review)
    row = _through(c, _file(c, "Fix the header"))
    assert row["owner_id"] == "bot:reviewer1"  # GPT, where the Builder is Claude; reviewer3 would be first in turn
    assert _lines(c, row["id"])[-1] == "Review 1 given to reviewer1 · a different model family"
    # reviewer2's family is unknown: never different, so with reviewer3 (Claude) it is skipped, and the line says so.
    monkeypatch.setattr(stage_pick, "chance", __import__("random").Random(1))
    unknown = _review(bots=["bot:reviewer2", "bot:reviewer3"], prefer="family")
    stage = E.stage(c, eng["stages"]["Review 2"])
    c.execute(
        "UPDATE stages SET pick=? WHERE id=?",
        (stage_pick.stored(stage_pick.config({**stage, "pick": unknown})), stage["id"]),
    )
    stage = E.stage(c, eng["stages"]["Review 2"])
    choice = stage_pick.choose(c, E.task(c, row["id"]), stage, stage_pick.config(stage), take=False)
    assert choice.why == "took turns, nobody of a different model family was free"


def test_ac7_a_kickback_from_review_1_comes_back_to_the_same_reviewer_and_the_owner_stays_the_builder(c, bots):
    eng = _eng(c)
    _last(c, eng["stages"]["Review 1"], "bot:reviewer1")
    row = _through(c, _file(c, "Fix the menu"))
    builder = row["assignee_id"]
    assert row["owner_id"] == "bot:reviewer2"
    fixes = E.move(c, row["id"], "Fixes 1", "bot:reviewer2", "Two tests are missing", seen_version=row["version"])
    assert fixes["owner_id"] == builder
    _last(c, eng["stages"]["Review 1"], "bot:reviewer2")  # turns would now give it to reviewer1
    again = E.move(c, row["id"], "Review 1", builder, "Fixed: both tests added", seen_version=fixes["version"])
    assert (again["owner_id"], again["assignee_id"]) == ("bot:reviewer2", builder)
    assert _lines(c, row["id"])[-1] == "Review 1 back to reviewer2 · had it here before"


def test_ac8_a_preference_that_would_leave_nobody_is_skipped_and_the_line_says_so(c, bots):
    _eng(c)
    _last(c, "eng-review-1", "bot:reviewer2")
    row = _through(c, _through(c, _file(c, "Fix the table")))  # Review 1 by reviewer1, then Fixes 1
    _pause(c, "reviewer2")
    review_2 = _through(c, row)
    assert review_2["owner_id"] == "bot:reviewer1"
    assert _lines(c, row["id"])[-1] == "Review 2 given to reviewer1 · took turns, nobody new to this task was free"


def test_ac9_everyone_full_waits_without_waking_anyone_and_the_first_with_room_gets_the_oldest(c, bots):
    eng = _eng(c, review=_review(limit_each=1, prefer="anyone"))
    first = _through(c, _file(c, "One"))
    second = _through(c, _file(c, "Two"))
    assert {first["owner_id"], second["owner_id"]} == set(REVIEWERS)
    third = _through(c, _file(c, "Three"))
    fourth = _through(c, _file(c, "Four"))
    for waiting in (third, fourth):
        assert waiting["owner_id"] is None and waiting["visit"]["pick_state"] == "full"
        assert [(w.state, w.line) for w in W.of(c, waiting)] == [("pick", "Waiting for a free reviewer")]
        assert X.wakes(c, waiting["id"], "bot:manager") == [] and E._turn_of(c, waiting) == (None, "")
    assert _next(c, eng["stages"]["Review 1"]).startswith("The next task waits for a free reviewer")
    never_stuck.check(c)
    assert E.task(c, third["id"])["owner_id"] is None  # still full: nobody chased
    freed = first["owner_id"]
    _through(c, first)  # its reviewer is done with it: one slot frees
    never_stuck.check(c)
    third, fourth = E.task(c, third["id"]), E.task(c, fourth["id"])
    assert third["owner_id"] == freed and fourth["owner_id"] is None  # the oldest first
    assert any(who == freed for _, who in X.wakes(c, third["id"]))
    assert third["visit"]["pick_state"] is None and third["visit"]["holder_id"] == freed


def test_ac9_all_paused_wakes_the_manager_once_and_tells_it_why(c, bots):
    _eng(c)
    _pause(c, "reviewer1")
    _pause(c, "reviewer2")
    row = _through(c, _file(c, "Fix the chart"))
    assert row["owner_id"] is None and row["visit"]["pick_state"] == "nobody"
    assert [who for _, who in X.wakes(c, row["id"]) if who == "bot:manager"] == ["bot:manager"]
    target, lead = E._turn_of(c, row)
    assert target == "bot:manager" and "every bot it picks from is paused" in lead
    assert _lines(c, row["id"])[-1] == "Review 1: nobody picked, every bot it picks from is paused"
    X.place(c, "reviewer2")
    never_stuck.check(c)
    assert E.task(c, row["id"])["owner_id"] == "bot:reviewer2"


def test_a_reassign_in_a_pick_stage_holds_the_stage_and_moves_the_owner_with_it(c, bots):
    _eng(c)
    row = _file(c, "Fix the toast")
    moved = E.reassign(c, row["id"], "bot:builder2" if row["owner_id"] == "bot:builder" else "bot:builder", ANA)
    assert moved["assignee_id"] == moved["owner_id"]
    review = _through(c, moved)
    other = next(b for b in REVIEWERS if b != review["owner_id"])
    E.reassign(c, review["id"], other, ANA)
    E.move(c, review["id"], "Fixes 1", ANA, "Missing a test")
    back = E.move(c, review["id"], "Review 1", ANA, "Test added")
    assert back["owner_id"] == other  # back to whoever had it last in Review 1


def test_ac11_settings_read_back_check_the_bots_and_only_a_person_changes_them(c, bots):
    _eng(c)
    record = pipeline_settings.read(c, "eng")
    build = record["stages"][0]
    assert build["owner"] == "pick" and build["pick"]["bots"] == BUILDERS and build["pick"]["owns_task"] is True
    assert build["pick"]["families"] == {"bot:builder": "", "bot:builder2": ""}
    assert build["pick_next"].startswith("The next task goes to builder")
    assert "pick" not in record["stages"][2]  # Fixes 1 has one owner: as today
    saved = pipeline_settings.save(c, ANA, record)  # read back and saved unchanged: nothing moves
    assert saved["stages"][0]["pick"] == build["pick"]
    changed = {**record, "settings_version": saved["settings_version"]}
    changed["stages"] = [dict(s) for s in record["stages"]]
    changed["stages"][1] = {**changed["stages"][1], "pick": {**changed["stages"][1]["pick"], "prefer": "anyone"}}
    with pytest.raises(H.Refused, match="Only a person changes who Review 1 picks from"):
        pipeline_settings.save(c, "bot:manager", changed, saved["settings_version"])
    unchanged = pipeline_settings.save(c, "bot:manager", {**saved}, saved["settings_version"])
    assert unchanged["stages"][1]["pick"]["prefer"] == "new"  # a bot may save the rest as it read it
    older = {**unchanged, "stages": [{k: v for k, v in s.items() if k != "pick"} for s in unchanged["stages"]]}
    kept = pipeline_settings.save(c, ANA, older)  # a client that does not know the pick keeps it
    assert kept["stages"][1]["pick"]["prefer"] == "new"
    wrong = {**kept, "stages": [dict(s) for s in kept["stages"]]}
    wrong["stages"][0] = {**wrong["stages"][0], "pick": {**wrong["stages"][0]["pick"], "bots": ["human:ana"]}}
    with pytest.raises(H.Refused, match="picks from bots only"):
        pipeline_settings.save(c, ANA, wrong)


def test_ac11_a_pick_makes_no_model_call(c, bots, monkeypatch):
    calls = []
    from clients import judge

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(judge, "urlopen", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(judge, "gateway_open", lambda *a, **k: calls.append(a))
    _eng(c, review=_review(limit_each=1))
    for n in range(4):
        _through(c, _file(c, f"Fix {n}"))
    never_stuck.check(c)
    assert calls == []


def test_a_restored_task_in_a_pick_stage_is_picked_for(c, bots):
    from backend import task_delete

    eng = _eng(c)
    _last(c, eng["stages"]["Review 1"], "bot:reviewer1")
    row = _through(c, _file(c, "Fix the legend"))
    assert row["owner_id"] == "bot:reviewer2"
    assert task_delete.delete_tasks(c, [row["id"]], apply=True, keep_work=True)["applied"]
    task_delete.restore_tasks(c, [row["id"]], ANA)
    back = E.task(c, row["id"])  # its visits went with the delete: a fresh pick, new to the task
    assert (back["stage_id"], back["owner_id"]) == (eng["stages"]["Review 1"], "bot:reviewer1")
    assert _lines(c, row["id"])[-1] == "Review 1 given to reviewer1 · new to this task"
    assert [who for _, who in X.wakes(c, row["id"])][-1] == "bot:reviewer1"
