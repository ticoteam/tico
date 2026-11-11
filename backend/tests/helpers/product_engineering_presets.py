"""The Product and Engineering presets' setup for tests: the setup answers and the role bots in their seats."""

import json

from backend import bot_settings as BS
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
PPM, EPM = "product-pipeline-manager", "engineering-pipeline-manager"  # the chart ids pipeline_seats finds them by
ROLE_BOTS = {
    PPM: "pipeline-manager",
    "pm": "segment-product-owner",  # the Product Owner
    EPM: "pipeline-manager",
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
    _seat(c, "Product Pipeline Manager", "pipeline-manager", PPM)
    _seat(c, "Product Owner", "segment-product-owner", "pm")
    _seat(c, "Engineering Pipeline Manager", "pipeline-manager", EPM)
    _seat(c, "Builder", "builder", "builder")
    _seat(c, "Reviewer 2", "reviewer", "rev-b")
    _seat(c, "Reviewer 1", "reviewer", "rev-a")
