"""First-run onboarding: pick templates, name things, and hand BotOps the setup backlog.

The catalog is written per test, so these assertions never depend on the cards a release
happens to ship. The company is Acme, its app is Atlas, its assistant Morgan.
"""

import json
import uuid
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from backend import providers
from backend.app import create_app
from backend.auth import Identity
from backend.config import Settings
from backend.store import H, encode

COMPANY = {"environment_id": "acme-7", "company_name": "Acme", "app_name": "Atlas",
           "assistant_name": "Morgan", "assistant_bot": "coo", "github_owner": "AcmeCorp"}
OWNER_EMAIL = "morgan@acme.example"
PEOPLE = {"default_user": "morgan", "people": [
    {"id": "morgan", "name": "Morgan Reed", "email": OWNER_EMAIL, "primary_for": ["*"]},
    {"id": "riley", "name": "Riley Quinn", "email": "riley@acme.example", "primary_for": []},
    {"id": "quinn", "name": "Quinn Ellis", "email": "quinn@acme.example", "primary_for": []}]}
# What a fresh environment seeds: the assistant people talk to, and the bot that builds the
# rest. Both start planned, exactly as templates/environment-registry/employees.yaml does.
SEED = {"coo": {"name": "coo", "status": "planned"},
        "botops": {"name": "botops", "status": "planned"}}
TOKEN = "local-owner-secret-token-0123456789"

ASSISTANT_CARD = {
    "template": "assistant", "slug": "coo", "name": "{{assistant_name}}", "required": True, "default": True,
    "bootstrap": True, "summary": "The assistant {{company_name}} talks to in {{app_name}}.",
    "owns": ["{{company_name}}'s task list"], "never": ["Spend {{company_name}}'s money"],
    "reasoning_effort": "high", "recommend_when": ["always"]}
BOTOPS_CARD = {
    "template": "botops", "slug": "botops", "name": "BotOps", "required": True,
    "bootstrap": True, "summary": "Sets every other bot up.", "owns": ["bot repositories"],
    "never": ["publishes"],
    "reasoning_effort": "high", "recommend_when": ["always"]}
SUPPORT_CARD = {
    "template": "support", "slug": "support", "name": "Support", "required": False,
    "bootstrap": False, "summary": "Answers the support inbox.", "owns": ["the inbox"],
    "never": ["refunds"],
    "reasoning_effort": "high", "recommend_when": ["has_support_inbox", "uses_tickets"]}
SALES_CARD = {
    "template": "sales", "slug": "sales", "name": "Sales", "required": False,
    "bootstrap": False, "summary": "Researches accounts and drafts outreach.",
    "owns": ["the pipeline"], "never": ["sends"],
    "reasoning_effort": "high",
    "recommend_when": ["sells_to_businesses", "has_pipeline"]}

ASSISTANT_AGENT = "# {{bot_name}}\n\nYou are {{assistant_name}}, {{company_name}}'s assistant in {{app_name}}.\n"
SUPPORT_AGENT = "# {{bot_name}}\n\nYou answer {{company_name}}'s support inbox.\n"


def write_catalog(root, cards):
    """One catalog on disk: a folder, a card, and the AGENT.md a person reviews."""
    root.mkdir(parents=True, exist_ok=True)
    for card, instructions in cards:
        folder = root / card["template"]
        folder.mkdir()
        (folder / "card.yaml").write_text(yaml.safe_dump(card))
        (folder / "AGENT.md").write_text(instructions)
    return root


