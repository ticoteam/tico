"""Agent Ops was called BotOps (#4863). Its slug, name, template and repository follow the new name; its id stays
`botops`, so every actor string, permission and handoff still names it. An existing company is renamed by a migration
on its next start, a new one is seeded under the new name, and what older computers, files and saved requests send
still works."""

import json
from pathlib import Path

import yaml

from backend import agent_ops_act, runner_versions, team_rules
from backend import company_file as C
from backend import bot_settings as BS
from backend.slugs import slugify
from backend.store import H
from backend import models as M
from backend.tests.helpers.api import assign, get, headers, post, put, runner
from backend.tests.helpers.template_renames import employees_file
from backend.tests.helpers.member_bots import agent_ops, turn  # noqa: F401  (the fixture)
from clients import catalog
from runner_support import catalog_job

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend/migrations/20261121173732_agent_ops_name.sql"


def test_its_own_routes_are_its_ids_whatever_holds_the_slug():
    ours, theirs = {"agent-ops": H.FLEET_MAINTAINER}.get, {"agent-ops": "0195c3a2-custom"}.get
    for method, rest in (("GET", ""), ("PATCH", "/definition"), ("POST", "/repositories")):
        by_id, by_slug = ("/api/v2/bots/" + ref + rest for ref in (H.FLEET_MAINTAINER, H.AGENT_OPS_SLUG))
        assert agent_ops_act.default_delegable(method, by_id, {}) is False  # its own route: never as a person
        assert agent_ops_act.default_delegable(method, by_slug, {}, resolve=ours) is False
        # A company's own bot that holds the slug is never taken for Agent Ops.
        assert agent_ops_act.by_id(by_slug, theirs) == "/api/v2/bots/0195c3a2-custom" + rest
        assert not agent_ops_act.OWN.fullmatch(agent_ops_act.by_id(by_slug, theirs))


def test_every_computer_is_sent_the_template_by_the_name_all_of_them_know():
    assert runner_versions.wire_template({"template": H.AGENT_OPS_SLUG, "runtime": "fake"})["template"] == "botops"
    assert runner_versions.wire_template({"template": "librarian"}) == {"template": "librarian"}
    # Its refresh report counts for Agent Ops under either name, and a newer runner reads the old name as the new one.
    assert runner_versions.reported_template("botops") == runner_versions.reported_template("agent-ops") == "agent-ops"
    cards = {H.AGENT_OPS_SLUG: {"bootstrap": True}}
    assert catalog_job.BotSetup.refresh_template(None, "botops", {"template": "botops"}, cards) == ("agent-ops", "")


def test_its_assignment_names_the_old_template_in_config_and_config_json_alike(api):
    post(api, "bots/register", {"template": "agent-ops", "display_name": "Agent Ops"})
    r = runner(api)
    with api.app.state.store.read() as c:
        placed = c.execute("SELECT generation FROM assignments WHERE bot='botops'").fetchone()
    assign(api, r, "agent-ops", generation=placed["generation"] if placed else 0)
    (row,) = [a for a in get(api, "runners/assignments", token=r["token"]) if a["bot"] == H.FLEET_MAINTAINER]
    assert row["config"]["template"] == json.loads(row["config_json"])["template"] == "botops"


def test_a_fresh_agent_ops_checkout_from_its_template_is_the_config_its_runner_is_sent(api):
    """A runner's readiness takes a bot.yaml only when its name is the bot's id, the config's name or its named folder
    (runner-rs readiness.rs configuration_valid). A fresh install's seeded Agent Ops can store neither a name nor a
    folder, and its catalog bot.yaml names its slug, not its id: its assignment sends that slug as the name, in config
    and config_json alike, or the install shows it as "Configuration differs from server" and it never runs a turn."""
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(id,slug,display_name,state) VALUES('botops','agent-ops','Agent Ops','active')")
        c.execute("INSERT INTO bot_config(bot,owner_id) VALUES('botops','ana')")
        BS.write(c, "botops", {"runtime": "fake", "status": "active", "template": "agent-ops"})
    machine = runner(api)
    with api.app.state.store.read() as c:
        placed = c.execute("SELECT generation FROM assignments WHERE bot='botops'").fetchone()
    assign(api, machine, "botops", generation=placed["generation"] if placed else 0)
    (row,) = [a for a in get(api, "runners/assignments", token=machine["token"]) if a["bot"] == H.FLEET_MAINTAINER]
    checkout = yaml.safe_load((ROOT / "templates/catalog/agent-ops/bot.yaml").read_text())
    assert checkout["name"] != H.FLEET_MAINTAINER  # its id alone would not match
    assert row["config"]["name"] == json.loads(row["config_json"])["name"] == checkout["name"] == H.AGENT_OPS_SLUG
    # A name the server stores is the one it sends.
    with api.app.state.store.transaction() as c:
        BS.update(c, "botops", {"name": "botops"})
    (row,) = [a for a in get(api, "runners/assignments", token=machine["token"]) if a["bot"] == H.FLEET_MAINTAINER]
    assert row["config"]["name"] == json.loads(row["config_json"])["name"] == "botops"


