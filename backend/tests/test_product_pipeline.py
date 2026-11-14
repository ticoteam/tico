"""Product, one pipeline from pitch to production (docs/pipelines-v3.md "Product"; pipeline_settings.PRODUCT): what a
company that builds software gets in place of the Product and Engineering pair, its person gates (any person, never a
bot), the 280-character pitch, the stages its feature owner holds, Validate's send back to Build, and adding it by hand
beside the pair a company set up before 0.6 keeps (`add_preset`, POST /api/v2/pipelines/{id}/from-preset).

Synthetic rows only (backend/tests/helpers/engine.py): the fictional company, where Ana owns it, and its role bots.
"""

import pytest

from backend import answers as A
from backend import pipeline_presets_more as more
from backend import pipeline_settings as PS
from backend import task_engine as E
from backend import task_requirements as R
from backend import validate_gate as G
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.product_engineering_presets import PPM, _answers, _role_bot, _seat, _team, legacy_pair
from backend.tests.helpers.sample_company import booted, environment, rows, signed_in  # noqa: F401
from backend.tests.helpers.stage_inputs import _link_pr, _push

ANA, BEN = X.ANA, "human:ben"
OWNER = "bot:pm"  # the Product Owner of _team, as the feature owner the tasks are given to
STAGES = [
    "Pitch",
    "Spec",
    "Related input",
    "OK spec",
    "Build",
    "Review 1",
    "Fixes 1",
    "Review 2",
    "Fixes 2",
    "QA",
    "OK release",
    "Merge",
    "Validate",
]
HELD_BY_THE_OWNER = {"Pitch", "Spec", "Related input", "OK spec", "Build", "Fixes 1", "Fixes 2"}
HELD_BY_THE_OWNER |= {"OK release", "Validate"}
PITCH = "Shoppers lose their cart when the tab closes. Keep it for 30 days."


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def _made(c):
    """What ensure_presets made, leaving out the Growth and Success pipelines (test_default_pipelines_more.py)."""
    return [p for p in PS.ensure_presets(c) if p not in more.PRESETS]


def _product(c):
    _team(c)
    _answers(c, software="yes")
    PS.ensure_presets(c)
    return PS.read(c, "product")


def _requirement(c, task_id, kind):
    row = E.task(c, task_id)
    return next(r for r in R.for_visit(c, row["visit"]) if r["kind"] == kind)


def _past_merge(c):
    """A task may be moved past Merge in these tests: its PR merged is never pushed past, so it is withdrawn."""
    c.execute(
        "UPDATE requirements SET withdrawn_at=? WHERE stage_id LIKE 'product-merge%' AND visit_id IS NULL", (H.now(),)
    )


def _task(c, body=PITCH, stage=None):
    row = H.task_create(c, ANA, "Saved carts", body, OWNER, pipeline="product", lint=False)
    return E.move(c, row["id"], stage, ANA, "On for this test") if stage else row


# ----------------------------------------------------------------------------- the default
def test_a_company_that_builds_software_gets_product_and_no_engineering(c):
    _team(c)
    _answers(c, software="")  # a blank answer is yes (D7)
    assert _made(c) == ["product"]
    assert _made(c) == []
    product = PS.read(c, "product")
    assert not c.execute("SELECT 1 FROM pipelines WHERE id='engineering'").fetchone()
    assert (product["name"], product["prefix"], product["preset"], product["factory_id"]) == (
        "Product",
        "P",
        PS.PRODUCT,
        "product",
    )
    assert (product["manager_id"], product["keep_warm_hours"]) == ("bot:" + PPM, 8)
    assert [s["name"] for s in product["stages"]] == STAGES
    owners = {s["name"]: s["owner"] for s in product["stages"]}
    assert {name for name, owner in owners.items() if owner == PS.ASSIGNEE} == HELD_BY_THE_OWNER
    assert (owners["Review 1"], owners["Review 2"], owners["Merge"]) == ("bot:rev-a", "bot:rev-b", "bot:rm")
    assert owners["QA"] == "bot:" + PPM  # no QA Owner in this team: up the ladder to the manager
    stages = {s["name"]: s for s in product["stages"]}
    assert stages["Related input"]["expected_minutes"] >= 240  # never Late before it moves on by itself
    assert stages["Merge"]["on_enter"] == {"act": "github.queue"}
    # The person gates: people only, nobody named, so any person clears them; QA's page names its person.
    gates = {name: r for name, s in stages.items() for r in s["requirements"] if r["who_oks"] == "people_only"}
    assert sorted(gates) == ["OK release", "OK spec", "Pitch", "QA"]
    assert {name: r["person_id"] for name, r in gates.items()} == {
        "Pitch": None,
        "OK spec": None,
        "OK release": None,
        "QA": ANA,
    }
    pitch = stages["Pitch"]["requirements"]
    assert [(r["kind"], r["settings"]) for r in pitch] == [("ok", {"body_max": 280})]
    assert stages["Pitch"]["id"] == "product-ideas", "Pitch keeps the id Tico Team's Product has"


