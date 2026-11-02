"""`hub bot check-names <bot>`: the names a bot's own checkout still uses from before the renames.

The map is clients/renames.json (written by scripts/cleanup/export_names.py): the column renames that have shipped, with
the tables each one moved on and the tables that keep the old name, the API fields that moved, and the hub CLI commands
that were renamed or removed. The command runs on this computer, beside the bot's files: no Tico server, no credential,
nothing is sent.

Only code is read, and only four things in it: the SQL of a `hub sql` call (a quoted argument, a heredoc or the rest of
the line), other SQL-looking text (a quoted statement, or a statement that starts a line in a code span or fenced block),
a key read from an API answer or a stored document (`row["refs_json"]`, `.get("attempt_id")`, `row.refs_json`), and a
`hub` command in a code span, a fenced block, a shell line or a quoted argument. Prose outside code spans is never
changed, so an English "owner", "created" or "the hub status page" stays as it is. Names from waves that have not
shipped are not in the map and are not looked for.

A column renamed on some tables only counts where its table shows: `SELECT owner FROM tasks` is a finding and names
tasks, `SELECT owner FROM meetings` is not. As a key, a word many tables use (`owner`, `state`, `at`) counts only when
the receiver is named after a table it moved on, in a file that talks to the hub (`task["owner"]`), or when it is a row
(`row["owner"]`) and the file's own SQL reads such a table. A key only a few tables keep (`from_actor`) counts anywhere.

--fix rewrites the findings marked fixable. A finding is not fixable when its new spelling needs other arguments, when
the name is a SQL alias (`AS created`), when the key is a word other code uses too (`attempt`), when the field changed
shape (`snoozed_until` is now part of `holds`), when the name has more than one new spelling or a table keeps it
(`owner`), when a key was only recognised from its surroundings, when SQL is not in a `hub sql` call (it may be the bot's
own database), or when the file is under memory/: a bot's notes are reported, never rewritten.
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

_STATEMENT = r"(?:SELECT|WITH|UPDATE|INSERT|DELETE)\b"
QUOTED_SQL = re.compile(r"(\"\"\"|'''|[\"'`])\s*(?=" + _STATEMENT + ")", re.I)
LINE_SQL = re.compile(r"^[ \t]*(?=" + _STATEMENT + ")", re.I | re.M)
LINE_SQL_END = re.compile(r";|\n[ \t]*\n")
SQL_STRING = re.compile(r"'(?:[^']|'')*'")
_NAME = r"[\"`]?([A-Za-z_]\w*)[\"`]?(?:\s+(?:AS\s+)?([A-Za-z_]\w*))?"
TABLE_REF = re.compile(r"\b(?:FROM|JOIN|UPDATE|INTO)\s+(?:OR\s+\w+\s+)?" + _NAME, re.I)
MORE_TABLES = re.compile(r"\s*,\s*" + _NAME)
OUTPUT_ALIAS = re.compile(r"\bAS\s+([A-Za-z_]\w*)", re.I)
QUALIFIER = re.compile(r"([A-Za-z_]\w*)\.$")
NOT_AN_ALIAS = {"as", "cross", "default", "except", "full", "group", "having", "inner", "intersect", "join", "left",
                "limit", "natural", "on", "order", "outer", "returning", "right", "select", "set", "union", "using",
                "values", "where", "window"}
KEY_READ = re.compile(r"\b(?P<recv>[A-Za-z_]\w*)(?:\[\s*[\"'](?P<a>\w+)[\"']\s*\]|\.get\(\s*[\"'](?P<b>\w+)[\"']"
                      r"|\.(?P<c>[a-z_]\w*)\b(?!\s*\())")
ROW_RECEIVERS = {"row", "r", "rec", "record", "item", "result"}
RECEIVER_TABLES = {"msg": "messages"}
TALKS_TO_HUB = re.compile(r"/api/v2/|\bHUB_[A-Z_]+\b|[\"'`$(;&|][ \t]*hub[ \t]|^[ \t]*hub[ \t]|\[[ \t\n]*[\"']hub[\"']", re.M)
MOST_KEPT_FOR_A_KEY = 2     # a name most of the schema keeps proves nothing as a key: `created_by` needs its table in view


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
    tables: tuple = ()   # the hub tables the rename applies to here, when it is not every table with that name
    expect: str = field(default="", repr=False)   # the text at start:end when it was read; --fix checks it first

    def text(self):
        where = f", on {', '.join(self.tables)}" if self.tables else ""
        hint = "" if self.fixable else ", change it by hand"
        return f"{self.path}:{self.line}: {self.old} -> {self.new} ({self.kind}{where}{hint})"


class Names:
    """The map, ready to match: column names, key names, commands (longest first) and their exemptions."""

    def __init__(self, data):
        self.columns = dict(data.get("columns") or {})
        self.tables = dict(data.get("table_columns") or {})
        self.api_kept = set(data.get("api_kept") or [])
        self.keys = {**{k: v for k, v in self.columns.items() if k.endswith("_json")}, **(data.get("api_fields") or {})}
        self.removed_keys = dict(data.get("api_fields_removed") or {})
        # The table-specific keys that are safe to read anywhere: they are compound names few tables keep.
        self.scoped_keys = {old for old, entry in self.tables.items()
                            if old not in self.columns and old not in self.keys and old not in self.removed_keys
                            and old not in self.api_kept and "_" in old and len(entry["kept_in"]) <= MOST_KEPT_FOR_A_KEY}
        self.keys.update({old: " or ".join(self.tables[old]["renames"]) for old in self.scoped_keys})
        rules = [(tuple(r["old"]), tuple(r["new"]), bool(r["fixable"]), False) for r in data.get("commands") or []]
        rules += [(tuple(r["old"]), tuple(r["new"]), False, True) for r in data.get("commands_removed") or []]
        self.commands = sorted(rules, key=lambda rule: -len(rule[0]))
        self.exempt = [tuple(e) for e in data.get("commands_exempt") or []]
        alt = "|".join(re.escape(n) for n in sorted(set(self.keys) | set(self.removed_keys), key=len, reverse=True))
        dotted = "|".join(re.escape(k) for k in sorted(self.keys, key=len, reverse=True) if k not in AMBIGUOUS_KEYS)
        self.quoted_key = re.compile(r"(?P<pre>\[\s*|\.get\(\s*|[{,]\s*)(?P<q>[\"'])(?P<name>" + alt + r")(?P=q)\s*(?=[\]:),}])")
        self.dotted_key = re.compile(r"\.(?P<name>" + dotted + r")\b(?!\s*\()")


    def one_spelling(self, name):
        """--fix may rewrite this name: it has one new spelling everywhere and no table keeps it."""
        entry = self.tables.get(name)
        return not entry or (len(entry["renames"]) == 1 and not entry["kept_in"])

    def renamed_on(self, name, tables):
        """{new: [tables]} for the tables among `tables` where this name moved."""
        renames = self.tables[name]["renames"]
        hits = {new: [t for t in moved if t in tables] for new, moved in renames.items()}
        return {new: moved for new, moved in hits.items() if moved}


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


def _hub_sql_bodies(s, line_start):
    """(start, end) of the SQL of each `hub sql` call in s."""
    for words, shell in _commands(s, line_start):
        if words[0][0] == "sql":
            yield _sql_body(s, words[0][2] + (0 if shell else 1))   # after the word `sql`, and its closing quote in argv form


def _sql_strings(s, sql_lines):
    """(start, end) of text that reads as SQL: a quoted statement (Python's adjacent strings count as one), and in a code
    span, a fenced block or a .sql file a statement that starts a line."""
    pos = 0
    while True:
        m = QUOTED_SQL.search(s, pos)
        if not m:
            break
        quote, end = m.group(1), s.find(m.group(1), m.end())
        if end < 0:
            pos = m.end()
            continue
        while len(quote) == 1:
            more = re.compile(r"\s*" + re.escape(quote)).match(s, end + 1)
            close = s.find(quote, more.end()) if more else -1
            if close < 0:
                break
            end = close
        yield m.end(), end
        pos = end + len(quote)
    if sql_lines:
        for m in LINE_SQL.finditer(s):
            stop = LINE_SQL_END.search(s, m.end())
            yield m.end(), stop.start() if stop else len(s)


def _statements(body):
    """(offset, text) of each statement in body, with its string literals blanked so they read as nothing."""
    blank = SQL_STRING.sub(lambda m: " " * len(m.group(0)), body)
    pos = 0
    for part in blank.split(";"):
        yield pos, part
        pos += len(part) + 1


def _statement_tables(stmt):
    """({table or alias: table}, offsets of the names in table position) of one statement."""
    tables, spots = {}, set()

    def take(m):
        tables[m.group(1)] = m.group(1)
        spots.add(m.start(1))
        if m.group(2) and m.group(2).lower() not in NOT_AN_ALIAS:
            tables[m.group(2)] = m.group(1)
            spots.add(m.start(2))
            return True
        return not m.group(2)

    for m in TABLE_REF.finditer(stmt):
        if m.group(1).lower() in NOT_AN_ALIAS:    # ON CONFLICT DO UPDATE SET names no table
            continue
        more = m if take(m) else None
        while more:
            more = MORE_TABLES.match(stmt, more.end())
            if more and not take(more):
                break
    return tables, spots


def _table_scoped_findings(body, base, names, out, in_hub_sql):
    """Each renamed name a statement uses on a table it moved on. In a `hub sql` body the names renamed on every table
    were already read, without needing their table; elsewhere the SQL may be the bot's own database, so it is reported."""
    for offset, stmt in _statements(body):
        tables, spots = _statement_tables(stmt)
        if not tables:
            continue
        aliases = set(OUTPUT_ALIAS.findall(stmt))
        for m in SQL_IDENT.finditer(stmt):
            name = m.group(1)
            if name not in names.tables or m.start(1) in spots or stmt[m.end():m.end() + 1] == "(":
                continue
            if in_hub_sql and name in names.columns:
                continue
            qualifier = QUALIFIER.search(stmt[max(0, m.start() - 64):m.start()])
            if qualifier and qualifier.group(1).lower() != "excluded":
                if qualifier.group(1) not in tables:
                    continue
                on = {tables[qualifier.group(1)]}
            elif name in aliases:
                continue
            else:
                on = set(tables.values())
            hits = names.renamed_on(name, on)
            if not hits:
                continue
            new = " or ".join(hits)
            start = base + offset + m.start(1)
            out.append(Finding(old=name, new=new, kind="sql", fixable=in_hub_sql and names.one_spelling(name),
                               start=start, end=start + len(name), replacement=new,
                               tables=tuple(t for moved in hits.values() for t in moved)))


def _sql_findings(s, base, names, out, line_start, sql_lines):
    for a, b in _hub_sql_bodies(s, line_start):
        body = s[a:b]
        for m in SQL_IDENT.finditer(body):
            name = m.group(1)
            if name is None or name not in names.columns or body[m.end():m.end() + 1] == "(":
                continue
            alias = bool(re.search(r"\bAS\s+$", body[:m.start()], re.I))
            out.append(Finding(old=name, new=names.columns[name], kind="sql", fixable=not alias,
                               start=base + a + m.start(1), end=base + a + m.end(1), replacement=names.columns[name]))
        _table_scoped_findings(body, base + a, names, out, True)
    for a, b in _sql_strings(s, sql_lines):
        _table_scoped_findings(s[a:b], base + a, names, out, False)


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
            out.append(_key_finding(name, names, start, end, name not in AMBIGUOUS_KEYS))
    for m in names.dotted_key.finditer(s):
        out.append(_key_finding(m.group("name"), names, base + m.start("name"), base + m.end("name"), True))


def _key_finding(name, names, start, end, fixable):
    scoped = name in names.scoped_keys
    tables = tuple(t for moved in names.tables[name]["renames"].values() for t in moved) if scoped else ()
    return Finding(old=name, new=names.keys[name], kind="key", fixable=fixable and (not scoped or names.one_spelling(name)),
                   start=start, end=end, replacement=names.keys[name], tables=tables)


def _receiver_table(receiver):
    """The table a variable is named after, as its singular or its plural: task and tasks both mean tasks."""
    return RECEIVER_TABLES.get(receiver) or receiver


def _context_key_findings(s, base, names, out, talks_to_hub, file_tables):
    """A common word read as a key counts only with its table in view; it is reported, never rewritten, since the same
    word may be the bot's own field."""
    for m in KEY_READ.finditer(s):
        name = m.group("a") or m.group("b") or m.group("c")
        if name not in names.tables or name in names.keys or name in names.removed_keys:
            continue
        receiver = m.group("recv")
        if receiver in ROW_RECEIVERS:
            on = file_tables
        elif talks_to_hub and name not in names.api_kept:
            table = _receiver_table(receiver)
            on = {table, table + "s"}
        else:
            continue
        hits = names.renamed_on(name, on)
        if not hits:
            continue
        group = "a" if m.group("a") else "b" if m.group("b") else "c"
        out.append(Finding(old=name, new=" or ".join(hits), kind="key", start=base + m.start(group), end=base + m.end(group),
                           tables=tuple(t for moved in hits.values() for t in moved)))


def scan_text(rel, text, names):
    """Every finding in one file, with its line and the text it would replace."""
    suffix = Path(rel).suffix.lower()
    found = []
    regions = [(a, b, shell_line or suffix in SHELL_SUFFIXES, shell_line or suffix == ".sql")
               for a, b, shell_line in _regions(text, suffix in PROSE_SUFFIXES)]
    file_tables, talks_to_hub = set(), bool(TALKS_TO_HUB.search(text))
    for a, b, line_start, sql_lines in regions:
        s = text[a:b]
        for x, y in [*_hub_sql_bodies(s, line_start), *_sql_strings(s, sql_lines)]:
            for _offset, stmt in _statements(s[x:y]):
                file_tables.update(_statement_tables(stmt)[0].values())
    for a, b, line_start, sql_lines in regions:
        s = text[a:b]
        _sql_findings(s, a, names, found, line_start, sql_lines)
        _command_findings(s, a, names, found, line_start)
        _key_findings(s, a, names, found)
        _context_key_findings(s, a, names, found, talks_to_hub, file_tables)
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
