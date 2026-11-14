"""The software presets' setup for tests: the setup answers, the role bots in their seats, and the Product and
Engineering pair a company set up before 0.6 has (`legacy_pair`)."""

import json

from backend import bot_settings as BS
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


def legacy_pair(c):
    """The Product and Engineering pair (pipeline_settings.LEGACY) as a company set up before 0.6 has it: made the way
    ensure_presets made them then, with the Product factory. Its next start keeps them and their upkeep, and does not
    make the one-pipeline Product beside them. Call it before ensure_presets."""
    PS._ensure_factory(c)
    for key in ("engineering", "product"):
        if not PS._made_from(c, key):
            PS.save(c, PS.SYSTEM, PS.preset(c, key))
