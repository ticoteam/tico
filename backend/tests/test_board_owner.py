"""A board owner changes their board (backend/pipeline_settings.py, backend/pipeline_changes.py): only the company's
owner and Admins choose who owns a board, a board owner who is not an Admin never loosens a person's OK they are not
named on, the Log says what each save changed from and to, and the owner and manager are told when someone else
changes their board.

Synthetic rows only (backend/tests/helpers/engine.py): Ana owns the company, Ben is an Admin, Cara is a member and
owns the Operations board in `_ops`, and bot:manager manages it.
"""

import copy

import pytest

from backend import pipeline_settings as PS
from backend import task_factories_routes as TF
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.test_pipeline_settings import _ops

ANA, BEN, CARA, DAN = X.ANA, "human:ben", "human:cara", "human:dan"
OWNER_REFUSAL = "Only the company's owner and Admins choose who owns a board"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        for slug in ("builder", "manager"):
            X.bot(conn, slug)
        conn.execute(
            "UPDATE registry_metadata SET value=? WHERE key='onboarding'",
            ('{"answers": {"software_product": "no"}, "completed": "2026-01-01T00:00:00Z"}',),
        )
        yield conn


def _refused(words):
    return pytest.raises(H.Refused, match=words)


def _board(c):
    """Operations, made by Ben (an Admin): Cara owns it, bot:manager manages it."""
    return PS.save(c, BEN, _ops())


def _notices(c, to):
    rows = c.execute(
        "SELECT body FROM messages WHERE kind='notice' AND recipient_id=? AND body LIKE '%Operations%' ORDER BY rowid",
        (to,),
    ).fetchall()
    return [r[0] for r in rows]


def _log(c, pid):
    return [line for line in TF.log(c, pid, "1=1") if not line["task_id"]]


def test_only_the_company_owner_and_admins_choose_who_owns_a_board(c):
    saved = PS.save(c, BEN, _ops(owner_id=None))
    saved = PS.save(c, BEN, dict(saved, owner_id=CARA))
    assert saved["owner_id"] == CARA
    assert _log(c, saved["id"])[0]["text"] == "Ben made Cara the owner"
    for who, owner, seen in ((CARA, ANA, None), (CARA, None, None), ("bot:manager", ANA, saved["settings_version"])):
        with _refused(OWNER_REFUSAL):
            PS.save(c, who, dict(copy.deepcopy(saved), owner_id=owner), seen_version=seen)
        assert PS.read(c, saved["id"])["owner_id"] == CARA
    assert PS.save(c, ANA, dict(saved, owner_id=None))["owner_id"] is None  # the company's owner may


def test_a_board_owner_who_is_not_an_admin_renames_and_changes_limits_order_and_goals(c):
    saved = _board(c)
    intake, sign = copy.deepcopy(saved["stages"])
    intake["name"] = "Triage"
    sign["task_limit"] = 5
    after = PS.save(c, CARA, dict(saved, goal="Keep it tidy", stages=[intake, sign]))
    assert [s["name"] for s in after["stages"]] == ["Triage", "Sign off"]
    assert (after["stages"][1]["task_limit"], after["goal"]) == (5, "Keep it tidy")
    assert [s["id"] for s in after["stages"]] == [s["id"] for s in saved["stages"]]  # a rename keeps the stage


def test_a_board_owner_cannot_loosen_a_persons_ok_they_are_not_named_on_and_nothing_is_kept(c):
    saved = _board(c)
    loose = copy.deepcopy(saved)
    loose["goal"] = "Changed on the way"
    loose["stages"][1]["requirements"] = []  # removes Ben's OK on Sign off
    with _refused("Sign off"):
        PS.save(c, CARA, loose)
    kept = PS.read(c, saved["id"])
    assert (kept["goal"], kept["settings_version"]) == (saved["goal"], saved["settings_version"])
    assert len(kept["stages"][1]["requirements"]) == 1
    # Opening it to bots loosens it too.
    bots = copy.deepcopy(saved)
    bots["stages"][1]["requirements"][0]["who_oks"] = "people_with_exceptions"
    with _refused("Sign off"):
        PS.save(c, CARA, bots)
    # An Admin may, and so may the person the OK names.
    assert PS.save(c, BEN, copy.deepcopy(loose))["goal"] == "Changed on the way"
    theirs = PS.save(c, BEN, dict(_ops(name="Facilities", prefix="FA")))
    theirs["stages"][1]["requirements"][0]["person_id"] = CARA
    theirs = PS.save(c, BEN, theirs)
    theirs["stages"][1]["requirements"] = []
    assert PS.save(c, CARA, theirs)["stages"][1]["requirements"] == []


def test_with_no_owner_set_who_may_change_a_board_is_as_before(c):
    saved = PS.save(c, BEN, _ops(owner_id=None))
    with _refused("Only the pipeline's owner, its manager and Admins can change it"):
        PS.save(c, CARA, dict(saved, goal="Cara's goal"))
    after = PS.save(c, "bot:manager", dict(saved, goal="The manager's goal"), seen_version=saved["settings_version"])
    assert after["goal"] == "The manager's goal"


def test_the_log_says_what_changed_from_and_to_with_the_version_and_the_full_list(c):
    saved = _board(c)
    intake, sign = copy.deepcopy(saved["stages"])
    intake["name"] = "Triage"
    after = PS.save(c, CARA, dict(saved, stages=[intake, sign]))
    line = _log(c, saved["id"])[0]
    assert line["text"] == "Cara renamed the stage Intake to Triage"
    assert (line["version"], line["changes"]) == (after["settings_version"], ["renamed the stage Intake to Triage"])
    sign["task_limit"] = 5
    intake["purpose"] = "Sort it"
    PS.save(c, CARA, dict(after, goal="Keep it tidy", stages=[intake, sign]))
    line = _log(c, saved["id"])[0]
    assert line["changes"] == [
        "changed the goal from Keep the office running to Keep it tidy",
        "changed Triage's goal from Sort what came in to Sort it",
        "changed Sign off's limit from 2 to 5",
    ]
    assert line["text"] == "Cara changed the goal from Keep the office running to Keep it tidy and 2 more changes"
    assert _log(c, saved["id"])[-1]["text"] == "Ben made the pipeline"


def test_the_owner_and_manager_are_told_when_someone_else_changes_their_board(c):
    saved = _board(c)
    assert _notices(c, CARA) == []  # making it tells nobody
    after = PS.save(c, BEN, dict(saved, goal="Keep it tidy"))
    said = "Ben changed the goal from Keep the office running to Keep it tidy on Operations."
    assert [n.split(" [")[0] for n in _notices(c, CARA)] == [said]
    assert "/tasks/" + saved["id"] in _notices(c, CARA)[0]  # a link to the board
    assert len(_notices(c, "bot:manager")) == 1
    assert _notices(c, BEN) == []
    PS.save(c, CARA, dict(after, goal="Keep it neat"))
    assert len(_notices(c, CARA)) == 1  # whoever made the change is not told
    assert len(_notices(c, "bot:manager")) == 2
