"""Tico's sample company (backend/sample_company.py): a new company starts as five real bots with short starter
tasks, from the welcome's button or TICO_SAMPLE_COMPANY=on, on the shipped templates."""

import re
import shutil
import uuid
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from backend import onboarding as O
from backend import sample_company
from backend.app import create_app
from backend.auth import Identity
from backend.company_file import from_yaml
from backend.config import Settings
from backend.store import H, encode
from backend import bot_settings as BS

from backend.tests.helpers.sample_company import (
    OWNER_EMAIL,
    PEOPLE,
    ROLE_BOTS,
    SEED,
    TOKEN,
    booted,
    environment,
    machine,
    rows,
    settings_for,
    signed_in,
    start,
    bot_ids,
)  # noqa: F401


# ---------------------------------------------------------------------------------------------- the file


# ---------------------------------------------------------------------------------------------- the welcome's button
def test_the_owner_starts_the_sample_company_and_every_bot_has_its_model_goal_and_starter_task(environment):
    api = environment()
    assert api.get("/api/v2/config", headers=signed_in()).json()["onboarding_needed"] is True
    done = start(api)
    assert done.status_code == 200, done.text
    record = done.json()
    assert record["completed"] and record["sample"]["by"] == "human:owner"
    assert record["names"]["company_name"] == "Sample Co"
    config = api.get("/api/v2/config", headers=signed_in()).json()
    assert config["onboarding_needed"] is False and config["company_name"] == "Sample Co"
    # No provider was chosen, so the sample's two are on, Claude the default.
    providers = api.get("/api/v2/providers", headers=signed_in()).json()
    assert providers["enabled"] == ["anthropic", "openai"] and providers["default"]["runtime"] == "claude"

    bots = {
        row["slug"]: row
        for row in rows(
            api,
            "SELECT b.slug,b.display_name,b.state,s.runtime AS runtime,s.model AS model "
            "FROM bots b LEFT JOIN bot_config bc ON bc.bot=b.id LEFT JOIN bot_settings s ON s.bot_id=b.id",
        )
    }
    assert {"assistant", "botops", *ROLE_BOTS} <= set(bots)
    assert {slug: bots[slug]["runtime"] for slug in ROLE_BOTS} == ROLE_BOTS  # spread over both subscriptions
    assert bots["builder"]["display_name"] == "Builder"
    setup = {row["bot"]: row for row in rows(api, "SELECT bot,onboarding_state,reports_to FROM bot_config")}
    # Set up by the sample's own answers: not parked, so their work is claimed once a computer runs them.
    ids = bot_ids(api)
    assert all(setup[ids[slug]]["onboarding_state"] == O.ONBOARDED for slug in ROLE_BOTS)
    assert setup[ids["builder"]]["reports_to"] == ids["head-of-product"]
    # Their first routines stay off: only the starter tasks run on day one.
    routines = rows(
        api,
        "SELECT s.bot,coalesce(sc.enabled,1) AS enabled FROM schedules s "
        "LEFT JOIN schedule_config sc ON sc.schedule_id=s.id WHERE s.bot IN (%s)" % ",".join("?" * len(ROLE_BOTS)),
        tuple(ids[slug] for slug in ROLE_BOTS),
    )
    assert routines and not any(r["enabled"] for r in routines)
    # No "Finish onboarding" reminder in Needs you for a sample bot.
    assert not rows(
        api, "SELECT id FROM tasks WHERE title LIKE 'Finish onboarding%' AND status NOT IN ('done','closed')"
    )

    goals = {
        row["owner_id"]: row for row in rows(api, "SELECT id,owner_id,title FROM goals WHERE owner_id LIKE 'bot:%'")
    }
    assert {"bot:" + ids[slug] for slug in ("assistant", *ROLE_BOTS)} <= set(goals)
    tasks = rows(
        api,
        "SELECT id,owner_id,requester_id,title,body,goal_id,status FROM tasks WHERE id IN (%s)"
        % ",".join("?" * len(record["sample"]["tasks"])),
        tuple(record["sample"]["tasks"]),
    )
    assert sorted(t["owner_id"] for t in tasks) == sorted("bot:" + ids[slug] for slug in ("assistant", *ROLE_BOTS))
    for task in tasks:
        assert task["requester_id"] == "human:owner" and task["status"] == "open"
        assert task["goal_id"] == goals[task["owner_id"]]["id"]
        # Each ends by asking the owner one Yes/No question on the task, which the owner sees in Needs you.
        ask = r'tico task ask <id> --to owner "Approve [^"]+\?" --choices Yes,No --version N'
        assert re.search(ask, task["body"]), task["body"]


