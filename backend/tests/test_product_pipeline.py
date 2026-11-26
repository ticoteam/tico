"""The default pipelines (docs/dev/pipelines-v3.md "Default pipelines"): Product decides, Engineering builds. What a new
company that builds software gets (pipeline_settings.PAIR: Ideas, Spec, Approval, In development and Validate; On deck,
Build, Review, Fixes, QA and Release), its one person sign-off, the handoff and the merge queue on Release; that a
company keeps what it has on an upgrade (Features, pipeline_settings.LEGACY, every new company's from 0.6; the pair as
it was before; Tico Team's own); and adding the pair by hand beside Features (`add_preset`, POST
/api/v2/pipelines/{id}/from-preset). Features' own stages, held by its feature owner, are tested last.

Synthetic rows only (backend/tests/helpers/engine.py): the fictional company, where Ana owns it, and its role bots.
"""

import copy
import json
from pathlib import Path

import pytest

from backend import answers as A
from backend import pipeline_presets_more as more
from backend import pipeline_settings as PS
from backend import stage_rules as SR
from backend import task_engine as E
from backend import task_requirements as R
from backend import validate_gate as G
from backend.item_sources import github as F
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.fake_github import FakeGitHub
from backend.tests.helpers.product_engineering_presets import (
    EPM,
    PPM,
    _answers,
    _role_bot,
    _seat,
    _team,
    features,
    legacy_pair,
    legacy_review_stages,
)
from backend.tests.helpers.fresh_install import booted, environment, rows, signed_in  # noqa: F401
from backend.tests.helpers.stage_inputs import _link_pr, _push

ANA, BEN = X.ANA, "human:ben"
OWNER = "bot:pm"  # the Product Owner of _team, as the feature owner the tasks are given to
FEATURES_STAGES = [
    "Spec",
    "Related input",
    "OK spec",
    "Build",
    "Review",
    "Fixes",
    "QA",
    "OK release",
    "Merge",
    "Validate",
]
HELD_BY_THE_OWNER = {"Spec", "Related input", "OK spec", "Build", "Fixes", "OK release", "Validate"}
PRODUCT_STAGES = ["Ideas", "Spec", "Approval", "In development", "Validate"]
ENGINEERING_STAGES = ["On deck", "Build", "Review", "Fixes", "QA", "Release"]
PITCH = "Shoppers lose their cart when the tab closes. Keep it for 30 days."


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def _made(c):
    """What ensure_presets made, leaving out the Growth and Success pipelines (test_default_pipelines_more.py)."""
    return [p for p in PS.ensure_presets(c) if p not in more.PRESETS]


def _features(c):
    """A company set up from 0.6 until Nov 26: Features under the id `product`, then a start."""
    _team(c)
    _answers(c, software="yes")
    features(c)
    PS.ensure_presets(c)
    return PS.read(c, "product")


def _new_company(c):
    _team(c)
    _answers(c, software="yes")
    PS.ensure_presets(c)
    return PS.read(c, "product"), PS.read(c, "engineering")


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


# ----------------------------------------------------------------------------- the default: Product and Engineering
def test_a_new_company_that_builds_software_gets_product_and_engineering_and_no_features(c):
    _team(c)
    _answers(c, software="")  # a blank answer is yes (D7)
    assert _made(c) == ["engineering", "product"], "Engineering first: Product files into it"
    assert _made(c) == []
    product, engineering = PS.read(c, "product"), PS.read(c, "engineering")
    assert PS._made_from(c, PS.FEATURES) is None
    assert [s["name"] for s in product["stages"]] == PRODUCT_STAGES
    assert [s["name"] for s in engineering["stages"]] == ENGINEERING_STAGES
    assert [(p["preset"], p["prefix"], p["factory_id"]) for p in (product, engineering)] == [
        ("product", "P", "product"),
        ("engineering", "E", "product"),
    ]
    assert (product["manager_id"], engineering["manager_id"]) == ("bot:" + PPM, "bot:" + EPM)
    owners = {s["name"]: s["owner"] for s in product["stages"]}
    assert set(owners.values()) == {"bot:pm"}, "the Product Owner holds every Product stage"
    owners = {s["name"]: s["owner"] for s in engineering["stages"]}
    assert owners == {
        "On deck": "bot:builder",
        "Build": "bot:builder",
        "Review": "bot:rev-a",
        "Fixes": "bot:builder",
        "QA": "bot:builder",
        "Release": "bot:rm",
    }
    stages = {s["name"]: s for s in product["stages"]}
    assert stages["Ideas"]["by_hand"], "an idea goes to Spec when a person or its owner moves it"
    assert stages["In development"]["on_enter"] == {"file_into": "engineering"}
    assert not c.execute("SELECT 1 FROM stages WHERE id IN ('engineering-merge', 'product-related')").fetchone()


