#!/usr/bin/env python3
"""Which browser scripts run each UI file's code: the UI test map, published to the ci/ui-test-map branch.

The measuring workflow (.github/workflows/ui-test-map.yml) runs every browser script (ui/tests/<name>.cjs) under
scripts/ui-coverage.cjs, and the boot baseline (scripts/ui-boot-baseline.cjs), which loads the app once and does nothing
else. A script exercises a UI file when it calls one of the file's functions more often, per page load, than the boot load
does, or calls one the boot load never calls. Loading the app runs every file's top-level code, so loading alone says
nothing. A UI file that at least SHARED_SHARE of the scripts exercise is "shared": a change to it runs the core scripts (the
CORE list of scripts/ui-tests.cjs) and its own list. A UI file the map does not list (CSS, a new file, an unexercised one, or
no map at all) keeps the selector's rule for files it does not know. The map is data: the workflow publishes it as the only
file on ci/ui-test-map, and nothing edits it by hand.

A UI file is keyed by its path without .js or .ts: a file converted from .js to .ts keeps its entry, so the map is not stale
after that rename. Names a later change deleted are dropped when the map loads.

    python3 scripts/ui_test_map.py check --map PATH    the schema of a published map
    python3 scripts/ui_test_map.py build --measured DIR --out PATH --main-sha SHA --run URL [--summary PATH]
                                                       a measured run -> the map
    python3 scripts/ui_test_map.py shard I N           the browser scripts of measuring shard I of N

The selector reads the map named by TICO_UI_TEST_MAP (the plan job fetches it from ci/ui-test-map): lookup(load_map(...),
path) answers for one file. ui_scripts_for(path) is the same answer as a list, and None when the map has no entry.
"""

import argparse
import functools
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MAP_ENV = "TICO_UI_TEST_MAP"
VERSION = 3
# A file is shared when at least this share of the browser scripts exercises it: it is on nearly every script's path, so a
# change to it runs the core scripts and its own list, not only the scripts that happen to use it.
SHARED_SHARE = 0.9
# The boot baseline's record name. No browser script may take it.
BOOT = "boot"
EXTENSIONS = (".js", ".ts")
FIELDS = ("files", "partial", "scripts", "shared", "shared_share", "source", "unexercised", "version")
SOURCE_FIELDS = ("main", "run")
SCRIPT_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
SHA = re.compile(r"[0-9a-f]{40}")


class MapError(Exception):
    """A measured run or a map does not hold together. The message says what to do."""


def stem(path):
    """A UI file's key: its path without .js or .ts. The page loads a .ts file as its compiled .js or as .ts, so both names
    are one module."""
    key = Path(path).as_posix()
    for extension in EXTENSIONS:
        if key.endswith(extension):
            return key[: -len(extension)]
    return key


def source_of(root, key):
    """The source file a key names (ui/app/x.ts or ui/app/x.js), or None when neither is in the tree."""
    for extension in EXTENSIONS:
        if (root / (key + extension)).is_file():
            return key + extension
    return None


def tests(root=ROOT):
    """Every browser script (ui/tests/*.cjs) by name, sorted: the names the map uses."""
    return sorted(path.stem for path in (root / "ui" / "tests").glob("*.cjs"))


def served_files(root=ROOT):
    """The UI files ui/index.html loads, as keys (stem), in load order, that exist."""
    html = (root / "ui" / "index.html").read_text(encoding="utf-8")
    listed = []
    for src in re.findall(r'<script\s+src="/tico/ui/([^"?#]+)"', html):
        if src not in listed:
            listed.append(src)
    return [stem(f"ui/{src}") for src in listed if (root / "ui" / src).is_file()]


