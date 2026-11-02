"""`hub bot check-names <bot>`: the names a bot's own checkout still uses from before the renames.

The map is clients/renames.json (written by scripts/cleanup/export_names.py): the column renames that have shipped,
the API fields that moved, and the hub CLI commands that were renamed or removed. The command runs on this computer,
beside the bot's files: no Tico server, no credential, nothing is sent.

Only code is read, and only three things in it: the SQL of a `hub sql` call (a quoted argument, a heredoc or the rest of
the line), a key read from an API answer or a stored document (`row["refs_json"]`, `.get("attempt_id")`,
`row.refs_json`), and a `hub` command in a code span, a fenced block, a shell line or a quoted argument. Prose outside
code spans is never changed, so an English "owner", "created" or "the hub status page" stays as it is. Names from waves
that have not shipped are not in the map and are not looked for.

--fix rewrites the findings marked fixable. A finding is not fixable when its new spelling needs other arguments, when
the name is a SQL alias (`AS created`), when the key is a word other code uses too (`attempt`), when the field changed
shape (`snoozed_until` is now part of `holds`), or when the file is under memory/: a bot's notes are reported, never
rewritten.
"""
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

MAP = Path(__file__).with_name("renames.json")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", "dist", "build"}
PROSE_SUFFIXES = {".md", ".markdown", ".rst", ".txt", ".yaml", ".yml"}   # only code spans and fenced blocks are read
MAX_BYTES = 2_000_000
MEMORY_DIR = "memory"
AMBIGUOUS_KEYS = {"attempt"}     # `attempt` is a common key in a bot's own code (a retry count): report, never rewrite

FENCE = re.compile(r"^([ \t]*)(`{3,}|~{3,})[^\n]*\n(.*?)^\1\2[ \t]*$", re.M | re.S)
SPAN = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
# `hub` after a quote, a backtick, `$`, `(`, `;`, `&` or `|`: a command, not a word in prose. At the start of a line too,
# where the text is a shell line or a code span or fenced block; a line of Python or JavaScript prose starts with words.
_WORDS = r"([a-z][a-z-]*(?:[ \t]+[a-z][a-z-]*)*)"
SHELL_HUB = re.compile(r"(?:^|[`\"'$(;&|])[ \t]*hub[ \t]+" + _WORDS, re.M)
SHELL_HUB_INLINE = re.compile(r"[`\"'$(;&|][ \t]*hub[ \t]+" + _WORDS)
SHELL_SUFFIXES = {".sh", ".bash", ".zsh"}
# `["hub", "sql", ...]` in Python, JavaScript or JSON: the first two words only (a set of names, `{"hub", "docs"}`, is not one)
ARGV_HUB = re.compile(r"\[[ \t\n]*[\"']hub[\"'][ \t]*,[ \t]*[\"']([a-z][a-z-]*)[\"'](?:[ \t]*,[ \t]*[\"']([a-z][a-z-]*)[\"'])?")
WORD = re.compile(r"[a-z][a-z-]*")
SQL_IDENT = re.compile(r"'(?:[^']|'')*'|(?<![\w:@$])([A-Za-z_]\w*)")
HEREDOC = re.compile(r"[ \t]*-?[ \t]*<<-?[ \t]*['\"]?(\w+)['\"]?[^\n]*\n")
SEP = re.compile(r"[ \t]*,?[ \t]*")


@dataclass
class Finding:
    path: str = ""       # relative to the bot's folder
    line: int = 0
    old: str = ""        # the name as written (a command's words joined)
    new: str = ""        # the new spelling, or what replaces it ("holds")
    kind: str = ""       # sql | key | command | removed
    fixable: bool = False
    start: int = 0       # offsets in the file's text
    end: int = 0
    replacement: str = ""
    expect: str = field(default="", repr=False)   # the text at start:end when it was read; --fix checks it first

    def text(self):
        hint = "" if self.fixable else ", change it by hand"
        return f"{self.path}:{self.line}: {self.old} -> {self.new} ({self.kind}{hint})"