@pytest.fixture
def environment(tmp_path):
    """A signed-in, loopback environment seeded with just coo and botops, plus a catalog."""
    clients = []

    def build(cards=None, seed=SEED, **overrides):
        index = len(clients)
        registry = tmp_path / ("registry-%d" % index)
        registry.mkdir()
        (registry / "hub-access.yaml").write_text(yaml.safe_dump(
            {"bot_admins": ["riley@acme.example"]}))
        token = tmp_path / ("token-%d" % index)
        token.write_text(TOKEN)
        token.chmod(0o600)
        catalog = overrides.pop("catalog_dir", None)          # a test of the shipped catalog names it
        if catalog is None:
            catalog = tmp_path / ("catalog-%d" % index)
            if cards is None:
                cards = [(ASSISTANT_CARD, ASSISTANT_AGENT), (BOTOPS_CARD, ""),
                         (SUPPORT_CARD, SUPPORT_AGENT), (SALES_CARD, "")]
            write_catalog(catalog, cards)
        settings = Settings(db_path=tmp_path / ("hub-%d.db" % index), registry_dir=registry,
                            catalog_dir=catalog, local_owner_token_file=token,
                            **{"owner_email": OWNER_EMAIL, "enabled_providers": ("openai",), **COMPANY, **overrides})
        client = TestClient(create_app(settings))
        client.__enter__()
        clients.append(client)
        with client.app.state.store.transaction() as c:
            H.sync_registry(c, seed, PEOPLE)
            c.execute("INSERT INTO registry_metadata VALUES('people',?)", (encode(PEOPLE),))
            for slug, config in seed.items():
                c.execute("INSERT INTO bot_config(bot,config,operator,repo) VALUES(?,?,?,?)",
                          (slug, encode(config), "morgan", "emp-" + slug))
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def signed_in(token=TOKEN):
    return {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid.uuid4())}


def build_team(api):
    """Finish setup with the picked bots made as bots, the way the sample company does (`complete(seats=False)`).
    Setup itself lays every picked role out as a Recruiting seat (bot-lifecycle-spec.md); these tests are about the
    bots themselves once they exist."""
    with api.app.state.store.transaction() as c:
        return api.app.state.onboarding.complete(c, api.app.state.auth.owner_identity(c), seats=False)


def as_person(api, person):
    """A signed-in non-owner, without standing an identity proxy up for the test."""
    api.app.state.store.settings.test_identities[person] = Identity(
        "human:" + person, "human", email=person + "@acme.example")
    return signed_in(person)


def machine(api, label="Owner Mac"):
    code = api.post("/api/v2/enrollments", json={"operator": "morgan"}, headers=signed_in()).json()["code"]
    return api.post("/api/v2/runners/enroll", json={"code": code, "label": label, "platform": "test"},
                    headers={"Idempotency-Key": str(uuid.uuid4())}).json()


def draft(api, **overrides):
    body = {"names": {"company_name": "Acme", "app_name": "Atlas", "assistant_name": "Morgan"},
            "answers": {"what_we_do": "We clean apartments.", "customers": "businesses",
                        "team_size": "6-10", "work_arrives": ["email", "tickets"],
                        "repetitive_work": "Answering the same questions.",
                        "never_without_person": ["send", "spend"]},
            "selected": {}}
    body.update(overrides)
    return api.put("/api/v2/onboarding", json=body, headers=signed_in())


def seats(api):
    with api.app.state.store.read() as c:
        return [dict(row) for row in c.execute("SELECT id,title,description,reports_to FROM seats WHERE closed_at IS NULL")]


def test_setup_lays_out_each_picked_role_once_as_a_recruiting_seat_and_creates_no_bot_for_it(environment):
    """bot-lifecycle-spec.md acceptance tests 1 and 20: a picked role is a Recruiting seat with its job description and
    reporting line; no bot, repository, computer slot or BotOps setup task. Completing twice changes nothing."""
    api = environment()
    machine(api)
    # The owner's edits to the instructions are the duties and limits recruiting must see; the template's text is not.
    edited = "# Help\n\nYou answer Acme's support inbox.\n\nRefund up to $40 without asking.\nNever phone a customer.\n"
    draft(api, selected={"support": {"template": "support", "display_name": "Help", "instructions": edited}})
    first = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).json()
    again = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert again.status_code == 200, again.text
    record = again.json()
    assert record["completed"] == first["completed"] and record["seats"] == first["seats"]
    assert sorted(row["slug"] for row in record["bots"]) == ["botops", "coo"]
    seat, = seats(api)
    assert record["seats"] == {"support": seat["id"]}
    assert seat["title"] == "Help" and seat["reports_to"] == "human:morgan" 
    assert "Refund up to $40 without asking." in seat["description"] and "Never phone a customer." in seat["description"]
    assert "Answers the support inbox." in seat["description"] and "You answer Acme" not in seat["description"]
    opened = api.get(f"/api/v2/seats/{seat['id']}", headers=signed_in())
    assert opened.status_code == 200 and opened.json()["description_by"] == "person"
    assert api.get("/api/v2/tasks", params={"owner": "botops"}, headers=signed_in()).json()["tasks"] == []
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM bots WHERE slug='support'").fetchone()
        assert not c.execute("SELECT 1 FROM assignments WHERE bot NOT IN ('coo','botops')").fetchone()
    assert _states(api)["botops"] == _states(api)["coo"] == "active"     # the built-ins work from day one


