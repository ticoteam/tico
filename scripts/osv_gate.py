#!/usr/bin/env python3
"""The advisory gate: the pinned OSV-Scanner (scripts/pinned_tool.sh) over every dependency input, failing only on a
known advisory that has no reviewed disposition. It is a gate, not a bump bot: it never changes a version.

Inputs: every tracked requirements*.txt, package-lock.json and Cargo.lock; the browser libraries copied into ui/vendor/
and templates/employee-repo/ui/vendor/ (scripts/advisories/vendored.json names each one's package and version); and the
uv versions that scripts, workflows and the Dockerfile pin (scripts/advisories/tools.json). The last two reach
OSV-Scanner as custom inventories, one per source file, so each finding names the file it came from.

Each finding is an advisory group (OSV-Scanner merges aliases: a GHSA and its PYSEC or CVE ids) on one package version in
one source. A group whose records are all RustSec maintenance notices (informational "unmaintained" or "notice") is
printed and never fails. Any other finding fails unless scripts/advisories/allowlist.json has an entry for that
advisory, package and source, with a reason and an expiry date no more than a year out. An expired entry fails as if it
were absent, so every disposition is reviewed again.

  scripts/osv_gate.py scan           the live gate: query OSV for every input (PR checks' advisories job)
  scripts/osv_gate.py selftest       offline: the pre-#4077 fixture trips the gate on the advisories that release fixed
                                     (uv, rustls-webpki, js-yaml, SQLite, rustix), and this tree clears them
  scripts/osv_gate.py drift          offline: the vendor manifest and the uv inventory match the tree (scripts/gates)
  scripts/osv_gate.py check RESULT   apply the dispositions to a saved `osv-scanner --format json` result
"""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import NamedTuple
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DATA = "scripts/advisories"
FIXTURE = ROOT / "scripts/tests/fixtures/advisories/pre-4077"
VENDOR_DIRS = ("ui/vendor/", "templates/employee-repo/ui/vendor/")
# Code a vendored library ships; licences, fonts and notices need no entry.
VENDOR_CODE = (".js", ".mjs", ".cjs", ".wasm")
# A fixture's lockfiles end in .fixture, so GitHub's dependency graph does not read their old versions as this tree's.
LOCKFILE = re.compile(r"(?:^|/)(requirements[^/]*\.txt|package-lock\.json|Cargo\.lock)(?:\.fixture)?$")
# A uv version pinned in a script: `pip install uv==X.Y.Z` or the `ghcr.io/astral-sh/uv:X.Y.Z` image.
UV_PIN = re.compile(r"(?<![\w.-])uv(?:==|:)v?(\d+\.\d+\.\d+)\b")
# Files that only describe pins, or hold test inputs: their versions are not what the tree installs.
NOT_PINS = ("docs/", "scripts/tests/", f"{DATA}/")
NOTICE_KINDS = {"unmaintained", "notice"}
MAX_DISPOSITION_DAYS = 366
EXPIRING_DAYS = 14


class Finding(NamedTuple):
    source: str  # the lockfile, vendored file or pinning script, relative to the root
    ecosystem: str
    package: str
    version: str
    ids: tuple  # the advisory group's ids and aliases
    summary: str
    notice: bool  # every record is a RustSec maintenance notice


def tracked(root, *pathspecs):
    """Files under `root` that git tracks or would track (relative paths), so a new file counts before its commit."""
    run = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *pathspecs],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return sorted({name for name in run.stdout.decode().split("\0") if name and (Path(root) / name).is_file()})


def load(root, name):
    return json.loads((Path(root) / DATA / name).read_text())


def lockfiles(root):
    """Every lockfile the gate scans, relative to `root`; the fixture tree under scripts/tests is not this tree's."""
    return [name for name in tracked(root) if LOCKFILE.search(name) and not name.startswith("scripts/tests/")]


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha256_entry(root, path):
    """A vendored file's SHA-256, or for a folder (a trailing /) the SHA-256 of its tracked files' sorted hash lines."""
    if not path.endswith("/"):
        return sha256_file(Path(root) / path)
    lines = "".join(f"{sha256_file(Path(root) / name)}  {name[len(path) :]}\n" for name in tracked(root, path))
    return hashlib.sha256(lines.encode()).hexdigest()