class Names:
    """The map, ready to match: column names, key names, commands (longest first) and their exemptions."""

    def __init__(self, data):
        self.columns = dict(data.get("columns") or {})
        self.keys = {**{k: v for k, v in self.columns.items() if k.endswith("_json")}, **(data.get("api_fields") or {})}
        self.removed_keys = dict(data.get("api_fields_removed") or {})
        rules = [(tuple(r["old"]), tuple(r["new"]), bool(r["fixable"]), False) for r in data.get("commands") or []]
        rules += [(tuple(r["old"]), tuple(r["new"]), False, True) for r in data.get("commands_removed") or []]
        self.commands = sorted(rules, key=lambda rule: -len(rule[0]))
        self.exempt = [tuple(e) for e in data.get("commands_exempt") or []]
        alt = "|".join(re.escape(n) for n in sorted(set(self.keys) | set(self.removed_keys), key=len, reverse=True))
        dotted = "|".join(re.escape(k) for k in sorted(self.keys, key=len, reverse=True) if k not in AMBIGUOUS_KEYS)
        self.quoted_key = re.compile(r"(?P<pre>\[\s*|\.get\(\s*|[{,]\s*)(?P<q>[\"'])(?P<name>" + alt + r")(?P=q)\s*(?=[\]:),}])")
        self.dotted_key = re.compile(r"\.(?P<name>" + dotted + r")\b(?!\s*\()")


def load_names(path=MAP):
    return Names(json.loads(Path(path).read_text()))


def _regions(text, prose):
    """(start, end, shell_line) for the text to read: the whole file for code, its code spans and fenced blocks for
    prose. A line that starts with `hub` is a command in a span or a fence, and in a shell script, not in other code."""
    if not prose:
        return [(0, len(text), False)]
    out, fenced = [], []
    for m in FENCE.finditer(text):
        fenced.append((m.start(), m.end()))
        out.append((m.start(3), m.end(3), True))
    for m in SPAN.finditer(text):
        if not any(a <= m.start() < b for a, b in fenced):
            out.append((m.start(1), m.end(1), True))
    return out


def _sql_body(s, pos):
    """(start, end) of the SQL a `hub sql` call runs: a quoted argument (it may run over lines), a heredoc, or the rest
    of the line."""
    i = SEP.match(s, pos).end()
    if i < len(s) and s[i] in "\"'":
        close = s.find(s[i], i + 1)
        return i + 1, len(s) if close < 0 else close
    h = HEREDOC.match(s, pos)
    if h:
        end = re.compile(rf"^[ \t]*{re.escape(h.group(1))}[ \t]*$", re.M).search(s, h.end())
        return h.end(), end.start() if end else len(s)
    eol = s.find("\n", pos)
    return pos, len(s) if eol < 0 else eol


def _commands(s, line_start):
    """Each `hub <words>` in s as (words, shell) where words are (text, start, end) with offsets in s."""
    for m in (SHELL_HUB if line_start else SHELL_HUB_INLINE).finditer(s):
        yield [(w.group(0), m.start(1) + w.start(), m.start(1) + w.end()) for w in WORD.finditer(m.group(1))], True
    for m in ARGV_HUB.finditer(s):
        words = [(m.group(1), m.start(1), m.end(1))]
        if m.group(2):
            words.append((m.group(2), m.start(2), m.end(2)))
        yield words, False


def _sql_findings(s, base, names, out, line_start):
    for words, shell in _commands(s, line_start):
        if words[0][0] != "sql":
            continue
        pos = words[0][2] + (0 if shell else 1)    # after the word `sql`, and after its closing quote in argv form
        a, b = _sql_body(s, pos)
        body = s[a:b]
        for m in SQL_IDENT.finditer(body):
            name = m.group(1)
            if name is None or name not in names.columns or body[m.end():m.end() + 1] == "(":
                continue
            alias = bool(re.search(r"\bAS\s+$", body[:m.start()], re.I))
            out.append(Finding(old=name, new=names.columns[name], kind="sql", fixable=not alias,
                               start=base + a + m.start(1), end=base + a + m.end(1), replacement=names.columns[name]))


