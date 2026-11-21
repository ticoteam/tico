"""No Product stage is left without an owner (docs/dev/pipelines-v3.md "Default pipelines", the ladder): a role no bot fills
goes up to the pipeline's manager, and a manager no bot fills to the General Manager, until the role's bot appears
(pipeline_settings.preset, _fill). The stages a feature owner holds are the task's assignee's, whoever the company has.
A company whose file lists none of the roles stands for one set up without them."""

from backend import company_file as C
from backend import task_engine as E
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.fresh_install import (  # noqa: F401
    OWNER_EMAIL,
    booted,
    environment,
    machine,
    rows,
    signed_in,
)

TM = "general-manager"
ASSIGNEE = "assignee"
# The stages the task's feature owner holds, from Spec to Validate.
OWNER_STAGES = (
    "product-spec",
    "product-related",
    "product-ok-spec",
    "product-build",
    "product-fixes-1",
    "product-ok-release",
    "product-validate",
)
ROLE_STAGES = ("product-review-1", "product-qa", "product-merge")


def _no_roles(environment):
    """A new company whose company file lists only the Assistant and Agent Ops, then a computer."""
    api = environment()
    draft = C.blank()
    draft["company"]["signals"] = ["sells_services"]
    draft = C.Turn(draft).finish()
    draft["company"].update(name="Kiln & Co", about="We make pottery.")
    draft["bots"] = [b for b in draft["bots"] if b["template"] in ("assistant", "agent-ops")]
    body = {"company_yaml": C.to_yaml(draft)}
    preview = api.post("/api/v2/setup/company/preview", json=body, headers=signed_in())
    assert preview.status_code == 200, preview.text
    done = api.post(
        "/api/v2/setup/company/import",
        json={**body, "fingerprint": preview.json()["fingerprint"]},
        headers=signed_in(),
    )
    assert done.status_code == 200, done.text
    machine(api)
    return api


def _restart(environment):
    return environment(seed_roster=False)


def _staffing(api):
    """(manager, {work stage: owner}) for Product, as bot slugs or "assignee"; an actor that names no bot that is not
    archived reads as None."""
    bots = {"bot:" + r["id"]: r["slug"] for r in rows(api, "SELECT id,slug FROM bots WHERE state<>'archived'")}
    bots[ASSIGNEE] = ASSIGNEE
    manager = rows(api, "SELECT manager_id FROM pipelines WHERE id='product'")[0]["manager_id"]
    stages = rows(
        api,
        "SELECT id,owner FROM stages WHERE pipeline_id='product' AND id NOT LIKE '%-done' AND id NOT LIKE '%-closed'",
    )
    return bots.get(manager), {r["id"]: bots.get(r["owner"]) for r in stages}


def _expected(manager, **roles):
    """Product's staffing: the feature owner's stages the assignee's, each role's stage its bot's, else the manager's."""
    return manager, {**dict.fromkeys(OWNER_STAGES, ASSIGNEE), **dict.fromkeys(ROLE_STAGES, manager), **roles}


def _no_roles_expected():
    return _expected(TM)


def test_a_new_company_without_the_roles_has_no_ownerless_product_stage(environment):
    api = _no_roles(environment)
    assert _staffing(api) == _no_roles_expected()
    assert not rows(api, "SELECT 1 FROM pipelines WHERE id='engineering'"), "Product replaces the pair: no Engineering"
    # Nothing is added to the company's team.
    assert not rows(
        api,
        "SELECT id FROM bots WHERE slug IN ('product-manager','engineering-manager','reviewer-1')",
    )


def test_a_second_start_changes_nothing(environment):
    api = _no_roles(environment)
    before = _staffing(api), rows(api, "SELECT id,settings_version FROM pipelines ORDER BY id")
    again = _restart(environment)
    assert (_staffing(again), rows(again, "SELECT id,settings_version FROM pipelines ORDER BY id")) == before


def test_a_half_staffed_company_is_staffed_on_its_next_start(environment):
    """A company whose Product lost its manager and its role stages' owners: the next start (complete_company, every
    start: pipeline_settings.ensure_presets) fills them, and the assignee's stages stay the assignee's."""
    api = _no_roles(environment)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE pipelines SET manager_id=NULL WHERE id='product'")
        c.execute("UPDATE stages SET owner=NULL WHERE pipeline_id='product' AND owner='bot:general-manager'")
    half = _staffing(api)
    assert half[0] is None and {half[1][s] for s in ROLE_STAGES} == {None}
    assert _staffing(_restart(environment)) == _no_roles_expected()


def test_a_role_bot_that_appears_later_takes_its_stages_from_the_stand_in(environment):
    api = _no_roles(environment)
    onboarding = api.app.state.onboarding
    who = Identity("human:owner", "owner", OWNER_EMAIL)
    with api.app.state.store.transaction() as c:
        for slug, template in (("reviewer-1", "reviewer"), ("product-manager", "product-manager")):
            choice = {"template": template, "display_name": slug, "instructions": ""}
            onboarding._define(c, who, slug, choice, onboarding._template(template))
    # The QA and Merge stages go to the new manager until their roles are filled.
    assert _staffing(_restart(environment)) == _expected("product-manager", **{"product-review-1": "reviewer-1"})


def test_a_new_company_on_the_default_chart_is_staffed_while_its_release_manager_is_planned(environment):
    """The default chart's live seats start planned until Agent Ops sets them up; the Release Owner is found then too."""
    api = environment()
    done = api.post("/api/v2/setup/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    assert rows(api, "SELECT state FROM bots WHERE slug='release-owner'")[0]["state"] == "planned"
    assert [r["id"] for r in rows(api, "SELECT id FROM pipelines WHERE id IN ('product','engineering')")] == ["product"]
    assert _staffing(api) == _expected(
        "product-manager",
        **{
            "product-review-1": "reviewer-1",
            "product-qa": "qa-owner",
            "product-merge": "release-owner",
        },
    )


def test_a_product_tasks_owner_holds_build_and_fixes(environment):
    """The task's assignee, its feature owner, owns Build and Fixes in the same task."""
    api = _no_roles(environment)
    owner = "human:owner"
    with api.app.state.store.transaction() as c:
        feature_owner = "bot:" + rows(api, "SELECT id FROM bots WHERE state<>'archived' ORDER BY slug LIMIT 1")[0]["id"]
        row = H.task_create(
            c, owner, "Saved carts", "Let a shopper keep a cart.", feature_owner, pipeline="product", lint=False
        )
        assert row["owner_id"] == feature_owner
        for stage in ("product-build", "product-fixes-1"):
            assert E.move(c, row["id"], stage, owner, "On")["owner_id"] == feature_owner
