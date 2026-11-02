"""The cleanup codemod: apply one wave of scripts/cleanup/renames.yaml to the code, deterministically.

    python3 scripts/cleanup/rename.py --wave 1            # rewrite the tree, write scripts/cleanup/report-wave1.md
    python3 scripts/cleanup/rename.py --wave 2 --check    # exit 1 if a name from waves <= 2 is still in code

It replaces exact identifiers only, never words in prose:
- Python (tokenize + ast): column tokens in SQL strings (f-string parts and implicit concatenations included) and
  SQL fragments; string literals used as keys (subscripts, .get/.pop/.setdefault, `in` row checks, dict keys,
  dict(old=...), class fields in rows.py/models.py, column arguments next to a table name).
- JavaScript under ui/ and app/: property access, ['old'], object keys, shorthand/destructuring, ?old=/&old= params.
- Rust (runner-rs, relay-rs, app/src): ["old"] indexes, .get("old"), #[serde(rename = "old")].
- Markdown (docs, templates, skills, examples, integrations): backticked identifiers and SQL in code.

A wave's `global` names are renamed wherever they appear in those contexts. Its `scoped` names ("table.column")
are renamed in SQL only when every table the statement touches that has the column renames it the same way, and
as keys only inside a function whose SQL touches such a table; every other site goes to the report.
`scripts/cleanup/keep.yaml` lists files or (file, name) pairs the codemod leaves alone (other databases, names that
only look like columns); --check ignores them too.
"""

import argparse
import ast
import fnmatch
import io
import re
import subprocess
import sys
import tokenize
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "scripts/cleanup"
WAVE_ORDER = ["1", "2", "2s", "2b", "3"]

PY_DIRS = ("backend", "e2e", "clients", "runner", "scripts", "setup", "evals", "connectors", "integrations", "tools", "ui", "docker",
           "docs-eval", "contracts")
PY_FILES = ("conftest.py",)
JS_GLOBS = ("ui/**/*.js", "ui/**/*.cjs", "ui/**/*.mjs", "app/shell/**/*.js", "scripts/**/*.cjs", "scripts/**/*.mjs")
RS_DIRS = ("runner-rs", "relay-rs", "app/src")
MD_DIRS = ("docs", "templates", "skills", "examples", "integrations", "contracts", "questions", "runner-rs")
JSON_GLOBS = ("contracts/runner/fixtures/*.json", "ui/tests/fixtures/*.json")   # the UI tests answer with these
SKIP_PARTS = ("node_modules", "/vendor/", "ui/assets/", "__pycache__", "scripts/cleanup/", "/target/")

SQL_STRONG = re.compile(
    r"^\s*SELECT\s|\b(SELECT\b[\s\S]*\bFROM|INSERT\s+(OR\s+\w+\s+)?INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|CREATE\s+(TEMP\s+|UNIQUE\s+)?"
    r"(TABLE|INDEX|TRIGGER|VIEW)|ALTER\s+TABLE|WHERE|JOIN|ORDER\s+BY|GROUP\s+BY|ON\s+CONFLICT|RETURNING|"
    r"COALESCE\s*\(|IFNULL\s*\(|IS\s+(NOT\s+)?NULL|VALUES\s*\(|json_extract\s*\(|json_each\s*\(|excluded\.)")
SQL_KEYWORDS = {
    "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "NULL", "IS", "IN", "AS", "ON", "BY", "ORDER", "GROUP", "DESC",
    "ASC", "LIMIT", "OFFSET", "LIKE", "GLOB", "BETWEEN", "CASE", "WHEN", "THEN", "ELSE", "END", "DISTINCT", "EXISTS",
    "SET", "VALUES", "INTO", "INSERT", "UPDATE", "DELETE", "JOIN", "LEFT", "INNER", "OUTER", "CROSS", "USING", "HAVING",
    "UNION", "ALL", "COLLATE", "NOCASE", "ESCAPE", "CAST", "INTEGER", "TEXT", "REAL", "BLOB", "DEFAULT", "PRIMARY",
    "KEY", "UNIQUE", "CHECK", "REFERENCES", "CONFLICT", "DO", "NOTHING", "REPLACE", "IGNORE", "RETURNING", "WITH",
    "RECURSIVE", "INDEXED", "TABLE", "INDEX", "TRIGGER", "VIEW", "CREATE", "ALTER", "ADD", "COLUMN", "DROP", "IF",
    "BEGIN", "BEFORE", "AFTER", "FOR", "EACH", "ROW", "NEW", "OLD", "RAISE", "ABORT", "TRUE", "FALSE", "OVER",
    "PARTITION", "FILTER", "MAX", "MIN", "COUNT", "SUM", "TOTAL", "AVG", "excluded", "OF", "TEMP", "RENAME", "TO",
    "WITHOUT", "ROWID", "STRICT", "NULLS", "FIRST", "LAST", "ISNULL", "NOTNULL", "MATCH", "REGEXP"}
TABLE_REF = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE(?:\s+IF\s+(?:NOT\s+)?EXISTS)?|ON|INDEXED\s+BY)\s+"
                       r"([A-Za-z_]\w*|\x00)(?:\s+(?:AS\s+)?([A-Za-z_]\w*))?")
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PLACEHOLDER = "\x00"   # an f-string replacement field inside SQL text


# ---------------------------------------------------------------------------------------------------------------
# The map

class Wave:
    def __init__(self, data, wave, schema):
        spec = data["waves"][wave]
        self.wave = wave
        self.glob = dict(spec.get("global") or {})
        self.scoped = {}                       # old -> {table: new}
        for key, new in (spec.get("scoped") or {}).items():
            table, old = key.split(".", 1)
            self.scoped.setdefault(old, {})[table] = new
        self.schema = schema                   # table -> set(columns) before this wave
        self.tables = set(schema)
        self.old_names = set(self.glob) | set(self.scoped)
        self.models = None                     # class name -> renamed fields (model_classes), filled by process
        self.broad = {}                        # old -> new: a scoped name that is a row's field wherever it is a key

    def sql_new(self, old, touched):
        """The new name of an unqualified column token in SQL touching `touched` (None: leave it)."""
        if old in self.glob:
            return self.glob[old]
        per_table = self.scoped.get(old)
        if not per_table or not touched or PLACEHOLDER in touched or any(t not in self.tables for t in touched):
            return None
        holders = {t for t in touched if old in self.schema.get(t, ())}
        news = {per_table.get(t) for t in holders}
        if holders and len(news) == 1 and None not in news:
            return news.pop()
        return None

    def sql_settled(self, old, touched):
        """Every touched table is known and none of those holding `old` renames it: a kept name, nothing to decide."""
        if not touched or PLACEHOLDER in touched or any(t not in self.tables for t in touched):
            return False
        per_table = self.scoped.get(old, {})
        return not any(old in self.schema.get(t, ()) and t in per_table for t in touched)

    def table_new(self, table, old):
        if old in self.glob:
            return self.glob[old]
        return self.scoped.get(old, {}).get(table)


def load(wave):
    data = yaml.safe_load((HERE / "renames.yaml").read_text())
    schema = base_schema()
    waves = {}
    for w in WAVE_ORDER:
        waves[w] = Wave(data, w, {t: set(c) for t, c in schema.items()})
        for old, new in waves[w].glob.items():
            for cols in schema.values():
                if old in cols:
                    cols.discard(old)
                    cols.add(new)
        for old, per_table in waves[w].scoped.items():
            for t, new in per_table.items():
                schema[t].discard(old)
                schema[t].add(new)
        if w == wave:
            break
    keep = yaml.safe_load((HERE / "keep.yaml").read_text()) if (HERE / "keep.yaml").exists() else {}
    for w, names in ((keep or {}).get("broad") or {}).items():
        if str(w) in waves:
            waves[str(w)].broad = names
    return data, waves, keep or {}


def base_schema():
    sys.path.insert(0, str(HERE))
    from gen_map import FIXTURE, load_schema
    tables, virtual = load_schema(FIXTURE)
    return {t: set(c) for t, c in tables.items()}


def glob_match(rel, pattern):
    """A path glob where `*` stays inside one folder and `**` crosses folders, `**/` none or more of them (fnmatch's
    `*` crosses folders and its `**/` needs one: ui/**/*.js missed ui/support.js)."""
    regex = re.escape(pattern).replace(r"\*\*/", "\1").replace(r"\*\*", "\0").replace(r"\*", "[^/]*") \
        .replace("\1", "(?:.*/)?").replace("\0", ".*")
    return re.fullmatch(regex, rel) is not None


class Keep:
    def __init__(self, data):
        self.files = list(data.get("files") or [])
        self.names = data.get("names") or {}      # glob -> [names]
        self.keys = data.get("keys") or {}        # glob -> [names] kept as Python keys/values; SQL still renamed
        self.sites = data.get("sites") or {}      # glob -> [line texts] whose names are all kept
        self.wire_models = set(data.get("wire_models") or [])   # request models whose fields are wire names
        # API paths whose request bodies are wire: a dict literal sent to one keeps its keys.
        self.wire_paths = [re.compile(p) for p in data.get("wire_paths") or []]

    def file(self, rel):
        return any(glob_match(rel, g) for g in self.files)

    def name(self, rel, name):
        return any(glob_match(rel, g) and name in names for g, names in self.names.items())

    def key(self, rel, name):
        return any(glob_match(rel, g) and (name in names or "*" in names) for g, names in self.keys.items())

    def site(self, rel, text, offset):
        """The line holding `offset` has a text keep.yaml `sites` lists for this file: a flag, count or other
        system's field that only looks like a renamed column, kept where a whole-file `keys` entry would be too wide."""
        found = [t for g, texts in self.sites.items() if glob_match(rel, g) for t in texts]
        if not found:
            return False
        start, end = text.rfind("\n", 0, offset) + 1, text.find("\n", offset)
        here = text[start:end if end >= 0 else len(text)]
        return any(t in here for t in found)


