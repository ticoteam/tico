"""No stage of Product or Engineering is left without an owner (docs/dev/pipelines-v3.md "Default pipelines", the ladder):
a role no bot fills goes up to the pipeline's manager, and a manager no bot fills to the General Manager, until the
role's bot appears (pipeline_settings.preset, _fill). A company whose file lists none of the roles stands for one set up
without them."""

from backend import company_file as C
from backend.auth import Identity
from backend.tests.helpers.fresh_install import (  # noqa: F401
    OWNER_EMAIL,
    booted,
    environment,
    machine,
    rows,
    signed_in,
)

TM = "general-manager"
PRODUCT = ("product-ideas", "product-spec", "product-spec-ok", "product-in-development", "product-validate")
ENGINEERING = (
    "engineering-on-deck",
    "engineering-build",
    "engineering-review-1",
    "engineering-fixes-1",
    "engineering-qa",
    "engineering-release",
)


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
    """{pipeline: (manager, {work stage: owner})} for Product and Engineering, as bot slugs; an actor that names no bot
    that is not archived reads as None."""
    bots = {"bot:" + r["id"]: r["slug"] for r in rows(api, "SELECT id,slug FROM bots WHERE state<>'archived'")}
    out = {}
    for pid in ("product", "engineering"):
        manager = rows(api, "SELECT manager_id FROM pipelines WHERE id=?", (pid,))[0]["manager_id"]
        stages = rows(
            api,
            "SELECT id,owner FROM stages WHERE pipeline_id=? AND id NOT LIKE '%-done' AND id NOT LIKE '%-closed'",
            (pid,),
        )
        out[pid] = (bots.get(manager), {r["id"]: bots.get(r["owner"]) for r in stages})
    return out


def _expected(product=TM, engineering=TM, **roles):
    """Each pipeline's staffing: each role's stage its bot's, else the pipeline's manager's."""
    return {
        "product": (product, {**dict.fromkeys(PRODUCT, product), **{k: v for k, v in roles.items() if k in PRODUCT}}),
        "engineering": (
            engineering,
            {**dict.fromkeys(ENGINEERING, engineering), **{k: v for k, v in roles.items() if k in ENGINEERING}},
        ),
    }


def _no_roles_expected():
    return _expected()


def test_a_new_company_without_the_roles_has_no_ownerless_stage(environment):
    api = _no_roles(environment)
    assert _staffing(api) == _no_roles_expected()
    assert not rows(api, "SELECT 1 FROM pipelines WHERE preset='features'"), "the pair, and no Features"
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
        c.execute("UPDATE pipelines SET manager_id=NULL WHERE id IN ('product', 'engineering')")
        c.execute("UPDATE stages SET owner=NULL WHERE owner='bot:general-manager'")
    half = _staffing(api)
    assert half["engineering"][0] is None and set(half["engineering"][1].values()) == {None}
    assert _staffing(_restart(environment)) == _no_roles_expected()


def test_a_role_bot_that_appears_later_takes_its_stages_from_the_stand_in(environment):
    api = _no_roles(environment)
    onboarding = api.app.state.onboarding
    who = Identity("human:owner", "owner", OWNER_EMAIL)
    with api.app.state.store.transaction() as c:
        for slug, template in (("reviewer-1", "reviewer"), ("product-manager", "product-manager")):
            choice = {"template": template, "display_name": slug, "instructions": ""}
            onboarding._define(c, who, slug, choice, onboarding._template(template))
    # Product's stages go to the new manager until the Product Owner comes; Review takes its reviewer.
    assert _staffing(_restart(environment)) == _expected("product-manager", **{"engineering-review-1": "reviewer-1"})


def test_a_new_company_on_the_default_chart_is_staffed_while_its_release_manager_is_planned(environment):
    """The default chart's live seats start planned until Agent Ops sets them up; the Release Owner is found then too."""
    api = environment()
    done = api.post("/api/v2/setup/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    assert rows(api, "SELECT state FROM bots WHERE slug='release-owner'")[0]["state"] == "planned"
    assert [
        r["id"] for r in rows(api, "SELECT id FROM pipelines WHERE id IN ('product','engineering') ORDER BY id")
    ] == [
        "engineering",
        "product",
    ]
    assert _staffing(api) == {}
