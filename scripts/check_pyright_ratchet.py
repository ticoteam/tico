"""Ratchet for scripts/check_types.sh: the basedpyright baseline and the legacy module list only shrink, and every backend
module added since the legacy list was written is strict.

basedpyright itself fails on new errors; check_types.sh only warns on baseline entries that no longer fire (flaky inference).
This script adds what it does not do:

    python3 scripts/check_pyright_ratchet.py            fail on a stale legacy entry, and on a module that is neither
                                                        listed as legacy nor strict
    python3 scripts/check_pyright_ratchet.py --base REF also fail when the baseline or the legacy list grew, or when
                                                        pyrightconfig.json loosened (more excludes, a strict path
                                                        removed, a weaker typeCheckingMode), against that git ref
    python3 scripts/check_pyright_ratchet.py --init     write scripts/pyright-legacy-modules.txt from today's modules
                                                        (once; it refuses to overwrite the list)

A module is strict when a path in pyrightconfig.json's "strict" list covers it, or one of its first 10 lines is
`# pyright: strict`. A new module may carry no other `# pyright:` mode comment. A legacy module is exempt; once it is
strict it must be removed from the list, so the list shrinks. Renames since REF carry their legacy and baseline entries
to the new path, so moving a file does not count as growth.

Migrations (backend/migrations/) are exempt from the strict rule: they run once, so they are checked at standard level
only.
"""

import argparse
import collections
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

import baseline_merge  # scripts/ is on sys.path when this runs as a script; the layout check uses its renderer

ROOT = Path(__file__).resolve().parent.parent
CONFIG = "pyrightconfig.json"
LEGACY = "scripts/pyright-legacy-modules.txt"
BASELINE = ".basedpyright/baseline.json"
DIRECTIVE_LINES = 10
MIGRATIONS = "backend/migrations/"  # run-once scripts: checked at standard level, never forced strict
DIRECTIVE = re.compile(r"^\s*#\s*pyright\s*:\s*(.*?)\s*$")
# basedpyright's typeCheckingMode values, weakest first. A config change may only keep the mode or raise it.
MODE_RANK = {"off": 0, "basic": 1, "standard": 2, "strict": 3, "recommended": 4, "all": 5}


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)


def git_show(ref, path):
    done = git("show", f"{ref}:{path}")
    return done.stdout if done.returncode == 0 else None


def excluded(rel, patterns):
    """True when a pyright exclude pattern covers the file: it matches the file or one of its parent directories."""
    parts = rel.split("/")
    for pattern in patterns:
        pattern = pattern.rstrip("/")
        if any(fnmatch.fnmatchcase("/".join(parts[:end]), pattern) for end in range(1, len(parts) + 1)):
            return True
    return False


def modules(config):
    """Every Python file pyright checks: under an include path, not excluded."""
    found = set()
    for item in config["include"]:
        base = ROOT / item
        candidates = [base] if base.is_file() else base.rglob("*.py")
        for path in candidates:
            rel = path.relative_to(ROOT).as_posix()
            if path.suffix == ".py" and not excluded(rel, config.get("exclude", [])):
                found.add(rel)
    return sorted(found)


def is_strict_path(rel, config):
    return any(rel == item or rel.startswith(item + "/") for item in (p.rstrip("/") for p in config.get("strict", [])))


def pyright_directives(text):
    """(line number, text) of each file-level `# pyright:` comment, except `# pyright: ignore` (a per-line suppression)."""
    for number, line in enumerate(text.splitlines(), 1):
        match = DIRECTIVE.match(line)
        if match and not match.group(1).startswith("ignore"):
            yield number, match.group(1)


def has_strict_directive(text):
    return any(number <= DIRECTIVE_LINES and directive == "strict" for number, directive in pyright_directives(text))


def read_list(text):
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def baseline_counts(text):
    counts = collections.Counter()
    for path, entries in json.loads(text).get("files", {}).items():
        counts[path[2:] if path.startswith("./") else path] += len(entries)
    return counts


def new_module_problems(path, text):
    directives = list(pyright_directives(text))
    if not any(number <= DIRECTIVE_LINES and directive == "strict" for number, directive in directives):
        return [
            f"new module must be strict: add `# pyright: strict` to {path} (in its first {DIRECTIVE_LINES} lines) "
            "and fix its errors, or baseline the ones a fix would change behavior with "
            "`scripts/check_types.sh --writebaseline` and say why in the PR"
        ]
    return [
        f"{path}:{number}: only `# pyright: strict` is allowed in a new module, not `# pyright: {directive}`"
        for number, directive in directives
        if directive != "strict"
    ]