def test_a_new_company_waits_for_setup_and_one_that_builds_no_software_gets_product_retired(c):
    _team(c)
    _answers(c, software="", completed=False)
    assert _made(c) == []
    _answers(c, software="yes")
    assert _made(c) == ["product"]
    _answers(c, software="no")
    PS.ensure_presets(c)
    assert PS.read(c, "product")["retired_at"]
    _answers(c, software="yes")
    PS.ensure_presets(c)
    assert not PS.read(c, "product")["retired_at"]


def test_a_role_bot_that_appears_later_takes_its_stage_from_the_manager(c):
    product = _product(c)
    assert {s["name"]: s["owner"] for s in product["stages"]}["QA"] == "bot:" + PPM
    _role_bot(c, "qa", "qa-owner")
    _seat(c, "QA Owner", "qa-owner", "qa")
    PS.ensure_presets(c)
    assert {s["name"]: s["owner"] for s in PS.read(c, "product")["stages"]}["QA"] == "bot:qa"


# ----------------------------------------------------------------------------- the pitch and the person gates
def test_a_pitch_over_280_characters_is_its_owners_to_shorten_before_a_person_is_asked(c):
    _product(c)
    task = _task(c, body="x" * 281)
    ok = _requirement(c, task["id"], "ok")
    (standing,) = R.unmet(c, E.task(c, task["id"]))
    assert (standing.on, standing.line) == (OWNER, "The pitch is 281 characters, over 280: shorten the task's body")
    with pytest.raises(H.Refused, match="over 280"):
        A.answer(c, task["id"], ok["id"], ANA, "ok")
    c.execute("UPDATE tasks SET body=? WHERE id=?", ("x" * 280, task["id"]))
    (standing,) = R.unmet(c, E.task(c, task["id"]))
    assert (standing.state, standing.on) == (R.WAITING, None), "now any person is asked"
    A.answer(c, task["id"], ok["id"], ANA, "ok")
    assert R.unmet(c, E.task(c, task["id"])) == []


def test_a_bots_pitch_is_not_set_aside_but_waits_for_a_persons_ok(c):
    """In the pair's Ideas a bot's own idea starts snoozed; Pitch asks a person's OK, so it waits in their list."""
    _product(c)
    row = H.task_create(c, OWNER, "Saved carts", PITCH, OWNER, pipeline="product", lint=False)
    task = E.task(c, row["id"])
    assert task["stage_id"] == PS.IDEAS_STAGE and not task.get("snooze")
    assert c.execute("SELECT 1 FROM idea_checks WHERE task_id=?", (row["id"],)).fetchone(), (
        "the duplicate check is owed"
    )


@pytest.mark.parametrize("stage", ["product-ideas", "product-ok-release"])
def test_any_person_clears_a_gate_and_no_bot_does(c, stage):
    _product(c)
    _past_merge(c)
    task = _task(c, stage=None if stage == PS.IDEAS_STAGE else stage)
    ok = _requirement(c, task["id"], "ok")
    standing = next(s for s in R.unmet(c, E.task(c, task["id"])) if s.requirement["kind"] == "ok")
    assert standing.state == R.WAITING and standing.on is None  # nobody named: it falls to the task's person
    for bot in (OWNER, "bot:" + PPM):
        with pytest.raises(H.Refused):
            A.answer(c, task["id"], ok["id"], bot, "ok", seen_version=E.task(c, task["id"])["version"])
    A.answer(c, task["id"], ok["id"], BEN, "ok")
    assert all(s.requirement["kind"] != "ok" for s in R.unmet(c, E.task(c, task["id"]))), "Ben, any person, cleared it"


def test_the_feature_owner_holds_build_and_both_fixes(c):
    _product(c)
    _past_merge(c)
    task = _task(c)
    for stage in ("product-build", "product-fixes-1", "product-fixes-2", "product-validate"):
        assert E.move(c, task["id"], stage, ANA, "On")["owner_id"] == OWNER


def test_the_owner_the_manager_picks_in_pitch_holds_build_the_fixes_and_validate_whoever_holds_a_stage_between(c):
    """Tico Team, Nov 14: a task with no assignee fell to the Engineering Manager in Build. The owner it has when it
    leaves Pitch is its feature owner: a stand-in the manager names later holds that stage only."""
    _product(c)
    _past_merge(c)
    _role_bot(c, "fo-2", "segment-product-owner")
    row = H.task_create(c, ANA, "Saved carts", PITCH, OWNER, pipeline="product", lint=False)
    c.execute("UPDATE task_move_guards SET open=1, task_id=? WHERE id=1", (row["id"],))
    c.execute("UPDATE tasks SET owner_id=NULL, assignee_id=NULL WHERE id=?", (row["id"],))  # filed with no owner
    c.execute("UPDATE task_move_guards SET open=0, task_id=NULL WHERE id=1")
    E.reassign(c, row["id"], OWNER, ANA, "Carts are yours")
    assert E.task(c, row["id"])["assignee_id"] == OWNER
    assert E.move(c, row["id"], "product-build", ANA, "On")["owner_id"] == OWNER
    E.reassign(c, row["id"], "bot:fo-2", ANA, "Stand in while pm is out")
    moved = E.move(c, row["id"], "product-fixes-1", ANA, "On")
    assert (moved["owner_id"], moved["assignee_id"]) == (OWNER, OWNER)
    assert E.move(c, row["id"], "product-validate", ANA, "On")["owner_id"] == OWNER


