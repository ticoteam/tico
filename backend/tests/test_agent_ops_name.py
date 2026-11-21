"""Agent Ops was called BotOps (#4863). Its slug, name, template and repository follow the new name; its id stays
`botops`, so every actor string and history row still names it. An existing company is renamed by a migration on its
next start, a new one is seeded under the new name, and the old name is left only where it is that id."""

import io
import re
import subprocess
import tokenize
from pathlib import Path

import yaml

from backend import team_rules
from backend import bot_settings as BS
from backend.slugs import slugify
from backend.store import H
from clients import catalog

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend/migrations/20261121173732_agent_ops_name.sql"
# Where the old name may still be written. Shipped migrations are never edited (and the new one renames the old rows);
# the runner names a bot by its id in its prompt, so its expected prompt for this bot's turn says botops.
ALIAS_PATHS = ("backend/migrations/", "backend/tests/test_agent_ops_name.py", "runner-rs/src/testdata/prompt-a2.txt")
# The Gateway's outbound count for the role it calls botops (backend/suggestions.py COUNTS).
ALIAS_WORDS = ("bots_botops",)
OLD = re.compile(r"(?i)botops")
# The id itself, outside Python: quoted ("botops", `botops`), an actor (bot:botops), a route or repository (bots/botops,
# ticoteam/botops) or a stored event name (botops.request_carried).
ID_OUTSIDE_PYTHON = re.compile(r"""(?:["'`/]|bot:)botops(?![\w-])|(?<![\w-])botops(?=["'`]|\.[a-z_])""")
STRINGS = {"STRING", "FSTRING_MIDDLE", "TSTRING_MIDDLE"}
# Cards whose name is not their slug: a person names the Assistant, and "&" has no slug.
NAMED_OTHERWISE = {"assistant", "fpa-analyst", "segment-marketing-owner"}


def _whole_id(text, at):
    """Whether the match at `at` is the lowercase id on its own, not part of a longer name."""
    word = text[at : at + 6]
    before = text[at - 1] if at else ""
    after = text[at + 6 : at + 7]
    return word == "botops" and not re.match(r"[\w-]", before) and not re.match(r"[\w-]", after)


def _python(text):
    """The lines where the old name is anything but the id: inside a string, or quoted in a comment."""
    bad = []
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        kind = tokenize.tok_name[token.type]
        if kind == "COMMENT" and OLD.search(token.string):
            bad += [token.start[0]] if _other(token.string) else []
            continue
        for found in OLD.finditer(token.string):
            if kind in STRINGS and _whole_id(token.string, found.start()):
                continue
            bad.append(token.start[0] + token.string.count("\n", 0, found.start()))
    return bad


def _other(text):
    bad = []
    for number, line in enumerate(text.splitlines(), 1):
        allowed = {m.start() + m.group(0).lower().index("botops") for m in ID_OUTSIDE_PYTHON.finditer(line)}
        if any(found.start() not in allowed for found in OLD.finditer(line)):
            bad.append(number)
    return bad


def test_the_old_name_is_left_only_as_the_id():
    files = subprocess.run(
        ["git", "grep", "-l", "-I", "-i", "botops"], cwd=ROOT, capture_output=True, text=True
    ).stdout.split()
    offenders = []
    for name in files:
        if name.startswith(ALIAS_PATHS):
            continue
        text = (ROOT / name).read_text(encoding="utf-8")
        for word in ALIAS_WORDS:
            text = text.replace(word, "x" * len(word))
        lines = _python(text) if name.endswith(".py") else _other(text)
        offenders += [f"{name}:{n}: {text.splitlines()[n - 1].strip()[:120]}" for n in lines]
    assert not offenders, "BotOps is Agent Ops (#4863); its id `botops` only in strings:\n" + "\n".join(offenders)


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


def test_a_new_company_gets_agent_ops_under_its_first_id(company):
    registry = yaml.safe_load((ROOT / "templates/environment-registry/employees.yaml").read_text())
    entry = next(e for e in registry["employees"] if e.get("slug") == H.AGENT_OPS_SLUG)
    assert (entry["name"], entry["display_name"], entry["repo"]) == (H.FLEET_MAINTAINER, "Agent Ops", "bot-agent-ops")
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
