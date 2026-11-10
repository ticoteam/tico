"""Reading the `tico` commands (and `hub`, its old name) a text teaches and parsing them with clients/hubcli.py, shared by
test_assistant_template_commands and test_bot_instruction_commands so neither imports the other."""

import contextlib
import io
import re
import shlex
from itertools import product

from clients import hubcli

SPAN = re.compile(r"`((?:tico|hub) [^`]+)`")  # `tico` is the CLI; `hub` is its old name
STARTS = ("tico ", "hub ")
PLACEHOLDER = re.compile(r"<[^<>]+>")
COMMENT = re.compile(r"\s+#\s.*$")
VERSION = re.compile(r"--version N\b")  # the version the bot was shown, written as the plan writes it


def commands_in(path):
    """(line number, command text) for each `hub` command written in the file.

    A backticked span is read across the lines of its paragraph, so a command that wraps is still checked. Whitespace inside
    the span is collapsed. Fenced lines that start with `hub` are read one line at a time."""
    found, paragraph, start, fenced, continued = [], [], 0, False, False

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
            if continued:  # the line before ended in `\`: this one is the rest of its command
                found[-1] = (found[-1][0], found[-1][1].rstrip("\\").rstrip() + " " + line.strip())
            elif line.lstrip().startswith(STARTS):
                found.append((number, line.strip()))
            continued = bool(found) and (continued or line.lstrip().startswith(STARTS)) and line.rstrip().endswith("\\")
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
    """Each argv with its `a|b|c` (and `--approve|--changes`) alternatives expanded, one at a time."""

    def split(token):
        if "|" not in token:
            return [token]
        parts = token.split("|")
        flags = [part.startswith("-") for part in parts]
        return parts if not any(flags) or all(flags) else [token]

    return [list(combo) for combo in product(*(split(token) for token in argv))]


def argv_of(text):
    """The argv after `tico`, with placeholders filled, or None when the line is prose rather than a command. The CLI's
    own top-level answers (`tico --version`, and `tico help`, which clients/hubcli.py main reads as --help) print and
    exit, so they are not parsed."""
    if "<group>" in text or "--help" in text or text.endswith("..."):
        return None
    text = COMMENT.sub("", text)  # a shell comment after the command: `tico decision ask --list  # the shared sets`
    filled = VERSION.sub("--version 1", PLACEHOLDER.sub("1", text))
    try:
        tokens = shlex.split(filled)
    except ValueError:
        return ["\x00unbalanced quotes"]
    argv, depth = [], 0
    for token in tokens[1:]:  # an optional `[--paths A B ...]` is left out whole, however many words it has
        opens = len(token) - len(token.lstrip("["))
        closes = len(token.rstrip(".")) - len(token.rstrip(".").rstrip("]"))
        if depth == 0 and not opens and not closes:
            argv.append(token)
        depth = max(0, depth + opens - closes)
    if argv in (["--version"], ["help"]):
        return None
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
