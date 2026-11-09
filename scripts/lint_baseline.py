"""Ratchet for the Python lint: the findings of each (file, rule) pair are counted, and the counts only go down.

Each source file's counts live in its own piece, baselines/ruff/<path>.json: how many findings each of its rules may
have. Counts are per (file, rule), never per line, so reformatting or moving code leaves them alone. The gate reads
ruff-baseline.toml, which extends ruff.toml and whose `[lint.per-file-ignores]` lists every pair that has a finding today,
so ruff passes on it. ruff.toml itself never mentions the baseline: a bare `ruff check` reports every finding.
ruff-baseline.toml is generated from the pieces and git-ignored: two PRs that change different files' counts change
different pieces, so a plain git merge has no conflict and no merge driver is needed.

    python3 scripts/lint_baseline.py --write          record the current counts in the pieces and regenerate ruff-baseline.toml
    python3 scripts/lint_baseline.py --check          fail on a finding past its count, on a count that fell without
                                                      --write, and on invalid `# noqa`
    python3 scripts/lint_baseline.py --check --base origin/main
                                                      also fail when a count grew, a suppression was added for a rule
                                                      that the ref already selects, or a rule was dropped from select
    python3 scripts/lint_baseline.py --generate       regenerate ruff-baseline.toml from the pieces (the gate reads it)
    python3 scripts/lint_baseline.py --migrate        turn ruff-baseline-counts.json of before the split into pieces

A rule that the --base ref does not select yet may bring its own baseline: that is how a rule is introduced, and its
findings become its starting counts. Only rules the ref already selects are held to "no growth". --write does not refuse
a higher count; the --base check does. --base reads the baseline of either layout, so a ref from before the split works.

The raw run reads ruff.toml alone, with every per-file ignore switched off (`--config "lint.per-file-ignores = {}"`), so
the comparison sees every real finding, and a `# noqa` that suppresses one counts as used. The gate reads the baseline,
where ruff reads a `# noqa` for a baselined rule as unused, so the gate ignores RUF100; the raw run still checks it. Test
files keep their own minimal rule set in ruff.toml and are not part of the baseline.
"""

import argparse
import ast
import collections
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import baseline_files

ROOT = Path(__file__).resolve().parent.parent
RUFF = ["uvx", "ruff@0.15.9"]
RULES_NAME = "ruff.toml"  # the rules; ruff.toml never refers to the baseline
BASELINE_NAME = "ruff-baseline.toml"  # generated from the pieces, git-ignored; extends ruff.toml
LAYOUT = "baselines/ruff"
OLD_COUNTS_NAME = "ruff-baseline-counts.json"  # the single counts file of before the split
RAW = ["--config", "lint.per-file-ignores = {}"]  # the raw run also switches off every per-file ignore
GATED_EXTRA = ["--extend-ignore", "RUF100"]
OWNER = "engineering"
# Never baselined: a blanket noqa comment (PGH004) must be fixed in the code.
NEVER_BASELINED = {"PGH004"}


def config_file():
    return ROOT / RULES_NAME


def baseline_file():
    return ROOT / BASELINE_NAME


def ruff_command(args, raw=False):
    """The `ruff check` command line, with its config named. A bare `ruff check` reads ruff.toml alone and reports every
    finding. The raw run reads ruff.toml with every per-file ignore off; the gate reads the generated baseline on top."""
    config = ["--config", RULES_NAME, *RAW] if raw else ["--config", BASELINE_NAME]
    return [*RUFF, "check", "--no-cache", *config, *args]


def ruff(args, raw=False):
    return subprocess.run(ruff_command(args, raw), cwd=ROOT, capture_output=True, text=True)


def relpath(filename):
    return os.path.relpath(os.path.realpath(filename), ROOT.resolve()).replace(os.sep, "/")


def raw_findings():
    """({(file, rule): [(line, message), ...]} for every finding with the baseline off, plus invalid-noqa warnings)."""
    done = ruff(["--output-format", "json"], raw=True)
    if done.returncode not in (0, 1):
        sys.exit(f"ruff failed ({done.returncode}):\n{done.stderr.strip()}")
    found = collections.defaultdict(list)
    for item in json.loads(done.stdout or "[]"):
        found[(relpath(item["filename"]), item["code"] or "syntax")].append((item["location"]["row"], item["message"]))
    invalid = sorted(
        {line.strip() for line in done.stderr.splitlines() if line.startswith("warning: Invalid") and "noqa" in line}
    )
    return found, invalid


