"""A new company that makes no software starts with none of the software work: no Product or Engineering seats, bots or
setup tasks, no Daily release with no Release Owner to ask, no two automations of one name, no BotOps task for GitHub
or for the AI provider it has not chosen, and a new database's start says nothing about moving routines."""

import pytest

from backend import redesign_migration
from backend import system_routines as SR
from backend.maintenance_steps import check_health
from backend.tests.helpers.sample_company import booted, environment, machine, rows, settings_for, signed_in  # noqa: F401

SOFTWARE = ("product", "engineering")
# The Managers of the teams every company has: each runs its team's pipelines.
OTHER_MANAGERS = {
    "Set up Growth Manager from the growth-manager template",
    "Set up Success Manager from the success-manager template",
}


def _set_up(api, software="no", provider=True):
    if provider:
        done = api.put("/api/v2/providers", json={"enabled": ["anthropic"]}, headers=signed_in())
        assert done.status_code == 200, done.text
    machine(api)
    body = {
        "names": {"company_name": "Kiln & Co", "app_name": "Tico", "assistant_name": "Assistant"},
        "answers": {
            "what_we_do": "We make pottery.",
            "customers": "consumers",
            "team_size": "1 (just me)",
            "software_product": software,
        },
        "selected": {},
    }
    assert api.put("/api/v2/onboarding", json=body, headers=signed_in()).status_code == 200
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text


def _software_templates(api):
    from backend.recruit import catalog

    cards = catalog(api.app.state.store.settings)["cards"]
    return {card["template"] for card in cards if card["department"] in SOFTWARE}


def test_a_company_that_makes_no_software_gets_no_product_or_engineering_seats_bots_or_setup_tasks(environment):
    api = environment()
    _set_up(api)
    software = _software_templates(api)
    seats = rows(api, "SELECT title, template FROM seats WHERE closed_at IS NULL")
    assert seats and not [s for s in seats if s["template"] in software], seats
    bots = rows(
        api,
        "SELECT b.slug, s.template FROM bots b JOIN bot_config bc ON bc.bot=b.id "
        "JOIN bot_settings s ON s.bot_id=b.id WHERE b.state<>'archived'",
    )
    assert not [b for b in bots if b["template"] in software], bots
    # Its own teams' Managers are set up; no Product or Engineering one.
    setup = {row["title"] for row in rows(api, "SELECT title FROM tasks WHERE title LIKE 'Set up % template'")}
    assert setup == OTHER_MANAGERS | {"Set up Legal Owner from the legal-owner template"}, setup


@pytest.mark.parametrize("software", ["yes", ""])
def test_a_software_company_and_one_that_did_not_say_get_the_product_and_engineering_bots(environment, software):
    """Only a "no" leaves the software seats out: a blank answer is yes (D7), and a software company starts with each
    team's Manager and every Product and Engineering stage owner live, the QA Owner and the Legal Owner."""
    api = environment()
    _set_up(api, software=software)
    titles = {row["title"] for row in rows(api, "SELECT title FROM tasks WHERE title LIKE 'Set up % template'")}
    assert titles == {
        "Set up Product Manager from the product-manager template",
        "Set up Feature Owner from the feature-owner template",
        "Set up Engineering Manager from the engineering-manager template",
        "Set up Reviewer 1 from the reviewer template",
        "Set up Reviewer 2 from the reviewer template",
        "Set up QA Owner from the qa-owner template",
        "Set up Release Owner from the release-owner template",
        *OTHER_MANAGERS,
        "Set up Legal Owner from the legal-owner template",
    }


def test_a_new_company_has_no_daily_release_and_no_two_automations_of_one_name(environment):
    api = environment()
    _set_up(api)
    with api.app.state.store.read() as c:
        hidden = SR.not_applying(c, api.app.state.store.settings)
    assert "release_hour" in hidden
    names = [row["name"] for row in rows(api, "SELECT name FROM automations WHERE deleted_at IS NULL")]
    assert sorted(n for n in set(names) if names.count(n) > 1) == []


def test_the_daily_release_applies_once_there_is_a_release_manager(environment):
    api = environment()
    _set_up(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_settings SET template='release-owner' WHERE bot_id='automations-owner'")
    with api.app.state.store.read() as c:
        assert "release_hour" not in SR.not_applying(c, api.app.state.store.settings)


def _health_titles(api):
    store = api.app.state.store
    for _ in range(2):  # a noisy check files on its second run
        check_health.run_once(store, api.app.state.auth, store.settings)
    return [row["title"] for row in rows(api, "SELECT title FROM tasks WHERE owner_id='bot:botops'")]


def test_a_new_company_files_no_botops_task_for_github_or_for_the_provider_it_has_not_chosen(environment):
    api = environment()
    _set_up(api, provider=False)
    titles = _health_titles(api)
    assert not [t for t in titles if "GitHub" in t or "Models" in t], titles


def test_a_new_database_start_tells_no_one_it_was_migrated(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app import create_app

    warned = []
    monkeypatch.setattr(redesign_migration.log, "warning", lambda *args, **kw: warned.append(args))
    printed = []
    monkeypatch.setattr(redesign_migration, "_printed_to_stderr", lambda: printed.append(1) or True)
    with TestClient(create_app(settings_for(tmp_path))) as client:
        with client.app.state.store.read() as c:
            record = redesign_migration.state(c)
            marks = c.execute("SELECT count(*) FROM redesign_migrated WHERE told IS NULL").fetchone()[0]
    assert record["state"] == "done" and record["new"] is True
    assert marks == 0
    assert [args for args in warned if "redesign cutover" in " ".join(map(str, args))] == []
    assert printed == []
