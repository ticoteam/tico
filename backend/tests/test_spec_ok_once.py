"""Product's spec is OK'd by a person once (docs/pipelines-v3.md, "Product"): Jev checks the Spec page in Spec and the
task moves on when Jev passes it; a person OKs it in OK spec. A pair Product (pipeline_settings.LEGACY) reshaped by hand
into the one-pipeline stages kept its person on the Spec page, so the spec waited on them in Spec and was asked of them
again in OK spec; migration 20261115192933_spec_ok_once takes that person off where the stage is still the default.

Synthetic rows only (backend/tests/helpers/engine.py): the fictional company, where Ana owns it, and its role bots.
Jev is a recorded verdict (backend/tests/helpers/page_requirement.py), never a model.
"""

import copy
import importlib

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
from backend.tests.helpers.product_engineering_presets import _answers, _team, legacy_pair

ANA = X.ANA
OWNER = "bot:pm"  # the Product Owner of _team, as the feature owner the tasks are given to
PITCH = "Shoppers lose their cart when the tab closes. Keep it for 30 days."
MIGRATION = "backend.migrations.20261115192933_spec_ok_once"


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


def _migrate(c):
    importlib.import_module(MIGRATION).up(c)


def _spec_page(c, pipeline_id="product", stage="product-spec"):
    record = PS.read(c, pipeline_id)
    found = next(s for s in record["stages"] if s["id"] == stage)
    return next(r for r in found["requirements"] if r["kind"] == "page")


def _reshaped(c, spec_who="none", person=ANA):
    """The pair's Product given the one-pipeline shape by hand, as a company did: Related input and OK spec after Spec,
    OK spec's people-only Spec page naming the person, and Spec's page no longer people only but still naming them."""
    legacy_pair(c)
    record = PS.read(c, "product")
    stages = record["stages"]
    at = next(n for n, s in enumerate(stages) if s["id"] == "product-spec")
    spec = stages[at]["requirements"][0]
    assert (spec["kind"], spec["who_oks"], spec["person_id"]) == ("page", "people_only", ANA), "the pair's Spec OK"
    spec["who_oks"] = spec_who
    shipped = {s["id"]: s for s in PS.PRESETS[PS.PRODUCT]["stages"]}
    added = [copy.deepcopy(shipped["product-related"]), copy.deepcopy(shipped["product-ok-spec"])]
    added[1]["requirements"][0]["person_id"] = person
    for stage in added:
        stage["owner"] = OWNER
    record["stages"] = [*stages[: at + 1], *added, *stages[at + 1 :]]
    return PS.save(c, ANA, record)


def _in_spec_with_a_met_page(c, store):
    row = H.task_create(c, ANA, "Saved carts", PITCH, OWNER, pipeline="product", lint=False)
    task = E.move(c, row["id"], "product-spec", ANA, "On for this test")
    fid, _ = _page(c, store)
    made = P.attach(c, task["id"], "Spec page", "page:" + fid, ANA)
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
    assert (ok_spec["who_oks"], ok_spec["person_id"], ok_spec["line"]) == ("people_only", None, "Spec page")
    pages = [r for s in PS.read(c, "product")["stages"] for r in s["requirements"] if r["line"] == "Spec page"]
    assert [R.people_only(r) or bool(r["person_id"]) for r in pages] == [False, True], "one person OK of the spec"


def test_a_spec_jev_passes_leaves_spec_without_a_person(c, store):
    PS.ensure_presets(c)
    task = _in_spec_with_a_met_page(c, store)
    (standing,) = R.standings(c, E.task(c, task["id"]))
    assert (standing.state, standing.line) == (R.MET, "Jev passed it")
    _beat(c)
    assert E.task(c, task["id"])["stage_id"] == "product-related"


# ----------------------------------------------------------------------------- the migration
def test_the_migration_takes_the_person_off_a_reshaped_pair_spec_and_its_waiting_tasks_move_on(c, store):
    _reshaped(c)
    task = _in_spec_with_a_met_page(c, store)
    (standing,) = R.standings(c, E.task(c, task["id"]))
    assert (standing.state, standing.on) == (R.WAITING, ANA), "before: Jev passed it, yet it waits on Ana in Spec"
    version = PS.read(c, "product")["settings_version"]
    ok_spec = _spec_page(c, stage="product-ok-spec")

    _migrate(c)
    assert _spec_page(c)["person_id"] is None
    assert _spec_page(c, stage="product-ok-spec") == ok_spec, "OK spec stays Ana's people-only OK"
    assert PS.read(c, "product")["settings_version"] == version + 1, "an editor open on the old record reads again"
    _migrate(c)
    assert PS.read(c, "product")["settings_version"] == version + 1, "running it again changes nothing"

    _beat(c)
    assert E.task(c, task["id"])["stage_id"] == "product-related"


@pytest.mark.parametrize("change", ["no_ok_spec", "spec_people_only", "second_requirement", "another_line"])
def test_the_migration_leaves_a_spec_the_company_changed_or_the_pair_as_it_was(c, change):
    if change == "no_ok_spec":
        legacy_pair(c)  # the pair's Product as made: its Spec page is the spec's only OK
    else:
        _reshaped(c, spec_who="people_only" if change == "spec_people_only" else "none")
    if change == "second_requirement":
        record = PS.read(c, "product")
        spec = next(s for s in record["stages"] if s["id"] == "product-spec")
        spec["requirements"].append({"kind": "owner_done"})
        PS.save(c, ANA, record)
    if change == "another_line":
        c.execute("UPDATE requirements SET line='Spec' WHERE stage_id='product-spec' AND kind='page'")
    before = PS.read(c, "product")
    _migrate(c)
    assert PS.read(c, "product") == before
    assert _spec_page(c)["person_id"] == ANA