def test_an_old_merged_pr_does_not_meet_pr_open_in_build(c):
    """Tico Team, Nov 14: a task linked to an old, merged PR before its spec skipped Build. PR open counts an open PR,
    or one linked since the task entered Build."""
    from backend.item_sources import github as GS

    _product(c)
    X.github_installed(c)
    task = _task(c)
    _link_pr(c, task["id"], 7, "a" * 40)
    _push(c, 7, "a" * 40, state="merged", merged_sha="b" * 40)
    row = E.move(c, task["id"], "product-build", ANA, "On")
    standing = next(s for s in R.unmet(c, row) if s.requirement.get("condition") == "pr_open")
    assert (standing.on, standing.line) == (OWNER, GS.NO_NEW_PR)
    _link_pr(c, task["id"], 8, "c" * 40)
    _push(c, 8, "c" * 40)
    assert all(s.requirement.get("condition") != "pr_open" for s in R.unmet(c, E.task(c, task["id"])))


def test_not_validated_sends_the_task_back_to_build_in_the_same_task(c):
    _product(c)
    _past_merge(c)
    task = _task(c, stage=G.STAGE)
    assert G.back_to(c, task["id"]) == "product-build"
    moved = G.not_validated(c, task["id"], ANA, "The cart is gone after a day.")
    assert (moved["stage_id"], moved["owner_id"]) == ("product-build", OWNER)
    assert not c.execute("SELECT 1 FROM links WHERE from_task_id=?", (task["id"],)).fetchone(), "nothing is filed"


# ----------------------------------------------------------------------------- a company that has the pair
def _pair_company(c):
    _team(c)
    _answers(c, software="yes")
    legacy_pair(c)
    PS.ensure_presets(c)


def test_a_company_with_the_pair_keeps_it_and_gets_no_product_beside_it(c):
    _pair_company(c)
    assert PS.read(c, "product")["preset"] == "product"
    assert PS.read(c, "engineering")["preset"] == "engineering"
    assert PS._made_from(c, PS.PRODUCT) is None
    assert PS.ensure_presets(c) == [], "a start makes nothing beside the pair"


def test_a_person_adds_product_beside_the_pair_and_nothing_of_the_pair_changes(c):
    _pair_company(c)
    before = PS.read(c, "product"), PS.read(c, "engineering")
    with pytest.raises(H.Refused, match="Only the company's owner and Admins"):
        PS.add_preset(c, OWNER, PS.PRODUCT, "features", "Features", "F")
    with pytest.raises(H.Refused, match="A pipeline product is there already"):
        PS.add_preset(c, ANA, PS.PRODUCT)
    with pytest.raises(H.Refused, match="not a preset to add"):
        PS.add_preset(c, ANA, "engineering", "engineering-2")
    added = PS.add_preset(c, ANA, PS.PRODUCT, "features", "Features", "F")
    ids = [s["id"] for s in added["stages"]]
    # Ids the pair's Product has get a number; the rest are the preset's.
    assert ids[:4] == ["product-ideas-2", "product-spec-2", "product-related", "product-ok-spec"]
    assert ids[-1] == "product-validate-2" and G.is_stage(ids[-1])
    assert (added["preset"], added["name"], added["prefix"], added["factory_id"]) == (
        PS.PRODUCT,
        "Features",
        "F",
        "product",
    )
    assert (PS.read(c, "product"), PS.read(c, "engineering")) == before
    assert PS._made_from(c, PS.PRODUCT) == "features"
    with pytest.raises(H.Refused, match="The company has Product already: features"):
        PS.add_preset(c, ANA, PS.PRODUCT, "features-2", "Features 2", "FF")
    assert "features" not in PS.ensure_presets(c)
    # Validate there sends back to its own Build.
    _past_merge(c)
    row = H.task_create(c, ANA, "Saved carts", PITCH, OWNER, pipeline="features", lint=False)
    E.move(c, row["id"], "product-validate-2", ANA, "On")
    assert G.back_to(c, row["id"]) == "product-build"


def test_the_route_adds_product_beside_the_pair(environment):
    api = environment()
    with api.app.state.store.transaction() as c:
        legacy_pair(c)
    body = {"preset": PS.PRODUCT, "name": "Features", "prefix": "F"}
    assert api.post("/api/v2/pipelines/overview/from-preset", json=body, headers=signed_in()).status_code == 404
    r = api.post("/api/v2/pipelines/features/from-preset", json=body, headers=signed_in())
    assert r.status_code == 200, r.text
    assert r.json()["pipeline"]["name"] == "Features"
    assert [row["id"] for row in rows(api, "SELECT id FROM pipelines WHERE preset=?", (PS.PRODUCT,))] == ["features"]
    again = api.post("/api/v2/pipelines/features-2/from-preset", json=body, headers=signed_in())
    assert again.status_code != 200 and "already" in again.text
