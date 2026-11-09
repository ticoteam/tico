#!/usr/bin/env python3
"""Per-file ratchet for the UI's JavaScript: type check (tsc, ui/tsconfig.json) and lint (Biome, biome.json).

Findings are counted per file, and each file with findings has a baseline file of its own: ui/baselines/tsc/<path>.json
for tsc and ui/baselines/biome/<path>.json for Biome, where <path> is the file's path under ui/ (ui/app/a.js has
ui/baselines/tsc/app/a.js.json), holding {"count": N}. A file with no baseline file must have no findings, and a count
may only shrink:

    python3 scripts/ui_baseline.py tsc --check              fail on new findings, and on a count below its baseline
    python3 scripts/ui_baseline.py tsc --write              lower each baseline to the current counts (never raise one)
    python3 scripts/ui_baseline.py tsc --check --base origin/main
                                                            also fail when a baseline grew against that git ref
    (the same three for `biome`)
    python3 scripts/ui_baseline.py migrate                  turn the single-file baselines of before the split
                                                            (ui/types-baseline.json, ui/lint-baseline.json) into the
                                                            per-file ones, and blank-line ui/index.html's script tags

One baseline file per source file keeps two PRs that convert different UI files off the same lines: a conversion
deletes its own baseline files and changes its own script tag, and ui/index.html keeps a blank line between script tags,
so git's three-way merge (GitHub's merge queue runs no custom merge drivers) keeps the two edits apart.

Findings under ui/vendor/ are third-party code that a type-checked module imports (ui/overview/building.ts imports
three.js); tsc reads them, so they are dropped here, not counted.

Findings in a .ts file are dropped too: a TypeScript file is governed by the strict ratchet alone
(scripts/check_ui_strict.py, one count per file under ui/baselines/strict/), so no file is gated twice.
"""

import argparse
import collections
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TSC = ["npx", "--yes", "-p", "typescript@5.9.3", "tsc", "-p", "ui/tsconfig.json", "--pretty", "false"]
TSC_LINE = re.compile(r"^(?P<file>[^()\n]+)\((?P<line>\d+),(?P<col>\d+)\): error (?P<code>TS\d+): (?P<msg>.*)$")
TSC_GLOBAL = re.compile(r"^error (?P<code>TS\d+): (?P<msg>.*)$")
VENDOR = "ui/vendor/"
TOP = 10
TOOLS = ("tsc", "biome")
# The single-file baselines of before the split. `migrate` turns them into the per-file layout, then deletes them.
SINGLE_FILE = {"tsc": "ui/types-baseline.json", "biome": "ui/lint-baseline.json"}
LAYOUT = "ui/baselines"
INDEX = "ui/index.html"
# A <script src> line. Two of them on adjacent lines conflict in a git merge when two PRs each edit one of them.
SCRIPT_TAG = re.compile(r"^\s*<script\b[^>]*\bsrc=", re.IGNORECASE)


class Finding:
    def __init__(self, file, line, col, code, msg):
        self.file, self.line, self.col, self.code, self.msg = file, line, col, code, msg

    def text(self):
        return f"{self.file}:{self.line}:{self.col}: {self.code} {self.msg}"

    def sort_key(self):
        return (self.line, self.col, self.code)


def fail(message):
    sys.exit(message)


def is_typescript(file):
    """A .ts source (not a declaration file): its findings belong to the strict ratchet, not this one."""
    return file.endswith(".ts") and not file.endswith(".d.ts")


def run_tsc():
    done = subprocess.run(TSC, cwd=ROOT, capture_output=True, text=True, check=False)
    findings, diagnostics = [], 0
    for raw in done.stdout.splitlines():
        m = TSC_LINE.match(raw)
        if m:
            diagnostics += 1
            file = m["file"].replace("\\", "/")
            if file.startswith(VENDOR) or is_typescript(file):
                continue
            findings.append(Finding(file, int(m["line"]), int(m["col"]), m["code"], m["msg"]))
            continue
        m = TSC_GLOBAL.match(raw)
        if m:
            diagnostics += 1
            findings.append(Finding("<config>", 0, 0, m["code"], m["msg"]))
    # A failing run with no diagnostic at all crashed. Findings in .ts files count too: the strict ratchet owns them.
    if done.returncode not in (0, 1, 2) or (done.returncode != 0 and not diagnostics):
        fail(f"tsc exited {done.returncode} without diagnostics:\n{done.stdout}{done.stderr}")
    return findings, "tsc " + TSC[3].split("@")[1]


def biome_command():
    pinned = json.loads((ROOT / "package.json").read_text())["devDependencies"]["@biomejs/biome"]
    local = ROOT / "node_modules" / ".bin" / "biome"
    if local.exists():
        version = subprocess.run(
            [str(local), "--version"], cwd=ROOT, capture_output=True, text=True, check=False
        ).stdout
        if pinned in version:
            return [str(local)], pinned
    # No install in this checkout: fetch the pinned version (the same one package.json pins).
    return ["npx", "--yes", "-p", f"@biomejs/biome@{pinned}", "biome"], pinned


