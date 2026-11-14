"""A preset's wording follows the preset on an upgrade where nobody edited it (backend/pipeline_settings.py `_follow`,
EARLIER), and a changed preset line keeps its old text in EARLIER, or a company still on that text would keep it.

The texts every preset shipped with are recorded, hashed, in backend/tests/fixtures/shipped_preset_texts.json. After
changing a preset line, add the text it had to EARLIER, then run `python -m backend.tests.test_preset_texts --write`.

Synthetic rows only (backend/tests/helpers/engine.py): the fictional company, where Ana owns it, and its role bots.
"""

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

from backend import answers as A
from backend import never_stuck as N
from backend import pipeline_settings as PS
from backend import task_engine as E
from backend import task_requirements as R
from backend.hubdb_core import KEEPER
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.product_engineering_presets import _answers, _team, legacy_pair

SHIPPED = Path(__file__).parent / "fixtures" / "shipped_preset_texts.json"
ANA = X.ANA
REVIEW_RC3 = PS.EARLIER["engineering-review-1/page/rubric"][-1]
FIXES_2_RUBRIC_RC3 = PS.EARLIER["engineering-qa/page/rubric"][-2]
FIXES_2_PURPOSE_RC3 = PS.EARLIER["engineering-fixes-2/purpose"][-2]
MERGE_PURPOSE_RC3 = PS.EARLIER["engineering-merge/purpose"][-1]


def _hash(text):
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()[:16]


def _now():
    """Every preset line by where it sits. Product's lines are under `features:`: it has the ids of the pair's Product
    it replaced (`product/goal`, `product-spec/purpose`), whose own lines keep their paths."""
    return {
        f"{key}:{path}" if key == PS.PRODUCT else path: text
        for key in PS.PRESETS
        for path, text in PS.preset_texts(key).items()
    }


def _lost(shipped, now):
    """The lines whose shipped text changed without that text going into EARLIER."""
    return sorted(
        path
        for path, digest in shipped.items()
        if path in now
        and _hash(now[path]) != digest
        and not any(_hash(old) == digest for old in PS.EARLIER.get(path.rpartition(":")[2], ()))
    )


# ----------------------------------------------------------------------------- an upgrade
@pytest.fixture
def c(company):
    with company.transaction() as conn:
        _team(conn)
        _answers(conn, software="yes")
        legacy_pair(conn)  # an upgrade: a company set up before 0.6 keeps the pair, and its wording follows the presets
        PS.ensure_presets(conn)
        yield conn


def _rubric(c, stage_id, text):
    c.execute(
        "UPDATE requirements SET settings=json_set(settings, '$.rubric', ?) "
        "WHERE stage_id=? AND kind='page' AND visit_id IS NULL",
        (text, stage_id),
    )


def _purpose(c, stage_id, text):
    c.execute("UPDATE stages SET purpose=? WHERE id=?", (text, stage_id))


def _before_qa(c):
    """Engineering as a company made before QA had a stage of its own has it: Fixes 2 holds the page a person OKs, as
    the Review page, and no Owner says done; Merge sends a task back to Fixes 2."""
    c.execute("DELETE FROM requirements WHERE stage_id='engineering-fixes-2' AND kind='owner_done'")
    c.execute("UPDATE requirements SET position=0 WHERE stage_id='engineering-fixes-2'")
    c.execute(
        "UPDATE requirements SET stage_id='engineering-fixes-2', line='Review page', position=1 "
        "WHERE stage_id='engineering-qa' AND kind='page'"
    )
    c.execute("DELETE FROM requirements WHERE stage_id='engineering-qa'")
    c.execute("DELETE FROM stages WHERE id='engineering-qa'")
    c.execute(
        "UPDATE stages SET position=position-1 WHERE pipeline_id='engineering' AND position > "
        "(SELECT position FROM stages WHERE id='engineering-fixes-2')"
    )
    _rubric(c, "engineering-fixes-2", PS.EARLIER["engineering-qa/page/rubric"][-1])
    _purpose(c, "engineering-fixes-2", PS.EARLIER["engineering-fixes-2/purpose"][-1])
    _purpose(c, "engineering-merge", MERGE_PURPOSE_RC3)