def test_an_old_company_file_reads_the_new_name_unless_a_person_chose_one():
    rows = [
        {"id": "ops", "template": "botops", "title": "BotOps", "department": "engineering"},
        {"id": "fixer", "template": "botops", "title": "Fleet Fixer", "department": "engineering"},
    ]
    bots = {b["id"]: b for b in C.from_yaml(employees_file(rows))["bots"]}
    assert (bots["ops"]["template"], bots["ops"]["title"]) == ("agent-ops", "Agent Ops")
    assert bots["fixer"]["title"] == "Fleet Fixer"


def test_a_registered_agent_ops_takes_its_slug_and_a_second_is_refused(api):
    body = {"template": "agent-ops", "display_name": "Agent Ops"}
    made = post(api, "bots/register", body)
    assert (made["id"], made["slug"], made["created"]) == ("botops", "agent-ops", True)
    post(api, "bots/register", body, expected=409)
    post(api, "bots/register", {**body, "display_name": "Ops Two"}, expected=409)


def _migrate(c):
    sql = "\n".join(line for line in MIGRATION.read_text().splitlines() if not line.startswith("--"))
    for statement in filter(str.strip, sql.split(";")):
        c.execute(statement)


def test_an_existing_company_is_renamed_on_start(company):
    with company.transaction() as c:
        H.sync_registry(c, {"botops": {"display_name": "BotOps", "status": "active"}}, [])
        c.execute("INSERT INTO bot_config(bot,owner_id) VALUES('botops','ana')")
        BS.write(c, "botops", {"template": "botops", "runtime": "fake"})
        c.execute(
            "INSERT INTO registry_metadata VALUES('rules',?)",
            ('{"botops_direct": false, "botops_manages_bots": true, "admin_sql": false}',),
        )
        assert H.bot(c, "botops")["slug"] == "botops"
        _migrate(c)
        _migrate(c)  # the next start changes nothing
        found = H.resolve_bot(c, "agent-ops")
        assert found == H.resolve_bot(c, "botops") == H.resolve_bot(c, "bot:botops")
        assert (found["id"], found["slug"], found["display_name"]) == ("botops", "agent-ops", "Agent Ops")
        assert BS.read(c, "botops")["template"] == "agent-ops"
        rules = team_rules.load(c)
        assert (rules["agent_ops_direct"], rules["agent_ops_manages_bots"], rules["admin_sql"]) == (False, True, False)
        stored = c.execute(
            "SELECT value, json_type(value,'$.agent_ops_direct') AS a, json_type(value,'$.agent_ops_manages_bots') AS b "
            "FROM registry_metadata WHERE key='rules'"
        ).fetchone()
        assert "botops_direct" not in stored["value"] and (stored["a"], stored["b"]) == ("false", "true")  # still JSON


def test_a_name_a_person_chose_stays(company):
    with company.transaction() as c:
        H.sync_registry(c, {"botops": {"display_name": "Fleet Fixer", "status": "active"}}, [])
        _migrate(c)
        assert (H.bot(c, "botops")["slug"], H.bot(c, "botops")["display_name"]) == ("agent-ops", "Fleet Fixer")
        assert BS.read(c, "botops")["template"] == "agent-ops"  # a seed that gave it no template names its own now


