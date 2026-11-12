"""No Product or Engineering stage is left without an owner (docs/pipelines-v3.md "Default pipelines", the ladder): a
role no bot fills goes up to the pipeline's manager, and a manager no bot fills to the General Manager, until the role's
bot appears (pipeline_settings.preset, _fill). The sample company, which has none of the six roles but a Builder, is the
company the first live one was, built on the same path."""

from backend import task_engine as E
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.sample_company import (  # noqa: F401
    OWNER_EMAIL,
    booted,
    environment,
    machine,
    rows,
    signed_in,
)

PRESETS = ("product", "engineering")
TM = "general-manager"
BUILDER_STAGES = (
    "engineering-on-deck",
    "engineering-build",
    "engineering-fixes-1",
    "engineering-fixes-2",
    "engineering-qa",
)


def _sample_company(environment):
    """A hosted install's first starts with no company file: TICO_SAMPLE_COMPANY=on (setup/settings.py), then a
    computer."""
    environment()
    api = environment(seed_roster=False, sample_company=True)
    machine(api)
    return api


def _restart(environment):
    return environment(seed_roster=False, sample_company=True)


def _staffing(api):
    """{pipeline: (manager, {work stage: owner})} for Product and Engineering, as bot slugs; an actor that names no bot
    that is not archived reads as None."""
    bots = {"bot:" + r["id"]: r["slug"] for r in rows(api, "SELECT id,slug FROM bots WHERE state<>'archived'")}
    out = {}
    for pipeline in PRESETS:
        manager = rows(api, "SELECT manager_id FROM pipelines WHERE id=?", (pipeline,))[0]["manager_id"]
        stages = rows(
            api,
            "SELECT id,owner FROM stages WHERE pipeline_id=? AND id NOT LIKE '%-done' AND id NOT LIKE '%-closed'",
            (pipeline,),
        )
        out[pipeline] = (bots.get(manager), {r["id"]: bots.get(r["owner"]) for r in stages})
    return out


def _sample_expected():
    engineering = {
        "engineering-review-1": TM,
        "engineering-review-2": TM,
        "engineering-merge": TM,
        "engineering-release": TM,
        **dict.fromkeys(BUILDER_STAGES, "builder"),
    }
    product = dict.fromkeys(("product-ideas", "product-spec", "product-in-development", "product-validate"), TM)
    return {"product": (TM, product), "engineering": (TM, engineering)}


def test_a_new_sample_company_has_no_ownerless_product_or_engineering_stage(environment):
    api = _sample_company(environment)
    assert _staffing(api) == _sample_expected()
    # Nothing is added to the sample's team.
    assert not rows(
        api,
        "SELECT id FROM bots WHERE slug IN ('product-manager','engineering-manager','reviewer-1')",
    )


def test_a_second_start_changes_nothing(environment):
    api = _sample_company(environment)
    before = _staffing(api), rows(api, "SELECT id,settings_version FROM pipelines ORDER BY id")
    again = _restart(environment)
    assert (_staffing(again), rows(again, "SELECT id,settings_version FROM pipelines ORDER BY id")) == before


def test_a_half_staffed_company_is_staffed_on_its_next_start(environment):
    """The first live company, as the release before this left it: no manager, and empty Review, Merge, Release and
    Product stages. The next start (complete_company, every start: pipeline_settings.ensure_presets) fills them."""
    api = _sample_company(environment)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE pipelines SET manager_id=NULL WHERE id IN ('product','engineering')")
        c.execute(
            "UPDATE stages SET owner=NULL WHERE pipeline_id IN ('product','engineering') AND owner='bot:general-manager'"
        )
    half = _staffing(api)
    assert half["product"][0] is None and set(half["product"][1].values()) == {None}
    assert half["engineering"][1]["engineering-review-1"] is None
    assert _staffing(_restart(environment)) == _sample_expected()


def test_a_role_bot_that_appears_later_takes_its_stages_from_the_stand_in(environment):
    api = _sample_company(environment)
    onboarding = api.app.state.onboarding
    who = Identity("human:owner", "owner", OWNER_EMAIL)
    with api.app.state.store.transaction() as c:
        for slug, template in (("reviewer-1", "reviewer"), ("engineering-manager", "engineering-manager")):
            choice = {"template": template, "display_name": slug, "instructions": ""}
            onboarding._define(c, who, slug, choice, onboarding._template(template))
    staffed = _staffing(_restart(environment))["engineering"]
    assert staffed[0] == "engineering-manager"
    assert staffed[1]["engineering-review-1"] == "reviewer-1"
    # Still no second reviewer nor Release Owner: their stages go to the new manager.
    assert staffed[1]["engineering-review-2"] == staffed[1]["engineering-merge"] == "engineering-manager"
    assert all(staffed[1][stage] == "builder" for stage in BUILDER_STAGES)


def test_a_new_company_on_the_default_chart_is_staffed_while_its_release_manager_is_planned(environment):
    """The default chart's live seats start planned until BotOps sets them up; the Release Owner is found then too."""
    api = environment()
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    assert rows(api, "SELECT state FROM bots WHERE slug='release-owner'")[0]["state"] == "planned"
    staffing = _staffing(api)
    assert staffing["product"] == (
        "product-manager",
        dict.fromkeys(("product-ideas", "product-spec", "product-in-development", "product-validate"), "product-owner"),
    )
    assert staffing["engineering"] == (
        "engineering-manager",
        {
            **dict.fromkeys(BUILDER_STAGES, "builder"),
            "engineering-review-1": "reviewer-1",
            "engineering-review-2": "reviewer-2",
            "engineering-merge": "release-owner",
            "engineering-release": "release-owner",
        },
    )


def test_a_product_task_handed_to_engineering_lands_on_an_owned_stage(environment):
    """Never stuck at the hand-off: In development files an Engineering task whose first stage has a bot owner."""
    api = _sample_company(environment)
    owner = "human:owner"
    with api.app.state.store.transaction() as c:
        row = H.task_create(
            c, owner, "Saved carts", "Let a shopper keep a cart.", owner, pipeline="product", lint=False
        )
        row = E.move(c, row["id"], "product-spec", owner)
        rid = c.execute("SELECT id FROM requirements WHERE stage_id='product-spec' AND kind='page'").fetchone()[0]
        c.execute(
            "INSERT INTO requirement_pages (id, visit_id, requirement_id, file_id, version, item_versions, attached_by_id, "
            "created_at) VALUES (?,?,?,?,?,?,?,?)",
            (H.new_id(), row["visit"]["id"], rid, "f-spec", 1, "{}", owner, H.now()),
        )
        E.move(c, row["id"], "product-in-development", owner, "The spec is OK'd")
        filed = c.execute(
            "SELECT to_task_id FROM links WHERE from_task_id=? AND kind='handoff'", (row["id"],)
        ).fetchall()
        assert len(filed) == 1
        eng = E.task(c, filed[0][0])
    builder = "bot:" + rows(api, "SELECT id FROM bots WHERE slug='builder'")[0]["id"]
    assert (eng["pipeline_id"], eng["stage_id"], eng["owner_id"]) == ("engineering", "engineering-on-deck", builder)