def test_approval_is_the_only_stage_a_person_signs_and_qa_and_release_are_nobody_s(c):
    product, engineering = _new_company(c)
    gates = [
        (s["name"], r["kind"], R.document_of(r), r["person_id"])
        for record in (product, engineering)
        for s in record["stages"]
        for r in s["requirements"]
        if r["who_oks"] == "people_only" or r.get("person_id")
    ]
    assert gates == [("Approval", "page", "Spec", ANA)]
    stages = {s["name"]: s for s in (*product["stages"], *engineering["stages"])}
    assert [R.document_of(r) for r in stages["Spec"]["requirements"]] == ["Spec"], "the decision model checks it first"
    qa = {PS.requirement_key(r): r for r in stages["QA"]["requirements"]}
    assert (qa["page"]["who_oks"], R.document_of(qa["page"]), qa["page"]["person_id"]) == ("none", "QA report", None)
    assert "item:github.checks_pass" in qa
    release = stages["Release"]
    assert release["on_enter"] == {"act": "github.queue"}
    assert [r["condition"] for r in release["requirements"]] == ["pr_merged", "in_release"]
    assert all(r["who_oks"] == "none" for r in release["requirements"])


def test_with_no_builder_or_reviewers_engineering_falls_to_its_manager_until_they_come(c):
    _role_bot(c, PPM, PPM)
    _role_bot(c, EPM, EPM)
    _answers(c, software="yes")
    PS.ensure_presets(c)
    engineering = PS.read(c, "engineering")
    assert {s["owner"] for s in engineering["stages"]} == {"bot:" + EPM}
    _role_bot(c, "builder", "builder")
    PS.ensure_presets(c)
    owners = {s["name"]: s["owner"] for s in PS.read(c, "engineering")["stages"]}
    assert (owners["Build"], owners["Review"]) == ("bot:builder", "bot:" + EPM)


def test_a_new_company_waits_for_setup_and_one_that_builds_no_software_gets_the_pair_retired(c):
    _team(c)
    _answers(c, software="", completed=False)
    assert _made(c) == []
    _answers(c, software="yes")
    assert _made(c) == ["engineering", "product"]
    _answers(c, software="no")
    PS.ensure_presets(c)
    assert PS.read(c, "product")["retired_at"] and PS.read(c, "engineering")["retired_at"]
    _answers(c, software="yes")
    PS.ensure_presets(c)
    assert not PS.read(c, "product")["retired_at"] and not PS.read(c, "engineering")["retired_at"]


def test_owner_on_the_team_is_on_engineerings_build_and_fixes_of_a_new_company(c):
    _new_company(c)
    rule = SR.get(c, SR.OWNER_ON_TEAM)
    assert (rule["pipeline_id"], rule["status"]) == ("engineering", "active")
    assert rule["stages"] == ["engineering-on-deck", "engineering-build", "engineering-fixes-1"]


def _approved(c):
    """A Product task a person approved in Approval: its Spec page counted there, and it moves on."""
    _new_company(c)
    row = H.task_create(c, ANA, "Saved carts", PITCH, OWNER, pipeline="product", lint=False)
    return E.move(c, row["id"], PS.SPEC_OK_STAGE, ANA, "On for this test")


def _filed(c, task_id):
    return [r[0] for r in c.execute("SELECT to_task_id FROM links WHERE from_task_id=? AND kind='handoff'", (task_id,))]


