#!/usr/bin/env python3
"""Which browser scripts run each UI source file's code: scripts/ui-test-map.json.

A UI change selects its browser scripts from this map. ui/index.html lists the classic scripts every page loads. A run of
the browser scripts under scripts/ui-coverage.cjs (the measuring workflow, .github/workflows/ui-test-map.yml) records, per
script and file, how many of the file's functions ran, other than its top-level code: every page loads every file, so
loading says nothing. A file that at least SHARED_SHARE of the scripts run is "shared": it selects the core scripts (the
CORE list of scripts/ui-tests.cjs), never all of them. A file that no script runs has no entry, so the selector falls back
to its narrow default. The map is data: nothing edits it by hand.

    python3 scripts/ui_test_map.py build --measured DIR [--shared-share S] [--summary FILE]
                                     a measured run (coverage/, timings-*.json) -> the map
    python3 scripts/ui_test_map.py empty     the unmeasured map: every file falls back
    python3 scripts/ui_test_map.py check     fail on a file or script the map names that no longer exists (static job)
    python3 scripts/ui_test_map.py prune     drop the names that no longer exist, after a UI file or script is deleted
    python3 scripts/ui_test_map.py shard I N print the browser scripts of measuring shard I of N

ui_scripts_for(path, core=None) is what the selector calls: the scripts for a UI file, the core scripts for a shared
one, or None when the map has no entry for the file.
"""

import argparse
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = "scripts/ui-test-map.json"
# A file is shared when at least this share of the measured scripts runs its code: it is on nearly every page's path, so a
# change to it runs the core scripts rather than the scripts that happen to load it.
SHARED_SHARE = 0.9
FIELDS = ("files", "partial", "scripts", "shared", "shared_share", "smoke", "unexercised")


class MapError(Exception):
    """A measured run or the map does not hold together. The message says what to do."""


def tests(root=ROOT):
    """Every browser script (ui/tests/*.cjs) by name, sorted: the names the map uses."""
    return sorted(path.stem for path in (root / "ui" / "tests").glob("*.cjs"))


def served_files(root=ROOT):
    """The UI scripts the page loads (ui/index.html's script tags), by their path from ui/, in load order."""
    html = (root / "ui" / "index.html").read_text(encoding="utf-8")
    listed = []
    for src in re.findall(r'<script\s+src="/tico/ui/([^"?#]+)"', html):
        if src not in listed:
            listed.append(src)
    return [f"ui/{src}" for src in listed if (root / "ui" / src).is_file()]


def load(root=ROOT):
    return json.loads((root / MAP_PATH).read_text(encoding="utf-8"))


def write(data, root=ROOT):
    (root / MAP_PATH).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ui_scripts_for(path, core=None, root=ROOT):
    """The browser scripts that run UI file `path`: its list in the map. A shared file gets the core scripts (`core`, or the
    map's smoke list). None when the map has no entry for the file, so the caller falls back to its own default."""
    key = Path(path).as_posix()
    try:
        data = load(root)
    except FileNotFoundError:
        return None
    if key in data["shared"]:
        return sorted(data["smoke"] if core is None else core)
    if key in data["files"]:
        return list(data["files"][key])
    return None


def problems(root=ROOT):
    """What is wrong with the map on this tree: a field that does not hold together, or a name that no longer exists."""
    if not (root / MAP_PATH).is_file():
        return [f"{MAP_PATH} is missing: run python3 scripts/ui_test_map.py empty"]
    try:
        data = load(root)
    except json.JSONDecodeError as error:
        return [f"{MAP_PATH} is not JSON: {error}"]
    if not isinstance(data, dict) or set(data) != set(FIELDS):
        return [f"{MAP_PATH} must hold exactly these fields: {', '.join(FIELDS)}"]
    found = []
    scripts = set(tests(root))
    ui_names = set(data["shared"]) | set(data["unexercised"]) | set(data["files"])
    for name in sorted(ui_names):
        if not name.startswith("ui/") or not name.endswith((".js", ".ts")):
            found.append(f"{name} is not a UI script (ui/…js or ui/…ts)")
        elif not (root / name).is_file():
            found.append(f"{name} is in the map but no longer exists: run python3 scripts/ui_test_map.py prune")
    named_scripts = set(data["smoke"]) | set(data["partial"]) | {s for ran in data["files"].values() for s in ran}
    for name in sorted(named_scripts - scripts):
        found.append(
            f"{name} is in the map but ui/tests/{name}.cjs no longer exists: run python3 scripts/ui_test_map.py prune"
        )
    for field in ("shared", "unexercised", "smoke", "partial"):
        if data[field] != sorted(set(data[field])):
            found.append(f"{MAP_PATH}: {field} must be sorted and unique")
    for name, ran in data["files"].items():
        if not ran or ran != sorted(set(ran)):
            found.append(f"{MAP_PATH}: files[{name}] must be a sorted, nonempty list of scripts")
    overlap = (set(data["shared"]) & set(data["files"])) | (set(data["unexercised"]) & set(data["files"]))
    overlap |= set(data["shared"]) & set(data["unexercised"])
    for name in sorted(overlap):
        found.append(f"{name} appears in more than one of shared, unexercised and files")
    if data["shared"] and not data["smoke"]:
        found.append(f"{MAP_PATH} has shared files but no smoke scripts")
    return found