def test_the_built_in_takes_the_slug_from_a_company_bot_that_held_it(api):
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {"botops": {"display_name": "BotOps", "status": "active"}}, [])
        c.execute("UPDATE bots SET slug='agent-ops', display_name='Ops Helper' WHERE id='ops'")
        _migrate(c)
        assert (H.bot(c, "botops")["slug"], H.bot(c, "ops")["slug"], H.bot(c, "ops")["display_name"]) == (
            "agent-ops",
            "agent-ops-2",
            "Ops Helper",
        )
        moved = c.execute("SELECT actor, detail FROM events WHERE action='bot.renamed' AND target='ops'").fetchone()
        assert moved["actor"] == H.TICO_ACTOR and json.loads(moved["detail"])["to"] == "agent-ops-2"
    # The slug stays the built-in's: no other bot is renamed to it.
    revision = get(api, "bots/ops")["revision"]
    post(api, "bots/ops/definition", {"slug": "agent-ops", "expected_revision": revision}, expected=409)


def test_a_new_companys_agent_ops_repository_is_one_tico_names_not_a_chosen_one(tmp_path):
    from backend.app import create_app
    from backend import repositories as R
    from backend.tests.helpers.fresh_install import OWNER_EMAIL, settings_for
    from clients.environments import seed_registry

    seed_registry(tmp_path, "Owner", OWNER_EMAIL, {"company_name": "Acme", "app_name": "Tico", "assistant_name": "A"})
    app = create_app(settings_for(tmp_path, github_owner="acme"))
    app.state.store.initialize()
    app.state.store.seed()
    with app.state.store.read() as c:
        assert H.bot(c, "botops")["slug"] == "agent-ops"
        assert "acme/bot-agent-ops" not in R.metadata(c, R.CHOSEN)  # the cross-install guard still applies to it
        assert BS.read(c, "botops")["template"] == "agent-ops"
    app.state.store.close()


def test_its_checkout_made_under_its_id_still_checks(tmp_path):
    from clients import preflight

    (tmp_path / "bot.yaml").write_text("name: botops\n")
    report = preflight.Report("agent-ops")
    tied = ["botops", "agent-ops"]  # the server's record of the bot: its id and its slug
    assert (
        preflight.check_manifest(report, "agent-ops", tmp_path, tied)["name"] == "botops" and not report.counts["FAIL"]
    )
    preflight.check_manifest(report, "agent-ops", tmp_path)
    assert report.counts["FAIL"] == 1  # a name nothing ties to the bot
    assert catalog_job.PUBLIC_SLUGS[H.FLEET_MAINTAINER] == H.AGENT_OPS_SLUG  # a new checkout's manifest says agent-ops


def test_a_rules_change_retried_across_the_update_matches_its_first_request(api):
    from backend.store import digest, encode

    key = "rules-retry-1"
    body = {"agent_ops_direct": False}
    first = api.put("/api/v2/access/rules", json=body, headers=headers("ana-test", key))
    assert first.status_code == 200, first.text
    legacy = {
        {"agent_ops_direct": "botops_direct", "agent_ops_manages_bots": "botops_manages_bots"}.get(k, k): v
        for k, v in M.AccessRules(**body).model_dump().items()
    }
    with api.app.state.store.transaction() as c:  # the key as a release before the rename saved it
        c.execute("UPDATE idempotency SET request_hash=? WHERE key=?", (digest(encode(legacy)), key))
    again = api.put("/api/v2/access/rules", json=body, headers=headers("ana-test", key))
    assert again.status_code == 200, again.text


def test_only_the_renamed_cards_exact_old_title_reads_the_new_name():
    from backend.company_file import draft as D

    assert D.renamed({"template": "botops", "title": "BotOps"})["title"] == "Agent Ops"
    assert D.renamed({"template": "botops", "title": "Fleet Fixer"})["title"] == "Fleet Fixer"
    assert "title" not in D.renamed({"template": "botops"})  # no default title for a row that had none
    assert D.renamed({"template": "bookkeeper"}) == {"template": "finance-owner"}


def test_a_held_slug_moves_to_the_first_free_one_even_before_the_built_in_exists(company):
    with company.transaction() as c:
        for bot, slug in (("mine", "agent-ops"), ("two", "agent-ops-2"), ("agent-ops-3", "helper")):
            c.execute("INSERT INTO bots(id,slug,display_name,state) VALUES(?,?,?,'active')", (bot, slug, bot.title()))
        _migrate(c)
        assert (H.bot(c, "mine")["slug"], H.bot(c, "mine")["display_name"]) == ("agent-ops-4", "Mine")
        moved = c.execute("SELECT detail FROM events WHERE action='bot.renamed' AND target='mine'").fetchone()
        assert json.loads(moved["detail"])["to"] == "agent-ops-4"