def test_the_sample_company_is_only_for_a_new_company_and_only_the_owner_starts_it(environment):
    api = environment()
    helper = dict(signed_in(), Authorization="Bearer helper")
    api.app.state.store.settings.test_identities["helper"] = Identity(
        "human:helper", "human", email="helper@sample.example"
    )
    assert api.post("/api/v2/setup/sample", json={}, headers=helper).status_code == 403
    assert start(api).status_code == 200
    again = start(api)
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "sample_company" and "already set up" in again.json()["error"]["detail"]
    builder = bot_ids(api)["builder"]
    assert len(rows(api, "SELECT id FROM tasks WHERE owner_id=?", ("bot:" + builder,))) == 1  # nothing made twice


def test_a_company_with_its_own_provider_keeps_it(environment):
    api = environment(enabled_providers=("openai",))
    assert start(api).status_code == 200
    providers = api.get("/api/v2/providers", headers=signed_in()).json()
    assert providers["enabled"] == ["openai"]
    bots = {
        row["slug"]: row["runtime"]
        for row in rows(api, "SELECT b.slug,s.runtime AS runtime FROM bots b LEFT JOIN bot_settings s ON s.bot_id=b.id")
    }
    # Claude is off here, so the Claude bots run on the company's own default, as a wizard pick would.
    assert bots["builder"] == "codex" and bots["head-of-product"] == "codex"


def test_the_bots_start_once_a_computer_runs_them(environment):
    api = environment()
    start(api)
    computer = machine(api)
    states = {row["slug"]: row["state"] for row in rows(api, "SELECT slug,state FROM bots")}
    assert all(states[slug] == "active" for slug in ("assistant", *ROLE_BOTS))
    placed = {
        row["bot"] for row in rows(api, "SELECT bot FROM assignments WHERE runner_id=?", (computer["runner_id"],))
    }
    ids = bot_ids(api)
    assert {ids[slug] for slug in ("assistant", *ROLE_BOTS)} <= placed
    # Each starter task woke its bot: a job waits for the computer to claim it.
    queued = {row["bot_id"] for row in rows(api, "SELECT bot_id FROM jobs WHERE state='queued'")}
    assert {ids[slug] for slug in ("assistant", *ROLE_BOTS)} <= queued


def test_each_role_bot_is_made_already_set_up_so_it_does_its_starter_task(environment, tmp_path):
    """The computer makes each bot's repository from its template (runner_support/catalog_job.py); a template's AGENTS.md
    asks the setup questions first while Tico marks the bot Needs setup, and a sample bot is not marked so: its
    knowledge/company.md says the sample's answers stood in for them."""
    import io
    import json
    from runner_support import catalog_job

    api = environment()
    start(api)
    record, config = (api.get(path, headers=signed_in()).json() for path in ("/api/v2/setup", "/api/v2/config"))

    class Server:  # the runner's view of this server, with no network
        def get(self, path, **query):
            return {"onboarding": record, "config": config}[path]

    projects = tmp_path / "projects"
    projects.mkdir()
    configs = {
        row["bot"]: json.loads(row["config"])
        for row in rows(api, f"SELECT bot,{BS.CONFIG} AS config FROM bot_config bc {BS.JOIN}")
    }
    ids = bot_ids(api)
    request = {
        "bots": [
            {
                "bot": slug,
                "entry": {"bot": slug, "state": "active", "config": configs[ids[slug]]},
                "path": str(projects / ("bot-" + slug)),
                "action": "bootstrap",
            }
            for slug in ROLE_BOTS
        ]
    }
    out = io.StringIO()
    catalog_job.main({"projects_dir": str(projects)}, Server(), io.StringIO(json.dumps(request)), out)
    answer = json.loads(out.getvalue())
    for slug in ROLE_BOTS:
        assert answer[slug]["problem"] == "" and answer[slug]["materialized"], answer[slug]
        repo = projects / ("bot-" + slug)
        assert not (repo / "state.md").exists()
        page = (repo / "knowledge" / "company.md").read_text()
        assert re.search(r"^## Setup\nDone \d{4}-\d\d-\d\d\. .*do not ask them\.$", page, re.M), page
        assert "## Routine\nYour first routine is off." in page
        # The template's own instructions gate on the Needs setup mark, which a sample bot does not have.
        assert "If setup has not finished (Tico marks you Needs setup)" in (repo / "AGENTS.md").read_text()


