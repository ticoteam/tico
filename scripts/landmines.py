"""Ratchet for the shortcuts that make a check pass without the code being right: fixed sleeps and waits, retries,
raised timeouts, and mocks of our own code in tests. skills/honest-code/SKILL.md says why and what to do instead.

Each rule counts its hits per file. A file's counts live in baselines/landmines/<path>.json, in the layout of
baselines/ruff (scripts/baseline_files.py), and the count of each (file, rule) must equal its baseline:

- above the baseline: every hit in that file is printed with the rule's message, and the check fails;
- below the baseline: the check fails as stale until --write lowers it in the same commit;
- a `ban` rule has no baseline: any hit fails.

    python3 scripts/landmines.py --check                     compare every count with its baseline, and check LESSONS.md
    python3 scripts/landmines.py --check --base origin/main  also fail when a baseline count is higher than on the ref
    python3 scripts/landmines.py --write                     record today's counts (ban rules are never recorded)
    python3 scripts/landmines.py --lessons                   check skills/honest-code/LESSONS.md alone

Python files are read with tokenize, so a pattern inside a string or a comment is not a hit (rules that need the raw
line say so). There is no inline opt-out: a line a rule should not match is fixed in the rule, with a test case.
A rule the --base ref's copy of this script does not have yet may bring its own baseline: that is how a rule starts.
"""

import argparse
import collections
import io
import json
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

import baseline_files

ROOT = Path(__file__).resolve().parent.parent
LAYOUT = "baselines/landmines"
LESSONS = "skills/honest-code/LESSONS.md"
# This script's own test holds every bad pattern as a fixture.
SELF = {"scripts/landmines.py", "scripts/tests/test_landmines.py"}

PY_TEST = re.compile(r"(^|/)(tests/.*\.py|test_[^/]*\.py|conftest\.py)$")
UI_TEST = re.compile(r"^ui/tests/.*\.(cts|ts|cjs|js|mjs)$")


def py_test(path):
    return bool(PY_TEST.search(path))


def ui_test(path):
    return bool(UI_TEST.match(path))


def retry_scope(path):
    return (
        py_test(path)
        or ui_test(path)
        or path == "pytest.ini"
        or path.startswith(".github/workflows/")
        or bool(re.match(r"(^|.*/)playwright\.config\.[a-z]+$", path))
    )


def big_t(match):
    return int(match.group(1).replace("_", "")) >= 40_000


@dataclass
class Rule:
    id: str
    mode: str  # "ratchet" or "ban"
    scope: object  # path -> bool
    patterns: list
    message: str
    lesson: str = ""
    view: str = "code"  # "code": strings and comments blanked (Python); "raw": the line as written
    keep: object = None  # match -> bool, to drop a match the pattern cannot exclude
    skip: list = field(default_factory=list)  # patterns that make a line not a hit

    def text(self):
        see = f" See {LESSONS}#{self.lesson}." if self.lesson else ""
        return f"{self.message}{see}"


