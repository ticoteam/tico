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

from backend import agent_ops_act, team_rules
from backend import bot_settings as BS
from backend.slugs import slugify
from backend.store import H
from backend.tests.helpers.api import post, put
from clients import catalog

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
# (team_rules.OLD_NAMES).
ALIAS_WORDS = ("bots_botops", "botops_direct", "botops_manages_bots")
OLD = re.compile(r"(?i:botops)|\bBot[ -]Ops\b")
# The id itself: quoted ("botops", `botops`, a SQL or keyword value =botops), an actor (bot:botops), a route or
# repository (bots/botops, ticoteam/botops), a stored event name (botops.request_carried), or a handoff that must reach
# the built-in even when a company's own bot holds the slug agent-ops (--owner botops, @botops, message send botops).
ID = re.compile(r"""(?:["'`/=]|bot:|--owner |@|message send )botops(?![\w-])|(?<![\w-])botops(?=["'`]|\.[a-z_])""")

# Cards whose name is not their slug: a person names the Assistant, and "&" has no slug.
NAMED_OTHERWISE = {"assistant", "fpa-analyst", "segment-marketing-owner"}


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
        for word in ALIAS_WORDS:
            text = text.replace(word, "x" * len(word))
        lines = _python(text) if name.endswith(".py") else _bad_lines(text)
        offenders += [f"{name}:{n}: {text.splitlines()[n - 1].strip()[:120]}" for n in sorted(set(lines))]
    assert not offenders, "BotOps is Agent Ops (#4863); `botops` only as its id:\n" + "\n".join(offenders)


def test_its_own_routes_read_the_same_by_id_and_by_slug():
    for method, rest in (("GET", ""), ("PATCH", "/definition"), ("POST", "/repositories")):
        by_id, by_slug = ("/api/v2/bots/" + ref + rest for ref in (H.FLEET_MAINTAINER, H.AGENT_OPS_SLUG))
        assert agent_ops_act.default_delegable(method, by_id, {}) is agent_ops_act.default_delegable(
            method, by_slug, {}
        )
        assert agent_ops_act.OWN.fullmatch(by_id) and agent_ops_act.OWN.fullmatch(by_slug)


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
        assert "botops_direct" not in c.execute("SELECT value FROM registry_metadata WHERE key='rules'").fetchone()[0]


def test_a_name_a_person_chose_stays(company):
    with company.transaction() as c:
        H.sync_registry(c, {"botops": {"display_name": "Fleet Fixer", "status": "active"}}, [])
        _migrate(c)
        assert (H.bot(c, "botops")["slug"], H.bot(c, "botops")["display_name"]) == ("agent-ops", "Fleet Fixer")
        assert BS.read(c, "botops")["template"] == "agent-ops"  # a seed that gave it no template names its own now


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


def test_slugs_match_names():
    cards = {card["template"]: card for card in catalog.cards()}
    assert cards[H.AGENT_OPS_SLUG]["name"] == "Agent Ops" and cards[H.AGENT_OPS_SLUG]["required"]
    unmatched = [
        (template, card["slug"], card["name"])
        for template, card in cards.items()
        if template not in NAMED_OTHERWISE and not (card["slug"] == template == slugify(card["name"]))
    ]
    assert not unmatched