def _command_findings(s, base, names, out, line_start):
    for words, shell in _commands(s, line_start):
        texts = tuple(w[0] for w in words)
        if any(texts[:len(e)] == e for e in names.exempt):
            continue
        hit = next((rule for rule in names.commands if texts[:len(rule[0])] == rule[0]), None)
        if not hit:
            continue
        old, new, fixable, removed = hit
        start, end = words[0][1], words[len(old) - 1][2]
        if removed:
            target = "hub " + " ".join(new) + " (the flags differ: hub automation create --file)"
        else:
            target = "hub " + " ".join(new)
        out.append(Finding(old="hub " + " ".join(old), new=target, kind="removed" if removed else "command",
                           fixable=fixable and shell, start=base + start, end=base + end,
                           replacement=" ".join(new)))


def _key_findings(s, base, names, out):
    for m in names.quoted_key.finditer(s):
        name = m.group("name")
        if name in AMBIGUOUS_KEYS and m.group("pre")[0] in "{,":
            continue                               # a dict literal's own key (a retry count), not a read of the API
        start, end = base + m.start("name"), base + m.end("name")
        if name in names.removed_keys:
            out.append(Finding(old=name, new=names.removed_keys[name], kind="removed", start=start, end=end))
        else:
            out.append(Finding(old=name, new=names.keys[name], kind="key", fixable=name not in AMBIGUOUS_KEYS,
                               start=start, end=end, replacement=names.keys[name]))
    for m in names.dotted_key.finditer(s):
        name = m.group("name")
        out.append(Finding(old=name, new=names.keys[name], kind="key", fixable=True,
                           start=base + m.start("name"), end=base + m.end("name"), replacement=names.keys[name]))


def scan_text(rel, text, names):
    """Every finding in one file, with its line and the text it would replace."""
    suffix = Path(rel).suffix.lower()
    found = []
    for a, b, shell_line in _regions(text, suffix in PROSE_SUFFIXES):
        s = text[a:b]
        line_start = shell_line or suffix in SHELL_SUFFIXES
        _sql_findings(s, a, names, found, line_start)
        _command_findings(s, a, names, found, line_start)
        _key_findings(s, a, names, found)
    found.sort(key=lambda f: (f.start, -(f.end - f.start)))
    out, last = [], -1
    for f in found:
        if f.start < last:
            continue                               # one spelling, one finding: the first reading wins
        last = f.end
        f.path, f.line, f.expect = rel, text.count("\n", 0, f.start) + 1, text[f.start:f.end]
        if rel.split("/", 1)[0] == MEMORY_DIR:
            f.fixable = False
        out.append(f)
    return out


def _files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
                continue
            yield path


def scan(root, names):
    root = Path(root)
    findings = []
    for path in _files(root):
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        findings += scan_text(str(path.relative_to(root)), text, names)
    return findings


def fix(root, findings):
    """Rewrite the fixable findings in place; returns how many were rewritten."""
    root, done, by_file = Path(root), 0, {}
    for f in findings:
        if f.fixable:
            by_file.setdefault(f.path, []).append(f)
    for rel, items in by_file.items():
        path = root / rel
        text = path.read_text(encoding="utf-8")
        for f in sorted(items, key=lambda f: -f.start):
            if text[f.start:f.end] == f.expect:
                text = text[:f.start] + f.replacement + text[f.end:]
                done += 1
        path.write_text(text, encoding="utf-8")
    return done


def command(target, fix_it, workspace, names=None):
    """`hub bot check-names <target>`: print each finding and return 0 when none is left, else 1."""
    from clients import manifest
    folder = Path(target)
    if not folder.is_dir():
        if not workspace:
            print(f"hub bot check-names: {target} is not a folder, and HUB_WORKSPACE is not set to find a bot by slug",
                  file=sys.stderr)
            return 1
        folder = manifest.repo_dir(Path(workspace), target)
    if not folder.is_dir():
        print(f"hub bot check-names: no checkout at {folder}", file=sys.stderr)
        return 1
    names = names or load_names()
    findings = scan(folder, names)
    fixed = fix(folder, findings) if fix_it else 0
    if fix_it:
        findings = scan(folder, names)
    for f in findings:
        print(f.text())
    left = len(findings)
    summary = f"{folder}: {left} old name{'' if left == 1 else 's'} left"
    if fix_it:
        summary += f"; {fixed} rewritten"
    elif any(f.fixable for f in findings):
        summary += f"; {sum(f.fixable for f in findings)} fixable with --fix"
    print(summary)
    return 0 if not left else 1
