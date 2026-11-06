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

from clients import hubcli

ASSISTANT = Path(__file__).resolve().parents[2] / "templates" / "catalog" / "assistant"

# Commands the template names that land with the track C branch (chris/assistant-tools). Remove each entry once that
# branch is merged, so the test then checks it like every other command.
EXPECTED_TO_EXIST = [
    ("needs-you", "show"),
    ("origin", "show"),
    ("assistant", "change", "list"),
    ("assistant", "change", "restore"),
    ("approval", "list"),  # chris/approval-list
]
# Flags the template uses that land with track B (chris/assistant-acts-as-you): `hub assistant propose --for` names the
# person of a background run's card. Remove this once that branch is merged.
EXPECTED_FLAGS = ["--for"]

SPAN = re.compile(r"`(hub [^`]+)`")
PLACEHOLDER = re.compile(r"<[^<>]+>")


def commands_in(path):
    """(line number, command text) for each `hub` command written in the file.

    A backticked span is read across the lines of its paragraph, so a command that wraps is still checked. Whitespace inside
    the span is collapsed. Fenced lines that start with `hub` are read one line at a time."""
    found, paragraph, start, fenced = [], [], 0, False

    def flush():
        if paragraph:
            text = "\n".join(paragraph)
            for match in SPAN.finditer(text):
                line = start + text[: match.start()].count("\n")
                found.append((line, " ".join(match.group(1).split())))

    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if line.lstrip().startswith("```"):
            flush()
            paragraph, fenced = [], not fenced
            continue
        if fenced:
            if line.lstrip().startswith("hub "):
                found.append((number, line.strip()))
            continue
        if not line.strip():
            flush()
            paragraph = []
            continue
        if not paragraph:
            start = number
        paragraph.append(line)
    flush()
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


def parse_error(argv, parser=None):
    """None when `hub <argv>` parses, else the parser's message."""
    parser = parser or hubcli.parser()
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        try:
            hubcli.parse(parser, argv)
        except SystemExit:
            return stderr.getvalue().strip().splitlines()[-1] if stderr.getvalue().strip() else "refused"
    return None


def expected_to_exist(argv):
    if any(flag in argv for flag in EXPECTED_FLAGS):
        return True
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
            out.append((path, number, text, argv))
    return out


def test_the_template_names_commands_to_check():
    # Guard the guard: if this finds nothing, the parse below would pass for the wrong reason.
    assert len(cases()) > 80


def test_every_hub_command_in_the_template_parses():
    """One parser, every command: the failures are all listed, each as file:line and the command."""
    parser = hubcli.parser()
    failures = []
    for path, number, text, argv in cases():
        where = f"{path.relative_to(ASSISTANT.parents[2])}:{number}"
        if argv == ["\x00unbalanced quotes"]:
            failures.append(f"{where}: unbalanced quotes in `{text}`")
            continue
        if expected_to_exist(argv):
            continue                      # lands with chris/assistant-tools
        # A bare mention ("`hub task create`" in prose, two words) only has to name a real command: a missing argument is fine.
        bare = len(argv) <= 2
        for option in variants(argv):
            error = parse_error(option, parser)
            if bare and error and "the following arguments are required" in error:
                continue
            if error is not None:
                failures.append(f"{where}: `{' '.join(option)}` does not parse: {error}")
    assert not failures, "\n".join(failures)


# Refresh copies only AGENT.md, playbooks/ and skills/ to a live Assistant (clients/catalog.py PRODUCT_OWNED). Anything the
# instructions name under those folders must exist, or an upgraded install would read a file that never arrives.
PATH_IN_TEXT = re.compile(r"`((?:playbooks|skills)/[\w./-]+)`")


def test_every_playbook_or_skill_the_assistant_names_exists():
    named = set()
    for path in [ASSISTANT / "AGENT.md", *sorted((ASSISTANT / "playbooks").glob("*.md"))]:
        named.update(PATH_IN_TEXT.findall(path.read_text()))
    assert named, "the Assistant names no playbook or skill"
    missing = sorted(p for p in named if not (ASSISTANT / p.rstrip("/")).exists() and not (ASSISTANT / (p.rstrip("/") + ".md")).exists())
    assert not missing, missing