def test_in_development_files_one_engineering_task_and_product_moves_on_when_it_is_done(c):
    row = _approved(c)
    moved = E.move(c, row["id"], "product-in-development", ANA, "The spec is approved")
    assert moved["stage_id"] == "product-in-development"
    filed = _filed(c, row["id"])
    assert len(filed) == 1
    eng = E.task(c, filed[0])
    assert (eng["pipeline_id"], eng["stage_id"]) == ("engineering", "engineering-on-deck")
    assert E.task(c, row["id"])["stage_id"] == "product-in-development", "it waits while Engineering builds"
    E.move(c, eng["id"], "engineering-done", ANA, "Shipped")
    assert E.task(c, row["id"])["stage_id"] == "product-validate"
    assert _filed(c, row["id"]) == filed, "nothing more is filed: no duplicate Engineering task"


def test_entering_release_queues_the_pr_and_the_task_leaves_once_merged_and_in_a_release(company):
    gh, repo = FakeGitHub(), "acme/app"
    with company.transaction() as c:
        _new_company(c)
        X.github_installed(c)
        row = H.task_create(c, ANA, "Saved carts", PITCH, "bot:builder", pipeline="engineering", lint=False)
        H.task_link(c, ANA, row["id"], f"https://github.com/{repo}/pull/1")
        gh.set_checks(repo, gh.open_pr(repo, 1), "success")
        F.save_and_settle(c, repo, 1, F.read_pr(gh, repo, 1), F.version_now())
        task = E.move(c, row["id"], "engineering-release", ANA, "On for this test")
        assert task["stage_id"] == "engineering-release"
        effects = c.execute(
            "SELECT e.kind, e.state FROM effects e JOIN visits v ON v.id = e.visit_id WHERE v.task_id=?", (row["id"],)
        ).fetchall()
        assert [tuple(e) for e in effects] == [("act", "owed")], "entering Release owes the PR's queue"
    assert F.run(company, gh) == 1
    assert [call[0] for call in gh.calls].count("enqueue") == 1
    gh.merge(repo, 1)
    with company.transaction() as c:
        F.save_and_settle(c, repo, 1, F.read_pr(gh, repo, 1), F.version_now())
        assert E.task(c, row["id"])["stage_id"] == "engineering-release", "merged, but in no release yet"
    gh.publish(repo, "v1.0.0")
    assert F.refresh(company, gh, H.shift(H.now(), minutes=10)) >= 1
    with company.transaction() as c:
        assert E.task(c, row["id"])["stage"]["name"] == "Done"


# ----------------------------------------------------------------------------- what a company has stays
def test_a_company_with_features_keeps_it_and_gets_no_engineering_or_second_product(c):
    product = _features(c)
    held = H.task_create(c, ANA, "Saved carts", PITCH, OWNER, pipeline="product", lint=False)
    held = E.move(c, held["id"], "product-build", ANA, "On for this test")
    before = PS.read(c, "product")
    assert (product["preset"], [s["name"] for s in product["stages"]]) == (PS.FEATURES, FEATURES_STAGES)
    assert _made(c) == []
    assert PS.read(c, "product") == before
    assert PS._made_from(c, "engineering") is None and PS._made_from(c, "product") is None
    assert not c.execute("SELECT 1 FROM pipelines WHERE id='engineering'").fetchone()
    row = E.task(c, held["id"])
    assert (row["status"], row["stage_id"], row["visit"]["id"]) == ("open", "product-build", held["visit"]["id"])


def test_a_company_with_features_retired_for_no_software_gets_it_back_and_no_pair(c):
    _features(c)
    _answers(c, software="no")
    PS.ensure_presets(c)
    assert PS.read(c, "product")["retired_at"]
    _answers(c, software="yes")
    assert _made(c) == []
    assert PS.read(c, "product")["preset"] == PS.FEATURES and not PS.read(c, "product")["retired_at"]


def test_a_company_with_the_old_pair_keeps_its_stages_and_requirements(c):
    _team(c)
    _answers(c, software="yes")
    legacy_pair(c)
    before = PS.read(c, "product"), PS.read(c, "engineering")
    assert "Merge" in [s["name"] for s in before[1]["stages"]] and "Approval" not in [
        s["name"] for s in before[0]["stages"]
    ]
    assert _made(c) == []
    assert (PS.read(c, "product"), PS.read(c, "engineering")) == before


