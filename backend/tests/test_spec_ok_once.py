"""Product's spec is OK'd by a person once (docs/dev/pipelines-v3.md, "Product"): Jev checks the Spec page in Spec and the
task moves on when Jev passes it; a person OKs it in OK spec. A pair Product (pipeline_settings.LEGACY) reshaped by hand
into the one-pipeline stages kept its person on the Spec page, so the spec waited on them in Spec and was asked of them
again in OK spec; migration spec_ok_once (in v0.6.0) took that person off where the stage was still the default.

Synthetic rows only (backend/tests/helpers/engine.py): the fictional company, where Ana owns it, and its role bots.
Jev is a recorded verdict (backend/tests/helpers/page_requirement.py), never a model.
"""

import pytest

from backend import never_stuck as N
from backend import pipeline_settings as PS
from backend import task_engine as E
from backend import task_pages as P
from backend import task_requirements as R
from backend.blobs import Blobs
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.page_requirement import _page, _say
from backend.tests.helpers.product_engineering_presets import _answers, _team

ANA = X.ANA
OWNER = "bot:pm"  # the Product Owner of _team, as the feature owner the tasks are given to
PITCH = "Shoppers lose their cart when the tab closes. Keep it for 30 days."


@pytest.fixture
def store(company):
    company.blobs = Blobs(company.settings)
    return company


@pytest.fixture
def c(store):
    with store.transaction() as conn:
        _team(conn)
        X.bot(conn, "writer")  # whose file the spec page is (page_requirement._page)
        _answers(conn, software="yes")
        yield conn


def _spec_page(c, pipeline_id="product", stage="product-spec"):
    record = PS.read(c, pipeline_id)
    found = next(s for s in record["stages"] if s["id"] == stage)
    return next(r for r in found["requirements"] if r["kind"] == "page")


def _in_spec_with_a_met_page(c, store):
    row = H.task_create(c, ANA, "Saved carts", PITCH, OWNER, pipeline="product", lint=False)
    task = E.move(c, row["id"], "product-spec", ANA, "On for this test")
    fid, _ = _page(c, store)
    made = P.attach(c, task["id"], _spec_page(c)["line"], "page:" + fid, ANA)
    _say(c, made, "met")
    return task


def _beat(c):
    X.settled(c)
    N.check(c)


# ----------------------------------------------------------------------------- the preset
def test_a_new_companys_spec_names_nobody_and_ok_spec_is_the_one_person_ok(c):
    PS.ensure_presets(c)
    spec = _spec_page(c)
    assert (spec["person_id"], R.people_only(spec)) == (None, False), "Jev checks Spec; no person is named"
    ok_spec = _spec_page(c, stage="product-ok-spec")
    assert (ok_spec["who_oks"], ok_spec["person_id"], ok_spec["line"]) == ("people_only", None, "Spec")
    pages = [r for s in PS.read(c, "product")["stages"] for r in s["requirements"] if r["line"] == "Spec"]
    assert [R.people_only(r) or bool(r["person_id"]) for r in pages] == [False, True], "one person OK of the spec"


def test_a_spec_jev_passes_leaves_spec_without_a_person(c, store):
    PS.ensure_presets(c)
    task = _in_spec_with_a_met_page(c, store)
    (standing,) = R.standings(c, E.task(c, task["id"]))
    assert (standing.state, standing.line) == (R.MET, "Decision model passed it")
    _beat(c)
    assert E.task(c, task["id"])["stage_id"] == "product-related"