def counts_of(found):
    return {pair: len(items) for pair, items in found.items()}


def load_counts(text):
    """{(file, rule): count} from one piece, or from the single counts file of before the split."""
    return {(path, code): n for path, codes in json.loads(text).items() for code, n in codes.items()}


def render_piece(path, codes):
    """The piece of source file `path`: {path: {rule: count}} as JSON, keys sorted."""
    return json.dumps({path: codes}, indent=2, sort_keys=True) + "\n"


def piece_path(path):
    """The piece of source file `path` (a repository path): baselines/ruff/<path>.json."""
    return ROOT / LAYOUT / (path + ".json")


def read_counts():
    """{(file, rule): count} from the pieces in the working tree."""
    counts = {}
    for piece in baseline_files.pieces(ROOT / LAYOUT):
        counts.update(load_counts(piece.read_text(encoding="utf-8")))
    return counts


def write_counts(counts):
    """Make the pieces hold `counts`: one piece per file with findings, and no piece for a file without any."""
    per_file = collections.defaultdict(dict)
    for (path, code), n in counts.items():
        per_file[path][code] = n
    texts = {piece_path(path): render_piece(path, codes) for path, codes in per_file.items()}
    baseline_files.write_pieces(ROOT / LAYOUT, texts)


def layout_problems():
    """Pieces that are not in the layout write_counts() writes: one file per piece, at its own path, in its canonical
    text, with at least one finding for every rule it lists."""
    problems = []
    for piece in baseline_files.pieces(ROOT / LAYOUT):
        where = piece.relative_to(ROOT).as_posix()
        text = piece.read_text(encoding="utf-8")
        data = json.loads(text)
        if len(data) != 1:
            problems.append(f"{where} holds {len(data)} files; a piece holds one")
            continue
        path, codes = next(iter(data.items()))
        if not codes or min(codes.values()) < 1:
            problems.append(f"{where} records no findings; delete it (python3 scripts/lint_baseline.py --write)")
        elif piece != piece_path(path) or text != render_piece(path, codes):
            problems.append(
                f"{where} is not in the layout scripts/lint_baseline.py writes; run python3 scripts/lint_baseline.py --write"
            )
    return problems


def render_toml(pairs):
    """The per-file-ignores table for `pairs`, an iterable of (file, rule). Generated from the pieces, never edited."""
    per_file = collections.defaultdict(list)
    for path, code in sorted(pairs):
        per_file[path].append(code)
    lines = [
        f'extend = "{RULES_NAME}"',
        "",
        "# Ruff baseline: findings that already exist, by file and rule. Generated and git-ignored from the pieces in",
        f"# {LAYOUT}/ by `python3 scripts/lint_baseline.py --generate`; do not edit it. The rules are in {RULES_NAME}.",
        f"# Owner of every rule listed here: {OWNER}. Fix findings, then re-run --write to lower their counts.",
        "# A count may only go down: `--check` fails on a finding past its count, and on a count that fell without --write.",
        "",
        "[lint.per-file-ignores]",
    ]
    for path in sorted(per_file):
        codes = ", ".join(json.dumps(code) for code in per_file[path])
        lines.append(f"{json.dumps(path)} = [{codes}]")
    return "\n".join(lines) + "\n"


def require_split():
    """Exit when git still tracks a single file of before the split: --migrate turns it into pieces first."""
    old = [name for name in (OLD_COUNTS_NAME, BASELINE_NAME) if baseline_files.tracked(ROOT, name)]
    if old:
        sys.exit(
            f"{' and '.join(old)} tracked by git (the single-file baseline from before the split): "
            "run python3 scripts/lint_baseline.py --migrate"
        )


def generate():
    """Write ruff-baseline.toml from the pieces; returns the counts. The gate's ruff run reads that file, so it calls this."""
    require_split()
    counts = read_counts()
    baseline_file().write_text(render_toml(counts), encoding="utf-8")
    return counts


def migrate():
    """Turn the single counts file of before the split into pieces, delete it, and untrack both old files. A branch from
    before the split runs this after its rebase, on its own copy of the counts file."""
    counts_path = ROOT / OLD_COUNTS_NAME
    if not (baseline_files.tracked(ROOT, OLD_COUNTS_NAME) or baseline_files.tracked(ROOT, BASELINE_NAME)):
        print("the ruff baseline is already in the per-file layout: nothing to migrate")
        return 0
    if not counts_path.exists():
        sys.exit(f"{OLD_COUNTS_NAME} is missing: restore it from the branch, then run --migrate")
    counts = load_counts(counts_path.read_text(encoding="utf-8"))
    write_counts(counts)
    baseline_files.untrack(ROOT, [OLD_COUNTS_NAME, BASELINE_NAME])
    counts_path.unlink()
    baseline_file().write_text(render_toml(counts), encoding="utf-8")
    print(
        f"migrated {OLD_COUNTS_NAME} to {LAYOUT}: {sum(counts.values())} findings in {len(counts)} (file, rule) pairs "
        f"across {len({p for p, _ in counts})} files; {BASELINE_NAME} is generated and git-ignored now"
    )
    return 0


