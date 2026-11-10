"""Reading the `hub` commands a text teaches and parsing them with clients/hubcli.py, shared by
test_assistant_template_commands and test_bot_instruction_commands so neither imports the other."""

import contextlib
import io
import re
import shlex
from itertools import product

from clients import hubcli

SPAN = re.compile(r"`(hub [^`]+)`")
PLACEHOLDER = re.compile(r"<[^<>]+>")
VERSION = re.compile(r"--version N\b")  # the version the bot was shown, written as the plan writes it


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
    filled = VERSION.sub("--version 1", PLACEHOLDER.sub("1", text))
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
