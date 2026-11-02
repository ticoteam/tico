"""`hub bot check-names` (clients/bot_names.py): which old names it flags in a bot's own files, what --fix rewrites, and
what it leaves alone. The fake bot holds the words it must not touch (an owner, a created field of another system, a
status page, a `hub notice list`) next to the names it must change."""

import json
import runpy
import sqlite3
from pathlib import Path

import yaml

from backend.migrations import renames_data
from clients import bot_names, hubcli

MAP = Path(bot_names.MAP)
ROOT = MAP.parents[1]

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
        ("AGENT.md", 4, "owner", "sql", False),                     # owner stays on other tables: report it
        ("AGENT.md", 4, "state_json", "sql", True),
        ("AGENT.md", 5, "hub say", "command", True),
        ("AGENT.md", 5, "hub routine", "removed", False),
        ("playbooks/check.md", 4, "owner", "sql", False),
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
    assert "SELECT id, refs, created_at FROM tasks WHERE owner = 'x' AND saved_state IS NULL" in agent
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
        ("AGENT.md", "hub routine"), ("AGENT.md", "owner"), ("memory/notes.md", "created"),
        ("playbooks/check.md", "created"), ("playbooks/check.md", "owner")]
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


def scoped_bot(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def test_a_name_renamed_on_some_tables_is_found_with_its_tables_and_stays_where_a_table_keeps_it(tmp_path):
    root = scoped_bot(tmp_path / "bot-acme", {
        "AGENT.md": (
            "`hub sql \"SELECT owner FROM tasks\"`\n"
            "`hub sql \"SELECT owner FROM meetings\"`\n"
            "`hub sql \"SELECT m.from_actor, n.from_actor FROM messages m JOIN notes n ON n.id = m.id\"`\n"
            "`hub sql \"SELECT due FROM tasks\"`\n"
            "`hub sql \"SELECT ts FROM kpi_readings UNION SELECT ts FROM events\"`\n"
        ),
        "scripts/read.py": (
            "import sqlite3\n"
            "own = sqlite3.connect('mine.db').execute(\"SELECT owner FROM tasks\")\n"
            "print(row[\"from_actor\"], row[\"owner\"])\n"
            "config = {\"owner\": 1, \"state\": 2}\n"
        ),
        "scripts/own.py": "import json\nprint(task[\"owner\"], row[\"owner\"])\n",        # no hub, no SQL: its own fields
        "scripts/api.py": (
            "import requests\n"
            "task = requests.get(BASE + '/api/v2/tasks/1').json()\n"
            "print(task[\"owner\"], task.get(\"due\"), meeting[\"owner\"], task[\"state\"])\n"
        ),
        "scripts/kept.py": "rows = run(['hub', 'sql', 'SELECT owner FROM meetings'])\nprint(row['owner'])\n",
    })
    found = {(f.path, f.line, f.old): f for f in bot_names.scan(root, bot_names.load_names())}
    assert sorted(found) == sorted([
        ("AGENT.md", 1, "owner"),
        ("AGENT.md", 3, "from_actor"),      # m.from_actor; notes keeps its own, so n.from_actor is not one
        ("AGENT.md", 4, "due"),
        ("AGENT.md", 5, "ts"),
        ("scripts/read.py", 2, "owner"),
        ("scripts/read.py", 3, "from_actor"),
        ("scripts/read.py", 3, "owner"),    # its own SQL reads tasks
        ("scripts/api.py", 3, "owner"),     # task["owner"], not meeting["owner"]
        ("scripts/api.py", 3, "due"),
    ])
    assert found[("AGENT.md", 1, "owner")].new == "owner_id"
    assert found[("AGENT.md", 1, "owner")].tables == ("tasks",)
    assert "on tasks" in found[("AGENT.md", 1, "owner")].text()
    assert found[("AGENT.md", 3, "from_actor")].new == "sender_id"
    assert found[("AGENT.md", 3, "from_actor")].tables == ("messages",)
    assert found[("scripts/read.py", 3, "from_actor")].new == "sender_id"
    assert found[("AGENT.md", 5, "ts")].new == "created_at or measured_at"

    # One spelling and no table keeping it: due, in a `hub sql` call. Everything else needs a person.
    assert sorted(key for key, f in found.items() if f.fixable) == [("AGENT.md", 4, "due")]
    bot_names.fix(root, list(found.values()))
    agent = (root / "AGENT.md").read_text()
    assert "SELECT due_at FROM tasks" in agent and "SELECT owner FROM tasks" in agent
    assert (root / "scripts/read.py").read_text().count("from_actor") == 1


def test_sql_split_over_adjacent_strings_and_writes_are_read_as_one_statement(tmp_path):
    root = scoped_bot(tmp_path / "bot-acme", {
        "scripts/q.py": (
            "A = (\"SELECT id, \"\n"
            "     \"requester FROM tasks t\")\n"
            "B = \"UPDATE tasks SET owner=?, title='owner' WHERE id=?\"\n"
            "C = \"INSERT INTO goals (owner, title) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET owner=excluded.owner\"\n"
            "D = \"SELECT a AS owner FROM tasks ORDER BY owner\"\n"
        ),
    })
    found = sorted((f.line, f.old) for f in bot_names.scan(root, bot_names.load_names()))
    assert found == [(2, "requester"), (3, "owner"), (4, "owner"), (4, "owner"), (4, "owner")]


def test_the_shipped_map_is_the_applied_rename_data_and_the_cli_command_table():
    shipped = json.loads(MAP.read_text())
    assert shipped == runpy.run_path(str(ROOT / "scripts/cleanup/export_names.py"))["build"]()

    # Every rename applied, where its name ends up (a rename of a rename counts once it lands), and nothing else.
    step = {(table, old): new for table, old, new, _wave in renames_data.COLUMNS}

    def ends_as(table, new):
        return ends_as(table, step[(table, new)]) if (table, new) in step else new

    assert {(table, old, new) for old, entry in shipped["table_columns"].items()
            for new, tables in entry["renames"].items() for table in tables} == {
        (table, old, ends_as(table, new)) for table, old, new, _wave in renames_data.COLUMNS}

    # The names renamed everywhere are the waves' global names, each with one spelling.
    plan = yaml.safe_load((ROOT / "scripts/cleanup/renames.yaml").read_text())
    waves = {str(k): v for k, v in plan["waves"].items()}
    assert set(shipped["columns"]) == {old for w in plan["applied_waves"]
                                       for old, new in (waves[str(w)].get("global") or {}).items() if new}
    for old, new in shipped["columns"].items():
        assert list(shipped["table_columns"][old]["renames"]) == [new]

    # kept_in is exactly the old schema's other tables with that name.
    conn = sqlite3.connect(":memory:")
    conn.executescript((ROOT / "backend/tests/fixtures/schema_pre_cleanup.sql").read_text())
    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    has = {t: {row[1] for row in conn.execute(f'PRAGMA table_info("{t}")')} for t in tables}
    conn.close()
    for old, entry in shipped["table_columns"].items():
        moved = {t for names in entry["renames"].values() for t in names}
        assert entry["kept_in"] == sorted(t for t in tables if old in has[t] and t not in moved)
    assert "meetings" in shipped["table_columns"]["owner"]["kept_in"]

    # hubcli sorts RENAMED by length when it loads; the order of the rules does not matter to the matching.
    cli = [{"old": list(old), "new": list(new), "fixable": not tail and rewrite is None}
           for old, new, tail, rewrite in hubcli.RENAMED]
    assert sorted(shipped["commands"], key=lambda r: r["old"]) == sorted(cli, key=lambda r: r["old"])