def selects(code, select):
    """True when one of ruff's `select` prefixes covers `code` (`B` covers `B007`, `T10` covers `T100`)."""
    return any(code.startswith(prefix) for prefix in select)


def forwards_to(source, dest):
    """True when `source` is now only a forwarder to `dest`'s module (runner/engine.py: `from runner_support import
    engine as _real; sys.modules[__name__] = _real`, or `runpy.run_module("runner_support.engine", ...)`), not code.
    Ruff allowances are Python's, so only a Python file can be one. Each allowed statement must be one of the exact
    forms below; any other expression (an annotation, a default, a keyword, a call in a test) makes the file code."""
    path = ROOT / source
    if path.suffix != ".py" or Path(dest).suffix != ".py" or not path.is_file():
        return False
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, ValueError):
        return False
    parts = Path(dest).with_suffix("").parts
    module = ".".join(parts[:-1] if parts[-1] in ("__init__", "__main__") else parts)
    package, _, name = module.rpartition(".")
    alias = "_real"  # the name a forwarder's target import binds; its module swap and __getattr__ use it too
    reaches = []

    def is_const(node, value):
        return isinstance(node, ast.Constant) and type(node.value) is type(value) and node.value == value

    def is_name(node, ident):
        return isinstance(node, ast.Name) and node.id == ident

    def is_target_import(node):  # from <package> import <name> as _real, or import <module> as _real
        if isinstance(node, ast.ImportFrom):
            return (
                node.level == 0
                and node.module == package
                and len(node.names) == 1
                and node.names[0].name == name
                and node.names[0].asname == alias
            )
        return (
            isinstance(node, ast.Import)
            and len(node.names) == 1
            and node.names[0].name == module
            and node.names[0].asname == alias
        )

    def is_run_module_of_target(call):  # runpy.run_module("<module>"[, run_name="__main__"][, alter_sys=True])
        return (
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "run_module"
            and is_name(call.func.value, "runpy")
            and len(call.args) == 1
            and is_const(call.args[0], module)
            and all(
                (kw.arg == "run_name" and is_const(kw.value, "__main__"))
                or (kw.arg == "alter_sys" and is_const(kw.value, True))
                for kw in call.keywords
            )
        )

    def is_module_swap(node):  # sys.modules[__name__] = _real: no annotation (AnnAssign), no type comment
        return (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and node.type_comment is None
            and isinstance(node.targets[0], ast.Subscript)
            and isinstance(node.targets[0].value, ast.Attribute)
            and node.targets[0].value.attr == "modules"
            and is_name(node.targets[0].value.value, "sys")
            and is_name(node.targets[0].slice, "__name__")
            and is_name(node.value, alias)
        )

    def is_main_test(test):  # exactly __name__ == "__main__"
        return (
            isinstance(test, ast.Compare)
            and is_name(test.left, "__name__")
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
            and len(test.comparators) == 1
            and is_const(test.comparators[0], "__main__")
        )

    def is_getattr_forwarder(node):  # def __getattr__(name): return getattr(_real, name)
        if not (
            isinstance(node, ast.FunctionDef)
            and node.name == "__getattr__"
            and not node.decorator_list
            and node.returns is None
            and not getattr(node, "type_params", None)
        ):
            return False
        args = node.args
        if not (
            len(args.args) == 1
            and args.args[0].arg == "name"
            and args.args[0].annotation is None
            and args.args[0].type_comment is None
            and not (args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg or args.defaults)
        ):
            return False
        if len(node.body) != 1 or not isinstance(node.body[0], ast.Return):
            return False
        call = node.body[0].value
        return (
            isinstance(call, ast.Call)
            and is_name(call.func, "getattr")
            and not call.keywords
            and len(call.args) == 2
            and is_name(call.args[0], alias)
            and is_name(call.args[1], "name")
        )

    def allowed(node):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return True  # a string statement, such as the docstring
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if is_target_import(node):
                reaches.append(node)
                return True
            return isinstance(node, ast.Import) and all(
                a.name in ("sys", "runpy") and a.asname is None for a in node.names
            )
        if is_module_swap(node):
            return True
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):  # runpy.run_module("<module>", ...)
            if is_run_module_of_target(node.value):
                reaches.append(node)
                return True
            return False
        if isinstance(node, ast.If):  # if __name__ == "__main__": ... else: ...
            return is_main_test(node.test) and all(map(allowed, node.body)) and all(map(allowed, node.orelse))
        return is_getattr_forwarder(node)  # an AnnAssign or any other statement is code

    return all(map(allowed, tree.body)) and bool(reaches)