# ---------------------------------------------------------------------------------------------------------------
# Report

class Report:
    def __init__(self):
        self.renamed = Counter()               # (file, kind) -> n
        self.sites = defaultdict(list)         # category -> [(file, line, name, text)]

    def edit(self, rel, kind):
        self.renamed[(rel, kind)] += 1

    def site(self, category, rel, line, name, text):
        self.sites[category].append((rel, line, name, text.strip()[:160]))


CATEGORY_HELP = {
    "dom-api": "a browser API's own field (an Animation's `finished` promise): kept",
    "js-method": "a method call on an object (`response.finished()`): not a field, kept",
    "site-kept": "a line keep.yaml `sites` keeps: a flag, count or other system's field named like a column",
    "sql-scoped-skip": "SQL naming a scoped column where the touched tables did not settle it (unknown/placeholder "
                       "table, or tables that disagree): fix by hand",
    "key-scoped-skip": "a key literal equal to a scoped name outside a function that touches its table",
    "key-bool": "a dict key equal to a renamed name whose value is a bool/number literal (a flag, not the column)",
    "value": "an exact literal equal to a renamed name in a value position (compare, list, argument, return): "
             "renamed only in wave 1",
    "renamed-value": "wave 1: a literal renamed in a value position (lists of columns, arguments): check each",
    "kwarg": "a keyword argument named like a renamed column (not dict(...)): the callee's parameter decides",
    "attribute": "attribute access .old in Python (a model or object field)",
    "both-keys": "an old name read or removed in a function that also sets the new name as a key (decode-in-place "
                 "such as row['refs'] = parse(row.pop('refs_json'))): renamed, check the order",
    "pop-dropped": "a standalone pop/del of the raw column in a function that sets its decoded twin under the new "
                   "name: the statement was deleted (it would now remove the decoded value)",
    "dup-key": "a dict, call or literal list that held both an old and a new name now names one twice (the later "
               "entry wins): keep the right one by hand",
    "json-key": "a key on a stored JSON document (refs, trigger, flow, step, detail, meta, payload, spec): existing "
                "rows keep the old key, so it is not renamed; rename it only together with a data migration",
    "key-kept": "a key or value keep.yaml `keys` leaves alone in this file: another system's field (Stripe, Xero) "
                "that shares the name",
    "key-foreign-dict": "a bare word key in a dict literal none of whose other keys is a column of a table holding it (a word table): kept",
    "event-name": "an event name (an argument of event(...), or event=/action=): not a column, kept",
    "wire-field": "a field of a keep.yaml wire model (what a runner or other separately updated client sends): "
                  "kept under its wire name",
    "mention": "an old name inside a string that is neither SQL nor an exact key (f-string or other text)",
    "js-scoped": "JavaScript site for a scoped (per-table) name: not renamed",
    "js-string": "JavaScript string literal equal to a renamed name outside ['x'] / in / URL params",
    "rust-json-key": "Rust json!/struct key equal to a renamed name: check whether it goes to or comes from the server",
    "rust-field": "Rust struct field named like a renamed column",
    "md-other": "Markdown mention outside a backticked identifier or SQL code",
    "parse-error": "file could not be parsed; not processed",
}


def write_report(path, wave, report, totals, report_only=False):
    flag = " --report-only" if report_only else ""
    lines = [f"# Cleanup codemod report, wave {wave}", "",
             "Generated by `python3 scripts/cleanup/rename.py --wave " + wave + flag + "`. Do not edit.", ""]
    if report_only:
        lines += ["Report only: this wave is mapped, not applied. The counts are what the codemod would rename.", ""]
    lines += [f"Renamed sites: {sum(report.renamed.values())} in {len({f for f, _ in report.renamed})} files.", ""]
    lines += ["## Renamed per file", "", "| file | renamed | kinds |", "|---|---|---|"]
    per_file = defaultdict(Counter)
    for (rel, kind), n in report.renamed.items():
        per_file[rel][kind] += n
    for rel in sorted(per_file):
        kinds = ", ".join(f"{k} {n}" for k, n in sorted(per_file[rel].items()))
        lines.append(f"| {rel} | {sum(per_file[rel].values())} | {kinds} |")
    lines.append("")
    for category in CATEGORY_HELP:
        sites = report.sites.get(category)
        if not sites:
            continue
        lines += [f"## {category} ({len(sites)})", "", CATEGORY_HELP[category] + ".", ""]
        for rel, line, name, text in sorted(set(sites)):
            lines.append(f"- `{rel}:{line}` `{name}`: `{text.replace('`', chr(39))}`")
        lines.append("")
    path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------------------------------------------
# SQL

def sql_tables(text):
    """(tables, aliases) a SQL text touches; PLACEHOLDER stands for an f-string field."""
    tables, aliases = set(), {}
    clean = strip_sql_literals(text)
    for m in TABLE_REF.finditer(clean):
        name, alias = m.group(1), m.group(2)
        if name.upper() in SQL_KEYWORDS and name != PLACEHOLDER:
            continue
        tables.add(name)
        if alias and alias.upper() not in SQL_KEYWORDS:
            aliases[alias] = name
        aliases.setdefault(name, name)
    # FROM a x, b y
    for m in re.finditer(r"\bFROM\s+([^;()]*?)(?=\bWHERE\b|\bJOIN\b|\bGROUP\b|\bORDER\b|\bLIMIT\b|\)|$)", clean):
        for part in m.group(1).split(",")[1:]:
            words = part.split()
            if words and WORD.fullmatch(words[0]) and words[0].upper() not in SQL_KEYWORDS:
                tables.add(words[0])
                aliases.setdefault(words[0], words[0])
                if len(words) > 1 and words[-1].upper() not in SQL_KEYWORDS and WORD.fullmatch(words[-1]):
                    aliases[words[-1]] = words[0]
    for m in re.finditer(r"\bWITH\s+(?:RECURSIVE\s+)?([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s+AS\s*\(|,\s*([A-Za-z_]\w*)\s+AS\s*\(",
                         clean):
        cte = m.group(1) or m.group(2)
        tables.add(cte)                      # a CTE is a table this statement defines: unknown to the schema
    return tables, aliases


def strip_sql_literals(text):
    return re.sub(r"'(?:[^']|'')*'", lambda m: " " * len(m.group(0)), text)


def sql_statements(text):
    """(start, end) of each statement in a script: `;` outside literals and comments ends one, except inside a
    CREATE TRIGGER's BEGIN ... END."""
    clean = re.sub(r"--[^\n]*", lambda m: " " * len(m.group(0)), strip_sql_literals(text))
    spans, start, i = [], 0, 0
    while True:
        at = clean.find(";", i)
        if at < 0:
            break
        if re.match(r"\s*CREATE\s+(TEMP\s+)?TRIGGER\b", clean[start:at], re.I) and \
                re.search(r"\bBEGIN\b", clean[start:at], re.I) and not re.search(r"\bEND\s*$", clean[start:at], re.I):
            i = at + 1
            continue
        spans.append((start, at + 1))
        start = i = at + 1
    if clean[start:].strip():
        spans.append((start, len(text)))
    return spans


def sql_edits(text, wave, tables=None, aliases=None):
    """[(start, end, new, old)] for column tokens in SQL `text` (single-quoted literals and -- comments skipped)."""
    if tables is None and ";" in text and wave.scoped:
        spans = sql_statements(text)
        if len(spans) > 1:
            edits, skipped = [], []
            for a, b in spans:
                found, missed = sql_edits(text[a:b], wave)
                edits += [(a + s, a + e, new, old) for s, e, new, old in found]
                skipped += [(a + at, word) for at, word in missed]
            return edits, skipped
    if tables is None:
        tables, aliases = sql_tables(text)
    edits, skipped = [], []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "'" or (ch == "\\" and text[i + 1:i + 2] == "'"):
            start = i + (2 if ch == "\\" else 1)
            j = start
            while j < n:
                if text[j] == "\\" and text[j + 1:j + 2] == "'":
                    break
                if text[j] == "'":
                    if text[j + 1:j + 2] == "'":
                        j += 2
                        continue
                    break
                j += 1
            i = j + (2 if text[j:j + 1] == "\\" else 1)
            continue
        if ch == "-" and text[i + 1:i + 2] == "-":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        m = WORD.match(text, i)
        if m and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")):
            word = m.group(0)
            if word in wave.old_names:
                prev = text[i - 1] if i else ""
                nxt = text[m.end():m.end() + 1]
                if nxt == "(":
                    pass                                     # a function call
                elif prev == ".":
                    q = re.search(r"([A-Za-z_]\w*)\.$", text[:i])
                    table = aliases.get(q.group(1), q.group(1)) if q else None
                    if q and q.group(1) in ("excluded", "NEW", "OLD", "new", "old"):
                        own = re.search(r"\bINSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)", text, re.I) \
                            if q.group(1) == "excluded" else re.search(r"\bTRIGGER\b.*?\bON\s+(\w+)", text, re.I | re.S)
                        table = own.group(1) if own else table
                    new = wave.table_new(table, word) if table in wave.tables or word in wave.glob else None
                    if new:
                        edits.append((i, m.end(), new, word))
                    elif word in wave.scoped and table not in wave.tables:
                        skipped.append((i, word))
                else:
                    new = wave.sql_new(word, tables)
                    if new:
                        edits.append((i, m.end(), new, word))
                    elif word in wave.scoped and not wave.sql_settled(word, tables):
                        skipped.append((i, word))
            i = m.end()
            continue
        i += 1
    return edits, skipped