RULES = [
    Rule(
        "no-retry",
        "ban",
        retry_scope,
        [
            re.compile(r"pytest\.mark\.flaky"),
            re.compile(r"rerunfailures"),
            re.compile(r"--reruns\b"),
            re.compile(r"\breruns\s*="),
            re.compile(r"^\s*retries\s*:\s*[0-9]"),
        ],
        "Do not retry a test. A test that passes on a rerun is flaky: fix its cause or delete it with its reason "
        "(SKILL.md, 'A test failed, then passed').",
        "L01",
    ),
    Rule(
        "no-fixed-sleep-py",
        "ratchet",
        py_test,
        [re.compile(r"\b(time|asyncio)\.sleep\(\s*(?!0\s*\)|0\.0\s*\))")],
        "Fixed sleep in a test. Wait on the event the fake emits (threading.Event, Stream.until), or move the clock "
        "with H.now or time_machine.travel(..., tick=False). A sleep guesses how long real work takes and fails on a "
        "loaded runner.",
        "L02",
    ),
    Rule(
        "no-fixed-wait-ui",
        "ratchet",
        ui_test,
        [re.compile(r"\bwaitForTimeout\("), re.compile(r"new Promise\b.*\bsetTimeout\(")],
        "Fixed wait in a browser script. Use until(check, what) or drawn(...) from support/e2e.cts, one "
        "page.waitForFunction that returns the value, or page.waitForResponse registered before the action.",
        "L02",
    ),
    Rule(
        "no-raised-timeout",
        "ratchet",
        ui_test,
        [re.compile(r"\btimeout\s*:\s*([0-9][0-9_]{3,})"), re.compile(r"(?<![\w.])t\(\s*([0-9][0-9_]*)\s*\)")],
        "A longer timeout is not a fix. Wait for the event the step depends on. A deadline goes through t() and fails "
        "with a message that names what never arrived.",
        "L26",
        keep=lambda m: not m.re.pattern.startswith("(?<!") or big_t(m),
    ),
    Rule(
        "no-unittest-mock",
        "ratchet",
        py_test,
        [
            re.compile(r"\bunittest\.mock\b"),
            re.compile(r"\bfrom\s+unittest\s+import\b.*\bmock\b"),
            re.compile(r"\b(MagicMock|AsyncMock|NonCallableMock)\b"),
            re.compile(r"\bmock\.patch\b"),
        ],
        "Do not replace our own code in a test. Run the real code. For something outside Tico use a fake "
        "(skills/honest-code/references/examples.md lists them) or pass the dependency in.",
        # environment values are not a double: this is what a unittest.TestCase has for monkeypatch.setenv
        skip=[re.compile(r"^(?!.*mock\.patch(?!\.dict\(\s*os\.environ)).*mock\.patch\.dict\(\s*os\.environ")],
    ),
    Rule(
        "no-global-time-patch",
        "ban",
        py_test,
        [
            re.compile(r"\b(?:setattr|patch\.object)\(\s*[\w.]*\btime\s*,\s*['\"]\w+['\"]"),
            re.compile(r"\b(?:setattr|patch)\(\s*['\"][\w.]*\btime\.\w+['\"]"),
        ],
        "This replaces sleep, time or monotonic on the time module for the whole process, so every thread sees it. "
        "Give the module its own seam (def _sleep(seconds): time.sleep(seconds); def _now(): return time.time()), "
        "as in clients/tico.py and docker/updater.py, and patch that, or use time_machine for the wall clock.",
        "L31",
        view="raw",
    ),
]
BY_ID = {rule.id: rule for rule in RULES}


def code_lines(text):
    """The lines of Python source with every string and comment blanked, so a pattern only matches code. Falls back to
    the raw lines when the file does not tokenize."""
    lines = text.splitlines()
    out = [list(line) for line in lines]
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type not in (tokenize.STRING, tokenize.COMMENT) and not (
                hasattr(tokenize, "FSTRING_MIDDLE") and tok.type == tokenize.FSTRING_MIDDLE
            ):
                continue
            (srow, scol), (erow, ecol) = tok.start, tok.end
            for row in range(srow, erow + 1):
                if row - 1 >= len(out):
                    break
                line = out[row - 1]
                start = scol if row == srow else 0
                end = ecol if row == erow else len(line)
                for i in range(start, min(end, len(line))):
                    line[i] = " "
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return lines
    return ["".join(line) for line in out]


def script_lines(text):
    """The lines of a JS or TS file, with whole-line comments blanked."""
    return ["" if line.lstrip().startswith(("//", "*", "/*")) else line for line in text.splitlines()]


def hits_in(path, text, rules):
    """{rule id: [(line number, line), ...]} for the rules whose scope takes `path`."""
    raw = text.splitlines()
    views = {}
    found = collections.defaultdict(list)
    for rule in rules:
        if not rule.scope(path):
            continue
        if rule.view == "raw":
            lines = views.setdefault("raw", script_lines(text) if not path.endswith(".py") else _no_comment_lines(raw))
        elif path.endswith(".py"):
            lines = views.setdefault("code", code_lines(text))
        else:
            lines = views.setdefault("script", script_lines(text))
        for number, line in enumerate(lines, 1):
            if any(pattern.search(line) for pattern in rule.skip):
                continue
            for pattern in rule.patterns:
                if any(rule.keep is None or rule.keep(m) for m in pattern.finditer(line)):
                    found[rule.id].append((number, raw[number - 1].strip() if number <= len(raw) else ""))
                    break
    return found


