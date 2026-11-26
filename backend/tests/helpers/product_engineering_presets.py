"""The software presets' setup for tests: the setup answers, the role bots in their seats, and the Product and
Engineering pair a company set up before 0.6 has (`legacy_pair`)."""

import json

from backend import bot_settings as BS
from backend import pipeline_seats as S
from backend import pipeline_settings as PS
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
PPM, EPM = "product-manager", "engineering-manager"
ROLE_BOTS = {
    PPM: "product-manager",
    "pm": "feature-owner",  # the Product Owner
    EPM: "engineering-manager",
    "builder": "builder",
    "rev-b": "reviewer",
    "rev-a": "reviewer",
    "rm": "release-owner",
}


def _answers(c, software="", completed=True):
    """The setup record (backend/onboarding.py): the software answer, and whether setup finished."""
    record = {"answers": {"software_product": software}, "completed": H.now() if completed else None}
    c.execute(
        "INSERT INTO registry_metadata VALUES('onboarding',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (json.dumps(record),),
    )


def _role_bot(c, slug, template):
    actor = X.bot(c, slug)
    BS.update(c, slug, {"template": template})
    return actor


def _seat(c, title, template, filled_by="", at=None):
    at = at or H.now()
    c.execute(
        "INSERT INTO seats (id, title, template, filled_by, created_at, created_by, updated_at, updated_by, closed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (H.new_id(), title, template, filled_by, at, ANA, at, ANA, at if filled_by else None),
    )


def _team(c):
    """The role bots, each seated as setup seats them. The reviewer seats are titled so `rev-a` is Reviewer 1."""
    for slug, template in ROLE_BOTS.items():
        _role_bot(c, slug, template)
    _seat(c, "Product Manager", "product-manager", PPM)
    _seat(c, "Product Owner", "feature-owner", "pm")
    _seat(c, "Engineering Manager", "engineering-manager", EPM)
    _seat(c, "Builder", "builder", "builder")
    _seat(c, "Reviewer 2", "reviewer", "rev-b")
    _seat(c, "Reviewer 1", "reviewer", "rev-a")


def legacy_review_stages(c, record):
    """Give a test pipeline the separate Review and Fixes stages that existing companies can have."""
    prefix = record["id"]
    stages = record["stages"]
    at = next(i for i, stage in enumerate(stages) if stage["id"] == f"{prefix}-review-1")
    review, fixes = stages[at : at + 2]
    page = review["requirements"][0]
    page["settings"]["rubric"] = PS.LEGACY_REVIEW_RUBRIC
    review.update(name="Review 1", purpose="Review the newest commit and attach a Review page.", requirements=[page])
    fixes["name"] = "Fixes 1"
    stages[at + 2 : at + 2] = [
        {
            **review,
            "id": f"{prefix}-review-2",
            "name": "Review 2",
            "purpose": "Review the newest commit again and attach a Review page.",
            "owner": S.resolve(c)[S.REVIEWER_2] or record["manager_id"],
            "requirements": [{**page, "id": None}],
        },
        {
            **fixes,
            "id": f"{prefix}-fixes-2",
            "name": "Fixes 2",
            "purpose": fixes["purpose"].replace("the review", "the second review")
            + " When it found nothing, say done at once.",
            "requirements": [{**r, "id": None} for r in fixes["requirements"]],
        },
    ]
    return record


MERGE_PURPOSE = PS._MERGE_PURPOSE
RELEASE_PURPOSE_BEFORE = PS.EARLIER["engineering-release/purpose"][-1]


def _as_before(c, key, record):
    """The pair as ensure_presets made it before Nov 26: Engineering with a QA a person approves and a Merge stage
    before Release; Product whose Ideas move on by themselves and whose Spec a person approves, with no Approval."""
    stages = record["stages"]
    if key == "engineering":
        qa = next(s for s in stages if s["id"] == PS.QA_STAGE)
        qa["purpose"] = PS._QA_PURPOSE_PERSON
        page = next(r for r in qa["requirements"] if r["kind"] == "page")
        page.update(who_oks="people_only", person_id=S.ok_person(c, record["manager_id"]))
        release = next(s for s in stages if s["id"] == "engineering-release")
        merge = {
            **release,
            "id": "engineering-merge",
            "name": "Merge",
            "purpose": MERGE_PURPOSE,
            "late_minutes": 120,
            "requirements": [PS.github("pr_merged")],
        }
        release.update(purpose=RELEASE_PURPOSE_BEFORE, on_enter={}, requirements=[PS.github("in_release")])
        stages.insert(stages.index(release), merge)
        return legacy_review_stages(c, record)
    ideas = next(s for s in stages if s["id"] == PS.IDEAS_STAGE)
    ideas.update(by_hand=False, purpose=PS._IDEAS_PURPOSE_ON_ROOM)
    approval = next(s for s in stages if s["id"] == PS.SPEC_OK_STAGE)
    spec = next(s for s in stages if s["id"] == "product-spec")
    spec["requirements"] = approval["requirements"]
    stages.remove(approval)
    return record


def legacy_pair(c):
    """The Product and Engineering pair as a company set up before 0.6 has it: made the way ensure_presets made them
    then, with the Product factory (`_as_before`). Its next start keeps them and their upkeep. Call it before
    ensure_presets."""
    PS._ensure_factory(c)
    for key in PS.PAIR:
        if not PS._made_from(c, key):
            PS.save(c, PS.SYSTEM, _as_before(c, key, PS.preset(c, key)))


def features(c):
    """Features (pipeline_settings.LEGACY) as a company set up from 0.6 until Nov 26 has it: made the way
    ensure_presets made it then, with the Product factory. Its next start keeps it and makes no pair beside it. Call it
    before ensure_presets."""
    PS._ensure_factory(c)
    if not PS._made_from(c, PS.FEATURES):
        PS.save(c, PS.SYSTEM, PS.preset(c, PS.FEATURES))