def is_sql(text):
    return bool(SQL_STRONG.search(text))


QUALIFIERS = {"NEW", "OLD", "excluded"}


def is_path(text, wave):
    """A dotted column reference on its own: 'm.refs_json', 'NEW.response_json', f'{alias}.refs_json'."""
    m = re.fullmatch(r"(\x00|[A-Za-z_]\w*)?\.([A-Za-z_]\w*)", text.strip())
    if not m or m.group(2) not in wave.old_names:
        return False
    q = m.group(1) or PLACEHOLDER
    return q == PLACEHOLDER or q in QUALIFIERS or q in wave.tables or len(q) <= 2 or m.group(2).endswith("_json")


def is_fragment(text, identifiers):
    """A piece of SQL without a statement keyword: 'created DESC', 't.updated>=?', 'id, created, title'."""
    clean = strip_sql_literals(text).replace(PLACEHOLDER, " ")
    if re.fullmatch(r"\s*[A-Za-z_]\w*(\.[A-Za-z_]\w*)+\s*", clean):
        return False                    # a lone dotted name ('order.created', 'system.update.started': an event type)
    if not clean.strip() or not re.search(r"[,=<>?()]|\b(AND|OR|DESC|ASC|IS|IN|LIKE|BETWEEN|NOT)\b|\w\.\w", clean):
        return False
    if re.search(r"[!;\"{}\[\]]|\s-\s|\.\s|:\s", clean):
        return False
    words = [m.group(0) for m in WORD.finditer(clean)
             if clean[m.end():m.end() + 1] not in (".", "(")]          # qualifiers and function names
    if not words:
        return False
    for w in words:
        if w in SQL_KEYWORDS or w in identifiers or w.upper() in {"JSON_EXTRACT", "COALESCE", "IFNULL", "LOWER",
                                                                    "UPPER", "LENGTH", "SUBSTR", "DATETIME", "DATE",
                                                                    "JULIANDAY", "STRFTIME", "NULLIF", "INSTR",
                                                                    "TRIM", "ABS", "ROUND", "JSON", "JSON_EACH"}:
            continue
        return False
    return True


# ---------------------------------------------------------------------------------------------------------------
# Python