def moved_since(ref, suppressed=frozenset()):
    """Old path -> new path for each file git sees as moved since `ref`, so a moved file keeps its suppressions. A move
    that left a small file at the old path (a forwarder) shows as a copy: -B breaks the rewritten old path apart. A
    copy, or anything whose old path still exists, counts only when the old path is now a forwarder to the new one with
    no suppression of its own (`suppressed`: paths that still switch a rule off); otherwise the copy's suppressions
    would count at both paths."""
    done = subprocess.run(["git", "diff", "--name-status", "-B", "-M", ref], cwd=ROOT, capture_output=True, text=True)
    moves = {}
    for line in done.stdout.splitlines():
        fields = line.split("\t")
        if fields[0][:1] not in ("R", "C") or len(fields) != 3:
            continue
        source, dest = fields[1], fields[2]
        if (ROOT / source).exists() and (source in suppressed or not forwards_to(source, dest)):
            continue
        moves[source] = dest
    return moves


def renamed(pairs, moves):
    """`pairs` (a set of (file, rule), or a Counter of counts keyed by it) with each moved file's entries moved to its
    new path."""
    if isinstance(pairs, collections.Counter):
        out = collections.Counter()
        for (path, code), n in pairs.items():
            out[(moves.get(path, path), code)] += n
        return out
    return {(moves.get(path, path), code) for path, code in pairs}


def carry_moves(old_ignores, new_ignores, ref):
    """`old_ignores` with each moved file's suppressions transferred (not copied) to its new path."""
    return renamed(old_ignores, moved_since(ref, {path for path, _ in new_ignores}))


def suppression_set(ruff_text, pairs):
    """Every rule code that is switched off somewhere: per-file ignores, the baseline's `pairs`, and rules missing from
    `select`."""
    config = tomllib.loads(ruff_text).get("lint", {})
    ignores = set(pairs)
    for table in ("per-file-ignores", "extend-per-file-ignores"):
        for path, codes in config.get(table, {}).items():
            ignores |= {(path, code) for code in codes}
    return ignores, set(config.get("select", []))


def counts_at(ref):
    """{(file, rule): count} at git ref, in either layout: the single counts file of before the split, else the pieces.
    None when the ref has neither."""
    old = baseline_files.show_at(ROOT, ref, OLD_COUNTS_NAME)
    if old is not None:
        return load_counts(old)
    texts = baseline_files.pieces_at(ROOT, ref, LAYOUT)
    if not texts:
        return None
    counts = {}
    for text in texts:
        counts.update(load_counts(text))
    return counts


def check_growth(ref):
    """Fail when the baseline grew against `ref`. A rule that `ref` does not select yet may bring its own baseline;
    every other suppression, and every count, may only stay the same or go down."""
    old_counts = counts_at(ref)
    if old_counts is None:
        print(f"lint: {ref} has no ruff baseline yet; growth check skipped", flush=True)
        return []
    old_config = baseline_files.show_at(ROOT, ref, "ruff.toml") or ""
    new_counts = read_counts()
    new_ignores, new_select = suppression_set(config_file().read_text(encoding="utf-8"), set(new_counts))
    old_ignores, old_select = suppression_set(old_config, set(old_counts))
    moves = moved_since(ref, {path for path, _ in new_ignores})
    old_ignores = renamed(old_ignores, moves)

    def introduced(code):
        return selects(code, new_select) and not selects(code, old_select)

    problems = []
    for code in sorted(old_select - new_select):
        problems.append(f"rule removed from ruff.toml select: {code}")
    for path, code in sorted(new_ignores - old_ignores):
        if not introduced(code):
            problems.append(f"new suppression: {code} in {path} (fix it, or get it reviewed as a baseline change)")
    old_counts = renamed(collections.Counter(old_counts), moves)
    for path, code in sorted(new_counts):
        if not introduced(code) and new_counts[(path, code)] > old_counts[(path, code)]:
            problems.append(
                f"count grew: {path} {code} {old_counts[(path, code)]} -> {new_counts[(path, code)]} "
                "(fix the findings; a baseline may only shrink)"
            )
    added = sorted(rule for rule in new_select if not selects(rule, old_select))
    if added:
        starting = sum(n for (_, code), n in new_counts.items() if any(code.startswith(r) for r in added))
        print(
            f"lint: {ref} does not select {', '.join(added)} yet; their {starting} baselined findings are the start",
            flush=True,
        )
    return problems