def test_a_registry_bot_whose_id_is_agent_ops_keeps_it_and_the_built_in_keeps_its_old_slug(company):
    with company.transaction() as c:
        H.sync_registry(c, {"botops": {"display_name": "BotOps", "status": "active"}}, [])
        H.sync_registry(c, {"agent-ops": {"display_name": "Ops", "status": "active"}}, [])
        _migrate(c)
        assert H.bot(c, "agent-ops")["slug"] == "agent-ops"  # not moved for nothing
        assert not c.execute("SELECT 1 FROM events WHERE action='bot.renamed' AND target='agent-ops'").fetchone()
        assert H.bot(c, "botops")["slug"] == "botops"  # no slug may name another bot's id
        kept = c.execute("SELECT detail FROM events WHERE action='bot.slug_kept' AND target='botops'").fetchone()
        assert json.loads(kept["detail"])["slug"] == "botops"


def test_only_the_agent_ops_template_takes_its_slug_made_or_not(api):
    other = {"display_name": "Ops", "slug": "agent-ops", "model": "hermes-profile", "harness": "hermes"}
    post(api, "bots", {**other, "effort": "as-configured"}, expected=409)
    post(api, "bots/register", {"slug": "agent-ops", "display_name": "Ops"}, expected=409)
    with api.app.state.store.read() as c:
        assert H.resolve_bot(c, "agent-ops") is None and H.bot(c, "botops") is None


def test_a_claimed_turn_names_the_old_template_too(api, agent_ops):
    with api.app.state.store.transaction() as c:
        BS.update(c, "botops", {"template": "agent-ops"})
    assert turn(api, agent_ops)["config"]["template"] == "botops"
    (row,) = [a for a in get(api, "runners/assignments", token=agent_ops["token"]) if a["bot"] == "botops"]
    assert row["config"]["template"] == json.loads(row["config_json"])["template"] == "botops"


def test_a_route_is_classified_as_sent_and_resolved_only_for_its_own_routes():
    lookup = {"register": "0195c3a2-custom"}.get  # a bot whose slug happens to be a route's word
    for method, path in (("POST", "/api/v2/bots/register"), ("GET", "/api/v2/bots/register")):
        assert agent_ops_act.default_delegable(method, path, {}, resolve=lookup) is agent_ops_act.default_delegable(
            method, path, {}
        )


def test_a_malformed_manifest_name_fails_the_check_plainly(tmp_path):
    from clients import preflight

    (tmp_path / "bot.yaml").write_text("name: [agent-ops]\n")
    report = preflight.Report("agent-ops")
    preflight.check_manifest(report, "agent-ops", tmp_path)
    assert report.counts["FAIL"] == 1


def test_a_company_bot_whose_id_is_agent_ops_keeps_its_managers_and_the_built_in_is_made_beside_it(api):
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {"agent-ops": {"display_name": "Ops", "status": "active"}}, [])
    auth = api.app.state.auth
    assert not auth.system_bot("agent-ops") and auth.system_bot(H.FLEET_MAINTAINER)  # classified by permanent id
    body = {"display_name": "Agent Ops", "template": "agent-ops", "model": "hermes-profile", "harness": "hermes"}
    made = post(api, "bots", {**body, "effort": "as-configured"})
    assert (made["id"], made["slug"]) == ("botops", "botops")  # no slug may name the other bot's id
    with api.app.state.store.read() as c:
        assert H.bot(c, "agent-ops")["slug"] == "agent-ops"


def test_the_handoffs_the_server_writes_address_its_permanent_id(company):
    from backend import why_here

    with company.read() as c:
        (_, fix) = why_here.stops_for(c, {"type": "bot", "bot": "bot:ops"}, {"title": "Daily totals"})
    assert (
        fix["command"].startswith("tico task create --owner botops ")
        and fix["how"] == "Have Agent Ops fix the bot's source"
    )
    jobs = (ROOT / "templates/catalog/assistant/playbooks/jobs.md").read_text()
    assert "tico task create --owner botops" in jobs and "--owner agent-ops" not in jobs


