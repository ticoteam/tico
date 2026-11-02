"""`hub bot check-names` (clients/bot_names.py): which old names it flags in a bot's own files, what --fix rewrites, and
what it leaves alone. The fake bot holds the words it must not touch (an owner, a created field of another system, a
status page, a `hub notice list`) next to the names it must change."""

import json
from pathlib import Path

from backend.migrations import renames_data
from clients import bot_names, hubcli

MAP = Path(bot_names.MAP)

FILES = {
    "AGENT.md": (
        "# Acme bot\n"
        "\n"
        "The owner decides. Nothing created yet; the hub status page is green.\n"
        "Run `hub sql \"SELECT id, refs_json, created FROM tasks WHERE owner = 'x' AND state_json IS NULL\"` to read.\n"
        "Tasks are `hub say hello`. `hub notice list` shows notices; `hub routine set --text-file p.txt` schedules.\n"
    ),
    "playbooks/check.md": (
        "Read the board:\n"
        "\n"
        "```sh\n"
        "hub sql \"SELECT owner, created, started_at FROM tasks\"\n"
        "hub sql \"SELECT count(*) AS created FROM tasks\"\n"
        "hub meetings granola status\n"
        "```\n"
    ),
    "scripts/report.py": (
        "import subprocess\n"
        "refs = row[\"refs_json\"]\n"
        "rid = who.get(\"attempt_id\")\n"
        "attempts = {\"attempt\": 1}\n"
        "subprocess.run([\"hub\", \"sql\", \"SELECT created FROM tasks\"])\n"
        "print(\"the hub status page is up\", row.attempt_id)\n"
        "NOTE = \"\"\"\n"
        "hub tools call these.\n"
        "\"\"\"\n"
        "TOOLS = {\"hub\", \"docs\", \"web\"}\n"
    ),
    "memory/notes.md": "`hub sql \"SELECT created FROM tasks\"`\n",
    "knowledge/notes.md": "The Stripe `created` field is epoch seconds. Use `snoozed_until` never.\n",
}


def write_bot(root):
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def found(root):
    return sorted((f.path, f.line, f.old, f.kind, f.fixable) for f in bot_names.scan(root, bot_names.load_names()))


def test_flags_the_old_names_in_code_and_not_the_words_that_look_like_them(tmp_path):
    root = write_bot(tmp_path / "bot-acme")
    assert found(root) == sorted([
        ("AGENT.md", 4, "refs_json", "sql", True),
        ("AGENT.md", 4, "created", "sql", True),
        ("AGENT.md", 4, "state_json", "sql", True),
        ("AGENT.md", 5, "hub say", "command", True),
        ("AGENT.md", 5, "hub routine", "removed", False),
        ("playbooks/check.md", 4, "created", "sql", True),
        ("playbooks/check.md", 5, "created", "sql", False),         # `AS created` names an alias, not the column
        ("playbooks/check.md", 6, "hub meetings", "command", True),
        ("memory/notes.md", 1, "created", "sql", False),            # a bot's notes are reported, never rewritten
        ("scripts/report.py", 2, "refs_json", "key", True),
        ("scripts/report.py", 3, "attempt_id", "key", True),
        ("scripts/report.py", 5, "created", "sql", True),
        ("scripts/report.py", 6, "attempt_id", "key", True),
    ])


def test_fix_rewrites_the_unambiguous_names_only_and_a_second_run_finds_the_rest(tmp_path):
    root = write_bot(tmp_path / "bot-acme")
    bot_names.fix(root, bot_names.scan(root, bot_names.load_names()))

    agent = (root / "AGENT.md").read_text()
    assert "SELECT id, refs, created_at FROM tasks WHERE owner = 'x' AND state IS NULL" in agent
    assert "Tasks are `hub message send hello`." in agent
    assert "`hub notice list`" in agent and "`hub routine set --text-file p.txt`" in agent
    check = (root / "playbooks/check.md").read_text()
    assert "SELECT owner, created_at, started_at FROM tasks" in check
    assert "count(*) AS created FROM" in check
    assert "hub meeting granola status" in check
    report = (root / "scripts/report.py").read_text()
    assert 'row["refs"]' in report and 'who.get("run_id")' in report
    assert "SELECT created_at FROM tasks" in report and "row.run_id" in report
    assert '{"attempt": 1}' in report
    assert (root / "memory/notes.md").read_text() == FILES["memory/notes.md"]
    assert (root / "knowledge/notes.md").read_text() == FILES["knowledge/notes.md"]

    left = bot_names.scan(root, bot_names.load_names())
    assert sorted((f.path, f.old) for f in left) == [
        ("AGENT.md", "hub routine"), ("memory/notes.md", "created"), ("playbooks/check.md", "created")]
    assert not any(f.fixable for f in left)


def test_the_cli_runs_here_without_a_server_and_finds_a_bot_by_slug(tmp_path, monkeypatch, capsys):
    from clients import remotecli

    def no_server(*args, **kwargs):
        raise AssertionError("check-names sent a request to the server")

    for name in ("HUB_API_URL", "HUB_TOKEN", "HUB_BOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HUB_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(remotecli, "main", no_server)
    write_bot(tmp_path / "bot-acme")

    assert hubcli.main(["bot", "check-names", "acme"]) == 1
    out = capsys.readouterr().out
    assert "AGENT.md:4: refs_json -> refs (sql)" in out
    assert "old names left" in out

    assert hubcli.main(["bot", "check-names", "acme", "--fix"]) == 1       # the unfixable ones remain
    assert "rewritten" in capsys.readouterr().out
    assert "refs_json" not in (tmp_path / "bot-acme" / "AGENT.md").read_text()


def test_the_cli_says_when_there_is_no_checkout(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("HUB_API_URL", raising=False)
    monkeypatch.setenv("HUB_WORKSPACE", str(tmp_path))
    assert hubcli.main(["bot", "check-names", "nobody"]) == 1
    assert "no checkout" in capsys.readouterr().err


def test_the_shipped_map_is_the_applied_rename_data_and_the_cli_command_table():
    shipped = json.loads(MAP.read_text())
    assert set(shipped["columns"].items()) == {(old, new) for _table, old, new, _wave in renames_data.COLUMNS}
    # hubcli sorts RENAMED by length when it loads; the order of the rules does not matter to the matching.
    cli = [{"old": list(old), "new": list(new), "fixable": not tail and rewrite is None}
           for old, new, tail, rewrite in hubcli.RENAMED]
    assert sorted(shipped["commands"], key=lambda r: r["old"]) == sorted(cli, key=lambda r: r["old"])