def test_tico_teams_own_product_and_engineering_change_in_nothing_at_a_start(c):
    """Tico Team's Product (v21) and Engineering (v22) as they ran on Nov 26, its people and bots made synthetic: the
    model this preset follows, never its target."""
    _team(c)
    X.bot(c, "builder-2")
    _answers(c, software="yes")
    PS._ensure_factory(c)
    shipped = json.loads((Path(__file__).parent / "fixtures" / "tico_team_pipelines.json").read_text())
    for pid in ("engineering", "product"):
        record = copy.deepcopy(shipped[pid])
        PS.save(c, PS.SYSTEM, record)
        c.execute("UPDATE pipelines SET settings_version=? WHERE id=?", (record["settings_version"], pid))
    before = PS.read(c, "product"), PS.read(c, "engineering")
    assert (before[0]["settings_version"], before[1]["settings_version"]) == (21, 22)
    assert _made(c) == []
    assert (PS.read(c, "product"), PS.read(c, "engineering")) == before


# ----------------------------------------------------------------------------- adding the pair beside Features
def test_a_person_adds_engineering_then_product_beside_features_and_features_cannot_be_added(c):
    _features(c)
    before = PS.read(c, "product")
    with pytest.raises(H.Refused, match="Only the company's owner and Admins"):
        PS.add_preset(c, OWNER, "engineering")
    with pytest.raises(H.Refused, match="not a preset to add"):
        PS.add_preset(c, ANA, PS.FEATURES, "features", "Features", "F")
    engineering = PS.add_preset(c, ANA, "engineering")
    with pytest.raises(H.Refused, match="A pipeline product is there already"):
        PS.add_preset(c, ANA, "product")
    product = PS.add_preset(c, ANA, "product", "decide", "Decide", "D")
    # As a new company's would be, but for the stage ids Features holds, which get a number.
    assert [s["name"] for s in engineering["stages"]] == ENGINEERING_STAGES
    assert [s["name"] for s in product["stages"]] == PRODUCT_STAGES
    ids = [s["id"] for s in product["stages"]]
    assert ids == ["product-ideas", "product-spec-2", "product-spec-ok", "product-in-development", "product-validate-2"]
    assert (product["preset"], product["factory_id"]) == ("product", "product")
    assert product["stages"][3]["on_enter"] == {"file_into": "engineering"}
    assert PS.read(c, "product") == before, "Features is as it was"
    assert PS._made_from(c, "product") == "decide"
    with pytest.raises(H.Refused, match="The company has Engineering already"):
        PS.add_preset(c, ANA, "engineering", "engineering-2", "Engineering 2", "EE")
    assert _made(c) == []
    # Validate there sends Not validated back to In development, which files new Engineering work.
    row = H.task_create(c, ANA, "Saved carts", PITCH, OWNER, pipeline="decide", lint=False)
    E.move(c, row["id"], "product-validate-2", ANA, "On")
    assert G.back_to(c, row["id"]) == "product-in-development"


def test_product_added_beside_features_files_into_engineering_whatever_its_id(c):
    _features(c)
    PS.add_preset(c, ANA, "engineering", "build", "Build", "B")
    product = PS.add_preset(c, ANA, "product", "decide", "Decide", "D")
    assert {s["name"]: s["on_enter"] for s in product["stages"]}["In development"] == {"file_into": "build"}


def test_owner_on_the_team_moves_from_features_to_engineering_once_features_is_retired(c):
    _features(c)
    assert SR.get(c, SR.OWNER_ON_TEAM)["pipeline_id"] == "product"
    PS.add_preset(c, ANA, "engineering")
    PS.ensure_presets(c)
    assert SR.get(c, SR.OWNER_ON_TEAM)["pipeline_id"] == "product", "Features is still in use"
    PS.retire(c, ANA, "product")
    PS.ensure_presets(c)
    rule = SR.get(c, SR.OWNER_ON_TEAM)
    assert (rule["pipeline_id"], rule["stages"]) == (
        "engineering",
        ["engineering-on-deck", "engineering-build", "engineering-fixes-1"],
    )