class Source:
    def __init__(self, text):
        self.text = text
        # Lines end at "\n" only, as tokenize and ast count them (str.splitlines also breaks at \x0c, \u2028...).
        self._lines = text.split("\n")
        self._lines = [line + "\n" for line in self._lines[:-1]] + ([self._lines[-1]] if self._lines[-1] else [])
        self.starts = [0]
        for line in self._lines:
            self.starts.append(self.starts[-1] + len(line))

    def offset(self, line, col):
        """Character offset of a tokenize (line, char col) position."""
        return self.starts[line - 1] + col

    def ast_offset(self, line, col_bytes):
        raw = self._lines[line - 1].encode("utf-8")[:col_bytes].decode("utf-8", "replace")
        return self.starts[line - 1] + len(raw)

    def line_of(self, offset):
        lo, hi = 0, len(self.starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    def line_text(self, line):
        return self._lines[line - 1] if 0 < line <= len(self._lines) else ""


def string_groups(src):
    """Groups of adjacent string tokens (implicit concatenation), each as a list of literal parts.

    A part is (start, end) source offsets of literal text, so edits land on the source exactly; an f-string's
    replacement fields become PLACEHOLDER in the group's text."""
    tokens = list(tokenize.generate_tokens(io.StringIO(src.text).readline))
    groups, current, depth, nested = [], None, 0, []
    i = 0

    def flush():
        nonlocal current
        if current:
            groups.append(current)
        current = None

    while i < len(tokens):
        tok = tokens[i]
        if tok.type == tokenize.STRING:
            start, end = src.offset(*tok.start), src.offset(*tok.end)
            raw = src.text[start:end]
            m = re.match(r"(?i)([rbu]*)('''|\"\"\"|'|\")", raw)
            prefix, quote = m.group(1), m.group(2)
            body_start, body_end = start + len(prefix) + len(quote), end - len(quote)
            if "b" in prefix.lower():
                flush()
                i += 1
                continue
            current = current or {"parts": [], "start": start, "triple": False}
            current["parts"].append((body_start, body_end))
            current["triple"] |= len(quote) == 3
            current["end"] = end
            i += 1
            continue
        if tok.type == tokenize.FSTRING_START:
            start = src.offset(*tok.start)
            quote_len = 3 if tok.string.endswith(("'''", '"""')) else 1
            current = current or {"parts": [], "start": start, "triple": False}
            current["triple"] |= quote_len == 3
            depth = 0
            i += 1
            while i < len(tokens):
                t = tokens[i]
                if t.type == tokenize.FSTRING_MIDDLE and depth == 0:
                    a, b = src.offset(*t.start), src.offset(*t.end)
                    current["parts"].append((a, b))
                elif t.type == tokenize.OP and t.string == "{":
                    if depth == 0:
                        current["parts"].append(None)
                    depth += 1
                elif t.type == tokenize.STRING and depth > 0:
                    nested.append(simple_group(src, t))
                elif t.type == tokenize.OP and t.string == "}":
                    depth -= 1
                elif t.type == tokenize.FSTRING_END and depth == 0:
                    current["end"] = src.offset(*t.end)
                    break
                i += 1
            i += 1
            continue
        if tok.type in (tokenize.NL, tokenize.COMMENT):
            i += 1
            continue
        flush()
        i += 1
    flush()
    return groups + [g for g in nested if g]


def simple_group(src, tok):
    """A plain string token (inside an f-string's replacement field) as its own group."""
    a, b = src.offset(*tok.start), src.offset(*tok.end)
    m = re.match(r"(?i)([ru]*)('{3}|\"{3}|'|\")", src.text[a:b])
    if not m:
        return None
    q = len(m.group(1)) + len(m.group(2))
    return {"parts": [(a + q, b - len(m.group(2)))], "start": a, "end": b, "triple": len(m.group(2)) == 3}


def group_text(src, group):
    """The group's text with PLACEHOLDER for fields, and a map from text index to source offset."""
    text, offsets = [], []
    for part in group["parts"]:
        if part is None:
            text.append(PLACEHOLDER)
            offsets.append(None)
            continue
        a, b = part
        text.append(src.text[a:b])
        offsets.extend(range(a, b))
    return "".join(text), offsets


class PyFile:
    KEY_METHODS = {"get", "pop", "setdefault", "__getitem__", "__contains__", "__delitem__"}

    def __init__(self, rel, text, wave, report, keep, check=False):
        self.rel, self.src, self.wave, self.report, self.keep = rel, Source(text), wave, report, keep
        self.edits = {}            # (start, end) -> (new, kind, old)
        self.check = check

    def kept(self, name):
        return self.keep.name(self.rel, name)

    def add(self, start, end, new, kind, old):
        if self.kept(old):
            return
        if kind not in ("sql", "comment") and self.keep.key(self.rel, old):
            self.site("key-kept", start, old)     # another system's field of the same name (keep.yaml keys)
            return
        self.edits[(start, end)] = (new, kind, old)

    def site(self, category, offset_or_line, name, line_is=False):
        line = offset_or_line if line_is else self.src.line_of(offset_or_line)
        if self.kept(name):
            return
        self.report.site(category, self.rel, line, name, self.src.line_text(line))

    def run(self):
        try:
            tree = ast.parse(self.src.text)
        except SyntaxError:
            self.report.site("parse-error", self.rel, 0, "", "")
            return self.src.text
        self.tree = tree
        self.parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                self.parents[child] = node
        self.docstrings = self._docstrings(tree)
        self.func_tables = self._function_tables(tree)
        self.event_names = self._event_names(tree)
        self._sql()
        self._keys(tree)
        self._prose_spans()
        text = self._apply()
        self._new_duplicates(text)
        return text

    @staticmethod
    def _duplicates(text):
        """(line, name) of every name a dict literal, call or literal list/tuple/set holds twice."""
        out = set()
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return out
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                names = [k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                names = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            elif isinstance(node, ast.Call):
                names = [k.arg for k in node.keywords if k.arg]
            else:
                continue
            out.update((node.lineno, n) for n in set(names) if names.count(n) > 1)
        return out

    def _new_duplicates(self, text):
        """A dict, call or column list that held both the old and the new name now holds one name twice (the
        later entry silently wins): report it so a person keeps the right one."""
        if text == self.src.text:
            return
        news = set(self.wave.glob.values()) | {n for d in self.wave.scoped.values() for n in d.values()}
        before = {name for _line, name in self._duplicates(self.src.text)}
        after = Source(text)
        for line, name in sorted(self._duplicates(text)):
            if name in news and name not in before:
                self.report.site("dup-key", self.rel, line, name, after.line_text(line))

    def _prose_spans(self):
        """Backticked identifiers in comments and docstrings: `refs_json`, `bot_config.config_json.x`."""
        for tok in tokenize.generate_tokens(io.StringIO(self.src.text).readline):
            if tok.type == tokenize.COMMENT or (tok.type == tokenize.STRING and tok.start[0] in self.docstrings):
                base = self.src.offset(*tok.start)
                for start, end, new, old in backtick_edits(tok.string, self.wave):
                    self.add(base + start, base + end, new, "comment", old)

    # -- helpers
    def _docstrings(self, tree):
        spans = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and \
                        isinstance(first.value.value, str):
                    spans.add(first.value.lineno)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                spans.add(node.value.lineno)
        return spans

    def _function_tables(self, tree):
        """node id of each function -> tables its SQL touches (for scoped names)."""
        out = {}
        if not self.wave.scoped:
            return out
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
                tables = set()
                for sub in ast.walk(node):
                    text = None
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        text = sub.value
                    elif isinstance(sub, ast.JoinedStr):
                        text = "".join(v.value if isinstance(v, ast.Constant) else PLACEHOLDER for v in sub.values)
                    if text and is_sql(text):
                        tables |= sql_tables(text)[0]
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and sub.value in self.wave.tables:
                        tables.add(sub.value)            # a table named in a helper call: H.update(c, "tasks", ...)
                out[id(node)] = tables
        return out

    def _sql_edits_here(self, text, line):
        """sql_edits; a fragment naming no table (`" AND ts>=?"`) takes the tables and aliases of its function."""
        if not self.wave.scoped or sql_tables(text)[0]:
            return sql_edits(text, self.wave)
        inner, best = None, None
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno <= line <= node.end_lineno:
                if best is None or node.lineno >= best:
                    inner, best = node, node.lineno
        if inner is None:
            return sql_edits(text, self.wave)
        tables, aliases = set(), {}
        for sub in ast.walk(inner):
            found = None
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                found = sub.value
            elif isinstance(sub, ast.JoinedStr):
                found = "".join(v.value if isinstance(v, ast.Constant) else PLACEHOLDER for v in sub.values)
            if found and is_sql(found):
                t, a = sql_tables(found)
                tables |= t
                aliases.update(a)
        return sql_edits(text, self.wave, tables=tables, aliases=aliases)

    def _enclosing_tables(self, node):
        cur = node
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return self.func_tables.get(id(cur), set())
            cur = self.parents.get(cur)
        return self.func_tables.get(id(self.tree), set())

    def scoped_new(self, old, node):
        """New name for a scoped key literal inside node's function, or None."""
        per_table = self.wave.scoped.get(old)
        if not per_table:
            return None
        tables = self._enclosing_tables(node) & self.wave.tables
        holders = {t for t in tables if old in self.wave.schema.get(t, ())}
        news = {per_table.get(t) for t in holders}
        if holders and len(news) == 1 and None not in news:
            return news.pop()
        broad = self.wave.broad.get(old)
        if broad and not holders and not self._input_key(node) and not self._test_body_key(node):
            return broad
        return None

    # A request's, a tool's or a command's input: `owner` and `bot` there take a name or an id, so they keep the role
    # name (the row they end up in has owner_id).
    INPUT_RECEIVERS = {"args", "arguments", "kwargs", "params", "body", "payload", "query", "request", "inputs",
                       "input", "form", "req", "tool_args", "fields", "patch", "changes", "update", "updates",
                       "change", "action", "raw", "pl", "sets", "entry", "document", "acl", "conversion", "journal",
                       "operation", "completion", "data"}
    INPUT_KEYWORDS = {"json", "body", "data", "params"}

    def _test_body_key(self, node):
        """A test's dict-literal key: a request's body or a saved flow, not a row (a row's fake names its table)."""
        parent = self.parents.get(node)
        return "/tests/" in "/" + self.rel and isinstance(parent, ast.Dict) and any(k is node for k in parent.keys)

    def _input_key(self, node):
        """Whether a key literal reads a request's input or sits in a body sent to the API."""
        parent = self.parents.get(node)
        target = None
        if isinstance(parent, ast.Subscript):
            target = parent.value
        elif isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute):
            target = parent.func.value
        if target is not None and json_doc_name(ast.unparse(target)) in self.INPUT_RECEIVERS:
            return True
        here = node
        for _ in range(6):
            up = self.parents.get(here)
            if up is None:
                return False
            if isinstance(up, ast.keyword) and up.arg in self.INPUT_KEYWORDS:
                return True
            if isinstance(up, ast.Assign) and any(isinstance(t, ast.Name) and t.id in self.INPUT_RECEIVERS
                                                  for t in up.targets):
                return True
            if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return False
            here = up
        return False

    EVENT_ARGS = {"event", "action", "kind"}

    def _event_names(self, tree):
        """Source offsets of string literals that are event names: the arguments of an `event(...)` call
        (H.event(c, actor, 'docs.updated', ...)) and `event=`/`action=` keywords or parameter defaults."""
        found = set()

        def mark(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.add(self.src.ast_offset(node.lineno, node.col_offset))
            elif isinstance(node, ast.IfExp):
                mark(node.body)
                mark(node.orelse)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name == "event":
                    for arg in node.args:
                        mark(arg)
                for kw in node.keywords:
                    if kw.arg in self.EVENT_ARGS:
                        mark(kw.value)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                for arg, default in zip(args.args[len(args.args) - len(args.defaults):], args.defaults):
                    if arg.arg in self.EVENT_ARGS:
                        mark(default)
                for arg, default in zip(args.kwonlyargs, args.kw_defaults):
                    if default is not None and arg.arg in self.EVENT_ARGS:
                        mark(default)
        return found

    # -- SQL strings
    def _sql(self):
        identifiers = set()
        for cols in self.wave.schema.values():
            identifiers |= cols
        identifiers |= self.wave.tables | set(self.wave.glob.values())
        for group in string_groups(self.src):
            text, offsets = group_text(self.src, group)
            if not any(name in text for name in self.wave.old_names):
                continue
            line = self.src.line_of(group["start"])
            words = set(WORD.findall(text))
            hits = words & self.wave.old_names
            if not hits:
                continue
            if line in self.docstrings and group["triple"]:
                continue
            if group["start"] in self.event_names and re.fullmatch(r"[\w.]+", text):
                self.site("event-name", group["start"], sorted(hits)[0])
                continue
            if is_sql(text) or is_fragment(text, identifiers) or is_path(text, self.wave):
                edits, skipped = self._sql_edits_here(text, line)
                for a, b, new, old in edits:
                    sa, sb = offsets[a], offsets[b - 1]
                    if sa is None or sb is None:
                        continue
                    self.add(sa, sb + 1, new, "sql", old)
                for a, word in skipped:
                    if offsets[a] is not None:
                        self.site("sql-scoped-skip", offsets[a], word)
            elif text in self.wave.old_names:
                pass                                            # an exact literal: the AST pass decides
            elif " " in text.strip() or len(group["parts"]) > 1 or PLACEHOLDER in text:
                for word in sorted(hits):
                    if re.search(rf"(?<![\w$.-]){re.escape(word)}(?![\w-])", text) and not self._prose(text):
                        self.site("mention", group["start"], word)

    @staticmethod
    def _prose(text):
        words = WORD.findall(text)
        return len(words) >= 4 and sum(1 for w in words if w.islower() and "_" not in w) >= len(words) * 0.6

    # -- key literals
    def _const_edit(self, node, new, kind):
        start = self.src.ast_offset(node.lineno, node.col_offset)
        end = self.src.ast_offset(node.end_lineno, node.end_col_offset)
        raw = self.src.text[start:end]
        m = re.fullmatch(r"(?i)([ru]*)(['\"])(\w+)\2", raw)
        if not m:
            return
        self.add(start + len(m.group(1)) + 1, end - 1, new, kind, node.value)

    def _keys(self, tree):
        wave = self.wave
        names = wave.old_names
        self.sets_new = self._new_keys_set(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value not in names:
                    continue
                if self._drop_stale_pop(node):
                    continue
                self._key_literal(node)
            elif isinstance(node, ast.keyword) and node.arg in names:
                call = self.parents.get(node)
                dict_like = isinstance(call, ast.Call) and (
                    (isinstance(call.func, ast.Name) and call.func.id == "dict")
                    or (isinstance(call.func, ast.Attribute) and call.func.attr == "update")
                    or self._table_call(call, node.arg))
                model_new = self._model_call(call, node.arg) if isinstance(call, ast.Call) else None
                if model_new:
                    start = self.src.ast_offset(node.lineno, node.col_offset)
                    self.add(start, start + len(node.arg), model_new, "kwarg-model", node.arg)
                elif dict_like:
                    new = wave.glob.get(node.arg) or self._table_call(call, node.arg) or self.scoped_new(node.arg, node)
                    if new:
                        start = self.src.ast_offset(node.lineno, node.col_offset)
                        self.add(start, start + len(node.arg), new, "kwarg-dict", node.arg)
                    else:
                        self.site("key-scoped-skip", node.lineno, node.arg, line_is=True)
                else:
                    self.site("kwarg", node.lineno, node.arg, line_is=True)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and \
                    node.target.id in names and isinstance(self.parents.get(node), ast.ClassDef):
                new = wave.glob.get(node.target.id)
                if self.parents[node].name in self.keep.wire_models:
                    self.site("wire-field", node.lineno, node.target.id, line_is=True)
                elif new and self._field_class(self.parents[node]):
                    start = self.src.ast_offset(node.target.lineno, node.target.col_offset)
                    self.add(start, start + len(node.target.id), new, "class-field", node.target.id)
                else:
                    self.site("attribute", node.lineno, node.target.id, line_is=True)
            elif isinstance(node, ast.Attribute) and node.attr in names and node.attr in wave.glob:
                new = self._model_attribute(node)
                if new:
                    end = self.src.ast_offset(node.end_lineno, node.end_col_offset)
                    self.add(end - len(node.attr), end, new, "model-attribute", node.attr)
                else:
                    self.site("attribute", node.lineno, node.attr, line_is=True)

    def _new_keys_set(self, tree):
        """function id -> new names it sets as keys (x['refs'] = ..., {'refs': ...})."""
        news = set(self.wave.glob.values()) | {n for d in self.wave.scoped.values() for n in d.values()}
        out = defaultdict(set)
        for node in ast.walk(tree):
            key = None
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) and \
                    isinstance(node.slice, ast.Constant):
                key = node.slice.value
            elif isinstance(node, ast.Dict):
                for k in node.keys:
                    if isinstance(k, ast.Constant) and k.value in news:
                        out[self._func_of(node)].add(k.value)
            if key in news:
                out[self._func_of(node)].add(key)
        return out

    def _drop_stale_pop(self, node):
        """`value.pop('labels_json', None)` or `del value['labels_json']` in a function that sets value['labels']:
        it removed the raw column next to its decoded twin. After the rename the twin has the column's name, so the
        pop would remove the decoded value: drop the statement instead."""
        wave, old = self.wave, node.value
        new = wave.glob.get(old)
        if not new or new not in self.sets_new.get(self._func_of(node), ()):
            return False
        parent = self.parents.get(node)
        stmt = None
        if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute) and parent.func.attr == "pop" \
                and parent.args and parent.args[0] is node and len(parent.args) == 2 and \
                isinstance(self.parents.get(parent), ast.Expr):
            stmt = self.parents[parent]
        elif isinstance(parent, ast.Subscript) and isinstance(parent.ctx, ast.Del):
            holder = self.parents.get(parent)
            if isinstance(holder, ast.Delete) and len(holder.targets) == 1:
                stmt = holder
        if stmt is None:
            self.site("both-keys", node.lineno, old, line_is=True)
            return False
        start = self.src.starts[stmt.lineno - 1]
        end = self.src.starts[stmt.end_lineno] if stmt.end_lineno < len(self.src.starts) else len(self.src.text)
        before = self.src.text[start:self.src.ast_offset(stmt.lineno, stmt.col_offset)]
        after = self.src.text[self.src.ast_offset(stmt.end_lineno, stmt.end_col_offset):end]
        if before.strip() or after.strip().split("#")[0].strip():
            self.site("both-keys", node.lineno, old, line_is=True)    # shares its line: leave it to a person
            return True
        if not self.kept(old):
            self.edits[(start, end)] = ("", "pop-dropped", self.src.text[start:end])
            self.site("pop-dropped", node.lineno, old, line_is=True)
        return True

    def _func_of(self, node):
        cur = self.parents.get(node)
        while cur is not None and not isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            cur = self.parents.get(cur)
        return id(cur) if cur is not None else 0

    def _field_class(self, cls):
        if cls.name in (self.wave.models or {}):
            return True
        bases = {getattr(b, "id", getattr(b, "attr", "")) for b in cls.bases}
        return bool(bases & {"TypedDict", "BaseModel", "Model"}) or self.rel in ("backend/rows.py", "backend/models.py")

    def _annotated_models(self, func):
        """Parameter name -> model class names its annotation names (body: M.TaskCreate | None)."""
        out = {}
        models = self.wave.models or {}
        for arg in [*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs]:
            if arg.annotation is None:
                continue
            names = {getattr(n, "id", None) or getattr(n, "attr", None) for n in ast.walk(arg.annotation)
                     if isinstance(n, (ast.Name, ast.Attribute))}
            hit = {n for n in names if n in models}
            if hit:
                out[arg.arg] = hit
        return out

    def _model_attribute(self, node):
        """The new name of `param.old` when `param` is annotated with a model class that declares `old`."""
        if not isinstance(node.value, ast.Name):
            return None
        if self._wire_annotated(node):
            self.site("wire-field", node.lineno, node.attr, line_is=True)
            return None
        if node.value.id == "body" and node.attr in getattr(self.wave, "wire_fields", ()):
            self.site("wire-field", node.lineno, node.attr, line_is=True)   # maybe a wire body handed on: keep
            return None
        if node.value.id == "body" and any(node.attr in f for f in self.wave.models.values()):
            return self.wave.glob.get(node.attr)       # a request body handed on to a helper without its annotation
        cur = self.parents.get(node)
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and not isinstance(cur, ast.Lambda):
                classes = self._annotated_models(cur).get(node.value.id)
                if classes is not None:
                    if any(node.attr in self.wave.models[c] for c in classes):
                        return self.wave.glob.get(node.attr)
                    return None
            cur = self.parents.get(cur)
        return None

    def _foreign_siblings(self, dct, node):
        """Whether dict literal `dct` has other string keys and none is a column of a table holding `node`'s name:
        then it is a lookup table of words ({'started': TEMPLATE, 'blocked': ...}), not a row."""
        holders = [cols for cols in self.wave.schema.values() if node.value in cols]
        others = [k.value for k in dct.keys if k is not node and isinstance(k, ast.Constant) and isinstance(k.value, str)]
        return bool(others) and not any(k in cols for k in others for cols in holders)

    def _wire_body(self, node):
        """Whether dict literal `node` is (inside) the body of a call to a keep.yaml wire path."""
        cur = node
        while isinstance(self.parents.get(cur), (ast.Dict, ast.List, ast.Tuple, ast.keyword)):
            cur = self.parents[cur]
        return self._wire_call(self.parents.get(cur))

    def _wire_call(self, call):
        """Whether `call` (or a call it is chained on: `api.post(path).json()`) names a keep.yaml wire path."""
        while isinstance(call, ast.Call):
            texts = [a.value for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            texts += ["".join(v.value if isinstance(v, ast.Constant) else "{}" for v in a.values)
                      for a in call.args if isinstance(a, ast.JoinedStr)]
            if any(p.search(t) for p in self.keep.wire_paths for t in texts):
                return True
            call = call.func.value if isinstance(call.func, ast.Attribute) else None
        return False

    def _wire_annotated(self, node):
        """Whether `param` in `param.x` is annotated, in an enclosing function, with a keep.yaml wire model."""
        cur = self.parents.get(node)
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg in [*cur.args.posonlyargs, *cur.args.args, *cur.args.kwonlyargs]:
                    if arg.arg == node.value.id and arg.annotation is not None:
                        names = {getattr(n, "id", None) or getattr(n, "attr", None) for n in ast.walk(arg.annotation)}
                        return bool(names & self.keep.wire_models)
            cur = self.parents.get(cur)
        return False

    def _model_call(self, call, name):
        """The new name of keyword `name` in a call to a model class that declares it: M.TaskCreate(due=...)."""
        func = call.func
        cls = getattr(func, "id", None) or getattr(func, "attr", None)
        if cls in (self.wave.models or {}) and name in self.wave.models[cls]:
            return self.wave.glob.get(name)
        return None

    def _json_doc(self, node, parent):
        """Whether this key literal is a key inside a stored JSON document (see JSON_DOCS)."""
        recv = None
        if isinstance(parent, ast.Subscript):
            recv = parent.value
        elif isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute):
            recv = parent.func.value
        elif isinstance(parent, ast.Compare):
            recv = parent.comparators[0]
        if recv is not None:
            return json_doc_name(ast.unparse(recv)) in JSON_DOCS
        if isinstance(parent, ast.Dict):
            holder = self.parents.get(parent)
            if isinstance(holder, ast.Call) and isinstance(holder.func, ast.Attribute) and holder.func.attr == "dumps":
                return True
            if isinstance(holder, (ast.Assign, ast.AnnAssign)):
                targets = holder.targets if isinstance(holder, ast.Assign) else [holder.target]
                return any(json_doc_name(ast.unparse(t)) in JSON_DOCS for t in targets)
            if isinstance(holder, ast.Dict) and parent in holder.values:
                key = holder.keys[holder.values.index(parent)]
                return isinstance(key, ast.Constant) and key.value in JSON_DOCS
            if isinstance(holder, ast.keyword):
                return holder.arg in JSON_DOCS
        return False

    def _column_list(self, node):
        """A value literal that is a column name by its company: a literal list/tuple/set or the string arguments
        of a call that are mostly columns of one table holding the name, or sit next to SQL naming that table, or a
        {table: column} dict value."""
        wave, old = self.wave, node.value
        parent = self.parents.get(node)
        if isinstance(parent, ast.Call) and getattr(parent.func, "id", getattr(parent.func, "attr", "")) in (
                "field_validator", "validator", "field_serializer"):
            cls = self.parents.get(self.parents.get(parent))
            while cls is not None and not isinstance(cls, ast.ClassDef):
                cls = self.parents.get(cls)
            return cls is not None and old in (wave.models or {}).get(cls.name, ())
        holders = {t for t, cols in wave.schema.items() if old in cols}
        if not holders:
            return False
        if isinstance(parent, ast.Dict) and node in parent.values:
            key = parent.keys[parent.values.index(node)]
            return isinstance(key, ast.Constant) and key.value in holders
        if isinstance(parent, ast.Tuple) and parent.elts.index(node) >= 1:
            before = parent.elts[parent.elts.index(node) - 1]
            if isinstance(before, ast.Constant) and before.value in holders:
                return True              # (table, column, ...): ("attempts", "finished")
        if isinstance(parent, (ast.List, ast.Tuple)) and isinstance(self.parents.get(parent), ast.keyword) and \
                self.parents[parent].arg == "required":
            return True                  # a schema's required fields: the properties beside it are keys, renamed
        if isinstance(parent, ast.Tuple) and len(parent.elts) == 2 and parent.elts[0] is node:
            outer = self.parents.get(parent)
            if isinstance(parent.elts[1], ast.Constant) and isinstance(parent.elts[1].value, str) and \
                    re.match(r"(TEXT|INTEGER|REAL|BLOB|NUMERIC)\b", parent.elts[1].value):
                return True              # (column, declaration) for an ADD COLUMN
            if isinstance(outer, (ast.Tuple, ast.List)) and all(
                    isinstance(e, ast.Tuple) and len(e.elts) == 2 and isinstance(e.elts[0], ast.Constant)
                    for e in outer.elts):
                firsts = [e.elts[0].value for e in outer.elts]
                return any(sum(x in wave.schema[t] for x in firsts) >= max(3, 0.6 * len(firsts)) for t in holders)
        if isinstance(parent, (ast.Tuple, ast.List, ast.Set)):
            elts = parent.elts
        elif isinstance(parent, ast.Call) and node in parent.args:
            elts = parent.args
        else:
            return False
        strings = [e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        for text in strings:
            if text is not old and is_sql(text) and set(sql_tables(text)[0]) & holders:
                return True
        if len(strings) != len(elts) or not all(WORD.fullmatch(x) for x in strings):
            return False
        if len(strings) == 2:
            return all(x in wave.glob for x in strings) and any(set(strings) <= wave.schema[t] for t in holders)
        return len(strings) >= 3 and any(sum(x in wave.schema[t] for x in strings) >= max(3, 0.6 * len(strings))
                                         for t in holders)

    def _key_literal(self, node):
        wave, old = self.wave, node.value
        parent = self.parents.get(node)
        context = None
        if isinstance(parent, ast.Subscript) and parent.slice is node:
            context = "key"
        elif isinstance(parent, ast.Call) and node in parent.args and isinstance(parent.func, ast.Attribute) \
                and parent.func.attr in self.KEY_METHODS and parent.args[0] is node:
            context = "key"
        elif isinstance(parent, ast.Compare) and parent.left is node and \
                all(isinstance(op, (ast.In, ast.NotIn)) for op in parent.ops):
            comp = parent.comparators[0]
            context = "value" if isinstance(comp, (ast.Tuple, ast.List, ast.Set)) else "key"
        elif isinstance(parent, ast.Dict) and node in parent.keys:
            value = parent.values[parent.keys.index(node)]
            if isinstance(value, ast.Constant) and isinstance(value.value, (bool, int, float)) and \
                    not isinstance(value.value, str) and old not in wave.glob_json():
                self.site("key-bool", node.lineno, old, line_is=True)
                return
            if old not in wave.glob_json() and self._foreign_siblings(parent, node):
                self.site("key-foreign-dict", node.lineno, old, line_is=True)
                return
            context = "key"
        elif isinstance(parent, ast.Call) and node in parent.args and self._column_arg(parent, node):
            context = "column-arg"
        if context == "key" and isinstance(parent, ast.Dict) and self._wire_body(parent):
            self.site("wire-field", node.lineno, old, line_is=True)
            return
        if context == "key" and self._wire_call(parent.value if isinstance(parent, ast.Subscript) else
                                                 parent.func.value if isinstance(parent, ast.Call) else None):
            self.site("wire-field", node.lineno, old, line_is=True)   # a field of a runner answer: post(path)['x']
            return
        if context == "key" and old not in wave.glob_json() and self._json_doc(node, parent):
            self.site("json-key", node.lineno, old, line_is=True)
            return
        if context in ("key", "column-arg"):
            new = wave.glob.get(old) or self.scoped_new(old, node)
            if new:
                self._const_edit(node, new, context)
            else:
                self.site("key-scoped-skip", node.lineno, old, line_is=True)
            return
        # value positions: wave 1 names (*_json) are column names wherever they appear; others by their company
        if old in wave.glob_json() or (old in wave.glob and self._column_list(node)):
            self._const_edit(node, wave.glob[old], "value")
            self.site("renamed-value", node.lineno, old, line_is=True)
        elif not (isinstance(parent, ast.Compare) and node in parent.comparators):
            if isinstance(parent, ast.Expr):
                return
            self.site("value", node.lineno, old, line_is=True)

    def _table_call(self, call, column):
        """The new name of `column` when the call names, as a literal, a table that renames it:
        clone('messages', ..., refs_json=...)."""
        for arg in call.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value in self.wave.tables and \
                    column in self.wave.schema.get(arg.value, ()):
                return self.wave.table_new(arg.value, column)
        return None

    def _column_arg(self, call, node):
        """A string argument next to a literal table name that has this column: add_column(c, 'tasks', 'x', ...)."""
        for arg in call.args:
            if arg is not node and isinstance(arg, ast.Constant) and isinstance(arg.value, str) and \
                    arg.value in self.wave.tables and node.value in self.wave.schema.get(arg.value, ()):
                return True
        return False

    def _apply(self):
        text = self.src.text
        for (start, end), (new, kind, old) in sorted(self.edits.items(), reverse=True):
            if text[start:end] != old:
                continue
            if self.keep.site(self.rel, self.src.text, start):
                self.report.site("site-kept", self.rel, self.src.text.count("\n", 0, start) + 1, old,
                                 line(self.src.text, start))
                continue
            text = text[:start] + new + text[end:]
            self.report.edit(self.rel, kind)
        return text


def _glob_json(self):
    return {n for n in self.glob if n.endswith("_json")}


Wave.glob_json = _glob_json


# ---------------------------------------------------------------------------------------------------------------
# JavaScript

JS_PUNCT = re.compile(r"\?\.|\.\.\.|=>|===|!==|==|!=|<=|>=|&&|\|\||\?\?|[{}()\[\];,.:?=<>+\-*/%!&|^~@#]")
REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^") | {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete",
                                             "void", "throw", "yield", "await", "=>", "&&", "||", "??", "===",
                                             "!==", "==", "!=", "<=", ">="}


def js_tokens(text):
    """(kind, start, end, value) with kind in ident, string, template, punct, number, regex; comments dropped.
    Template literals yield their text chunks as 'template' tokens and their ${...} contents as code."""
    out, i, n = [], 0, len(text)
    prev_sig = None

    def lex_template(i):
        # i points just after a backtick or a closing '}' of ${}
        start = i
        while i < n:
            c = text[i]
            if c == "\\":
                i += 2
                continue
            if c == "`":
                out.append(("template", start, i, text[start:i]))
                return i + 1, False
            if c == "$" and text[i + 1:i + 2] == "{":
                out.append(("template", start, i, text[start:i]))
                return i + 2, True
            i += 1
        out.append(("template", start, n, text[start:n]))
        return n, False

    depth_stack = []
    depth = 0
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "'\"":
            j = i + 1
            while j < n and text[j] != c:
                if text[j] == "\\":
                    j += 1
                elif text[j] == "\n":
                    break
                j += 1
            out.append(("string", i, j + 1, text[i + 1:j]))
            prev_sig = "str"
            i = j + 1
            continue
        if c == "`":
            i, opened = lex_template(i + 1)
            if opened:
                depth_stack.append(depth)
                depth = 0
            prev_sig = "str"
            continue
        if c == "}" and depth_stack and depth == 0:
            depth = depth_stack.pop()
            i, opened = lex_template(i + 1)
            if opened:
                depth_stack.append(depth)
                depth = 0
            prev_sig = "str"
            continue
        if c == "/" and (prev_sig is None or prev_sig in REGEX_PREV):
            j, in_class = i + 1, False
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "[":
                    in_class = True
                elif text[j] == "]":
                    in_class = False
                elif text[j] == "/" and not in_class:
                    break
                elif text[j] == "\n":
                    break
                j += 1
            j += 1
            while j < n and text[j].isalpha():
                j += 1
            out.append(("regex", i, j, text[i:j]))
            prev_sig = "regex"
            i = j
            continue
        m = re.match(r"[A-Za-z_$][\w$]*", text[i:i + 200])
        if m:
            word = m.group(0)
            out.append(("ident", i, i + len(word), word))
            prev_sig = word if word in REGEX_PREV else "ident"
            i += len(word)
            continue
        m = re.match(r"\d[\w.]*", text[i:i + 100])
        if m:
            out.append(("number", i, i + len(m.group(0)), m.group(0)))
            prev_sig = "num"
            i += len(m.group(0))
            continue
        m = JS_PUNCT.match(text, i)
        if m:
            p = m.group(0)
            if p == "{":
                depth += 1
            elif p == "}":
                depth -= 1
            out.append(("punct", i, m.end(), p))
            prev_sig = p
            i = m.end()
            continue
        i += 1
    return out


# A line naming the browser's animation APIs: `.finished` there is an Animation's or a ViewTransition's promise.
JS_DOM_LINE = re.compile(r"getAnimations|startViewTransition|\.animate\(")
# A `{` after one of these opens a block (a function body, an if/else, a try), not an object literal or pattern.
JS_BLOCK_BEFORE = {")", "=>", "else", "try", "finally", "do", ";", "{", "}", ""}


def js_file(rel, text, wave, report, keep):
    toks = js_tokens(text)
    edits = {}
    names = wave.old_names
    brackets = []                    # stack of (char, index of token) for {[(
    for k, tok in enumerate(toks):
        kind, start, end, value = tok
        if kind == "punct" and value in "{[(":
            brackets.append((value, k))
        elif kind == "punct" and value in "}])" and brackets:
            brackets.pop()
        prev = toks[k - 1] if k else None
        nxt = toks[k + 1] if k + 1 < len(toks) else None
        if kind in ("string", "template"):
            for m in re.finditer(r"([?&])([a-z_]+)=", value):
                name = m.group(2)
                if name in wave.glob and not keep.name(rel, name):
                    a = start + (1 if kind == "string" else 0) + m.start(2)
                    edits[(a, a + len(name))] = (wave.glob[name], "url-param", name)
            if kind == "string" and value in names:
                if keep.name(rel, value):
                    continue
                new = wave.glob.get(value)
                subscript = prev and prev[3] == "[" and nxt and nxt[3] == "]"
                in_check = nxt and nxt[0] == "ident" and nxt[3] == "in"
                obj_key = nxt and nxt[3] == ":" and prev and prev[3] in ("{", ",") and brackets and \
                    brackets[-1][0] == "{"
                if new and (subscript or in_check or obj_key):
                    edits[(start + 1, end - 1)] = (new, "js-key", value)
                elif new is None:
                    report.site("js-scoped", rel, text.count("\n", 0, start) + 1, value, line(text, start))
                else:
                    report.site("js-string", rel, text.count("\n", 0, start) + 1, value, line(text, start))
            continue
        if kind != "ident" or value not in names or keep.name(rel, value):
            continue
        new = wave.glob.get(value)
        ln = text.count("\n", 0, start) + 1
        if new is None and value in wave.broad:
            new = wave.broad[value]
            if not (prev and prev[3] in (".", "?.")) and not rel.startswith("ui/tests/"):
                report.site("js-scoped", rel, ln, value, line(text, start))   # the app's object keys: often a request
                continue
        if new is None:
            if prev and prev[3] in (".", "?.") or (nxt and nxt[3] == ":"):
                report.site("js-scoped", rel, ln, value, line(text, start))
            continue
        if prev and prev[3] in (".", "?."):
            if nxt and nxt[3] == "(":            # a method (Playwright's response.finished()), not a field
                report.site("js-method", rel, ln, value, line(text, start))
                continue
            if JS_DOM_LINE.search(line(text, start)):   # an Animation's or a view transition's `finished` promise
                report.site("dom-api", rel, ln, value, line(text, start))
                continue
            owner = toks[k - 2] if k >= 2 else None
            if owner and owner[0] == "ident" and owner[3] in ("elements", "dataset", "form"):   # a form control, an attribute
                report.site("dom-name", rel, ln, value, line(text, start))
                continue
            if owner and owner[0] == "ident" and owner[3] in JSON_DOCS and not value.endswith("_json"):
                report.site("json-key", rel, ln, value, line(text, start))
                continue
            edits[(start, end)] = (new, "js-prop", value)
            continue
        in_object = brackets and brackets[-1][0] == "{" and prev and prev[3] in ("{", ",")
        if not in_object:
            continue
        opener = brackets[-1][1]
        before = toks[opener - 1][3] if opener else ""
        if before in ("import", "export") or before in JS_BLOCK_BEFORE:
            continue                     # a block, not an object: `let a = [], created = []` declares a variable
        if nxt and nxt[3] == ":" and not (prev and prev[3] == "?"):
            edits[(start, end)] = (new, "js-key", value)
        elif nxt and nxt[3] in (",", "}", "="):
            edits[(start, end)] = (f"{new}: {value}", "js-shorthand", value)
    out = text
    for (a, b), (new, kind, old) in sorted(edits.items(), reverse=True):
        if out[a:b] != old:
            continue
        if keep.site(rel, text, a):
            report.site("site-kept", rel, text.count("\n", 0, a) + 1, old, line(text, a))
            continue
        out = out[:a] + new + out[b:]
        report.edit(rel, kind)
    return out


def line(text, offset):
    a = text.rfind("\n", 0, offset) + 1
    b = text.find("\n", offset)
    return text[a:b if b >= 0 else len(text)]


# ---------------------------------------------------------------------------------------------------------------
# Rust

def rust_file(rel, text, wave, report, keep):
    edits = {}

    def rename(m, group, kind):
        name = m.group(group)
        if name in wave.glob and not keep.name(rel, name):
            edits[(m.start(group), m.end(group))] = (wave.glob[name], kind, name)
        elif name in wave.scoped:
            report.site("js-scoped", rel, text.count("\n", 0, m.start()) + 1, name, line(text, m.start()))

    for m in re.finditer(r'\[\s*"(\w+)"\s*\]', text):
        rename(m, 1, "rust-index")
    for m in re.finditer(r'\.(?:get|get_mut|remove|contains_key|pointer)\(\s*"/?(\w+)"', text):
        rename(m, 1, "rust-get")
    for m in re.finditer(r'#\[serde\([^\]]*?rename\s*=\s*"(\w+)"', text):
        rename(m, 1, "rust-serde")
    for m in re.finditer(r'"(\w+)"\s*:', text):
        if m.group(1) in wave.glob_json() and not keep.name(rel, m.group(1)):
            rename(m, 1, "rust-json-key")
        elif m.group(1) in wave.old_names and not keep.name(rel, m.group(1)):
            report.site("rust-json-key", rel, text.count("\n", 0, m.start()) + 1, m.group(1), line(text, m.start()))
    for m in re.finditer(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(\w+)\s*:\s*[A-Z&(]", text, re.M):
        if m.group(1) in wave.glob and not keep.name(rel, m.group(1)):
            report.site("rust-field", rel, text.count("\n", 0, m.start()) + 1, m.group(1), line(text, m.start()))
    # SQL in Rust string literals (the runner's own state files are not hub tables: only global names, and only in
    # statements naming a hub table)
    out = text
    for (a, b), (new, kind, old) in sorted(edits.items(), reverse=True):
        out = out[:a] + new + out[b:]
        report.edit(rel, kind)
    return out


# ---------------------------------------------------------------------------------------------------------------
# Markdown and JSON fixtures

def backtick_edits(text, wave):
    """Renames inside `backticked` spans of prose: an identifier, a dotted path (`bot_config.config_json.x`), a comma
    list of columns (`id, name, teams_json`), or SQL."""
    edits = []
    for m in re.finditer(r"(?<!`)`([^`]+)`(?!`)", text):
        span, base = m.group(1), m.start(1)
        if "\n\n" in span or ("\n" in span and not is_sql(span)):
            continue
        if is_sql(span):
            edits += [(base + a, base + b, new, old) for a, b, new, old in sql_edits(span, wave)[0]]
            continue
        if re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?:\[[^\]]*\])?(?:\.[A-Za-z_]\w*)*", span):
            segments = list(re.finditer(r"[A-Za-z_]\w*", span))
            for k, seg in enumerate(segments):
                old = seg.group(0)
                if old not in wave.old_names:
                    continue
                prev = segments[k - 1].group(0) if k else None
                if prev in wave.tables:
                    new = wave.table_new(prev, old)
                elif k == 0 or old.endswith("_json"):
                    new = wave.glob.get(old)
                else:
                    new = None
                if new:
                    edits.append((base + seg.start(), base + seg.end(), new, old))
            continue
        if re.fullmatch(r"[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)+", span):
            for seg in re.finditer(r"[A-Za-z_]\w*", span):
                if seg.group(0) in wave.glob:
                    edits.append((base + seg.start(), base + seg.end(), wave.glob[seg.group(0)], seg.group(0)))
    return edits


def md_file(rel, text, wave, report, keep):
    edits = {}
    fences = [(m.start(), m.end()) for m in re.finditer(r"^[ \t]*```.*?^[ \t]*```", text, re.S | re.M)]

    def in_fence(i):
        return any(a <= i < b for a, b in fences)

    for a, b in fences:
        block = text[a:b]
        if is_sql(block):
            for s, e, new, old in sql_edits(block, wave)[0]:
                if not keep.name(rel, old):
                    edits[(a + s, a + e)] = (new, "md-sql", old)
    for s, e, new, old in backtick_edits(text, wave):
        if not in_fence(s) and not keep.name(rel, old):
            edits[(s, e)] = (new, "md-ident", old)
    for name in wave.glob:
        if not name.endswith("_json"):
            continue
        for m in re.finditer(rf"(?<![\w`.]){name}(?![\w`])", text):
            if (m.start(), m.end()) not in edits and not in_fence(m.start()):
                report.site("md-other", rel, text.count("\n", 0, m.start()) + 1, name, line(text, m.start()))
    out = text
    for (a, b), (new, kind, old) in sorted(edits.items(), reverse=True):
        if out[a:b] != old:
            continue
        out = out[:a] + new + out[b:]
        report.edit(rel, kind)
    return out


def sh_file(rel, text, wave, report, keep):
    """Shell scripts with inline Python or sqlite3: SQL inside quoted strings, global names only."""
    edits = {}
    for m in re.finditer(r"\"([^\"\n]*)\"|'([^'\n]*)'", text):
        body = m.group(1) if m.group(1) is not None else m.group(2)
        base = m.start(1) if m.group(1) is not None else m.start(2)
        if is_sql(body):
            for a, b, new, old in sql_edits(body, wave)[0]:
                if old in wave.glob and not keep.name(rel, old):
                    edits[(base + a, base + b)] = (new, "sh-sql", old)
    out = text
    for (a, b), (new, kind, old) in sorted(edits.items(), reverse=True):
        if out[a:b] == old:
            out = out[:a] + new + out[b:]
            report.edit(rel, kind)
    return out


def json_fixture(rel, text, wave, report, keep):
    """Wire fixtures: rename object keys equal to global names (the runner contract carries row columns)."""
    out, n = text, 0
    for old, new in wave.glob.items():
        if keep.name(rel, old):
            continue
        out, k = re.subn(rf'"{old}"(\s*:)', rf'"{new}"\1', out)
        n += k
    for _ in range(n):
        report.edit(rel, "json-key")
    return out


# ---------------------------------------------------------------------------------------------------------------
# Driver

def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if not any(s in p for s in SKIP_PARTS)]