def _no_comment_lines(raw):
    return ["" if line.lstrip().startswith("#") else line for line in raw]


def tracked_files():
    done = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    return sorted(name for name in done.stdout.decode().split("\0") if name)


def scan(rules=RULES):
    """{path: {rule id: [(line number, line), ...]}} over every tracked file some rule takes."""
    result = {}
    for path in tracked_files():
        if path in SELF or not any(rule.scope(path) for rule in rules):
            continue
        file = ROOT / path
        if not file.is_file():
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        found = hits_in(path, text, rules)
        if found:
            result[path] = dict(found)
    return result


def counts_of(found):
    return {
        path: {rule: len(lines) for rule, lines in sorted(rules.items()) if BY_ID[rule].mode != "ban"}
        for path, rules in found.items()
    }


def piece_path(path):
    return ROOT / LAYOUT / f"{path}.json"


def render_piece(path, rules):
    return json.dumps({path: dict(sorted(rules.items()))}, indent=2) + "\n"


def load_pieces(texts):
    counts = {}
    for text in texts:
        for path, rules in json.loads(text).items():
            counts[path] = {rule: int(n) for rule, n in rules.items()}
    return counts


def read_baseline():
    return load_pieces(piece.read_text(encoding="utf-8") for piece in baseline_files.pieces(ROOT / LAYOUT))


def baseline_at(ref):
    return load_pieces(baseline_files.pieces_at(ROOT, ref, LAYOUT))


def write():
    counts = {path: rules for path, rules in counts_of(scan()).items() if any(rules.values())}
    baseline_files.write_pieces(
        ROOT / LAYOUT, {piece_path(path): render_piece(path, rules) for path, rules in counts.items()}
    )
    total = sum(sum(rules.values()) for rules in counts.values())
    print(f"landmines: wrote {total} hits in {len(counts)} files to {LAYOUT}/")


def compare(found, baseline):
    """The problems of `found` against `baseline`, one string each."""
    problems = []
    for path in sorted(set(found) | set(baseline)):
        rules = found.get(path, {})
        allowed = baseline.get(path, {})
        for rule_id in sorted(set(rules) | set(allowed)):
            rule = BY_ID.get(rule_id)
            if rule is None:
                problems.append(f"{LAYOUT}/{path}.json: unknown rule {rule_id}; remove it")
                continue
            lines = rules.get(rule_id, [])
            limit = 0 if rule.mode == "ban" else allowed.get(rule_id, 0)
            if rule.mode == "ban" and rule_id in allowed:
                problems.append(f"{LAYOUT}/{path}.json: {rule_id} is a ban and has no baseline; remove the entry")
            if len(lines) > limit:
                head = f"{rule_id}: {path} has {len(lines)}, its baseline allows {limit}. {rule.text()}"
                problems.append("\n".join([head, *(f"  {path}:{n}: {line}" for n, line in lines)]))
            elif len(lines) < limit:
                problems.append(
                    f"{rule_id}: {path} now has {len(lines)}; lower its baseline to {len(lines)} with "
                    "`python3 scripts/landmines.py --write` in this commit"
                )
    return problems


def rules_at(ref):
    """The rule ids the ref's copy of this script defines, or None when the ref has no copy."""
    text = baseline_files.show_at(ROOT, ref, "scripts/landmines.py")
    if text is None:
        return None
    return set(re.findall(r'^\s*Rule\(\s*\n\s*"([a-z0-9-]+)"', text, re.M))


def growth(baseline, base_baseline, base_rules):
    """A baseline count higher than on the base, for a rule the base already has."""
    if base_rules is None:
        return []
    problems = []
    for path, rules in sorted(baseline.items()):
        for rule_id, count in sorted(rules.items()):
            if rule_id not in base_rules:
                continue
            before = base_baseline.get(path, {}).get(rule_id, 0)
            if count > before:
                problems.append(
                    f"{rule_id}: the baseline for {path} grew from {before} to {count}. A baseline only goes down. "
                    f"{BY_ID[rule_id].text() if rule_id in BY_ID else ''}".rstrip()
                )
    return problems