def test_the_route_adds_engineering_beside_features(environment):
    api = environment()
    with api.app.state.store.transaction() as c:
        features(c)
    body = {"preset": "engineering"}
    assert api.post("/api/v2/pipelines/overview/from-preset", json=body, headers=signed_in()).status_code == 404
    r = api.post("/api/v2/pipelines/engineering/from-preset", json=body, headers=signed_in())
    assert r.status_code == 200, r.text
    assert r.json()["pipeline"]["name"] == "Engineering"
    assert [row["id"] for row in rows(api, "SELECT id FROM pipelines WHERE preset='engineering'")] == ["engineering"]
    again = api.post("/api/v2/pipelines/engineering-2/from-preset", json=body, headers=signed_in())
    assert again.status_code != 200 and "already" in again.text
    refused = api.post("/api/v2/pipelines/features/from-preset", json={"preset": PS.FEATURES}, headers=signed_in())
    assert refused.status_code != 200 and "not a preset to add" in refused.text


# ----------------------------------------------------------------------------- Features, kept for the companies that have it
@pytest.mark.parametrize(("preset", "prefix"), [("engineering", "engineering"), (PS.FEATURES, "product")])
def test_review_and_fixes_have_the_same_gates_in_both_presets(preset, prefix):
    stage_prefixes = (f"{prefix}-review-", f"{prefix}-fixes-")
    review, fixes = [s for s in PS.PRESETS[preset]["stages"] if s["id"].startswith(stage_prefixes)]
    assert (review["id"], review["name"]) == (f"{prefix}-review-1", "Review")
    assert review["requirements"] == [
        {"kind": "page", "line": "Review page", "settings": {"at_head": True, "rubric": PS.REVIEW_RUBRIC}},
        {"kind": "linked_done"},
    ]
    assert (fixes["id"], fixes["name"]) == (f"{prefix}-fixes-1", "Fixes")
    assert fixes["requirements"] == [{"kind": "owner_done"}, PS.github("checks_pass")]


def test_features_has_its_stages_and_its_feature_owner_holds_them(c):
    product = _features(c)
    assert [s["name"] for s in product["stages"]] == FEATURES_STAGES
    owners = {s["name"]: s["owner"] for s in product["stages"]}
    assert {name for name, owner in owners.items() if owner == PS.ASSIGNEE} == HELD_BY_THE_OWNER
    assert (owners["Review"], owners["Merge"], owners["QA"]) == ("bot:rev-a", "bot:rm", "bot:" + PPM)


@pytest.mark.parametrize("preset", ["engineering", PS.FEATURES])
@pytest.mark.parametrize("renamed_review", [False, True])
def test_start_keeps_tasks_and_visits_in_the_separate_review_and_fixes_stages(c, preset, renamed_review):
    _team(c)
    _answers(c, software="yes")
    PS._ensure_factory(c)
    record = legacy_review_stages(c, PS.preset(c, preset))
    if renamed_review:
        review = next(s for s in record["stages"] if s["name"] == "Review 1")
        review["name"] = "Review"
        review["requirements"].append({"kind": "linked_done"})
    old = PS.save(c, PS.SYSTEM, record)
    pid = old["id"]
    held = []
    for stage in (f"{pid}-review-2", f"{pid}-fixes-2"):
        row = H.task_create(c, ANA, f"Task in {stage}", PITCH, OWNER, pipeline=pid, lint=False)
        row = E.move(c, row["id"], stage, ANA, "On for this test")
        held.append((row["id"], stage, row["visit"]["id"]))
    assert old["settings_version"] == 1
    PS.ensure_presets(c)
    assert PS.read(c, pid) == old
    for task_id, stage, visit in held:
        row = E.task(c, task_id)
        assert (row["status"], row["stage_id"], row["visit"]["id"]) == ("open", stage, visit)


def test_a_role_bot_that_appears_later_takes_its_stage_from_the_manager(c):
    product = _features(c)
    assert {s["name"]: s["owner"] for s in product["stages"]}["QA"] == "bot:" + PPM
    _role_bot(c, "qa", "qa-owner")
    _seat(c, "QA Owner", "qa-owner", "qa")
    PS.ensure_presets(c)
    assert {s["name"]: s["owner"] for s in PS.read(c, "product")["stages"]}["QA"] == "bot:qa"