def classify(rel):
    if rel.endswith(".py") and (rel.split("/", 1)[0] in PY_DIRS or rel in PY_FILES):
        return "py"
    if rel.endswith((".js", ".cjs", ".mjs")) and any(glob_match(rel, g) for g in JS_GLOBS):
        return "js"
    if rel.endswith(".rs") and rel.startswith(tuple(d + "/" for d in RS_DIRS)):
        return "rs"
    if rel.endswith(".md") and (rel.split("/", 1)[0] in MD_DIRS or "/" not in rel):
        return "md"
    if rel.endswith(".json") and any(glob_match(rel, g) for g in JSON_GLOBS):
        return "json"
    if rel.endswith(".sh") and rel.split("/", 1)[0] in ("docker", "scripts", "setup", "deploy"):
        return "sh"
    return None


HANDLERS = {"js": js_file, "rs": rust_file, "md": md_file, "json": json_fixture, "sh": sh_file}


MODEL_ROOTS = {"TypedDict", "BaseModel"}
# Receivers whose keys are keys inside a stored JSON document (messages.refs, automations.flow triggers and steps,
# events.detail, meeting metadata). Existing rows keep the old keys, so these are reported, not renamed.
JSON_DOCS = {"refs", "trigger", "flow", "step", "detail", "meta", "metadata", "payload", "spec"}