def growth(ref, config, legacy):
    """Problems versus git ref `ref`. Skipped while the ref has no baseline (the first PR that adds one)."""
    old_baseline = git_show(ref, BASELINE)
    if old_baseline is None:
        print(f"pyright ratchet: {ref} has no {BASELINE} yet; growth checks skipped")
        return []
    renames = {}  # old path -> new path, for each file git sees as renamed since ref
    for line in git("diff", "--name-status", "-M", ref).stdout.splitlines():
        fields = line.split("\t")
        if len(fields) == 3 and fields[0].startswith("R"):
            renames[fields[1]] = fields[2]
    problems = []
    old_counts = collections.Counter()
    for path, count in baseline_counts(old_baseline).items():
        old_counts[renames.get(path, path)] += count
    current = (ROOT / BASELINE).read_text(encoding="utf-8") if (ROOT / BASELINE).exists() else '{"files": {}}'
    for path, count in sorted(baseline_counts(current).items()):
        if not (ROOT / path).exists():
            continue  # a key for a deleted or renamed file: basedpyright's lock mode already fails on it
        if count > old_counts.get(path, 0):
            problems.append(
                f"baseline grew: {path} has {count} entries, {ref} had {old_counts.get(path, 0)}; "
                "fix the error instead of baselining it"
            )
    old_legacy = git_show(ref, LEGACY)
    if old_legacy is not None:
        carried = {renames.get(path, path) for path in read_list(old_legacy)}
        for path in sorted(p for p in set(legacy) - carried if (ROOT / p).exists()):
            problems.append(f"legacy list grew: {path} (a new module must be strict; the list only shrinks)")
    old_config_text = git_show(ref, CONFIG)
    if old_config_text is not None:
        old_config = json.loads(old_config_text)
        for pattern in sorted(set(config.get("exclude", [])) - set(old_config.get("exclude", []))):
            problems.append(f"pyrightconfig.json exclude added: {pattern} (excluded code is not checked)")
        for path in sorted(set(old_config.get("strict", [])) - set(config.get("strict", []))):
            problems.append(f"pyrightconfig.json strict path removed: {path}")
        old_mode = old_config.get("typeCheckingMode", "standard")
        if MODE_RANK.get(config.get("typeCheckingMode"), -1) < MODE_RANK.get(old_mode, -1):
            problems.append(
                f"pyrightconfig.json typeCheckingMode weakened: {old_mode} -> {config.get('typeCheckingMode')}"
            )
    return problems


def init(config, found):
    path = ROOT / LEGACY
    if path.exists():
        print(f"{LEGACY} exists; edit it by hand and only remove lines", file=sys.stderr)
        return 1
    legacy = [rel for rel in found if not is_strict_path(rel, config)]
    lines = [
        "# Backend modules that predate the strict rule, one path per line. Generated once by",
        "# `python3 scripts/check_pyright_ratchet.py --init`. This list only shrinks: remove a line when its module is",
        "# deleted or becomes strict. A new module is never added here; it must carry `# pyright: strict` (migrations are exempt).",
    ]
    path.write_text("\n".join(lines + legacy) + "\n", encoding="utf-8")
    print(f"wrote {LEGACY}: {len(legacy)} modules ({len(found) - len(legacy)} already strict by path)")
    return 0


def layout_problems():
    """The baseline must be in the one layout scripts/baseline_merge.py writes; its merge driver depends on it."""
    path = ROOT / BASELINE
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if text != baseline_merge.render_pyright(baseline_merge.parse_pyright(text)):
        return [
            f"{BASELINE} is not in the layout scripts/baseline_merge.py writes; run scripts/check_types.sh --writebaseline"
        ]
    return []


def check(config, found, base):
    problems = layout_problems()
    legacy_text = (ROOT / LEGACY).read_text(encoding="utf-8") if (ROOT / LEGACY).exists() else None
    if legacy_text is None:
        return [f"missing {LEGACY}; create it once with `python3 scripts/check_pyright_ratchet.py --init`"]
    legacy = read_list(legacy_text)
    if legacy != sorted(set(legacy)):
        problems.append(f"{LEGACY} must be sorted with no duplicates")
    checked = set(found)
    for path in legacy:
        if path not in checked:
            problems.append(f"stale legacy entry, no such checked module (remove its line): {path}")
        elif is_strict_path(path, config) or has_strict_directive((ROOT / path).read_text(encoding="utf-8")):
            problems.append(f"legacy module is strict now (remove its line from {LEGACY}): {path}")
    legacy_set = set(legacy)
    for path in found:
        if path in legacy_set or is_strict_path(path, config) or path.startswith(MIGRATIONS):
            continue
        problems += new_module_problems(path, (ROOT / path).read_text(encoding="utf-8"))
    if base:
        problems += growth(base, config, legacy)
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--init", action="store_true", help=f"write {LEGACY} from today's modules (once)")
    parser.add_argument("--base", metavar="REF", help="git ref to compare growth against (e.g. origin/main)")
    args = parser.parse_args(argv)
    config = json.loads((ROOT / CONFIG).read_text(encoding="utf-8"))
    found = modules(config)
    if args.init:
        return init(config, found)
    problems = check(config, found, args.base)
    for problem in problems:
        print(problem)
    if problems:
        print(f"pyright ratchet: {len(problems)} problem(s); see scripts/check_pyright_ratchet.py")
        return 1
    strict = sum(1 for rel in found if is_strict_path(rel, config))
    print(
        f"pyright ratchet: ok ({len(found)} modules: {strict} strict by path, the rest legacy or strict by directive)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