def uv_pins(root):
    """{file: sorted uv versions} for every tracked file that pins uv (NOT_PINS left out)."""
    pins = {}
    for name in tracked(root):
        if name.startswith(NOT_PINS) or name.endswith(".md"):
            continue
        try:
            text = (Path(root) / name).read_text()
        except (OSError, UnicodeDecodeError):
            continue
        found = sorted(set(UV_PIN.findall(text)))
        if found:
            pins[name] = found
    return pins


def drift(root=ROOT):
    """Problems that make the vendor manifest or the uv inventory disagree with the tree; empty when they agree."""
    problems = []
    vendored = load(root, "vendored.json")
    for path, entry in sorted(vendored.items()):
        if not (Path(root) / path).exists():
            problems.append(f"{DATA}/vendored.json lists {path}, which is gone: remove its entry")
            continue
        got = sha256_entry(root, path)
        if got != entry["sha256"]:
            problems.append(
                f"{path} changed (SHA-256 {got}): set its version and sha256 in {DATA}/vendored.json to what it now is"
            )
    for name in tracked(root, *VENDOR_DIRS):
        if name.endswith(VENDOR_CODE) and not any(
            name == p or (p.endswith("/") and name.startswith(p)) for p in vendored
        ):
            problems.append(f"{name} is vendored code with no package and version in {DATA}/vendored.json")
    listed = load(root, "tools.json")["uv"]["pins"]
    found = uv_pins(root)
    for name in sorted(set(listed) | set(found)):
        want, got = listed.get(name), found.get(name)
        if want != got:
            problems.append(
                f"{name} pins uv {', '.join(got or []) or 'nowhere'}, but {DATA}/tools.json lists "
                f"{', '.join(want or []) or 'nothing'} for it: list what the file pins"
            )
    return problems


def custom_inventories(root, out_dir):
    """{custom inventory file: source} for the vendored libraries and the pinned uv, one file per source."""
    sources = {}
    packages = {path: [entry] for path, entry in load(root, "vendored.json").items()}
    tools = load(root, "tools.json")
    for tool, spec in tools.items():
        for path, versions in spec["pins"].items():
            packages.setdefault(path, []).extend(
                {"package": tool, "ecosystem": spec["ecosystem"], "version": version} for version in versions
            )
    for index, (source, entries) in enumerate(sorted(packages.items())):
        inventory = {
            "results": [
                {
                    "packages": [
                        {"package": {"name": e["package"], "version": e["version"], "ecosystem": e["ecosystem"]}}
                        for e in entries
                    ]
                }
            ]
        }
        target = Path(out_dir) / f"{index:03d}" / "osv-scanner.json"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(inventory))
        sources[str(target)] = source
    return sources


def scanner():
    """The verified OSV-Scanner binary, downloaded once by scripts/pinned_tool.sh."""
    run = subprocess.run(
        ["bash", str(ROOT / "scripts/pinned_tool.sh"), "path", "osv-scanner"],
        capture_output=True,
        text=True,
        check=False,
    )
    if run.returncode != 0:
        sys.stderr.write(run.stderr)
        raise SystemExit("osv_gate: the pinned OSV-Scanner could not be fetched")
    return run.stdout.strip().splitlines()[-1]


def run_scanner(root, work, offline_db=None):
    """Scan `root`'s inputs into OSV-Scanner's JSON result; returns (result, {scanned path: source})."""
    root, work = Path(root).resolve(), Path(work)
    locks = {str(root / name): name.removesuffix(".fixture") for name in lockfiles(root)}
    custom = custom_inventories(root, work / "inventories")
    # An empty config stands in for any osv-scanner.toml beside an input: dispositions live in the allowlist only.
    config = work / "osv-scanner.toml"
    config.write_text("")
    output = work / "result.json"
    command = [scanner(), "scan", "source", "--no-resolve", "--all-vulns", "--format", "json"]
    command += ["--config", str(config), "--output-file", str(output)]
    if offline_db:
        command += ["--offline", "--local-db-path", str(offline_db)]
    for path in locks:
        kind = LOCKFILE.search(path).group(1)
        command += ["--lockfile", ("requirements.txt" if kind.startswith("requirements") else kind) + ":" + path]
    for path in custom:
        command += ["--lockfile", "osv-scanner:" + path]
    for attempt in (1, 2):
        output.unlink(missing_ok=True)
        run = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
        # 0: no advisory; 1: advisories found. Anything else (an OSV outage, an unreadable input) is an error.
        if run.returncode in (0, 1) and output.exists():
            return json.loads(output.read_text()), locks | custom
        sys.stderr.write(run.stdout[-4000:] + run.stderr[-4000:])
        print(f"osv_gate: OSV-Scanner exited {run.returncode} (attempt {attempt} of 2)", file=sys.stderr)
        if offline_db:
            break
    raise SystemExit("osv_gate: OSV-Scanner did not produce a result")


