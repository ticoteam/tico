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

from backend import pipeline_settings as PS
from backend.hubdb_core import KEEPER
from backend.tests.helpers import engine as X
from backend.tests.helpers.product_engineering_presets import _answers, _team

SHIPPED = Path(__file__).parent / "fixtures" / "shipped_preset_texts.json"
ANA = X.ANA
REVIEW_RC3 = PS.EARLIER["engineering-review-1/page/rubric"][-1]
FIXES_2_RUBRIC_RC3 = PS.EARLIER["engineering-fixes-2/page/rubric"][-1]
FIXES_2_PURPOSE_RC3 = PS.EARLIER["engineering-fixes-2/purpose"][-1]


def _hash(text):
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()[:16]


def _now():
    return {path: text for key in PS.PRESETS for path, text in PS.preset_texts(key).items()}


def _lost(shipped, now):
    """The lines whose shipped text changed without that text going into EARLIER."""
    return sorted(
        path
        for path, digest in shipped.items()
        if path in now
        and _hash(now[path]) != digest
        and not any(_hash(old) == digest for old in PS.EARLIER.get(path, ()))
    )


# ----------------------------------------------------------------------------- an upgrade
@pytest.fixture
def c(company):
    with company.transaction() as conn:
        _team(conn)
        _answers(conn, software="yes")
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


def _as_rc3(c):
    """Engineering as an rc.3 company has it: its Review pages' and Fixes 2's check lines and Fixes 2's purpose."""
    _rubric(c, "engineering-review-1", REVIEW_RC3)
    _rubric(c, "engineering-review-2", "  " + REVIEW_RC3.replace(" ", "\n", 3) + " ")  # whitespace is not an edit
    _rubric(c, "engineering-fixes-2", FIXES_2_RUBRIC_RC3)
    _purpose(c, "engineering-fixes-2", FIXES_2_PURPOSE_RC3)


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


def test_an_rc3_company_gets_the_newer_check_lines_and_purpose_and_nothing_else_changes(c):
    _as_rc3(c)
    old = PS.read(c, "engineering")
    PS.ensure_presets(c)
    new = PS.read(c, "engineering")
    want = copy.deepcopy(old)
    for sid in ("engineering-review-1", "engineering-review-2"):
        _page(want, sid)["settings"]["rubric"] = PS.REVIEW_RUBRIC
    _page(want, "engineering-fixes-2")["settings"]["rubric"] = PS.FIXES_2_RUBRIC
    _stage(want, "engineering-fixes-2")["purpose"] = _stage(PS.PRESETS["engineering"], "engineering-fixes-2")["purpose"]
    assert new == want  # ids, owners, people, settings and the version as they were: still an untouched preset
    assert new["settings_version"] == 1 and PS._untouched(c, "engineering")
    (logged,) = _follows(c)
    assert sorted(logged["newer_preset_text"]) == [
        "engineering-fixes-2/page/rubric",
        "engineering-fixes-2/purpose",
        "engineering-review-1/page/rubric",
        "engineering-review-2/page/rubric",
    ]
    assert len(logged["changes"]) == 4
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
    ] == [tuple(r) for r in stages]
    assert "engineering-review-2" not in [s["id"] for s in new["stages"]]
    # A check line is the page's own; a purpose names the stages around it, so it waits for the preset's shape.
    assert _page(new, "engineering-review-1")["settings"]["rubric"] == PS.REVIEW_RUBRIC
    assert _page(new, "engineering-fixes-2")["settings"]["rubric"] == PS.FIXES_2_RUBRIC
    assert _stage(new, "engineering-fixes-2")["purpose"] == FIXES_2_PURPOSE_RC3


def test_a_stage_whose_requirements_changed_keeps_its_purpose_and_a_retired_pipeline_is_left_alone(c):
    record = PS.read(c, "engineering")
    _stage(record, "engineering-fixes-2")["requirements"].append({"kind": "owner_done"})
    PS.save(c, ANA, record)
    _as_rc3(c)
    PS.ensure_presets(c)
    new = PS.read(c, "engineering")
    assert _stage(new, "engineering-fixes-2")["purpose"] == FIXES_2_PURPOSE_RC3
    assert _page(new, "engineering-fixes-2")["settings"]["rubric"] == PS.FIXES_2_RUBRIC
    _as_rc3(c)
    c.execute("UPDATE pipelines SET retired_at='2026-11-11T00:00:00Z' WHERE id='engineering'")
    before = PS.read(c, "engineering")
    PS.ensure_presets(c)
    assert PS.read(c, "engineering") == before


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
