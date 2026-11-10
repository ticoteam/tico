"""Every task command a bot is taught must parse with clients/hubcli.py.

Bots copy the commands they read in policies/, the templates their repositories are made from, the shared skills and
the integration pages. When the task engine changed (docs/pipelines-v3.md), those texts kept teaching commands that
no longer existed, so a bot in a new company failed on its first task. This parses every `hub task`, `hub ok`,
`hub needs-you` and `hub approval` command quoted there, and the BotOps routine's text, with the real parser, the way
test_assistant_template_commands does for the Assistant. A mention that leaves out a required argument passes; an
unknown command, flag or choice fails. Parsing only: nothing is sent to a server."""

import re
from pathlib import Path

from backend import routines
from backend.tests.test_assistant_template_commands import argv_of, commands_in, parse_error, variants
from clients import hubcli

ROOT = Path(__file__).resolve().parents[2]
FOLDERS = ("policies", "templates", "skills", "integrations")
# The command groups the task engine owns: what changed with pipelines v3.
GROUPS = ("task", "tasks", "ok", "needs-you", "approval")
SPAN = re.compile(r"`(hub [^`]+)`")


def cases():
    out = []
    for folder in FOLDERS:
        for path in sorted((ROOT / folder).rglob("*")):
            if path.suffix not in (".md", ".yaml") or not path.is_file():
                continue
            for number, text in commands_in(path):
                argv = argv_of(text)
                if argv and argv[0] in GROUPS:
                    out.append((str(path.relative_to(ROOT)), number, text, argv))
    for match in SPAN.finditer(routines.SWEEP_TEXT):
        argv = argv_of(match.group(1))
        if argv and argv[0] in GROUPS:
            out.append(("backend/routines.py SWEEP_TEXT", 0, match.group(1), argv))
    return out


def test_the_texts_name_task_commands_to_check():
    # Guard the guard: if this finds nothing, the parse below would pass for the wrong reason.
    found = cases()
    assert len(found) > 100
    assert {where.split("/")[0] for where, *_ in found} >= {"policies", "templates", "integrations"}


def test_every_task_command_a_bot_is_taught_parses():
    parser = hubcli.parser()
    failures = []
    for where, number, text, argv in cases():
        if argv == ["\x00unbalanced quotes"]:
            failures.append(f"{where}:{number}: unbalanced quotes in `{text}`")
            continue
        for option in variants(argv):
            error = parse_error(option, parser)
            if error and "the following arguments are required" in error:
                continue
            if error is not None:
                failures.append(f"{where}:{number}: `{' '.join(option)}` does not parse: {error}")
    assert not failures, "\n".join(failures)
