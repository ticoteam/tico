"""No text teaches `hub <command>`: the teamwork CLI is `tico`, and `hub` only its old name.

A bot copies the command lines it reads (its instructions, playbooks, skills, policies, the docs, the runner's prompt,
the BotOps routine's text and the CLI's own help), so one `hub task show` left in any of them teaches the old name to
every bot that reads it. scripts/cleanup/rename_hub_to_tico.py made the change; it is rerunnable, and its check is the
first test here, so a merge that brings back a `hub` line fails until the script is run again. The alias's own tests
and fixtures (the script's KEEP list), docs/history and reports keep `hub` on purpose.
"""

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from backend import routines
from clients import hubcli

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "cleanup" / "rename_hub_to_tico.py"
# Where a bot reads what to run, and the runner's prompt as it is written and as it renders.
TAUGHT = ("policies", "templates", "skills", "integrations", "docs", "runner-rs/src")
# Prose that names the CLI by its old name.
OLD_NAME = re.compile(r"`hub` CLI\b|\bhub CLI\b|\b[Hh]ub commands\b|`hub <")


def load_script():
    spec = importlib.util.spec_from_file_location("rename_hub_to_tico", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_rename_script_finds_nothing_left_to_change():
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"], cwd=ROOT, capture_output=True, text=True, timeout=300
    )
    assert done.returncode == 0, (
        "a text teaches `hub <command>`; run python3 scripts/cleanup/rename_hub_to_tico.py\n"
        + done.stdout
        + done.stderr
    )
    assert done.stdout.startswith("would rewrite 0 ")


def test_no_taught_text_calls_the_cli_by_its_old_name():
    names = subprocess.run(
        ["git", "ls-files", "--", *TAUGHT], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    found = []
    for name in names:
        if name.startswith("docs/history/") or not name.endswith((".md", ".yaml", ".txt", ".rs")):
            continue
        for number, line in enumerate((ROOT / name).read_text(encoding="utf-8").splitlines(), start=1):
            if OLD_NAME.search(line):
                found.append(f"{name}:{number}: {line.strip()[:120]}")
    assert len(names) > 500  # guard the guard
    assert not found, "\n".join(found)


def test_the_botops_sweep_names_no_hub_command():
    pats = load_script().patterns()
    assert not pats["every"].search(routines.SWEEP_TEXT)


def parsers(parser, path=()):
    yield path, parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for word, sub in action.choices.items():
                yield from parsers(sub, (*path, word))


def test_every_help_page_says_tico():
    pats = load_script().patterns()
    pages = list(parsers(hubcli.parser()))
    assert len(pages) > 300
    wrong = []
    for path, parser in pages:
        text = parser.format_help()
        if not text.startswith("usage: tico") or pats["every"].search(text) or OLD_NAME.search(text):
            wrong.append(" ".join(path) or "(top)")
    assert not wrong, wrong


@pytest.mark.parametrize(
    "line, fenced, expected",
    [
        ("Run `hub task show 7` first.", False, "Run `tico task show 7` first."),
        ("hub task list --owner me", False, "tico task list --owner me"),
        ("  - $ hub bot list", False, "  - $ tico bot list"),
        ("cd /w && hub needs-you show", False, "cd /w && tico needs-you show"),
        ('subprocess.run(["hub", "task", "show", id])', False, 'subprocess.run(["tico", "task", "show", id])'),
        ('f"hub task show {id}"', False, 'f"tico task show {id}"'),
        ("usage: hub task show [-h] id", False, "usage: tico task show [-h] id"),
        ("scripts/hub task list", False, "scripts/tico task list"),
        ("    hub whoami", True, "    tico whoami"),
        ("`hub --help` lists the groups", False, "`tico --help` lists the groups"),
        # Prose, the server and names that only contain the word stay.
        ("Tico keeps a hub task list for each bot.", False, "Tico keeps a hub task list for each bot."),
        ("hub status page is green", False, "hub status page is green"),
        ("the tico_task_show tool and HUB_TOKEN", False, "the tico_task_show tool and HUB_TOKEN"),
        ("https://hub.acme.example/task", False, "https://hub.acme.example/task"),
        ("~/.tico/hub.db task", False, "~/.tico/hub.db task"),
    ],
)
def test_the_script_rewrites_command_lines_and_leaves_prose(line, fenced, expected):
    script = load_script()
    assert script.rewrite_line(line, fenced, script.patterns())[0] == expected
