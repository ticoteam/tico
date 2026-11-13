"""Every `hub` command the Assistant template writes must parse with clients/hubcli.py.

The Assistant reads its playbooks in a run and copies the commands it finds there, so a command that does not
exist is a command that fails in a person's chat. Each `hub ...` span in backticks, and each fenced line that
starts with `hub`, is parsed with the real argument parser. Placeholders such as `<id>` take a sample value, and
`a|b` alternatives are each tried. Parsing only: nothing is sent to a server."""

import re
from pathlib import Path

import pytest

from backend.tests.helpers.assistant_template_commands import argv_of, commands_in, parse_error, variants
from clients import hubcli

ASSISTANT = Path(__file__).resolve().parents[2] / "templates" / "catalog" / "assistant"


def cases():
    out = []
    for path in sorted(ASSISTANT.rglob("*")):
        if path.suffix not in (".md", ".yaml") or not path.is_file():
            continue
        for number, text in commands_in(path):
            argv = argv_of(text)
            if argv is None:
                continue
            out.append((path, number, text, argv))
    return out


def test_every_hub_command_in_the_template_parses():
    """One parser, every command: the failures are all listed, each as file:line and the command."""
    parser = hubcli.parser()
    failures = []
    for path, number, text, argv in cases():
        where = f"{path.relative_to(ASSISTANT.parents[2])}:{number}"
        if argv == ["\x00unbalanced quotes"]:
            failures.append(f"{where}: unbalanced quotes in `{text}`")
            continue
        # A bare mention ("`tico task create`" in prose, two words) only has to name a real command: a missing argument is fine.
        bare = len(argv) <= 2
        for option in variants(argv):
            error = parse_error(option, parser)
            if bare and error and "the following arguments are required" in error:
                continue
            if error is not None:
                failures.append(f"{where}: `{' '.join(option)}` does not parse: {error}")
    assert not failures, "\n".join(failures)


# Refresh brings AGENTS.md, playbooks/ and skills/ to a live Assistant (clients/catalog.py PRODUCT_OWNED). Anything the
# instructions name under those folders must exist, or an upgraded install would read a file that never arrives.
PATH_IN_TEXT = re.compile(r"`((?:playbooks|skills)/[\w./-]+)`")