def test_the_inbox_manager_turned_on_in_setup_is_a_message_bot_linked_to_the_owner_not_a_seat(environment):
    """A message bot (`kind: helper`) serves one person and is never on the chart: setup makes the bot, reporting to
    the owner and linked to their mailbox (docs/starter-bots.md), while a role picked beside it is still a seat."""
    from backend import routines, views
    inbox = {"template": "inbox", "slug": "inbox", "name": "Inbox Manager", "required": False, "bootstrap": False,
             "kind": "helper", "summary": "Runs one human's mailbox.", "owns": ["the morning brief"],
             "never": ["sends"], "reasoning_effort": "high", "recommend_when": []}
    api = environment(cards=[(ASSISTANT_CARD, ASSISTANT_AGENT), (BOTOPS_CARD, ""), (SUPPORT_CARD, SUPPORT_AGENT),
                             (inbox, "# {{bot_name}}\n\nYou run one human's mailbox.\n")])
    machine(api)
    # The wizard sends a message bot under the owner (docs/onboarding.md, Finish).
    draft(api, selected={"inbox": {"template": "inbox", "display_name": "Inbox Manager", "reports_to": "human:morgan"},
                         "support": {"template": "support", "display_name": "Help"}})
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    assert sorted(row["slug"] for row in done.json()["bots"]) == ["botops", "coo", "inbox"]
    assert set(done.json()["seats"]) == {"support"}
    with api.app.state.store.read() as c:
        assert c.execute("SELECT reports_to FROM bot_config WHERE bot='inbox'").fetchone()[0] == "human:morgan"
        owner = next(p for p in views.roster(c)["people"] if p["id"] == "morgan")
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot='inbox'").fetchone()[0])
    assert owner["inbox_bot"] == "inbox" and routines.declared_mailbox(config) == OWNER_EMAIL


def test_direct_signup_with_no_chosen_chart_is_the_built_ins_and_an_empty_chart(environment):
    """bot-lifecycle-spec.md acceptance test 4: no company file and nothing picked still finishes setup, with only the
    built-in bots and no seat, and the chart empty to recruit into."""
    api = environment()
    machine(api)
    draft(api)
    done = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert done.status_code == 200, done.text
    assert done.json()["completed"] and done.json()["company_import_failed"] is None
    assert sorted(row["slug"] for row in done.json()["bots"]) == ["botops", "coo"]
    assert seats(api) == [] and api.get("/api/v2/org", headers=signed_in()).json()["seats"] == []
    assert _states(api)["botops"] == _states(api)["coo"] == "active"
    added = api.post("/api/v2/seats", json={"title": "Head of Support"}, headers=signed_in())
    assert added.status_code == 200 and added.json()["stage"] == "recruiting"


def test_completing_twice_duplicates_neither_a_bot_nor_a_task(environment):
    api = environment()
    # The owner's edits to the instructions are the duties and limits recruiting must see; the template's text is not.
    edited = "# Help\n\nYou answer Acme's support inbox.\n\nRefund up to $40 without asking.\nNever phone a customer.\n"
    draft(api, selected={"support": {"template": "support", "display_name": "Help", "instructions": edited}})
    first = build_team(api)
    again = build_team(api)
    assert again["completed"] == first["completed"]
    assert [row["slug"] for row in again["bots"]] == [row["slug"] for row in first["bots"]]
    assert {row["slug"]: row["setup_task_id"] for row in again["bots"]} == {
        row["slug"]: row["setup_task_id"] for row in first["bots"]}
    tasks = api.get("/api/v2/tasks", params={"owner": "botops"}, headers=signed_in()).json()["tasks"]
    assert len(tasks) == 1


