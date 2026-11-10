#!/usr/bin/env python3
"""Rewrite the taught `hub <command>` lines as `tico <command>`: the teamwork CLI's name is `tico`, `hub` its old one.

Rerunnable and idempotent: it only ever turns `hub ` into `tico ` (and `scripts/hub ` into `scripts/tico `,
`["hub", "<command>"` into `["tico", "<command>"`), so a second run finds nothing, and after a merge brings new text a
rerun changes only that text.

A match is the word `hub` (not part of a longer word, a path or a variable) followed by one of the CLI's real top-level
commands (from clients.hubcli.parser()), `--help`, `-h` or `--version`. It is rewritten only where it is plainly a
command line:
  - inside a backtick span on its line,
  - on a line of a fenced block, or at the start of a line (after indentation, a list marker, `>` or `$ `),
  - after a shell separator (`$(`, `(`, `;`, `&&`, `||`, `|`),
  - right after a quote or a backtick (`"hub task list"`, `f"hub task show {id}"`, a span wrapped from the line
    above), or after the CLI's own `usage:`, `Example:` or
    `Did you mean:` in its output.
Inside backticks and quotes the CLI's old command words (its renames and removals) count too, since those spans name
a command; at the start of a line they do not, so wrapped prose such as "hub status page" stays.

Every other `hub <command>` (prose such as "the hub task list") is left alone and listed with --report for a person.

Never touched: docs/history/, reports/, scripts/cleanup/report-*.md, clients/renames.json (check-names' map of old
spellings), the fixtures and tests that keep `hub` on purpose as the alias's coverage (KEEP below), and this file.

    python3 scripts/cleanup/rename_hub_to_tico.py            # rewrite, and print counts
    python3 scripts/cleanup/rename_hub_to_tico.py --check    # change nothing; exit 1 if anything would change
    python3 scripts/cleanup/rename_hub_to_tico.py --report   # also list the matches left for a person
    python3 scripts/cleanup/rename_hub_to_tico.py PATH...    # only these files or folders
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# An applied migration is never edited (backend/migrations checks each file's checksum), comments included.
SKIP_PREFIXES = ("docs/history/", "reports/", ".git/", "backend/migrations/")
SKIP_GLOBS = ("scripts/cleanup/report-*.md",)
KEEP = {
    "clients/renames.json",  # check-names' map of the old spellings a bot's files may still use
    "scripts/cleanup/rename_hub_to_tico.py",
    "scripts/hub",  # the alias itself
    "scripts/tests/test_tico_dispatch.py",  # the alias's own tests
    "backend/tests/test_tico_cli_name.py",
    "runner-rs/tests/fixtures/recovery-shell-events.json",  # keeps hub rows next to its tico rows: alias coverage
    "backend/tests/test_lost_lease_reads.py",  # runs those fixture rows by their command text
    "backend/tests/test_no_taught_hub.py",  # the test that no taught line says hub
}
BINARY = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".woff", ".woff2", ".zip", ".gz", ".sqlite", ".db", ".ico", ".icns"}
BINARY |= {".mp4", ".mp3", ".wav", ".webp", ".ttf", ".otf", ".lock", ".svg", ".car", ".a", ".so", ".dylib"}


def command_words():
    """(the CLI's top-level commands, its old command words too): the words after `hub` that make it a command."""
    import argparse as ap

    from clients import hubcli

    sub = next(a for a in hubcli.parser()._actions if isinstance(a, ap._SubParsersAction))
    real = set(sub.choices)
    old = {rule[0][0] for rule in hubcli.RENAMED} | {rule[0][0] for rule in hubcli.REMOVED}
    return real, real | old


def alternation(words):
    return "|".join(re.escape(w) for w in sorted(words, key=lambda w: (-len(w), w)))


def patterns():
    real, every = command_words()
    flags = r"--help\b|-h\b|--version\b"
    end = r"(?![\w-])"
    return {
        "real": re.compile(rf"(?<![\w/.$-])hub (?=(?:(?:{alternation(real)}){end}|{flags}))"),
        "every": re.compile(rf"(?<![\w/.$-])hub (?=(?:(?:{alternation(every)}){end}|{flags}))"),
        "scripts": re.compile(rf"(?<![\w-])scripts/hub (?=(?:(?:{alternation(every)}){end}|{flags}))"),
        "argv": re.compile(rf"""\[(\s*)(["'])hub\2(\s*,\s*)(?=(["'])(?:{alternation(every)})\4)"""),
    }


FENCE = re.compile(r"^\s*(```|~~~)")
LINE_START = re.compile(r"^\s*(?:(?:[-*+]|\d+[.)]|>|\$|#|//|///|//!|--)\s+)*$")
SHELL_BEFORE = re.compile(r"(?:\$\(|[(;|]|&&|\|\|)\s*$")
QUOTE_BEFORE = re.compile(r"""(?:["'][ \t]*|`)$""")  # a backtick right before: a span opened on the line above
# The CLI's own output, quoted in tests and docs: `usage: hub task show`, `Example: ...`, `Did you mean: ...`.
OUTPUT_BEFORE = re.compile(r"(?:usage|Example|Did you mean):[ \t]+$")


def in_backticks(line, at):
    return line.count("`", 0, at) % 2 == 1


def rewrite_line(line, fenced, pats):
    """The line with its plain command `hub `s made `tico `, and (rewritten count, left matches)."""
    line, n = pats["scripts"].subn("scripts/tico ", line)
    line, k = pats["argv"].subn(lambda m: f"[{m.group(1)}{m.group(2)}tico{m.group(2)}{m.group(3)}", line)
    n += k
    out, last, left = [], 0, []
    for m in pats["every"].finditer(line):
        before = line[: m.start()]
        real = bool(pats["real"].match(line, m.start()))
        quoted = in_backticks(line, m.start()) or QUOTE_BEFORE.search(before) or OUTPUT_BEFORE.search(before)
        plain = real and (fenced or LINE_START.match(before) or SHELL_BEFORE.search(before))
        if quoted or plain:
            out.append(line[last : m.start()] + "tico ")
            last = m.end()
            n += 1
        else:
            left.append(m.start())
    out.append(line[last:])
    return "".join(out), n, left


def tracked(only=()):
    names = subprocess.run(
        ["git", "ls-files", "-z", "--", *only], cwd=ROOT, capture_output=True, check=True
    ).stdout.split(b"\0")
    for raw in names:
        name = raw.decode()
        if not name or name.startswith(SKIP_PREFIXES) or name in KEEP:
            continue
        if any(Path(name).match(glob) for glob in SKIP_GLOBS):
            continue
        if Path(name).suffix.lower() in BINARY:
            continue
        yield name


def run(write=True, report=False, only=()):
    pats = patterns()
    files = lines = changes = 0
    leftovers = []
    for name in tracked(only):
        path = ROOT / name
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "hub" not in text:
            continue
        fenced, new_lines, changed_lines, count = False, [], 0, 0
        for number, line in enumerate(text.split("\n"), start=1):
            if FENCE.match(line):
                fenced = not fenced
                new_lines.append(line)
                continue
            new, n, left = rewrite_line(line, fenced, pats)
            if n:
                changed_lines += 1
                count += n
            leftovers.extend((name, number, line.strip()[:160]) for _ in left)
            new_lines.append(new)
        if count:
            files += 1
            lines += changed_lines
            changes += count
            if write:
                path.write_text("\n".join(new_lines), encoding="utf-8")
    verb = "rewrote" if write else "would rewrite"
    print(f"{verb} {changes} `hub` command words on {lines} lines in {files} files; {len(leftovers)} left for a person")
    if report:
        for name, number, line in leftovers:
            print(f"{name}:{number}: {line}")
    return changes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="change nothing; exit 1 when anything would change")
    ap.add_argument("--report", action="store_true", help="list each match left for a person")
    ap.add_argument("paths", nargs="*", help="only these files or folders (default: the whole checkout)")
    args = ap.parse_args(argv)
    changes = run(write=not args.check, report=args.report, only=args.paths)
    return 1 if args.check and changes else 0


if __name__ == "__main__":
    sys.exit(main())