def findings(result, sources=None):
    """A Finding for each advisory group on each package version in each source of a scanner result."""
    out = []
    for block in result.get("results") or []:
        path = block.get("source", {}).get("path", "")
        source = (sources or {}).get(path, path)
        for item in block.get("packages") or []:
            package = item["package"]
            records = {v["id"]: v for v in item.get("vulnerabilities") or []}
            for group in item.get("groups") or []:
                ids = tuple(sorted(set(group.get("ids", [])) | set(group.get("aliases") or [])))
                members = [records[i] for i in group.get("ids", []) if i in records]
                kinds = {
                    (affected.get("database_specific") or {}).get("informational")
                    for record in members
                    for affected in record.get("affected") or [{}]
                }
                summary = next((m["summary"] for m in members if m.get("summary")), "")
                notice = bool(members) and kinds <= NOTICE_KINDS
                out.append(
                    Finding(source, package["ecosystem"], package["name"], package["version"], ids, summary, notice)
                )
    return out


def disposition(finding, entries):
    """(index, entry) of the allowlist entry for this advisory, package and source, else None."""
    for index, entry in enumerate(entries):
        if (
            entry.get("id") in finding.ids
            and entry.get("package") == finding.package
            and finding.source in entry.get("sources", [])
        ):
            return index, entry
    return None


def expired(entry, today):
    try:
        return datetime.date.fromisoformat(entry["expires"]) < today
    except (KeyError, TypeError, ValueError):
        return True


def blocking(found, entries, today):
    """The findings that fail the gate: not a maintenance notice, and no unexpired disposition."""
    return [f for f in found if not f.notice and ((d := disposition(f, entries)) is None or expired(d[1], today))]


def allowlist_problems(entries, today):
    problems = []
    for index, entry in enumerate(entries):
        where = f"{DATA}/allowlist.json entry {index + 1} ({entry.get('id', '?')})"
        missing = [key for key in ("id", "package", "sources", "reason", "expires") if not entry.get(key)]
        if missing:
            problems.append(f"{where} needs {', '.join(missing)}")
            continue
        try:
            expires = datetime.date.fromisoformat(entry["expires"])
        except ValueError:
            problems.append(f"{where}: expires must be a YYYY-MM-DD date")
            continue
        if (expires - today).days > MAX_DISPOSITION_DAYS:
            problems.append(f"{where}: expires more than a year out; review it within a year")
    return problems


def evaluate(found, entries, today):
    """(failures, notices, accepted, warnings): printable lines for `found` against the allowlist entries."""
    failures = allowlist_problems(entries, today)
    notices, accepted, warnings = [], [], []
    used = set()
    for f in found:
        line = f"{f.source}: {f.package} {f.version} ({f.ecosystem}) {' / '.join(f.ids)}: {f.summary}"
        if f.notice:
            notices.append(line)
            continue
        match = disposition(f, entries)
        if match is None:
            failures.append(f"{line}\n    no disposition: fix it, or add a reviewed entry to {DATA}/allowlist.json")
            continue
        index, entry = match
        used.add(index)
        if expired(entry, today):
            failures.append(f"{line}\n    its disposition expired on {entry.get('expires')}: review it again")
            continue
        accepted.append(f"{line}\n    accepted until {entry['expires']}: {entry['reason']}")
        if (datetime.date.fromisoformat(entry["expires"]) - today).days <= EXPIRING_DAYS:
            warnings.append(f"the disposition for {entry['id']} on {f.package} expires on {entry['expires']}")
    for index, entry in enumerate(entries):
        if index not in used:
            warnings.append(
                f"{DATA}/allowlist.json entry {entry.get('id')} on {entry.get('package')} matched nothing: remove it"
            )
    return failures, notices, accepted, warnings