def test_a_setup_pick_never_turns_agent_ops_into_another_template(api):
    from backend import onboarding as O

    onboarding = api.app.state.onboarding
    cards = {card["template"]: card for card in O.read_cards(onboarding.settings)}
    with api.app.state.store.read() as c:
        record = O.load(c)
    names = O.display_names(onboarding.settings, record)
    for pick, kept in (
        ({"template": "support-owner", "display_name": "Support", "instructions": ""}, "Agent Ops"),
        ({"template": "botops", "display_name": "Fleet Fixer", "instructions": ""}, "Fleet Fixer"),
    ):
        plan = onboarding._plan({**record, "selected": {"botops": pick}}, cards, names)
        assert (plan["botops"]["template"], plan["botops"]["display_name"]) == ("agent-ops", kept)


def _picked(c, choice):
    c.execute(
        "UPDATE registry_metadata SET value=json_set(value, '$.selected.botops', json(?)) WHERE key='onboarding'",
        (json.dumps(choice),),
    )
    _migrate(c)
    return json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='onboarding'").fetchone()[0])["selected"]


def test_setup_completed_again_builds_it_under_the_new_name_and_keeps_a_chosen_one(api):
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {"botops": {"display_name": "BotOps", "status": "active"}}, [])
        old = {"template": "botops", "display_name": "BotOps", "instructions": "Mind the fleet."}
        assert _picked(c, old)["botops"] == {**old, "template": "agent-ops", "display_name": "Agent Ops"}
        chosen = {"template": "botops", "display_name": "Fleet Fixer", "instructions": ""}
        assert _picked(c, chosen)["botops"] == {**chosen, "template": "agent-ops"}
        # Setup keeps the pick under the bot's id, and the catalog reads it there.
        card = next(card for card in api.app.state.onboarding.catalog(c) if card["template"] == H.AGENT_OPS_SLUG)
        assert card["name"] == "Fleet Fixer"


def test_one_agent_ops_a_second_from_its_template_is_refused(api):
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {"botops": {"slug": "agent-ops", "display_name": "Agent Ops", "status": "active"}}, [])
        c.execute("INSERT INTO bot_config(bot,owner_id) VALUES('botops','ana')")
    body = {"display_name": "Agent Ops", "template": "agent-ops", "model": "hermes-profile", "harness": "hermes"}
    post(api, "bots", {**body, "effort": "as-configured"}, expected=409)
    with api.app.state.store.read() as c:
        assert [r["id"] for r in H.bots(c) if r["slug"].startswith("agent-ops")] == ["botops"]


def test_a_confirm_card_saved_with_the_old_rule_names_still_runs(api):
    assert agent_ops_act.own_powers({"botops_direct": False})  # always the person's own tap, under either name
    put(api, "access/rules", {"botops_direct": False, "botops_manages_bots": False})
    with api.app.state.store.read() as c:
        rules = team_rules.load(c)
    assert (rules["agent_ops_direct"], rules["agent_ops_manages_bots"]) == (False, False)
    with api.app.state.store.transaction() as c:
        assert team_rules.save(c, "human:ana", {"botops_direct": True})["agent_ops_direct"] is True


def test_a_new_company_gets_agent_ops_under_its_first_id(company):
    registry = yaml.safe_load((ROOT / "templates/environment-registry/employees.yaml").read_text())
    entry = next(e for e in registry["employees"] if e.get("slug") == H.AGENT_OPS_SLUG)
    assert (entry["name"], entry["display_name"], entry["repo"]) == (H.FLEET_MAINTAINER, "Agent Ops", "bot-agent-ops")
    assert entry["template"] == H.AGENT_OPS_SLUG  # an older computer otherwise falls back to its own copy
    with company.transaction() as c:
        H.sync_registry(c, {entry["name"]: entry}, [])
        found = H.resolve_bot(c, "agent-ops")
        assert (found["id"], found["slug"], found["display_name"]) == ("botops", "agent-ops", "Agent Ops")
    assert H.new_bot_id(H.AGENT_OPS_SLUG) == H.FLEET_MAINTAINER  # made from its card, it keeps the id too


def test_its_slug_matches_its_name():
    card = next(card for card in catalog.cards() if card["template"] == H.AGENT_OPS_SLUG)
    assert card["slug"] == card["template"] == slugify(card["name"]) == H.AGENT_OPS_SLUG and card["required"]
    registry = yaml.safe_load((ROOT / "templates/environment-registry/employees.yaml").read_text())
    entry = next(e for e in registry["employees"] if e.get("slug") == H.AGENT_OPS_SLUG)
    assert slugify(entry["display_name"]) == entry["slug"]