# ---------------------------------------------------------------------------------------------- TICO_SAMPLE_COMPANY
# The contract with the recommended install (Dev 212): TICO_SAMPLE_COMPANY=on loads the sample once, on the first start
# of an empty team; a restart never loads it again; unset or any other value loads nothing.
def test_flag_on_loads_the_sample_company_once_and_a_restart_does_not_duplicate_it(environment):
    environment()  # the install's first start makes the team and its roster
    first = environment(seed_roster=False, sample_company=True)
    sample = first.get("/api/v2/setup", headers=signed_in()).json()["sample"]
    assert sample["by"] == "human:owner" and len(sample["tasks"]) == 5
    titles = [task["title"] for row in sample_company.team()[1].values() for task in row["tasks"]]
    starter = "SELECT title FROM tasks WHERE title IN (%s) ORDER BY title" % ",".join("?" * len(titles))
    assert len(rows(first, starter, tuple(titles))) == len(titles)
    goals = rows(first, "SELECT id FROM goals ORDER BY id")
    again = environment(seed_roster=False, sample_company=True)  # a restart, same database
    assert again.get("/api/v2/setup", headers=signed_in()).json()["sample"] == sample
    assert len(rows(again, starter, tuple(titles))) == len(titles)
    assert rows(again, "SELECT id FROM goals ORDER BY id") == goals
    assert len(rows(again, "SELECT slug FROM bots WHERE slug='builder'")) == 1
    loads = rows(again, "SELECT id FROM events WHERE action='onboarding.sample_loaded'")
    assert len(loads) == 1


def test_flag_off_loads_nothing(environment):
    environment()
    restarted = environment(seed_roster=False)
    assert restarted.get("/api/v2/setup", headers=signed_in()).json()["sample"] is None
    assert not rows(restarted, "SELECT slug FROM bots WHERE slug='builder'")
    assert not rows(restarted, "SELECT id FROM events WHERE action='onboarding.sample_loaded'")


def test_flag_on_leaves_a_team_that_is_set_up_alone(environment):
    api = environment()
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug='assistant'")
    restarted = environment(seed_roster=False, sample_company=True)
    assert restarted.get("/api/v2/setup", headers=signed_in()).json()["sample"] is None
    assert not rows(restarted, "SELECT slug FROM bots WHERE slug='builder'")


def test_a_team_name_already_set_is_kept_and_the_starter_work_uses_it(environment):
    environment(company_name="Brightside Salons")
    api = environment(seed_roster=False, company_name="Brightside Salons", sample_company=True)
    record = api.get("/api/v2/setup", headers=signed_in()).json()
    assert record["sample"] and record["names"]["company_name"] == "Brightside Salons"
    bodies = " ".join(
        t["title"] + t["body"]
        for t in rows(
            api,
            "SELECT title,body FROM tasks WHERE id IN (%s)" % ",".join("?" * len(record["sample"]["tasks"])),
            tuple(record["sample"]["tasks"]),
        )
    )
    assert "Brightside Salons" in bodies and "Sample Co" not in bodies
    # With no name set (config's "Company" is none), the team is called Sample Co.
    assert sample_company.team_name(settings_for_name("Company"), {"names": {}}) == ""
    assert sample_company.team_name(settings_for_name("Company"), {"names": {"company_name": "Saved"}}) == "Saved"


def settings_for_name(name):
    return type("S", (), {"company_name": name})()


@pytest.mark.parametrize(
    "value, on",
    [
        ("on", True),
        ("ON", True),
        (" on ", True),
        ("1", False),
        ("true", False),
        ("yes", False),
        ("off", False),
        ("", False),
        (None, False),
    ],
)
def test_only_on_turns_the_flag_on_and_a_rehearsal_never_does(monkeypatch, tmp_path, value, on):
    monkeypatch.setenv("TICO_DB", str(tmp_path / "hub.db"))
    monkeypatch.delenv("TICO_REHEARSAL", raising=False)
    if value is None:
        monkeypatch.delenv("TICO_SAMPLE_COMPANY", raising=False)
    else:
        monkeypatch.setenv("TICO_SAMPLE_COMPANY", value)
    assert Settings.from_env().sample_company is on
    monkeypatch.setenv("TICO_REHEARSAL", "1")
    assert Settings.from_env().sample_company is False
