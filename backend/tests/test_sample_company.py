"""Tico's sample company (backend/sample_company.py): a new company starts as five real bots with short starter
tasks, from the welcome's button or TICO_SAMPLE_COMPANY=on, on the shipped templates."""

import re
import uuid

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

OWNER_EMAIL = "owner@sample.example"
PEOPLE = {"default_user": "owner", "people": [
    {"id": "owner", "name": "Owner Person", "email": OWNER_EMAIL, "primary_for": ["*"]},
    {"id": "helper", "name": "Helper Person", "email": "helper@sample.example", "primary_for": []}]}
# What a fresh environment seeds (templates/environment-registry/employees.yaml): the assistant and BotOps, planned.
SEED = {"coo": {"name": "coo", "status": "planned"}, "botops": {"name": "botops", "status": "planned"}}
TOKEN = "local-owner-secret-token-0123456789"
ROLE_BOTS = {"head-of-product": "claude", "builder": "codex", "support-agent": "codex", "content-marketer": "codex"}


def settings_for(tmp_path, **overrides):
    registry = tmp_path / "registry"
    registry.mkdir(exist_ok=True)
    (registry / "hub-access.yaml").write_text(yaml.safe_dump({"bot_admins": ["helper@sample.example"]}))
    token = tmp_path / "token"
    token.write_text(TOKEN)
    token.chmod(0o600)
    return Settings(**{"db_path": tmp_path / "hub.db", "registry_dir": registry, "local_owner_token_file": token,
                       "owner_email": OWNER_EMAIL, "environment_id": "sample-1", "company_name": "", "app_name": "Tico",
                       "assistant_name": "Assistant", "assistant_bot": "coo", "enabled_providers": (), **overrides})


@pytest.fixture
def environment(tmp_path):
    """A signed-in loopback environment on the shipped catalog, seeded like a fresh install."""
    clients = []

    def build(seed_roster=True, **overrides):
        client = TestClient(create_app(settings_for(tmp_path, **overrides)))
        client.__enter__()
        clients.append(client)
        if seed_roster:
            with client.app.state.store.transaction() as c:
                H.sync_registry(c, SEED, PEOPLE)
                c.execute("INSERT INTO registry_metadata VALUES('people',?) ON CONFLICT(key) DO NOTHING",
                          (encode(PEOPLE),))
                for slug, config in SEED.items():
                    c.execute("INSERT INTO bot_config(bot,config,operator,repo) VALUES(?,?,?,?)",
                              (slug, encode(config), "owner", "bot-" + slug))
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def signed_in():
    return {"Authorization": "Bearer " + TOKEN, "Idempotency-Key": str(uuid.uuid4())}


def start(api):
    return api.post("/api/v2/setup/sample", json={}, headers=signed_in())


def machine(api):
    code = api.post("/api/v2/enrollments", json={"operator": "owner"}, headers=signed_in()).json()["code"]
    return api.post("/api/v2/runners/enroll", json={"code": code, "label": "Server", "platform": "test"},
                    headers={"Idempotency-Key": str(uuid.uuid4())}).json()


def rows(api, sql, args=()):
    with api.app.state.store.read() as c:
        return [dict(row) for row in c.execute(sql, args)]