@pytest.mark.parametrize(
    ("preset", "manager", "staffing"),
    [
        (PS.FEATURES, PPM, {"product-review-2": "bot:rev-b"}),
        ("engineering", EPM, {"engineering-review-2": "bot:rev-b", "engineering-fixes-2": "bot:builder"}),
    ],
)
def test_legacy_stages_get_their_role_bots_when_they_appear(c, preset, manager, staffing):
    _role_bot(c, manager, manager)
    _role_bot(c, "rev-a", "reviewer")
    _seat(c, "Reviewer 1", "reviewer", "rev-a")
    _answers(c, software="yes")
    PS._ensure_factory(c)
    old = PS.save(c, PS.SYSTEM, legacy_review_stages(c, PS.preset(c, preset)))
    assert {s["id"]: s["owner"] for s in old["stages"] if s["id"] in staffing} == dict.fromkeys(
        staffing, f"bot:{manager}"
    )

    _role_bot(c, "rev-b", "reviewer")
    _seat(c, "Reviewer 2", "reviewer", "rev-b")
    if preset == "engineering":
        _role_bot(c, "builder", "builder")
    PS.ensure_presets(c)
    current = PS.read(c, old["id"])
    assert {s["id"]: s["owner"] for s in current["stages"] if s["id"] in staffing} == staffing
    assert old["settings_version"] == current["settings_version"] == 1


# ----------------------------------------------------------------------------- the approvals
def test_a_bots_feature_starts_in_spec_and_is_not_set_aside(c):
    """In the pair's Ideas a bot's own idea starts snoozed; Product has no pitch, so its feature starts in Spec."""
    _features(c)
    row = H.task_create(c, OWNER, "Saved carts", PITCH, OWNER, pipeline="product", lint=False)
    task = E.task(c, row["id"])
    assert task["stage_id"] == "product-spec" and not task.get("snooze")


@pytest.mark.parametrize("stage", ["product-ok-spec", "product-ok-release"])
def test_no_bot_adds_an_ok_of_its_own_where_a_person_approves_a_document(c, stage):
    """Who approves the written version is test_stage_approvals.py; here, the preset's approvals take no bot's OK."""
    _features(c)
    _past_merge(c)
    task = _task(c, stage=stage)
    standing = next(s for s in R.unmet(c, E.task(c, task["id"])) if s.requirement["kind"] == "page")
    assert (standing.state, standing.on) == (R.NOT_MET, OWNER), "the owner writes the document first"
    for bot in (OWNER, "bot:" + PPM):
        with pytest.raises(H.Refused, match="approve the"):
            E.add_requirement(c, task["id"], bot, "ok", ANA, "OK it", seen_version=E.task(c, task["id"])["version"])


def test_the_feature_owner_holds_build_fixes_and_validate(c):
    _features(c)
    _past_merge(c)
    task = _task(c)
    for stage in ("product-build", "product-fixes-1", "product-validate"):
        assert E.move(c, task["id"], stage, ANA, "On")["owner_id"] == OWNER


def test_the_owner_the_manager_picks_in_pitch_holds_build_the_fixes_and_validate_whoever_holds_a_stage_between(c):
    """Tico Team, Nov 14: a task with no assignee fell to the Engineering Manager in Build. The owner it has when it
    leaves Spec is its feature owner: a stand-in the manager names later holds that stage only."""
    _features(c)
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

    _features(c)
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
    _features(c)
    _past_merge(c)
    task = _task(c, stage=G.STAGE)
    assert G.back_to(c, task["id"]) == "product-build"
    moved = G.not_validated(c, task["id"], ANA, "The cart is gone after a day.")
    assert (moved["stage_id"], moved["owner_id"]) == ("product-build", OWNER)
    assert not c.execute("SELECT 1 FROM links WHERE from_task_id=?", (task["id"],)).fetchone(), "nothing is filed"


def test_a_company_that_started_without_owner_on_the_team_gets_it_at_its_next_start(c):
    _new_company(c)
    c.execute("DELETE FROM stage_rules WHERE id=?", (SR.OWNER_ON_TEAM,))
    PS.ensure_presets(c)
    assert SR.get(c, SR.OWNER_ON_TEAM)["pipeline_id"] == "engineering"