def _as_rc3(c):
    """Engineering as an rc.3 company has it: Fixes 2 holds the page a person OKs, and its Review pages' and Fixes 2's
    check lines and Fixes 2's purpose read as they shipped then."""
    _before_qa(c)
    _rubric(c, "engineering-review-1", REVIEW_RC3)
    _rubric(c, "engineering-review-2", "  " + REVIEW_RC3.replace(" ", "\n", 3) + " ")  # whitespace is not an edit
    _rubric(c, "engineering-fixes-2", FIXES_2_RUBRIC_RC3)
    _purpose(c, "engineering-fixes-2", FIXES_2_PURPOSE_RC3)


def _no_ids(record):
    """A record without its requirements' ids: QA's are made anew when it is added."""
    out = copy.deepcopy(record)
    for stage in out["stages"]:
        for req in stage["requirements"]:
            req["id"] = None
    return out


def _stage(record, stage_id):
    return next(s for s in record["stages"] if s["id"] == stage_id)


def _page(record, stage_id):
    return next(r for r in _stage(record, stage_id)["requirements"] if r["kind"] == "page")


def _follows(c):
    return [
        json.loads(r[0])
        for r in c.execute(
            "SELECT detail FROM events WHERE action='pipeline.save' AND actor=? ORDER BY rowid", (KEEPER,)
        )
        if "newer_preset_text" in json.loads(r[0])
    ]


def test_an_rc3_company_gets_qa_and_the_newer_check_lines_and_purposes_and_nothing_else_changes(c):
    now = PS.read(c, "engineering")
    _as_rc3(c)
    PS.ensure_presets(c)
    new = PS.read(c, "engineering")
    # ids, owners, people, settings and the version as a company made now has them: still an untouched preset
    assert _no_ids(new) == _no_ids(now)
    assert new["settings_version"] == 1 and PS._untouched(c, "engineering")
    (logged,) = _follows(c)
    assert sorted(logged["newer_preset_text"]) == [
        "engineering-fixes-2/purpose",
        "engineering-merge/purpose",
        "engineering-qa/page/line",
        "engineering-qa/page/rubric",
        "engineering-review-1/page/rubric",
        "engineering-review-2/page/rubric",
    ]
    PS.ensure_presets(c)  # a second start changes nothing
    assert PS.read(c, "engineering") == new and len(_follows(c)) == 1


def test_an_edited_line_stays_and_a_changed_pipeline_moves_up_one_version(c):
    record = PS.read(c, "engineering")
    _page(record, "engineering-review-1")["settings"]["rubric"] = "Our own check: " + REVIEW_RC3
    _stage(record, "engineering-fixes-2")["purpose"] = FIXES_2_PURPOSE_RC3 + " Ask Ben first."
    PS.save(c, ANA, record)
    _rubric(c, "engineering-review-2", REVIEW_RC3)
    version = PS.read(c, "engineering")["settings_version"]
    assert version > 1
    PS.ensure_presets(c)
    new = PS.read(c, "engineering")
    assert _page(new, "engineering-review-1")["settings"]["rubric"] == "Our own check: " + REVIEW_RC3
    assert _stage(new, "engineering-fixes-2")["purpose"] == FIXES_2_PURPOSE_RC3 + " Ask Ben first."
    assert _page(new, "engineering-review-2")["settings"]["rubric"] == PS.REVIEW_RUBRIC
    assert new["settings_version"] == version + 1
    PS.ensure_presets(c)
    assert PS.read(c, "engineering")["settings_version"] == version + 1 and len(_follows(c)) == 1