# ---------------------------------------------------------------------------------------------- the file
def test_the_sample_is_a_company_file_of_five_bots_each_with_a_goal_and_short_starter_tasks():
    draft, extra = sample_company.team()
    assert draft == from_yaml(sample_company.FILE.read_text())          # import can read it as it is
    assert [b["template"] for b in draft["bots"]] == ["coo", "product-lead", "pr-reviewer", "support", "content"]
    assert [b["title"] for b in draft["bots"]] == ["Assistant", "Head of Product", "Builder", "Support Agent",
                                                   "Content Marketer"]
    assert sorted(g["owner"] for g in draft["goals"]) == sorted(b["id"] for b in draft["bots"])
    runs_on = [row["runs_on"] for row in extra.values()]
    assert set(runs_on) == set(sample_company.RUNS_ON) and min(runs_on.count(r) for r in set(runs_on)) >= 2
    for row in extra.values():
        assert 1 <= len(row["tasks"]) <= 2
        for task in row["tasks"]:
            assert set(task) == {"title", "approve", "body"} and len(task["body"]) < 1200
            # Nothing leaves the company: no approval to send, spend or publish, no mail, no publishing.
            assert not re.search(r"approval request|mail send|support send|file publish|hub message", task["body"])
    # A made-up company that names none of the test fixtures' people or companies.
    assert not re.search(r"\b(ana|ben|cara|acme|globex)\b", sample_company.FILE.read_text(), re.I)


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

    bots = {row["slug"]: row for row in rows(api, "SELECT slug,display_name,runtime,model,state FROM bots")}
    assert {"coo", "botops", *ROLE_BOTS} <= set(bots)
    assert {slug: bots[slug]["runtime"] for slug in ROLE_BOTS} == ROLE_BOTS          # spread over both subscriptions
    assert bots["builder"]["display_name"] == "Builder"
    setup = {row["bot"]: row for row in rows(api, "SELECT bot,onboarding_state,reports_to FROM bot_config")}
    # Set up by the sample's own answers: not parked, so their work is claimed once a computer runs them.
    assert all(setup[slug]["onboarding_state"] == O.ONBOARDED for slug in ROLE_BOTS)
    assert setup["builder"]["reports_to"] == "head-of-product"
    # Their first routines stay off: only the starter tasks run on day one.
    routines = rows(api, "SELECT s.bot,coalesce(sc.enabled,1) AS enabled FROM schedules s "
                         "LEFT JOIN schedule_config sc ON sc.schedule_id=s.id WHERE s.bot IN (%s)"
                    % ",".join("?" * len(ROLE_BOTS)), tuple(ROLE_BOTS))
    assert routines and not any(r["enabled"] for r in routines)
    # No "Finish onboarding" reminder in Needs you for a sample bot.
    assert not rows(api, "SELECT id FROM tasks WHERE title LIKE 'Finish onboarding%' AND status NOT IN ('done','closed')")

    goals = {row["owner"]: row for row in rows(api, "SELECT id,owner,title FROM goals WHERE owner LIKE 'bot:%'")}
    assert {"bot:coo", *("bot:" + slug for slug in ROLE_BOTS)} <= set(goals)
    tasks = rows(api, "SELECT id,owner,requester,title,body,goal_id,status FROM tasks WHERE id IN (%s)"
                 % ",".join("?" * len(record["sample"]["tasks"])), tuple(record["sample"]["tasks"]))
    assert sorted(t["owner"] for t in tasks) == sorted(["bot:coo", *("bot:" + slug for slug in ROLE_BOTS)])
    for task in tasks:
        assert task["requester"] == "human:owner" and task["status"] == "open"
        assert task["goal_id"] == goals[task["owner"]]["id"]
        # Each ends by asking the owner one Yes/No question, the card in Needs you.
        assert re.search(r'hub task question --on owner "Approve [^"]+\?"', task["body"]), task["body"]


def test_the_sample_company_is_only_for_a_new_company_and_only_the_owner_starts_it(environment):
    api = environment()
    helper = dict(signed_in(), Authorization="Bearer helper")
    api.app.state.store.settings.test_identities["helper"] = Identity("human:helper", "human",
                                                                      email="helper@sample.example")
    assert api.post("/api/v2/setup/sample", json={}, headers=helper).status_code == 403
    assert start(api).status_code == 200
    again = start(api)
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "sample_company" and "already set up" in again.json()["error"]["detail"]
    assert len(rows(api, "SELECT id FROM tasks WHERE owner='bot:builder'")) == 1        # nothing made twice


def test_a_company_with_its_own_provider_keeps_it(environment):
    api = environment(enabled_providers=("openai",))
    assert start(api).status_code == 200
    providers = api.get("/api/v2/providers", headers=signed_in()).json()
    assert providers["enabled"] == ["openai"]
    bots = {row["slug"]: row["runtime"] for row in rows(api, "SELECT slug,runtime FROM bots")}
    # Claude is off here, so the Claude bots run on the company's own default, as a wizard pick would.
    assert bots["builder"] == "codex" and bots["head-of-product"] == "codex"


def test_the_bots_start_once_a_computer_runs_them(environment):
    api = environment()
    start(api)
    computer = machine(api)
    states = {row["slug"]: row["state"] for row in rows(api, "SELECT slug,state FROM bots")}
    assert all(states[slug] == "active" for slug in ("coo", *ROLE_BOTS))
    placed = {row["bot"] for row in rows(api, "SELECT bot FROM assignments WHERE runner_id=?",
                                         (computer["runner_id"],))}
    assert {"coo", *ROLE_BOTS} <= placed
    # Each starter task woke its bot: a job waits for the computer to claim it.
    queued = {row["bot"] for row in rows(api, "SELECT bot FROM jobs WHERE state='queued'")}
    assert {"coo", *ROLE_BOTS} <= queued