def test_a_new_company_starts_with_nothing_new_in_whats_new(environment, monkeypatch, tmp_path):
    from backend import releases
    monkeypatch.setenv("TICO_VERSION", "0.2.0")
    monkeypatch.setattr(releases, "ROOT", tmp_path)
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n## [0.2.0] - 2026-10-02\n- Search your tasks.\n"
                                           "## [0.1.0] - 2026-10-01\n- Browse the team.\n")
    api = environment()

    def unread(headers):
        return api.get("/api/v2/changelog", headers=headers).json()["unread_count"]
    riley = as_person(api, "riley")
    assert unread(signed_in()) == 2 and unread(riley) == 2
    draft(api)
    assert api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).status_code == 200
    assert unread(signed_in()) == 0
    assert api.get("/api/v2/config", headers=signed_in()).json()["changelog"] == {"unread_count": 0}
    # Only the owner who set it up was caught up, and a later update is still news to them.
    assert unread(riley) == 2
    assert api.post("/api/changelog", json={"title": "Pipelines", "bullets": ["Pin a pipeline."]},
                    headers=signed_in()).status_code == 200
    assert unread(signed_in()) == 1
    # Completing again catches up nothing more.
    assert api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).status_code == 200
    assert unread(signed_in()) == 1


def test_only_the_owner_sets_the_company_up(environment):
    api = environment()
    riley = as_person(api, "riley")                # a bot administrator, not the owner
    assert api.get("/api/v2/onboarding", headers=riley).status_code == 200
    assert api.put("/api/v2/onboarding", json={"names": {}, "answers": {}, "selected": {}},
                   headers=riley).status_code == 403
    assert api.post("/api/v2/onboarding/complete", json={}, headers=riley).status_code == 403

    quinn = as_person(api, "quinn")               # neither the owner nor a bot administrator
    assert api.get("/api/v2/onboarding", headers=quinn).status_code == 403
    # The catalog itself is readable by anyone who is signed in; it names no company records.
    assert api.get("/api/v2/catalog", headers=quinn).status_code == 200


def test_the_owner_saves_a_revisioned_choice_and_a_stale_editor_is_refused(environment):
    api = environment(seed={}, enabled_providers=())
    read = api.get("/api/v2/providers", headers=signed_in()).json()
    assert not read["configured"] and read["revision"] == 0
    # No model to sign in to before a provider is chosen: the config carries no runtime until then.
    assert api.get("/api/v2/config", headers=signed_in()).json()["default_runtime"] == ""
    saved = api.put("/api/v2/providers", headers=signed_in(), json={
        "enabled": ["anthropic", "google"], "expected_revision": 0})
    assert saved.status_code == 200, saved.text
    assert saved.json()["default"] == {"runtime": "claude", "model": "claude-opus-5-5"}
    assert saved.json()["revision"] == 1
    stale = api.put("/api/v2/providers", headers=signed_in(), json={"enabled": ["openai"], "expected_revision": 0})
    assert stale.status_code == 409
    config = api.get("/api/v2/config", headers=signed_in()).json()
    assert config["providers_configured"] is True and config["default_runtime"] == "claude"
    models = api.get("/api/v2/models", headers=signed_in()).json()
    assert models["enabled_providers"] == ["anthropic", "google"]
    assert models["default"]["model"] == "claude-opus-5-5"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE action='providers.updated'").fetchone()[0] == 1


def test_every_shipped_template_saves_as_the_wizard_sends_it(environment):
    """The wizard sends each picked bot's instructions back as the catalog rendered them (ui/app/welcome.js). A shipped
    AGENT.md longer than the setup save accepts stopped every owner on the team chart (BotOps, 24,048 characters,
    against 20,000). The limit is the API's own: the save itself, and the model "Add from catalog" posts."""
    from backend.models import BotDefinitionCreate
    api = environment(catalog_dir=Settings.catalog_dir)
    cards = api.get("/api/v2/templates", headers=signed_in()).json()["cards"]
    assert any(card["template"] == "botops" for card in cards)
    saved = api.put("/api/v2/setup", headers=signed_in(), json={
        "names": {"company_name": "Acme", "app_name": "Atlas", "assistant_name": "Morgan"}, "answers": {},
        "selected": {card["slug"]: {"template": card["template"], "display_name": card["name"],
                                    "instructions": card["instructions"]} for card in cards}})
    assert saved.status_code == 200, saved.text[:500]
    assert len(saved.json()["selected"]) == len(cards)
    for card in cards:
        BotDefinitionCreate(display_name=card["name"], template=card["template"], instructions=card["instructions"])