def test_a_removed_stage_is_passed_over_and_a_purpose_follows_only_where_every_preset_stage_is(c):
    record = PS.read(c, "engineering")
    record["stages"] = [s for s in record["stages"] if s["id"] != "engineering-review-2"]
    PS.save(c, ANA, record)
    _as_rc3(c)
    stages = c.execute(
        "SELECT id, retired_at IS NULL FROM stages WHERE pipeline_id='engineering' ORDER BY id"
    ).fetchall()
    PS.ensure_presets(c)
    new = PS.read(c, "engineering")
    assert [
        tuple(r)
        for r in c.execute("SELECT id, retired_at IS NULL FROM stages WHERE pipeline_id='engineering' ORDER BY id")
    ] == sorted([*(tuple(r) for r in stages), ("engineering-qa", 1)])
    assert "engineering-review-2" not in [s["id"] for s in new["stages"]]
    # A check line is the page's own; a purpose names the stages around it, so it waits for the preset's shape.
    assert _page(new, "engineering-review-1")["settings"]["rubric"] == PS.REVIEW_RUBRIC
    assert _page(new, "engineering-qa")["settings"]["rubric"] == PS.QA_RUBRIC
    assert _stage(new, "engineering-fixes-2")["purpose"] == FIXES_2_PURPOSE_RC3


def test_a_stage_whose_requirements_changed_keeps_its_purpose_and_a_retired_pipeline_is_left_alone(c):
    record = PS.read(c, "engineering")
    _stage(record, "engineering-fixes-2")["requirements"].append(PS.github("pr_open"))
    PS.save(c, ANA, record)
    _as_rc3(c)
    PS.ensure_presets(c)
    new = PS.read(c, "engineering")
    assert _stage(new, "engineering-fixes-2")["purpose"] == FIXES_2_PURPOSE_RC3
    assert _page(new, "engineering-qa")["settings"]["rubric"] == PS.QA_RUBRIC
    _as_rc3(c)
    c.execute("UPDATE pipelines SET retired_at='2026-11-11T00:00:00Z' WHERE id='engineering'")
    before = PS.read(c, "engineering")
    PS.ensure_presets(c)
    assert PS.read(c, "engineering") == before


# ----------------------------------------------------------------------------- QA, added to a company made before it
def test_an_edited_engineering_pipeline_gets_qa_with_the_page_its_person_oks_as_it_was_set(c):
    _before_qa(c)
    record = PS.read(c, "engineering")
    page = _page(record, "engineering-fixes-2")
    page["settings"] = {**page["settings"], "rubric": "Our own check."}
    PS.save(c, ANA, record)
    version = PS.read(c, "engineering")["settings_version"]
    PS.ensure_presets(c)
    new = PS.read(c, "engineering")
    names = [s["name"] for s in new["stages"]]
    assert names == ["On deck", "Build", "Review 1", "Fixes 1", "Review 2", "Fixes 2", "QA", "Merge", "Release"]
    fixes, qa = _stage(new, "engineering-fixes-2"), _stage(new, "engineering-qa")
    assert [r["condition"] or r["kind"] for r in fixes["requirements"]] == ["owner_done", "checks_pass"]
    assert [r["condition"] or r["kind"] for r in qa["requirements"]] == ["checks_pass", "page"]
    assert qa["owner"] == fixes["owner"] == "bot:builder"
    moved = _page(new, "engineering-qa")
    assert (moved["person_id"], moved["who_oks"], moved["settings"]["rubric"]) == (
        ANA,
        "people_only",
        "Our own check.",
    )
    assert moved["line"] == "QA page"  # an unedited line follows the preset
    assert new["settings_version"] == version + 2  # QA added, then the page's line moved to the preset's
    # Once: a second start changes nothing, and a QA stage a person removes stays removed.
    PS.ensure_presets(c)
    assert PS.read(c, "engineering")["settings_version"] == version + 2
    new["stages"] = [s for s in new["stages"] if s["id"] != "engineering-qa"]
    PS.save(c, ANA, new)
    PS.ensure_presets(c)
    assert "QA" not in [s["name"] for s in PS.read(c, "engineering")["stages"]]