def run_biome():
    cmd, pinned = biome_command()
    done = subprocess.run(
        [*cmd, "lint", "--reporter=json", "--max-diagnostics=none", "ui"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        report = json.loads(done.stdout)
    except json.JSONDecodeError:
        fail(f"biome did not print JSON (exit {done.returncode}):\n{done.stdout[:2000]}{done.stderr[:2000]}")
    findings = []
    for item in report.get("diagnostics", []):
        path = item["location"]["path"]
        file = (path.get("file") if isinstance(path, dict) else path).replace("\\", "/")
        if file.startswith(VENDOR):
            continue
        start = item["location"]["start"]
        findings.append(Finding(file, start["line"], start["column"], item["category"], item["message"]))
    return findings, f"biome {pinned}"


def counts_of(findings):
    return collections.Counter(f.file for f in findings)


def layout_dir(tool):
    return ROOT / LAYOUT / tool


def baseline_path(tool, file):
    """The baseline file of one source file: ui/app/a.js -> ui/baselines/<tool>/app/a.js.json."""
    if not file.startswith("ui/"):
        fail(f"{file}: not under ui/, so it has no baseline")
    return layout_dir(tool) / (file[len("ui/") :] + ".json")


def load_layout(tool):
    """{file: count} from the per-file baselines in the working tree. No folder, or no file, means no findings."""
    base = layout_dir(tool)
    return {
        "ui/" + path.relative_to(base).as_posix()[: -len(".json")]: int(json.loads(path.read_text())["count"])
        for path in sorted(base.rglob("*.json"))
    }


def check_single_file_gone(tool):
    if (ROOT / SINGLE_FILE[tool]).exists():
        fail(
            f"{SINGLE_FILE[tool]} is the single-file baseline from before the split; "
            "run: python3 scripts/ui_baseline.py migrate"
        )


def load_baseline(path):
    if not path.exists():
        return None
    return {k: int(v) for k, v in json.loads(path.read_text())["files"].items()}


def baseline_at(ref, rel):
    if (
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=ROOT, capture_output=True, check=False
        ).returncode
        != 0
    ):
        fail(f"--base {ref}: no such commit in this checkout (fetch it first)")
    shown = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=ROOT, capture_output=True, text=True, check=False)
    if shown.returncode != 0:
        return None
    return {k: int(v) for k, v in json.loads(shown.stdout)["files"].items()}


def blobs_at(ref, names):
    """The contents of files at a git ref, read in one git cat-file call."""
    request = "".join(f"{ref}:{name}\n" for name in names).encode()
    out = subprocess.run(
        ["git", "cat-file", "--batch"], cwd=ROOT, input=request, capture_output=True, check=True
    ).stdout
    blobs, pos = [], 0
    for _ in names:
        end = out.index(b"\n", pos)
        size = int(out[pos:end].split()[2])
        blobs.append(out[end + 1 : end + 1 + size])
        pos = end + 1 + size + 1
    return blobs


def layout_at(ref, tool):
    """{file: count} at a git ref, in either layout: the single file of before the split, else the per-file baselines.
    None when the ref holds neither (the ref is older than the baselines)."""
    single = baseline_at(ref, SINGLE_FILE[tool])
    if single is not None:
        return single
    folder = f"{LAYOUT}/{tool}"
    listed = subprocess.run(
        ["git", "ls-tree", "-r", "-z", "--name-only", ref, "--", folder], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode()
    names = [name for name in listed.split("\0") if name]
    if not names:
        return None
    jsons = [name for name in names if name.endswith(".json")]
    counts = {}
    for name, blob in zip(jsons, blobs_at(ref, jsons), strict=True):
        counts["ui/" + name[len(folder) + 1 : -len(".json")]] = int(json.loads(blob)["count"])
    return counts


def write_baseline(path, counts):
    body = {"files": {f: counts[f] for f in sorted(counts) if counts[f] > 0}}
    path.write_text(json.dumps(body, indent=2) + "\n")


def write_layout(tool, counts):
    """Make a tool's per-file baselines match counts: one file for each file with findings, none for a count of zero.
    Files that no longer have a baseline are deleted, and folders left empty are removed."""
    base = layout_dir(tool)
    wanted = {baseline_path(tool, file): count for file, count in counts.items() if count > 0}
    base.mkdir(parents=True, exist_ok=True)
    (base / ".gitkeep").touch()  # git keeps the folder when every baseline in it is gone
    for path in base.rglob("*.json"):
        if path not in wanted:
            path.unlink()
    for path, count in sorted(wanted.items()):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"count": count}) + "\n")
    for folder in sorted(base.rglob("*"), reverse=True):
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()


def print_top(counts, total_files):
    print(f"top {min(TOP, len(counts))} files of {total_files}:")
    for file, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP]:
        print(f"  {n:6d}  {file}")