def test_the_team_is_created_with_no_provider_and_no_computer_and_starts_once_they_exist(environment):
    api = environment(seed={}, enabled_providers=())
    draft(api, selected={"support": {"template": "support", "display_name": "Support", "instructions": ""}})
    done = build_team(api)
    assert {"botops", "support"} <= {row["slug"] for row in done["bots"]}
    with api.app.state.store.read() as c:
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot='support'").fetchone()[0])
        assert not c.execute("SELECT 1 FROM assignments").fetchone()
    assert not {"model", "runtime"} & set(config)                # it follows the company default, once there is one
    assert _states(api)["botops"] == "planned"                   # nothing is placed or running yet
    machine(api)                                                 # a computer appears: the bots are placed
    assert _states(api)["botops"] == "active"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM assignments WHERE bot='support'").fetchone()
    api.put("/api/v2/providers", headers=signed_in(), json={"enabled": ["openai"], "expected_revision": 0})
    with api.app.state.store.read() as c:                        # a provider is added: the bot resolves to it
        assert providers.bot_choice(c, api.app.state.store.settings, config)[0] == "codex"


def _states(api):
    with api.app.state.store.read() as c:
        return {row["slug"]: row["state"] for row in c.execute("SELECT slug,state FROM bots")}


def test_completing_onboarding_activates_the_bootstrap_bots_once_a_machine_hosts_them(environment):
    api = environment(seed={})
    machine(api)
    draft(api, selected={"support": {"template": "support", "display_name": "Support", "instructions": ""},
                         "coo": {"template": "assistant", "display_name": "Morgan", "instructions": ""}})
    build_team(api)
    states = _states(api)
    assert states["botops"] == "active" and states["coo"] == "active"
    assert states["support"] == "planned"          # activating what BotOps builds stays a person's call
    task = api.post("/api/v2/tasks", json={"owner": "botops", "title": "Look at this", "body": "x"},
                    headers=signed_in())
    assert task.status_code == 200, task.text


LIBRARIAN_CARD = {
    "template": "librarian", "slug": "librarian", "name": "Librarian", "required": True,
    "bootstrap": True, "summary": "Answers from the docs.", "owns": ["the map"], "never": ["invents"],
    "reasoning_effort": "medium", "recommend_when": ["always"]}
LIBRARIAN_MANIFEST = {"name": "librarian", "schedules": [{
    "id": "refresh-the-map", "title": "Refresh the map", "cron": "30 3 * * *", "timezone": "America/Los_Angeles",
    "template": "playbooks/refresh-the-map.md"}]}


def _routines(api):
    with api.app.state.store.read() as c:
        return [dict(row) for row in c.execute(
            "SELECT id,cron,playbook,deleted_at FROM schedules WHERE bot='librarian'")]


def test_the_librarian_is_always_built_with_its_daily_routine_seeded_once(environment):
    api = environment(seed={}, cards=[(ASSISTANT_CARD, ASSISTANT_AGENT), (BOTOPS_CARD, ""), (LIBRARIAN_CARD, "# L\n")])
    machine(api)
    folder = Path(api.app.state.store.settings.catalog_dir) / "librarian"
    (folder / "playbooks").mkdir()
    (folder / "employee.yaml").write_text(yaml.safe_dump(LIBRARIAN_MANIFEST))
    (folder / "playbooks/refresh-the-map.md").write_text("Refresh {{company_name}}'s map of the docs.\n")
    draft(api)
    record = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).json()
    assert sorted(row["slug"] for row in record["bots"]) == ["botops", "coo", "librarian"]
    assert all(row["setup_task_id"] is None for row in record["bots"])         # the runner sets it up, not BotOps
    assert _states(api)["librarian"] == "active"
    (routine,) = _routines(api)
    assert routine["id"] == "librarian:refresh-the-map" and routine["cron"] == "30 3 * * *"
    assert routine["playbook"].strip() == "Refresh Acme's map of the docs."      # the company's words, filled in
    api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert len(_routines(api)) == 1                                              # twice is still one
    with api.app.state.store.transaction() as c:                                 # a person deleted it
        c.execute("UPDATE schedules SET deleted_at=? WHERE bot='librarian'", (H.now(),))
    api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert [r["deleted_at"] is not None for r in _routines(api)] == [True]       # and it stays deleted


def _botops_goals(api):
    with api.app.state.store.read() as c:
        return [dict(row) for row in c.execute("SELECT * FROM goals WHERE owner='bot:botops'")]


def test_existing_builtin_goals_survive_upgrade(environment):
    api = environment()
    assert _botops_goals(api) == []
    made = api.post("/api/v2/goals", json={"title": "Own goal", "owner": "botops"}, headers=signed_in())
    assert made.status_code == 200, made.text
    api.app.state.store.initialize(seed_market=False)
    assert [g["title"] for g in _botops_goals(api)] == ["Own goal"]