def test_a_task_waiting_in_fixes_2_on_the_old_page_moves_on_to_qa_once_the_builder_says_done(c):
    """A task in Fixes 2 when QA is added, its page attached and waiting on the person: the page leaves Fixes 2 with
    the requirement, so the task waits on the Builder's done (the never-stuck check wakes it) and then moves to QA,
    where the Builder attaches the QA page and the person is asked. Nothing is left waiting on a requirement gone."""
    _before_qa(c)
    c.execute("UPDATE requirements SET withdrawn_at=? WHERE kind='item' AND stage_id LIKE 'engineering-%'", (H.now(),))
    row = H.task_create(c, ANA, "Saved carts", "Let a shopper keep a cart.", ANA, pipeline="engineering", lint=False)
    row = E.move(c, row["id"], "engineering-fixes-2", ANA, "Waiting on the person's OK")
    old = c.execute("SELECT id FROM requirements WHERE stage_id='engineering-fixes-2' AND kind='page'").fetchone()[0]
    c.execute(
        "INSERT INTO requirement_pages (id, visit_id, requirement_id, file_id, version, item_versions, attached_by_id, "
        "created_at) VALUES (?,?,?,?,?,?,?,?)",
        (H.new_id(), row["visit"]["id"], old, "f-page", 1, "{}", row["owner_id"], H.now()),
    )
    PS.ensure_presets(c)
    c.execute("UPDATE requirements SET withdrawn_at=? WHERE stage_id='engineering-qa' AND kind='item'", (H.now(),))
    row = E.task(c, row["id"])
    assert row["stage_id"] == "engineering-fixes-2"
    (done,) = R.standings(c, row)
    assert (done.requirement["kind"], done.state, done.on) == ("owner_done", R.NOT_MET, "bot:builder")
    A.answer(c, row["id"], done.requirement["id"], "bot:builder", "done", "Fixed", seen_version=row["version"])
    qa = E.task(c, row["id"])
    assert qa["stage_id"] == "engineering-qa"
    (page,) = R.standings(c, qa)
    assert (R.name_of(page.requirement), page.state, page.on) == ("QA page", R.NOT_MET, "bot:builder")
    assert N.rules_hold(c) == []


def test_a_task_past_fixes_2_when_qa_is_added_keeps_the_persons_ok_for_its_release(c):
    """A task a person OK'd in Fixes 2 and in Release when QA is added never visits QA: its OK on the page that moved
    still lets the Release Owner cut its release. A task with no such OK is still refused, named for QA."""
    from backend.item_sources import github_release as GR

    _before_qa(c)
    # Merge's PR merged can't be pushed past: taken off, so the tasks can be moved to Release.
    c.execute("UPDATE requirements SET withdrawn_at=? WHERE kind='item' AND stage_id LIKE 'engineering-%'", (H.now(),))
    page = c.execute("SELECT id FROM requirements WHERE stage_id='engineering-fixes-2' AND kind='page'").fetchone()[0]
    row = H.task_create(c, ANA, "Saved carts", "Let a shopper keep a cart.", ANA, pipeline="engineering", lint=False)
    row = E.move(c, row["id"], "engineering-fixes-2", ANA, "Reviewed")
    c.execute(
        "INSERT INTO answers (id, visit_id, requirement_id, by_id, answer, notes, created_at) VALUES (?,?,?,?,?,?,?)",
        (H.new_id(), row["visit"]["id"], page, ANA, "ok", "Ship it", H.now()),
    )
    oked = E.move(c, row["id"], "engineering-release", ANA, "Merged")["id"]
    # Moved over Fixes 2 from On deck: never signed off anywhere.
    row = H.task_create(c, ANA, "Guest checkout", "Let a guest pay.", ANA, pipeline="engineering", lint=False)
    never = E.move(c, row["id"], "engineering-release", ANA, "Merged")["id"]
    tasks = [oked, never]
    assert [GR.unsigned(c, E.task(c, t)) for t in tasks] == [None, "Fixes 2"]
    PS.ensure_presets(c)
    assert [GR.unsigned(c, E.task(c, t)) for t in tasks] == [None, "QA"]


def _write():
    """Record the texts the presets ship with now, once every changed line's old text is in EARLIER."""
    now = _now()
    if SHIPPED.exists():
        lost = _lost(json.loads(SHIPPED.read_text()), now)
        if lost:
            sys.exit(f"Add the old text of {', '.join(lost)} to EARLIER (backend/pipeline_settings.py) first.")
    SHIPPED.write_text(json.dumps({path: _hash(text) for path, text in sorted(now.items())}, indent=1) + "\n")


if __name__ == "__main__" and sys.argv[1:] == ["--write"]:
    _write()