# LESSONS.md

INCIDENT = re.compile(r"^\d{4}-\d{2}-\d{2} (#\d+|[0-9a-f]{10})\b")


def split_incidents(cell):
    """The incidents of a row: split on `;` outside brackets, so a note in brackets may hold a `;`."""
    parts, depth, current = [], 0, ""
    for char in cell:
        depth += {"(": 1, ")": -1}.get(char, 0)
        if char == ";" and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return [part.strip() for part in parts if part.strip()]


def lesson_rows(text):
    rows = []
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 7 and re.fullmatch(r"L\d+", cells[0]):
            rows.append(
                dict(zip(["id", "lesson", "incidents", "class", "seen", "status", "where"], cells, strict=True))
            )
    return rows


def lesson_problems(text, rule_ids, exists):
    problems = []
    rows = lesson_rows(text)
    ids = {row["id"] for row in rows}
    if not rows:
        return [f"{LESSONS}: no lesson rows found"]
    for row in rows:
        where = f"{LESSONS} {row['id']}"
        incidents = split_incidents(row["incidents"])
        for incident in incidents:
            if not INCIDENT.match(incident):
                problems.append(f"{where}: incident {incident!r} needs a date and a PR number or 10-character SHA")
        if not row["seen"].isdigit() or int(row["seen"]) != len(incidents):
            problems.append(f"{where}: seen is {row['seen']} but it lists {len(incidents)} incidents")
        if row["class"] not in ("script", "judgment"):
            problems.append(f"{where}: class must be script or judgment")
        status = row["status"]
        kind = status.split(" ", 1)[0]
        if kind not in ("open", "proposed", "encoded", "example", "retired"):
            problems.append(f"{where}: unknown status {status!r}")
        if row["class"] == "script" and kind == "open" and row["seen"].isdigit() and int(row["seen"]) >= 2:
            problems.append(
                f"{where}: a script lesson seen {row['seen']} times is still open. Add its rule to "
                "scripts/landmines.py (or ruff) and mark it encoded, or mark it proposed with the rule id"
            )
        if kind == "encoded":
            named = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)+", status.split("`")[0]) if "`" not in status else []
            paths = re.findall(r"`([^`]+)`", status)
            for name in named:
                if name not in rule_ids:
                    problems.append(f"{where}: encoded rule {name} is not in scripts/landmines.py")
            for path in paths:
                if not exists(path.split()[0]):
                    problems.append(f"{where}: encoded check {path} does not exist")
            if not named and not paths:
                problems.append(f"{where}: an encoded row names its rule id or `script path`")
        if row["where"] not in ("-", ""):
            path = row["where"].strip("`")
            if not exists(path):
                problems.append(f"{where}: where path {path} does not exist; fix it or retire the row")
    for rule in RULES:
        if rule.lesson and rule.lesson not in ids:
            problems.append(f"scripts/landmines.py {rule.id}: cites {rule.lesson}, which is not in {LESSONS}")
    return problems


def check_lessons():
    file = ROOT / LESSONS
    if not file.is_file():
        return [f"{LESSONS} is missing"]
    text = file.read_text(encoding="utf-8")
    problems = lesson_problems(text, set(BY_ID), lambda path: (ROOT / path).exists())
    proposed = [row["id"] for row in lesson_rows(text) if row["status"].startswith("proposed")]
    if proposed:
        print(f"landmines: {len(proposed)} lessons wait for a rule: {', '.join(proposed)}")
    return problems


def check(base):
    found = scan()
    baseline = read_baseline()
    problems = compare(found, baseline)
    if base:
        problems += growth(baseline, baseline_at(base), rules_at(base))
    problems += check_lessons()
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    action.add_argument("--lessons", action="store_true")
    parser.add_argument("--base", metavar="REF", help="git ref to compare the baseline against (e.g. origin/main)")
    args = parser.parse_args(argv)
    if args.write:
        write()
        return 0
    problems = check_lessons() if args.lessons else check(args.base)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"landmines: {len(problems)} problems; skills/honest-code/SKILL.md says how to fix each", file=sys.stderr)
        return 1
    print("landmines: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