def test_each_role_bot_is_made_already_set_up_so_it_does_its_starter_task(environment, tmp_path):
    """The computer makes each bot's repository from its template (runner/catalog_job.py); a template's state.md
    says setup has not started, and its AGENT.md asks the setup questions first until it does."""
    import io
    import json
    from runner import catalog_job
    api = environment()
    start(api)
    record, config = (api.get(path, headers=signed_in()).json() for path in ("/api/v2/setup", "/api/v2/config"))

    class Server:                                  # the runner's view of this server, with no network
        def get(self, path, **query):
            return {"onboarding": record, "config": config}[path]

    projects = tmp_path / "projects"
    projects.mkdir()
    configs = {row["bot"]: json.loads(row["config"]) for row in rows(api, "SELECT bot,config FROM bot_config")}
    request = {"bots": [{"bot": slug, "entry": {"bot": slug, "state": "active", "config": configs[slug]},
                         "path": str(projects / ("bot-" + slug)), "action": "bootstrap"} for slug in ROLE_BOTS]}
    out = io.StringIO()
    catalog_job.main({"projects_dir": str(projects)}, Server(), io.StringIO(json.dumps(request)), out)
    answer = json.loads(out.getvalue())
    for slug in ROLE_BOTS:
        assert answer[slug]["problem"] == "" and answer[slug]["materialized"], answer[slug]
        state = (projects / ("bot-" + slug) / "state.md").read_text()
        assert re.search(r"^## Setup\nDone \d{4}-\d\d-\d\d\. .*do not ask them\.$", state, re.M), state
        assert "Not started" not in state and "start setup" not in state and "switched on when your setup" not in state
        # The template's own instructions still gate on state.md, which now says setup is done.
        assert "If `state.md` says setup has not finished" in (projects / ("bot-" + slug) / "AGENT.md").read_text()


# ---------------------------------------------------------------------------------------------- TICO_SAMPLE_COMPANY
# The contract with the recommended install (Dev 212): TICO_SAMPLE_COMPANY=on loads the sample once, on the first start
# of an empty team; a restart never loads it again; unset or any other value loads nothing.
def test_flag_on_loads_the_sample_company_once_and_a_restart_does_not_duplicate_it(environment):
    environment()                                        # the install's first start makes the team and its roster
    first = environment(seed_roster=False, sample_company=True)
    sample = first.get("/api/v2/setup", headers=signed_in()).json()["sample"]
    assert sample["by"] == "human:owner" and len(sample["tasks"]) == 5
    titles = [task["title"] for row in sample_company.team()[1].values() for task in row["tasks"]]
    starter = "SELECT title FROM tasks WHERE title IN (%s) ORDER BY title" % ",".join("?" * len(titles))
    assert len(rows(first, starter, tuple(titles))) == len(titles)
    goals = rows(first, "SELECT id FROM goals ORDER BY id")
    again = environment(seed_roster=False, sample_company=True)        # a restart, same database
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
        c.execute("UPDATE bots SET state='active' WHERE slug='coo'")
    restarted = environment(seed_roster=False, sample_company=True)
    assert restarted.get("/api/v2/setup", headers=signed_in()).json()["sample"] is None
    assert not rows(restarted, "SELECT slug FROM bots WHERE slug='builder'")


def test_a_team_name_already_set_is_kept_and_the_starter_work_uses_it(environment):
    environment(company_name="Brightside Salons")
    api = environment(seed_roster=False, company_name="Brightside Salons", sample_company=True)
    record = api.get("/api/v2/setup", headers=signed_in()).json()
    assert record["sample"] and record["names"]["company_name"] == "Brightside Salons"
    bodies = " ".join(t["title"] + t["body"] for t in rows(api, "SELECT title,body FROM tasks WHERE id IN (%s)"
                      % ",".join("?" * len(record["sample"]["tasks"])), tuple(record["sample"]["tasks"])))
    assert "Brightside Salons" in bodies and "Sample Co" not in bodies
    # With no name set (config's "Company" is none), the team is called Sample Co.
    assert sample_company.team_name(settings_for_name("Company"), {"names": {}}) == ""
    assert sample_company.team_name(settings_for_name("Company"), {"names": {"company_name": "Saved"}}) == "Saved"


def settings_for_name(name):
    return type("S", (), {"company_name": name})()


@pytest.mark.parametrize("value, on", [("on", True), ("ON", True), (" on ", True), ("1", False), ("true", False),
                                       ("yes", False), ("off", False), ("", False), (None, False)])
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
