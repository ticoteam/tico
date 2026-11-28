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
from backend.tests.helpers.pipeline_settings import ops as _ops

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


def test_a_board_owner_cannot_hand_a_persons_ok_to_someone_else(c):
    """Handing Ben's OK to Cara and then removing it in a second save would take it off without Ben or an Admin."""
    saved = _board(c)
    mine = copy.deepcopy(saved)
    mine["stages"][1]["requirements"][0]["person_id"] = CARA
    with _refused("Sign off"):
        PS.save(c, CARA, mine)
    assert PS.read(c, saved["id"])["stages"][1]["requirements"][0]["person_id"] == BEN
    # A document approval's list of approvers is held the same way: none taken off, none added.
    page = {
        "kind": "page",
        "settings": {"document": "Spec", "rubric": "States the goal."},
        "who_oks": "people_only",
        "person_ids": [BEN, ANA],
        "line": "Ben or Ana approve the spec",
    }
    saved["stages"][1]["requirements"].append(page)
    saved = PS.save(c, BEN, saved)
    for people in ([BEN], [BEN, ANA, CARA], [CARA]):
        swapped = copy.deepcopy(saved)
        swapped["stages"][1]["requirements"][1]["person_ids"] = people
        with _refused("Sign off"):
            PS.save(c, CARA, swapped)
    assert PS.read(c, saved["id"])["stages"][1]["requirements"][1]["person_ids"] == [BEN, ANA]


def test_only_the_company_owner_and_admins_make_a_board_private_or_company_wide(c):
    saved = _board(c)  # it holds no task, so its privacy may change, but not by its owner or manager
    for who, seen in ((CARA, None), ("bot:manager", saved["settings_version"])):
        with _refused("Only the company's owner and Admins make a board private or company-wide"):
            PS.save(c, who, dict(copy.deepcopy(saved), private=True), seen_version=seen)
    assert PS.read(c, saved["id"])["private"] is False
    made = PS.save(c, BEN, dict(saved, private=True))
    assert made["private"] is True
    with _refused("Only the company's owner and Admins make a board private or company-wide"):
        PS.save(c, CARA, dict(copy.deepcopy(made), private=False))
    assert PS.save(c, ANA, dict(made, private=False))["private"] is False


def test_a_late_time_change_is_in_the_log_and_the_owner_and_manager_are_told(c):
    from backend import stage_rules as SR

    saved = _board(c)
    sign = saved["stages"][1]
    assert SR.add_late(c, sign["id"], sign["name"], saved["id"], 24 * 60)
    rule = SR.get(c, f"sr_late_{sign['id']}")
    SR.edit(c, CARA, rule["id"], {"timer": {"kind": "in_stage", "hours": 4}}, rule["revision"])
    line = _log(c, saved["id"])[0]
    assert line["text"] == "Cara changed the Late time on Sign off from 24 h to 4 h"
    assert line["version"] is None  # a rule is not a save of the pipeline's settings
    assert [n.split(" [")[0] for n in _notices(c, "bot:manager")] == [line["text"] + " on Operations."]
    assert _notices(c, CARA) == []  # whoever made the change is not told
    SR.retire(c, BEN, rule["id"])
    assert _log(c, saved["id"])[0]["text"] == "Ben retired the rule Late on Sign off"
    assert len(_notices(c, CARA)) == 1
    # A bot's change is only a suggestion until a person takes it: nothing changed, so nothing is logged.
    SR.add(c, "bot:manager", {"name": "Nudge", "pipeline_id": saved["id"], "stages": [sign["id"]], **_TIMER})
    assert _log(c, saved["id"])[0]["text"] == "Ben retired the rule Late on Sign off"


_TIMER = {"event": "timer", "timer": {"kind": "in_stage", "hours": 2}, "then": "tell_owner", "reason": "Two hours"}