@functools.lru_cache(maxsize=None)
def core_list(root=ROOT):
    """The core scripts: what scripts/ui-tests.cjs runs with no names. `--list` prints them and runs nothing."""
    try:
        out = subprocess.run(
            ["node", "scripts/ui-tests.cjs", "--list"], cwd=root, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise MapError(f"could not read the core scripts from scripts/ui-tests.cjs: {error}") from error
    return tuple(sorted(line.strip() for line in out.splitlines() if line.strip()))


def _is_file_name(name):
    """A UI file's key: a path under ui/ without its extension."""
    return (
        isinstance(name, str)
        and re.fullmatch(r"ui/[^\s]+", name) is not None
        and ".." not in name
        and not name.endswith(EXTENSIONS)
    )


def _is_script_name(name):
    return isinstance(name, str) and SCRIPT_NAME.fullmatch(name) is not None


def _is_count(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _names(value):
    """The strings of a list, as a set; nothing when the value is not a list of strings (validate() reports that)."""
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return set(value)
    return set()


def _sorted_unique(value, check, nonempty=False):
    """Why a list is not a sorted, unique list of valid names (None when it is one)."""
    if not isinstance(value, list) or not all(check(item) for item in value):
        return "must be a list of valid names"
    if value != sorted(set(value)):
        return "must be sorted and without repeats"
    if nonempty and not value:
        return "must not be empty"
    return None


def validate(data):
    """What is wrong with a map's shape, as messages (empty when it holds together). It does not look at the tree: a name a
    later change deleted is pruned when the map loads, not refused here."""
    if not isinstance(data, dict) or set(data) != set(FIELDS):
        return [f"a map holds exactly these fields: {', '.join(FIELDS)}"]
    found = []
    if data["version"] != VERSION:
        found.append(f"version must be {VERSION}")
    source = data["source"]
    well_formed = (
        isinstance(source, dict)
        and set(source) == set(SOURCE_FIELDS)
        and isinstance(source["main"], str)
        and SHA.fullmatch(source["main"]) is not None
        and isinstance(source["run"], str)
        and source["run"] != ""
    )
    if not well_formed:
        found.append("source holds main (the main commit's 40-character SHA) and run (the measuring run's URL)")
    if not _is_count(data["scripts"]) or data["scripts"] < 1:
        found.append("scripts is the number of measured browser scripts (at least 1)")
    share = data["shared_share"]
    if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 < share <= 1:
        found.append("shared_share is a number above 0 and at most 1")
    for field, check in (("shared", _is_file_name), ("unexercised", _is_file_name), ("partial", _is_script_name)):
        problem = _sorted_unique(data[field], check)
        if problem:
            found.append(f"{field} {problem}")
    files = data["files"]
    if not isinstance(files, dict):
        found.append("files maps each UI file to the browser scripts that run it")
    else:
        for name, ran in files.items():
            if not _is_file_name(name):
                found.append(f"files has {name!r}, which is not a UI file (its path under ui/, without .js or .ts)")
            problem = _sorted_unique(ran, _is_script_name, nonempty=True)
            if problem:
                found.append(f"files[{name}] {problem}")
        for name in sorted(_names(data["shared"]) - set(files)):
            found.append(f"shared {name} has no list of scripts in files")
        for name in sorted(set(files) & _names(data["unexercised"])):
            found.append(f"{name} is in both files and unexercised")
    return found


def read_map(path):
    """The map in `path`, as JSON. Its shape is not checked here: validate() does that."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MapError(f"{path} is not a readable map: {error}") from error


def prune(data, root=ROOT):
    """The map without the names this tree no longer has: a UI file or browser script deleted or renamed since the map was
    measured. A file whose list empties is dropped: it has no entry, and the selector keeps its rule for such a file."""
    scripts = set(tests(root))
    files = {}
    for key, ran in data["files"].items():
        kept = [script for script in ran if script in scripts]
        if kept and source_of(root, key) is not None:
            files[key] = kept
    return {
        **data,
        "files": files,
        "partial": [script for script in data["partial"] if script in scripts],
        "shared": [key for key in data["shared"] if key in files],
        "unexercised": [key for key in data["unexercised"] if source_of(root, key) is not None],
    }


def load_map(root=ROOT, map_path=None):
    """The published map, pruned to this tree, or None when there is none: no path given and TICO_UI_TEST_MAP unset, or the
    file is missing (the branch or its file is not there yet). A map that is there but does not validate is an error."""
    if map_path is None:
        map_path = os.environ.get(MAP_ENV, "")
        if not map_path:
            return None
    if not Path(map_path).is_file():
        return None
    data = read_map(map_path)
    found = validate(data)
    if found:
        raise MapError(f"{map_path} does not validate: " + "; ".join(found))
    return prune(data, root)


def lookup(data, path):
    """What the map holds for UI file `path` (any extension): (its browser scripts, shared), or None when it has no entry.
    `data` is a loaded map (load_map), or None when there is no map."""
    if data is None:
        return None
    key = stem(path)
    if key not in data["files"]:
        return None
    return list(data["files"][key]), key in data["shared"]


def ui_scripts_for(path, core=None, root=ROOT, map_path=None):
    """The browser scripts a change to UI file `path` runs, as a list; None when the map has no entry for it, and the selector
    keeps its rule for files it does not know. A shared file runs `core` (default: the list scripts/ui-tests.cjs prints) and its
    own scripts. A browser script runs itself while it is in the tree; a deleted one has no entry (None). Raises MapError for a
    path outside ui/."""
    key = Path(path).as_posix()
    if not key.startswith("ui/"):
        raise MapError(f"{key} is not a UI file")
    test = re.fullmatch(r"ui/tests/([^/]+)\.cjs", key)
    if test:
        return [test.group(1)] if (root / key).is_file() else None
    found = lookup(load_map(root, map_path), key)
    if found is None:
        return None
    scripts, shared = found
    if shared:
        scripts = set(scripts) | set(core_list(root) if core is None else core)
    return sorted(scripts)


def exercised_files(record, boot):
    """The UI files a script exercises: one of their functions ran more often than the boot load runs it, scaled to the
    script's page loads, or it ran a function the boot load never runs."""
    out = set()
    for file, counts in record["files"].items():
        boot_counts = boot["files"].get(file, {})
        for key, count in counts.items():
            if count * boot["loads"] > boot_counts.get(key, 0) * record["loads"]:
                out.add(file)
                break
    return out


def _record(measured, name):
    path = measured / "coverage" / f"{name}.json"
    if not path.is_file():
        raise MapError(f"no coverage record for {name}: it did not start under scripts/ui-coverage.cjs")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise MapError(f"the coverage record for {name} is not JSON: {error}") from error
    if not isinstance(record, dict) or not {"pages", "loads", "files"} <= set(record):
        raise MapError(f"the coverage record for {name} is not from scripts/ui-coverage.cjs")
    # The hook keys a file by its path without extension already; a record from an older hook may still name .js or .ts.
    files = {}
    for file, counts in record["files"].items():
        merged = files.setdefault(stem(file), {})
        for key, calls in counts.items():
            merged[key] = merged.get(key, 0) + calls
    return {**record, "files": files}


def build(root, measured, main_sha, run_url, share=SHARED_SHARE):
    """The map from a measured run: coverage/<name>.json for every browser script and for the boot baseline, and
    timings-<shard>.json for the scripts that passed. A script with no record stops the build: its browser never started under
    the hook, so its files would be missing from the map without a sign. Returns (map, scripts that opened no page)."""
    measured = Path(measured)
    names = tests(root)
    if BOOT in names:
        raise MapError(f"no browser script may be named {BOOT}: that name is the boot baseline's record")
    boot = _record(measured, BOOT)
    if boot["loads"] < 1:
        raise MapError("the boot baseline loaded no page, so there is nothing to compare the scripts with")
    records = {name: _record(measured, name) for name in names}
    exercised = {name: exercised_files(records[name], boot) for name in names}
    universe = set(served_files(root))
    for record in records.values():
        universe |= set(record["files"])
    ran_by = {file: [name for name in names if file in exercised[name]] for file in sorted(universe)}
    shared = sorted(file for file, ran in ran_by.items() if ran and len(ran) >= share * len(names))
    passed = set()
    for timing in sorted(measured.glob("timings-*.json")):
        passed |= set(json.loads(timing.read_text(encoding="utf-8")))
    data = {
        "version": VERSION,
        "source": {"main": main_sha, "run": run_url},
        "scripts": len(names),
        "shared_share": share,
        "shared": shared,
        "files": {file: ran for file, ran in ran_by.items() if ran},
        "unexercised": [file for file, ran in ran_by.items() if not ran],
        "partial": [name for name in names if name not in passed],
    }
    found = validate(data)
    if found:
        raise MapError("the built map does not validate: " + "; ".join(found))
    return data, [name for name in names if records[name]["pages"] == 0]


def summary(data, no_page):
    """A short Markdown summary of a build, for the workflow summary."""
    per_file = [len(ran) for ran in data["files"].values()]
    lines = [
        f"- Browser scripts measured: {data['scripts']}; did not pass in the measured run: {len(data['partial'])}"
        f"{' (' + ', '.join(data['partial']) + ')' if data['partial'] else ''}",
        f"- Shared (the core scripts and their own list; at least {data['shared_share']} of the scripts exercise them): "
        f"{len(data['shared'])}",
        f"- Files with a list of scripts: {len(data['files'])}; median scripts per file: "
        f"{statistics.median(per_file) if per_file else 0}",
        f"- Files no script exercises (no entry: the selector's rule for unknown files): {len(data['unexercised'])}",
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
    checked = commands.add_parser("check", help="validate a published map's schema")
    checked.add_argument("--map", required=True, type=Path, help="the map to validate")
    built = commands.add_parser("build", help="write the map from a measured run")
    built.add_argument("--measured", required=True, type=Path, help="the measured run's folder")
    built.add_argument("--out", required=True, type=Path, help="where to write the map")
    built.add_argument("--main-sha", required=True, help="the main commit the run measured")
    built.add_argument("--run", required=True, help="the measuring run's URL")
    built.add_argument("--shared-share", type=float, default=SHARED_SHARE)
    built.add_argument("--summary", type=Path, help="also write the Markdown summary here")
    sharded = commands.add_parser("shard", help="print one measuring shard's browser scripts")
    sharded.add_argument("index", type=int)
    sharded.add_argument("count", type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            data = read_map(args.map)
            found = validate(data)
            for line in found:
                print(f"ui-test-map: {line}", file=sys.stderr)
            if found:
                return 1
            print(
                f"ui-test-map: {args.map} matches the schema ({len(data['files'])} files, {len(data['shared'])} shared)"
            )
        elif args.command == "build":
            data, no_page = build(ROOT, args.measured, args.main_sha, args.run, args.shared_share)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
