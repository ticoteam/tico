"""Agent Ops was called BotOps (#4863). Its slug, name, template and repository follow the new name; its id stays
`botops`, so every actor string and history row still names it. An existing company is renamed by a migration on its
next start, a new one is seeded under the new name, and the old name is left only where it is that id."""

import io
import json
import re
import subprocess
import tokenize
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
from clients import catalog
from runner_support import catalog_job

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend/migrations/20261121173732_agent_ops_name.sql"
# Where the old name may still be written, all of it on purpose. Files: shipped migrations, which are never edited (and
# the new one renames the old rows); a red-main case is a record of what happened; the runner names a bot by its id in
# its prompt, so its expected prompt for this bot's turn says botops.
ALIAS_PATHS = (
    "backend/migrations/",
    "backend/tests/test_agent_ops_name.py",
    "scripts/red_main_cases.json",
    "runner-rs/src/testdata/prompt-a2.txt",
)
# Names other systems or saved records still use: the Gateway's outbound count for its role botops
# (backend/suggestions.py COUNTS), and the two team rules' old names a Confirm card saved before the rename sends
# (team_rules.OLD_NAMES), and the old default title an exported company file still carries (draft.RENAMED_TITLES).
ALIAS_WORDS = ("bots_botops", "botops_direct", "botops_manages_bots", 'RENAMED_TITLES = {"botops": "BotOps"}')
OLD = re.compile(r"(?i:botops)|\bBot[ -]Ops\b")
# The tasks the server files for the built-in name its id, so they reach it whatever bot holds a slug: these call
# sites, and the test that reads one back.
ALIAS_AT = {
    "backend/general_manager.py": "--owner botops",
    "backend/hubdb.py": "--owner botops",
    "backend/tests/test_maintenance_health.py": "--owner botops",
}
# The id itself: quoted ("botops", `botops`, a SQL or keyword value =botops), an actor (bot:botops), a route or
# repository (bots/botops, ticoteam/botops) or a stored event name (botops.request_carried).
ID = re.compile(r"""(?:["'`/=]|bot:)botops(?![\w-])|(?<![\w-])botops(?=["'`]|\.[a-z_])""")


def _bad_lines(text, first=1):
    """The line numbers in `text` (its first line numbered `first`) where the old name is anything but the id."""
    bad = []
    for number, line in enumerate(text.splitlines() or [""], first):
        allowed = {m.end() - 6 for m in ID.finditer(line)}
        if any(found.start() not in allowed or found.group(0) != "botops" for found in OLD.finditer(line)):
            bad.append(number)
    return bad


def _python(text):
    """In Python the id is a value, so it is only in a string; a comment may quote it."""
    bad = []
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if not OLD.search(token.string):
            continue
        if tokenize.tok_name[token.type] in ("STRING", "FSTRING_MIDDLE", "TSTRING_MIDDLE", "COMMENT"):
            bad += _bad_lines(token.string, token.start[0])
        else:
            bad.append(token.start[0])
    return bad


def test_the_old_name_is_left_only_as_the_id():
    files = subprocess.run(
        ["git", "grep", "-l", "-I", "-E", "(B|b)(O|o)(T|t)(O|o)(P|p)(S|s)|Bot[ -]Ops"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout.split()
    offenders = []
    for name in files:
        if name.startswith(ALIAS_PATHS):
            continue
        text = (ROOT / name).read_text(encoding="utf-8")
        for word in (*ALIAS_WORDS, *([ALIAS_AT[name]] if name in ALIAS_AT else [])):
            text = text.replace(word, "x" * len(word))
        lines = _python(text) if name.endswith(".py") else _bad_lines(text)
        offenders += [f"{name}:{n}: {text.splitlines()[n - 1].strip()[:120]}" for n in sorted(set(lines))]
    assert not offenders, "BotOps is Agent Ops (#4863); `botops` only as its id:\n" + "\n".join(offenders)


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
    assert preflight.check_manifest(report, "agent-ops", tmp_path)["name"] == "botops" and not report.counts["FAIL"]
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