def test_the_log_says_where_each_stage_moved_and_what_an_approval_changed_from_and_to(c):
    saved = _board(c)
    intake, sign = copy.deepcopy(saved["stages"])
    sign["task_limit"] = None  # the first stage has no limit
    after = PS.save(c, BEN, dict(saved, stages=[sign, intake]))
    assert _log(c, saved["id"])[0]["changes"] == [
        "changed the order of the stages: Sign off from 2 to 1, Intake from 1 to 2",
        "changed Sign off's limit from 2 to none",
    ]
    sign, intake = copy.deepcopy(after["stages"])
    sign["requirements"][0]["who_oks"] = "people_with_exceptions"
    sign["requirements"][0]["person_id"] = ANA
    PS.save(c, BEN, dict(after, stages=[sign, intake]))
    assert _log(c, saved["id"])[0]["changes"] == [
        'changed "Ben OKs it" on Sign off: who OKs from people only to people, with exceptions',
        'changed "Ben OKs it" on Sign off: person from Ben to Ana',
    ]


def test_a_board_owner_never_changes_what_someone_elses_approval_checks(c):
    saved = _board(c)
    page = {
        "kind": "page",
        "settings": {"document": "Spec", "rubric": "States the goal."},
        "who_oks": "people_only",
        "person_ids": [BEN, ANA],
        "line": "Ben or Ana approve the spec",
    }
    saved["stages"][1]["requirements"].append(page)
    saved = PS.save(c, BEN, saved)
    for settings in (
        {"document": "Spec", "rubric": "Anything goes."},
        {"document": "Notes", "rubric": "States the goal."},
    ):
        changed = copy.deepcopy(saved)
        changed["stages"][0]["name"] = "Inbox"  # a change she may make, refused with the rest of the save
        changed["stages"][1]["requirements"][1]["settings"] = settings
        with _refused("Sign off"):
            PS.save(c, CARA, changed)
    kept = PS.read(c, saved["id"])
    assert kept["stages"][0]["name"] == "Intake"
    assert kept["stages"][1]["requirements"][1]["settings"] == {"document": "Spec", "rubric": "States the goal."}
    # Her own changes still save, and an Admin may change the approval.
    renamed = copy.deepcopy(saved)
    renamed["stages"][0]["name"] = "Inbox"
    assert PS.save(c, CARA, renamed)["stages"][0]["name"] == "Inbox"
    admin = PS.read(c, saved["id"])
    admin["stages"][1]["requirements"][1]["settings"] = {"document": "Spec", "rubric": "Anything goes."}
    assert PS.save(c, BEN, admin)["stages"][1]["requirements"][1]["settings"]["rubric"] == "Anything goes."


def test_a_rule_moved_off_a_board_is_a_removal_in_its_log(c):
    from backend import stage_rules as SR

    saved = _board(c)
    sign = saved["stages"][1]
    rule = SR.add(c, CARA, {"name": "Nudge", "pipeline_id": saved["id"], "stages": [sign["id"]], **_TIMER})
    assert _log(c, saved["id"])[0]["text"] == "Cara added the rule Nudge on Sign off"
    SR.edit(c, BEN, rule["id"], {"pipeline_id": "", "stages": []}, SR.get(c, rule["id"])["revision"])
    line = _log(c, saved["id"])[0]
    assert line["text"] == "Ben removed the rule Nudge from Sign off"
    assert [n.split(" [")[0] for n in _notices(c, CARA)][-1] == line["text"] + " on Operations."
    assert [n.split(" [")[0] for n in _notices(c, "bot:manager")][-1] == line["text"] + " on Operations."


def test_a_late_time_that_counts_something_else_says_so(c):
    from backend import stage_rules as SR

    saved = _board(c)
    sign = saved["stages"][1]
    assert SR.add_late(c, sign["id"], sign["name"], saved["id"], 24 * 60)
    rule = SR.get(c, f"sr_late_{sign['id']}")
    SR.edit(c, CARA, rule["id"], {"timer": {"kind": "quiet", "hours": 24}}, rule["revision"])
    assert (
        _log(c, saved["id"])[0]["text"] == "Cara changed the Late time on Sign off from 24 h in the stage to 24 h quiet"
    )


def test_a_cleared_wording_reads_none_not_the_old_wording(c):
    from backend import pipeline_changes as PC

    saved = _board(c)
    sign = saved["stages"][1]
    rid = sign["requirements"][0]["id"]
    changes = {f"stages.{sign['id']}.requirements.{rid}.line": ["Ben OKs it", ""]}
    assert PC.lines(c, saved["id"], changes) == ['changed the wording on Sign off from Ben OKs it to none']