def report(failures, notices, accepted, warnings):
    """Print the verdict (with GitHub annotations under Actions); returns the exit status."""
    actions = os.environ.get("GITHUB_ACTIONS") == "true"
    for title, lines, level in (
        ("Maintenance notices (never fail)", notices, "notice"),
        ("Accepted advisories", accepted, None),
        ("Warnings", warnings, "warning"),
        ("Advisories without a disposition", failures, "error"),
    ):
        if not lines:
            continue
        print(f"\n{title}:")
        for line in lines:
            print("  " + line.replace("\n", "\n  "))
            if actions and level:
                print(f"::{level}::{line.splitlines()[0]}")
    if failures:
        print(f"\nosv_gate: {len(failures)} known advisory finding(s) without a disposition")
        return 1
    print(f"\nosv_gate: ok ({len(accepted)} accepted, {len(notices)} maintenance notices)")
    return 0


def offline_db(records_dir, target):
    """A local OSV database for OSV-Scanner's --offline mode from recorded advisory JSON: <target>/osv-scalibr/<eco>/all.zip."""
    by_ecosystem = {}
    for path in sorted(Path(records_dir).glob("*.json")):
        record = json.loads(path.read_text())
        for ecosystem in {a["package"]["ecosystem"] for a in record.get("affected", [])}:
            by_ecosystem.setdefault(ecosystem, []).append((path.name, path.read_text()))
    for ecosystem, records in by_ecosystem.items():
        folder = Path(target) / "osv-scalibr" / ecosystem
        folder.mkdir(parents=True)
        with zipfile.ZipFile(folder / "all.zip", "w") as archive:
            for name, text in records:
                archive.writestr(name, text)
    return target


def selftest(today):
    """Run the pinned scanner offline, with the recorded advisories, on the pre-#4077 fixture and on this tree.

    The fixture must fail on each advisory expected.json lists for it and print its maintenance notices without
    failing; this tree must not fail on any recorded advisory. Returns the problems found."""
    expected = json.loads((FIXTURE / "expected.json").read_text())
    entries = load(ROOT, "allowlist.json")
    problems = []
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        db = offline_db(FIXTURE / "osv", work / "db")
        (work / "fixture").mkdir()
        (work / "tree").mkdir()
        found = findings(*run_scanner(FIXTURE, work / "fixture", offline_db=db))
        failing = {(f.source, i) for f in blocking(found, entries, today) for i in f.ids}
        notices = {(f.source, i) for f in found if f.notice for i in f.ids}
        for source, ids in expected["fail"].items():
            problems += [f"the fixture did not fail on {i} in {source}" for i in ids if (source, i) not in failing]
        for source, ids in expected["notice"].items():
            problems += [
                f"the fixture did not print notice {i} for {source}" for i in ids if (source, i) not in notices
            ]
        for source, ids in expected["accepted"].items():
            problems += [f"the fixture failed on accepted {i} in {source}" for i in ids if (source, i) in failing]
        found = findings(*run_scanner(ROOT, work / "tree", offline_db=db))
        for f in blocking(found, entries, today):
            problems.append(
                f"this tree still matches recorded {' / '.join(f.ids)}: {f.package} {f.version} in {f.source}"
            )
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan")
    sub.add_parser("selftest")
    sub.add_parser("drift")
    check = sub.add_parser("check")
    check.add_argument("result")
    args = parser.parse_args(argv)
    today = datetime.datetime.now(datetime.UTC).date()
    if args.command == "drift":
        problems = drift()
        for problem in problems:
            print(problem)
        return 1 if problems else 0
    if args.command == "selftest":
        problems = selftest(today)
        for problem in problems:
            print(problem)
        print("osv_gate selftest: " + ("failed" if problems else "ok"))
        return 1 if problems else 0
    entries = load(ROOT, "allowlist.json")
    if args.command == "check":
        found = findings(json.loads(Path(args.result).read_text()))
    else:
        with tempfile.TemporaryDirectory() as work:
            result, sources = run_scanner(ROOT, work)
            found = findings(result, sources)
    return report(*evaluate(found, entries, today))


if __name__ == "__main__":
    sys.exit(main())
