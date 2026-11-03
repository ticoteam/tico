"""Every `hub` command the Assistant template writes must parse with clients/hubcli.py.

The Assistant reads its playbooks in a run and copies the commands it finds there, so a command that does not
exist is a command that fails in a person's chat. Each `hub ...` span in backticks, and each fenced line that
starts with `hub`, is parsed with the real argument parser. Placeholders such as `<id>` take a sample value, and
`a|b` alternatives are each tried. Parsing only: nothing is sent to a server."""

import contextlib
import io
import re
import shlex
from itertools import product
from pathlib import Path

import pytest

from clients import hubcli

ASSISTANT = Path(__file__).resolve().parents[2] / "templates" / "catalog" / "assistant"

# Commands the template names that land with the track C branch (chris/assistant-tools). Remove each entry once that
# branch is merged, so the test then checks it like every other command.
EXPECTED_TO_EXIST = [
    ("needs-you", "show"),
    ("origin", "show"),
    ("assistant", "change", "list"),
    ("assistant", "change", "restore"),
]

SPAN = re.compile(r"`(hub [^`]+)`")
PLACEHOLDER = re.compile(r"<[^<>]+>")


def commands_in(path):
    """(line number, command text) for each `hub` command written in the file."""
    found, fenced = [], False
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            if line.lstrip().startswith("hub "):
                found.append((number, line.strip()))
            continue
        found.extend((number, span) for span in SPAN.findall(line))
    return found


def variants(argv):
    """Each argv with its `a|b|c` alternatives expanded, one at a time."""
    choices = [token.split("|") if "|" in token and not token.startswith("-") else [token] for token in argv]
    return [list(combo) for combo in product(*choices)]


def argv_of(text):
    """The argv after `hub`, with placeholders filled, or None when the line is prose rather than a command."""
    if "<group>" in text or "--help" in text or text.endswith("..."):
        return None
    filled = PLACEHOLDER.sub("1", text)
    try:
        tokens = shlex.split(filled)
    except ValueError:
        return ["\x00unbalanced quotes"]
    argv = [t for t in tokens[1:] if not (t.startswith("[") or t.endswith("]"))]
    return argv or None


def parse_error(argv):
    """None when `hub <argv>` parses, else the parser's message."""
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        try:
            hubcli.parse(hubcli.parser(), argv)
        except SystemExit:
            return stderr.getvalue().strip().splitlines()[-1] if stderr.getvalue().strip() else "refused"
    return None


def expected_to_exist(argv):
    return any(tuple(argv[: len(prefix)]) == prefix for prefix in EXPECTED_TO_EXIST)


def cases():
    out = []
    for path in sorted(ASSISTANT.rglob("*")):
        if path.suffix not in (".md", ".yaml") or not path.is_file():
            continue
        for number, text in commands_in(path):
            argv = argv_of(text)
            if argv is None:
                continue
            out.append(pytest.param(path, number, text, argv, id=f"{path.relative_to(ASSISTANT)}:{number}"))
    return out


def test_the_template_names_commands_to_check():
    # Guard the guard: if this finds nothing, the parse below would pass for the wrong reason.
    assert len(cases()) > 80


@pytest.mark.parametrize(("path", "number", "text", "argv"), cases())
def test_every_hub_command_in_the_template_parses(path, number, text, argv):
    if argv == ["\x00unbalanced quotes"]:
        pytest.fail(f"{path.relative_to(ASSISTANT.parents[2])}:{number}: unbalanced quotes in `{text}`")
    if expected_to_exist(argv):
        pytest.skip("lands with chris/assistant-tools")
    # A bare mention ("`hub task create`" in prose, two words) only has to name a real command: a missing argument is fine.
    bare = len(argv) <= 2
    for option in variants(argv):
        error = parse_error(option)
        if bare and error and "the following arguments are required" in error:
            continue
        assert error is None, (
            f"{path.relative_to(ASSISTANT.parents[2])}:{number}: `{' '.join(option)}` does not parse: {error}"
        )
