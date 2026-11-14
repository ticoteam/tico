"""Every way a new company is set up leaves it complete at once: what a start does to complete a company
(Onboarding.complete_company) has run by the end of the first run, so the next start finds nothing to add. Before it,
General had no manager and BotOps no "Sweep stuck tasks" routine until the server restarted."""

import pytest

from backend import company_file as C
from backend import pipeline_presets_more as more
from backend import routines
from backend.tests.helpers.sample_company import (  # noqa: F401
    booted,
    environment,
    machine,
    rows,
    signed_in,
    start,
)


def whole(api):
    """What a start completes: each pipeline's manager, the routines and automations, and the bots."""
    return {
        "managers": rows(api, "SELECT id, manager_id FROM pipelines ORDER BY id"),
        "routines": rows(api, "SELECT bot, routine_key FROM schedules ORDER BY bot, routine_key"),
        "automations": rows(api, "SELECT name, owner_id FROM automations WHERE deleted_at IS NULL ORDER BY id"),
        "bots": rows(api, "SELECT slug, state FROM bots ORDER BY slug"),
    }


def _providers(api):
    done = api.put("/api/v2/providers", json={"enabled": ["anthropic"]}, headers=signed_in())
    assert done.status_code == 200, done.text


def onboarding(api):
    """The setup page: a draft, then Create my team."""
    body = {
        "names": {"company_name": "Kiln & Co", "app_name": "Tico", "assistant_name": "Assistant"},
        "answers": {"what_we_do": "We make pottery.", "customers": "consumers", "team_size": "1 (just me)"},
        "selected": {},
    }
    assert api.put("/api/v2/onboarding", json=body, headers=signed_in()).status_code == 200
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text


def company_file(api):
    """A company file's import."""
    draft = C.blank()
    draft["company"]["signals"] = ["sells_services"]
    draft = C.Turn(draft).finish()
    draft["company"].update(name="Kiln & Co", about="We make pottery.")
    draft["bots"] = [b for b in draft["bots"] if b["template"] in ("assistant", "botops")]
    body = {"company_yaml": C.to_yaml(draft)}
    preview = api.post("/api/v2/onboarding/company/preview", json=body, headers=signed_in())
    assert preview.status_code == 200, preview.text
    done = api.post(
        "/api/v2/onboarding/company/import",
        json={**body, "fingerprint": preview.json()["fingerprint"]},
        headers=signed_in(),
    )
    assert done.status_code == 200, done.text


def sample(api):
    done = start(api)
    assert done.status_code == 200, done.text


# Setup starts the default chart's bots; a company file starts the bots it lists (here only the COO and BotOps). With no
# Manager for the team, the General Manager stands in (pipeline_settings.preset: the ladder).
MANAGERS = {
    "onboarding": {"product": "product-manager"},
    "company_file": {"product": "general-manager"},
    "sample": {"product": "general-manager"},
}


@pytest.mark.parametrize("first_run", [onboarding, company_file, sample])
def test_a_new_company_is_complete_when_its_first_run_ends_and_a_restart_adds_nothing(environment, first_run):
    api = environment()
    _providers(api)
    machine(api)  # the owner's computer, so the built-ins can run and BotOps is active
    first_run(api)
    after_setup = whole(api)
    # No answer to "Software is the product" is yes: Product and Engineering come too. Their managers are the default
    # chart's Product Manager and Engineering Manager where setup started them (pipeline_settings.ensure_presets).
    slugs = {f"bot:{r['id']}": r["slug"] for r in rows(api, "SELECT id, slug FROM bots")}
    managers = {r["id"]: slugs.get(r["manager_id"], r["manager_id"]) for r in after_setup["managers"]}
    # The Growth and Success pipelines' managers: each its team's Manager once the chart has it, else the
    # General Manager (pipeline_presets_more).
    others = {
        p: more.manager_role(p) if more.manager_role(p) in slugs.values() else "general-manager"
        for p in more.PRESETS
        if p not in more.PRIVATE
    }
    # Legal and Finance (private): made only with their owner bot as manager, never the General Manager.
    by_template = {
        r["template"]: r["slug"]
        for r in rows(
            api, "SELECT b.slug, s.template FROM bots b JOIN bot_settings s ON s.bot_id = b.id ORDER BY b.slug DESC"
        )
    }
    for p in more.PRIVATE:
        owner = by_template.get(more.ROLES[p]["manager"])
        if owner:
            others[p] = owner
    assert managers == {"general": "general-manager", **MANAGERS[first_run.__name__], **others}
    assert {"bot": "botops", "routine_key": routines.SWEEP_KEY} in after_setup["routines"]
    restarted = environment(seed_roster=False)
    assert whole(restarted) == after_setup