def base_name(file, old):
    """The name a file had at the base ref: a .ts file that renamed a .js file (scripts/ui_flip.py) keeps the .js count."""
    if file.endswith(".ts") and file not in old and file[:-3] + ".js" in old:
        return file[:-3] + ".js"
    return file


def check(findings, tool, base_ref):
    label, rel = tool, f"{LAYOUT}/{tool}"
    counts = counts_of(findings)
    total = sum(counts.values())
    baseline = load_layout(tool)
    problems = 0
    by_file = collections.defaultdict(list)
    for f in findings:
        by_file[f.file].append(f)

    for file in sorted(set(counts) | set(baseline)):
        now, was = counts.get(file, 0), baseline.get(file, 0)
        if now > was:
            problems += 1
            print(f"NEW  {file}: {now} findings, baseline allows {was}")
            for f in sorted(by_file[file], key=Finding.sort_key):
                print(f"       {f.text()}")
        elif now < was:
            problems += 1
            print(f"LOWER {file}: baseline {was}, now {now}; run: python3 scripts/ui_baseline.py {label} --write")
        elif was == 0 and file in baseline:
            problems += 1
            print(
                f"STALE {file}: no findings, so its baseline goes; run: python3 scripts/ui_baseline.py {label} --write"
            )

    if base_ref:
        old = layout_at(base_ref, tool)
        if old is None:
            print(f"note: {rel} is absent at {base_ref}; the growth check starts with the next change to it")
        else:
            for file in sorted(baseline):
                was = old.get(base_name(file, old), 0)
                if baseline[file] > was:
                    problems += 1
                    print(
                        f"GREW {file}: {was} at {base_ref}, now {baseline[file]} in {rel}; "
                        "baselines only shrink, so fix the findings instead"
                    )

    print(
        f"{label}: {total} findings in {len(counts)} files; baseline {sum(baseline.values())} "
        f"in {len(baseline)} files; {'ok' if not problems else f'{problems} problem(s)'}"
    )
    print_top(counts, len(counts))
    return problems


def write(findings, tool):
    counts = counts_of(findings)
    old = load_layout(tool)
    grown = [f for f in sorted(counts) if counts[f] > old.get(f, 0)]
    if grown:
        for file in grown:
            print(f"refusing to raise {file}: {counts[file]} findings, baseline {old.get(file, 0)}")
        fail("fix the new findings; a baseline only shrinks")
    write_layout(tool, counts)
    print(f"wrote {LAYOUT}/{tool}: {sum(counts.values())} findings in {len(counts)} files")
    return 0


def adjacent_script_lines(text):
    """The line numbers of the <script src> tags that directly follow another one."""
    lines = text.split("\n")
    return [n + 1 for n in range(1, len(lines)) if SCRIPT_TAG.match(lines[n]) and SCRIPT_TAG.match(lines[n - 1])]


def space_script_tags(text):
    """The text with a blank line between each two <script src> tags that sit on adjacent lines."""
    out = []
    for line in text.split("\n"):
        if out and SCRIPT_TAG.match(line) and SCRIPT_TAG.match(out[-1]):
            out.append("")
        out.append(line)
    return "\n".join(out)


def migrate():
    for tool in TOOLS:
        single = ROOT / SINGLE_FILE[tool]
        if not single.exists():
            continue
        counts = {k: int(v) for k, v in json.loads(single.read_text())["files"].items()}
        write_layout(tool, counts)
        single.unlink()
        files = sum(1 for count in counts.values() if count > 0)
        print(f"migrated {SINGLE_FILE[tool]} to {LAYOUT}/{tool}: {sum(counts.values())} findings in {files} files")
    index = ROOT / INDEX
    text = index.read_bytes().decode("utf-8")
    spaced = space_script_tags(text)
    if spaced != text:
        index.write_bytes(spaced.encode("utf-8"))
    added = spaced.count("\n") - text.count("\n")
    print(f"spaced {INDEX}: {added} blank line(s) added")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("tool", choices=[*TOOLS, "migrate"])
    parser.add_argument("--check", action="store_true", help="fail on new findings or stale counts")
    parser.add_argument("--write", action="store_true", help="lower the baseline to the current counts")
    parser.add_argument("--base", metavar="REF", help="git ref whose baseline must not be exceeded (e.g. origin/main)")
    args = parser.parse_args(argv)

    if args.tool == "migrate":
        if args.check or args.write or args.base:
            parser.error("migrate takes no --check, --write or --base")
        return migrate()
    if args.check == args.write:
        parser.error("give one of --check or --write")
    check_single_file_gone(args.tool)

    if args.tool == "tsc":
        findings, label = run_tsc()
    else:
        findings, label = run_biome()
    config = [f for f in findings if f.file == "<config>"]
    if args.write:
        if config:
            fail("\n".join(f.text() for f in config))
        return write(findings, args.tool)
    if config:
        for f in config:
            print(f"{f.code} {f.msg}")
        fail(f"{label} reported a configuration error")
    return 1 if check(findings, args.tool, args.base) else 0


if __name__ == "__main__":
    sys.exit(main())