def write():
    generate()  # refuses a checkout that still tracks the single files of before the split; the rewrite below regenerates
    found, _ = raw_findings()
    counts = counts_of(found)
    blocked = sorted(pair for pair in counts if pair[1] in NEVER_BASELINED)
    if blocked:
        for path, code in blocked:
            print(f"{path}: {code} must be fixed, not baselined", file=sys.stderr)
        return 1
    previous = read_counts()
    for path, code in sorted(counts):
        if counts[(path, code)] > previous.get((path, code), 0):
            print(
                f"warning: {path} {code} rose {previous.get((path, code), 0)} -> {counts[(path, code)]}; "
                "`--check --base origin/main` refuses a count that grew"
            )
    write_counts(counts)
    baseline_file().write_text(render_toml(counts), encoding="utf-8")
    per_rule = collections.Counter()
    for (_, code), n in counts.items():
        per_rule[code] += n
    print(
        f"wrote {LAYOUT}/ and {BASELINE_NAME}: {sum(counts.values())} findings in {len(counts)} (file, rule) "
        f"pairs across {len({p for p, _ in counts})} files"
    )
    for code in sorted(per_rule, key=lambda c: (-per_rule[c], c)):
        print(f"  {code:<8} {per_rule[code]:>5}")
    return 0


def check(base_ref):
    declared = generate()  # the gate's ruff run reads ruff-baseline.toml, so it is written from the pieces first
    problems = layout_problems()
    found, invalid = raw_findings()
    actual = counts_of(found)
    for pair in sorted(set(actual) | set(declared)):
        path, code = pair
        now, allowed = actual.get(pair, 0), declared.get(pair, 0)
        if now > allowed:
            details = [f"    {path}:{line}: {code} {message}" for line, message in sorted(found[pair])]
            problems.append(
                "\n".join(
                    [
                        f"new finding: {path} {code}: {now} found, the baseline allows {allowed} "
                        "(the new ones are among these)",
                        *details,
                    ]
                )
            )
        elif now < allowed:
            problems.append(
                f"count fell: {path} {code} {allowed} -> {now}; lower the baseline with "
                "`python3 scripts/lint_baseline.py --write`"
            )
    for path, code in sorted(declared):
        if code in NEVER_BASELINED:
            problems.append(f"{code} may not be baselined: {path}")
    problems += invalid
    gated = ruff(["--output-format", "concise", *GATED_EXTRA])
    if gated.returncode != 0 and not problems:
        problems.append("ruff with the baseline reports findings the raw comparison did not:\n" + gated.stdout)
    if base_ref:
        problems += check_growth(base_ref)
    for problem in problems:
        print(problem)
    if problems:
        print(f"lint: {len(problems)} problem(s); see scripts/lint_baseline.py for the rules")
        return 1
    print(
        f"lint: ok ({sum(actual.values())} findings, all within their counts; {sum(declared.values())} baselined "
        f"in {len(declared)} (file, rule) pairs)"
    )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--write", action="store_true", help="record the current counts in the pieces and regenerate the baseline"
    )
    mode.add_argument("--check", action="store_true", help="fail on new findings, lowered counts and stale entries")
    mode.add_argument("--generate", action="store_true", help=f"regenerate {BASELINE_NAME} from {LAYOUT}")
    mode.add_argument("--migrate", action="store_true", help=f"turn {OLD_COUNTS_NAME} of before the split into pieces")
    parser.add_argument("--base", metavar="REF", help="git ref to compare the baseline against (e.g. origin/main)")
    args = parser.parse_args(argv)
    if args.write:
        return write()
    if args.generate:
        counts = generate()
        print(
            f"wrote {BASELINE_NAME} from {LAYOUT}: {sum(counts.values())} findings in {len(counts)} (file, rule) pairs"
        )
        return 0
    if args.migrate:
        return migrate()
    return check(args.base)


if __name__ == "__main__":
    sys.exit(main())