def prune(root=ROOT):
    """Drops every name that no longer exists, from the lists and from the files that ran them."""
    data = load(root)
    scripts = set(tests(root))

    def exists(name):
        return (root / name).is_file()

    data["shared"] = [name for name in data["shared"] if exists(name)]
    data["unexercised"] = [name for name in data["unexercised"] if exists(name)]
    files = {}
    for name, ran in data["files"].items():
        kept = [script for script in ran if script in scripts]
        if exists(name) and kept:
            files[name] = kept
    data["files"] = files
    data["smoke"] = [script for script in data["smoke"] if script in scripts]
    data["partial"] = [script for script in data["partial"] if script in scripts]
    write(data, root)
    return data


def empty(root=ROOT):
    """The unmeasured map: no file has an entry, so every UI file falls back to the selector's default."""
    data = {
        "files": {},
        "partial": [],
        "scripts": 0,
        "shared": [],
        "shared_share": SHARED_SHARE,
        "smoke": [],
        "unexercised": [],
    }
    write(data, root)
    return data


def smoke_list(root=ROOT):
    """The core scripts: what scripts/ui-tests.cjs runs with no names. `--list` prints them and runs nothing."""
    try:
        out = subprocess.run(
            ["node", "scripts/ui-tests.cjs", "--list"], cwd=root, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise MapError(f"could not read the core scripts from scripts/ui-tests.cjs: {error}") from error
    return sorted(line.strip() for line in out.splitlines() if line.strip())


def build(root, measured, share=SHARED_SHARE, smoke=None):
    """The map from a measured run. `measured` holds coverage/<script>.json for every browser script (scripts/ui-coverage.cjs
    writes it) and timings-<shard>.json for the scripts that passed. A script with no coverage record stops the build: its
    browser never started under the hook, so its files would be missing from the map without a sign."""
    measured = Path(measured)
    names = tests(root)
    records = {}
    for name in names:
        record = measured / "coverage" / f"{name}.json"
        if not record.is_file():
            raise MapError(f"no coverage record for {name}: the script did not start under scripts/ui-coverage.cjs")
        records[name] = json.loads(record.read_text(encoding="utf-8"))
    passed = set()
    for timing in sorted(measured.glob("timings-*.json")):
        passed |= set(json.loads(timing.read_text(encoding="utf-8")))
    ran_by = {}
    for file in served_files(root):
        ran_by[file] = [name for name in names if records[name]["files"].get(file, {}).get("ran", 0) > 0]
    shared = sorted(file for file, ran in ran_by.items() if ran and len(ran) >= share * len(names))
    data = {
        "files": {file: ran for file, ran in sorted(ran_by.items()) if ran and file not in shared},
        "partial": [name for name in names if name not in passed],
        "scripts": len(names),
        "shared": shared,
        "shared_share": share,
        "smoke": smoke_list(root) if smoke is None else sorted(smoke),
        "unexercised": sorted(file for file, ran in ran_by.items() if not ran),
    }
    write(data, root)
    return data, sorted(name for name in names if records[name].get("pages", 0) == 0)


def summary(data, no_page):
    """A short Markdown summary of a build, for the pull request body and the workflow summary."""
    per_file = [len(ran) for ran in data["files"].values()]
    lines = [
        f"- Browser scripts measured: {data['scripts']}; did not pass in the measured run: {len(data['partial'])}"
        f"{' (' + ', '.join(data['partial']) + ')' if data['partial'] else ''}",
        f"- Shared (run the {len(data['smoke'])} smoke scripts, share at least {data['shared_share']}): {len(data['shared'])}"
        f" ({', '.join(data['shared'])})",
        f"- Files with a list of scripts: {len(data['files'])}; median scripts per file: "
        f"{statistics.median(per_file) if per_file else 0}",
        f"- Files no script runs (narrow fallback): {len(data['unexercised'])}",
    ]
    if no_page:
        lines.append(f"- Scripts that opened no page: {', '.join(no_page)}")
    return "\n".join(lines) + "\n"


def shard(index, count, root=ROOT):
    """The browser scripts of one measuring shard. Round robin over the sorted names spreads the long and short ones."""
    if not 0 <= index < count:
        raise MapError(f"shard {index} is not in 0..{count - 1}")
    return tests(root)[index::count]


def main(argv=None):
    parser = argparse.ArgumentParser(description="The map from each UI file to the browser scripts that run its code.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="fail on a name the map holds that no longer exists")
    commands.add_parser("prune", help="drop the names that no longer exist")
    commands.add_parser("empty", help="write the unmeasured map")
    built = commands.add_parser("build", help="write the map from a measured run")
    built.add_argument("--measured", required=True, type=Path, help="the measured run's folder")
    built.add_argument("--shared-share", type=float, default=SHARED_SHARE)
    built.add_argument("--summary", type=Path, help="also write the Markdown summary here")
    sharded = commands.add_parser("shard", help="print one measuring shard's browser scripts")
    sharded.add_argument("index", type=int)
    sharded.add_argument("count", type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            found = problems()
            for line in found:
                print(line, file=sys.stderr)
            if found:
                return 1
            data = load()
            print(f"ui-test-map: {len(data['files'])} files, {len(data['shared'])} shared, all names exist")
        elif args.command == "prune":
            prune()
            print("ui-test-map: pruned")
        elif args.command == "empty":
            empty()
            print("ui-test-map: written unmeasured")
        elif args.command == "build":
            data, no_page = build(ROOT, args.measured, args.shared_share)
            text = summary(data, no_page)
            if args.summary:
                args.summary.write_text(text, encoding="utf-8")
            print(text, end="")
        elif args.command == "shard":
            print("\n".join(shard(args.index, args.count)))
    except MapError as error:
        print(f"ui-test-map: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