@pytest.mark.parametrize("state,draining", [("paused", 1)])
def test_enrollment_places_paused_and_draining_bots_but_skips_archived(environment, state, draining):
    from backend import onboarding
    api = environment(seed={"ana": {"name": "Ana", "status": "active", "template": "assistant"},
                            "sam": {"name": "Sam", "status": "archived", "template": "assistant"},
                            "waiting": {"name": "Waiting", "status": state, "template": "assistant"}})
    placement = api.app.state.execution.runner_enrolled.__self__
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_control(bot,draining) VALUES('waiting',?)", (draining,))
        if draining:
            H.event(c, "system:deploy", "bot.drain", "waiting", {"release": "test-release"})
        record = onboarding.load(c)
        record["completed"] = H.now()
        placement._store(c, record, placement.auth.owner_identity(c).actor)
    computer = machine(api)
    with api.app.state.store.transaction() as c:
        assert placement.assign_pending(c, computer["runner_id"]) == []
        rows = c.execute("SELECT bot,runner_id FROM assignments ORDER BY bot").fetchall()
        assert [(row["bot"], row["runner_id"]) for row in rows] == [
            ("ana", computer["runner_id"]), ("waiting", computer["runner_id"])]
        assert c.execute("SELECT state FROM bots WHERE slug='sam'").fetchone()[0] == "archived"
        assert c.execute("SELECT state FROM bots WHERE slug='waiting'").fetchone()[0] == state
        assert c.execute("SELECT draining FROM bot_control WHERE bot='waiting'").fetchone()[0] == draining


def test_the_wizard_is_told_when_the_team_name_is_only_the_servers_stand_in(tmp_path):
    """No TICO_TEAM_NAME leaves "Company", which the first-run wizard must not offer as the team's name (Dev 237)."""
    unnamed = Settings(db_path=tmp_path / "a.db").environment()
    assert unnamed["company_name"] == "Company" and unnamed["company_name_default"] is True
    named = Settings(db_path=tmp_path / "b.db", company_name="Acme").environment()
    assert named["company_name"] == "Acme" and named["company_name_default"] is False


COUNSEL_CARD = {
    "template": "tico-counsel", "slug": "tico-counsel", "name": "Counsel", "required": True, "bootstrap": True,
    "kind": "helper", "summary": "Reads documents for Rules.", "owns": ["suggested rules"], "never": ["advises"],
    "reasoning_effort": "high", "recommend_when": ["never"], "private_tasks_default": True}


def test_a_companys_own_bot_named_counsel_is_never_taken_for_the_built_in_counsel(environment):
    # A legal team's own bot, still being set up: planned and on no computer, which is the state a built-in is adopted in.
    own = {"name": "counsel", "status": "planned", "display_name": "Legal Desk"}
    api = environment(seed={**SEED, "counsel": own},
                      cards=[(ASSISTANT_CARD, ASSISTANT_AGENT), (BOTOPS_CARD, ""), (COUNSEL_CARD, "# Counsel\n")])

    def theirs():
        with api.app.state.store.read() as c:
            return (dict(c.execute("SELECT * FROM bots WHERE slug='counsel'").fetchone()),
                    dict(c.execute("SELECT * FROM bot_config WHERE bot='counsel'").fetchone()),
                    c.execute("SELECT COUNT(*) FROM assignments WHERE bot='counsel'").fetchone()[0])
    before = theirs()
    machine(api)
    draft(api)
    assert api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).status_code == 200
    with api.app.state.store.transaction() as c:
        api.app.state.onboarding.ensure_counsel(c)
    assert theirs() == before
    assert _states(api)[H.COUNSEL] == "active"
    assert not api.app.state.auth.system_bot("counsel") and api.app.state.auth.system_bot(H.COUNSEL)
    # The built-in still works: a can-I question is its task, not theirs.
    asked = api.post("/api/v2/rules/ask", json={"question": "Can I text customers on Sunday?"}, headers=signed_in())
    assert asked.status_code == 200, asked.text
    task = api.get("/api/v2/tasks/" + asked.json()["task_id"], headers=signed_in()).json()
    assert (task.get("task") or task)["owner"] == "bot:" + H.COUNSEL