def json_doc_name(text):
    """The last identifier of a receiver expression: self.trigger -> trigger, row['refs'] -> refs."""
    names = re.findall(r"[A-Za-z_]\w*", text.replace("'", " ").replace('"', " "))
    return names[-1] if names else ""


def model_classes(wave):
    """Class name -> the renamed fields it declares, for every pydantic model or TypedDict in the repo (a class
    whose bases reach BaseModel or TypedDict through classes defined here). Their fields are API or row field names,
    so a field, an attribute read through a parameter annotated with the class, and a keyword to its constructor
    are renamed together."""
    bases, fields = {}, defaultdict(set)
    for rel in tracked_files():
        if classify(rel) != "py":
            continue
        try:
            tree = ast.parse((ROOT / rel).read_text())
        except (SyntaxError, UnicodeDecodeError, FileNotFoundError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases.setdefault(node.name, set()).update(getattr(b, "id", getattr(b, "attr", "")) for b in node.bases)
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and \
                            item.target.id in wave.glob:
                        fields[node.name].add(item.target.id)

    def is_model(name, seen=()):
        if name in MODEL_ROOTS:
            return True
        return name not in seen and any(is_model(b, (*seen, name)) for b in bases.get(name, ()))

    return {name: fields.get(name, set()) for name in bases if is_model(name)}


def process(wave, keep, report, write, only=None):
    changed = []
    if wave.models is None:
        models = model_classes(wave)
        wave.models = {k: v for k, v in models.items() if k not in keep.wire_models}
        wave.wire_fields = set().union(*[v for k, v in models.items() if k in keep.wire_models])
    for rel in tracked_files():
        kind = classify(rel)
        if kind is None or keep.file(rel) or (only and not any(rel.startswith(o) for o in only)):
            continue
        path = ROOT / rel
        try:
            text = path.read_text()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        if not any(name in text for name in wave.old_names):
            continue
        if kind == "py":
            new = PyFile(rel, text, wave, report, keep).run()
        else:
            new = HANDLERS[kind](rel, text, wave, report, keep)
        if new != text:
            changed.append(rel)
            if write:
                path.write_text(new)
    return changed


# ---------------------------------------------------------------------------------------------------------------
# SQL sites the string pass could not settle: a fragment with no table of its own ("owner=?", "t.owner IN ..."). Waves
# past 2 rename common words, so these are most of what is left. A site is settled from what the code beside it says:
# an alias bound nearby, or the one table nearby that has the column. Tables that disagree stay a site, to fix by hand.

NEAR = 12
USUAL_ALIAS = {"t": "tasks", "tasks": "tasks", "m": "messages", "messages": "messages", "j": "jobs", "jobs": "jobs",
               "a": "attempts", "attempts": "attempts", "cv": "conversations", "conversations": "conversations"}
LEFT_TO_HAND = ("backend/events.py", "backend/finance/store/schema", "clients/agent_skill.py")   # NEW.x names a trigger's table


def settle_sql_sites(wave, report, write):
    sys.path.insert(0, str(HERE))
    from gen_map import EVERY_TABLE, load_schema
    everywhere = {t: set(c) for t, c in load_schema(EVERY_TABLE)[0].items()}
    columns = {t: set(c) | everywhere.get(t, set()) for t, c in wave.schema.items()}
    for t, c in everywhere.items():
        columns.setdefault(t, set(c))
    sites = report.sites.get("sql-scoped-skip", [])
    by_file = defaultdict(list)
    for site in sites:
        by_file[site[0]].append(site)
    left = []
    for rel, here in sorted(by_file.items()):
        if not rel.endswith(".py") or rel.startswith(LEFT_TO_HAND):
            left += here
            continue
        path = ROOT / rel
        lines = path.read_text().split("\n")
        changed = False
        for site in here:
            _, no, name, _text = site
            per_table = wave.scoped.get(name, {})
            text = lines[no - 1] if no <= len(lines) else ""
            window = " ".join(lines[max(0, no - NEAR):no + NEAR])

            def by_alias(m):
                bound = re.search(rf"\b(?:FROM|JOIN|UPDATE)\s+(\w+)(?:\s+AS)?\s+{re.escape(m[1])}\b", window)
                table = bound[1] if bound else USUAL_ALIAS.get(m[1])
                new = per_table.get(table)
                return f"{m[1]}.{new}" if new else m[0]

            fixed = re.sub(rf"\b(\w+)\.({re.escape(name)})\b", by_alias, text)
            nearby = set(re.findall(r"(?:FROM|JOIN|UPDATE|INTO|TABLE)\s+(\w+)", window)) | set(re.findall(r'"(\w+)\(', text))
            have = {t for t in nearby if name in columns.get(t, ())}
            if have and have <= set(per_table) and len({per_table[t] for t in have}) == 1:
                new = per_table[sorted(have)[0]]
                fixed = re.sub(rf"(?<![\w.$'\"]){re.escape(name)}(?![\w'\"(])", new, fixed)
            if fixed == text:
                left.append(site)
            else:
                lines[no - 1] = fixed
                changed = True
                report.edit(rel, "sql-settled")
        if changed and write:
            path.write_text("\n".join(lines))
    if sites:
        report.sites["sql-scoped-skip"] = left


GATING = ("sql-scoped-skip", "key-scoped-skip", "js-scoped", "dup-key")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wave", required=True, choices=WAVE_ORDER)
    parser.add_argument("--check", action="store_true", help="exit 1 if names from waves <= N remain in code")
    parser.add_argument("--only", nargs="*", help="limit to paths starting with these prefixes")
    parser.add_argument("--report-only", action="store_true",
                        help="write report-wave<N>.md without changing any file (a wave mapped but not applied)")
    args = parser.parse_args()
    data, waves, keep_data = load(args.wave)
    keep = Keep(keep_data)
    if args.check:
        failed = 0
        for w in WAVE_ORDER[:WAVE_ORDER.index(args.wave) + 1]:
            report = Report()
            changed = process(waves[w], keep, report, write=False, only=args.only)
            left = sum(report.renamed.values())
            gating = sum(len(report.sites.get(c, [])) for c in GATING)
            if w not in ("1", "2"):     # a per-table wave: most of these are other tables' columns of the same name
                print(f"wave {w}: {left} sites the codemod would rename, {gating} sites it cannot decide (not gating); "
                      "scripts/cleanup/check_sql.py checks its SQL")
                continue
            print(f"wave {w}: {left} sites still to rename in {len(changed)} files; {gating} unresolved sites")
            for rel in changed[:30]:
                print("  ", rel)
            failed |= bool(left or gating)
        return 1 if failed else 0
    report = Report()
    changed = process(waves[args.wave], keep, report, write=not args.report_only, only=args.only)
    if args.wave not in ("1", "2"):
        settle_sql_sites(waves[args.wave], report, write=not args.report_only)
    if args.only:
        print("--only: report not written")
    else:
        write_report(HERE / f"report-wave{args.wave}.md", args.wave, report, None, args.report_only)
    print(f"wave {args.wave}{' (report only)' if args.report_only else ''}: {sum(report.renamed.values())} renames in {len(changed)} files; "
          + ", ".join(f"{c} {len(s)}" for c, s in sorted(report.sites.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
