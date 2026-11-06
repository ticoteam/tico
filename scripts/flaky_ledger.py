"""The flaky-test ledger, scripts/flaky-tests.json: the tests that get one retry in CI.

A listed test whose first attempt fails runs once more, and a pass on that retry is printed and written to the job
summary (conftest.py for pytest, scripts/ui-tests.cjs for browser scripts). A second failure fails the run as before.
Every entry names its test, a reason, an owner and two dates: added, and expires (at most 14 days later).

Past its expiry an entry stops getting its retry, so the flaky test fails queue groups again and forces a fix. This
check then warns, listing the expired entries. A pull request that changes the ledger fails instead, because extending
or keeping an entry has to be deliberate. A malformed entry always fails.

The check runs in the PR checks' static job (with --base, the pull request's base) and locally with
`python3 scripts/flaky_ledger.py`. It is stdlib only.
"""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "scripts" / "flaky-tests.json"
LEDGER_PATH = "scripts/flaky-tests.json"
MAX_DAYS = 14
FIELDS = ("id", "reason", "owner", "added", "expires")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def load(path=LEDGER):
    """The entries of the ledger file. Raises ValueError when the file is not an object with a `tests` list."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    tests = data.get("tests") if isinstance(data, dict) else None
    if not isinstance(tests, list):
        raise ValueError("flaky-tests.json must be an object with a 'tests' list")
    return tests


def today():
    return dt.datetime.now(dt.timezone.utc).date()


def _date(value):
    if not isinstance(value, str) or not DATE.fullmatch(value):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _defined_in(path, name):
    """Whether the file defines a test function or method `name` (sync or async)."""
    return re.search(rf"^\s*(async\s+)?def {re.escape(name)}\(", path.read_text(encoding="utf-8"), re.M) is not None


def problems(entries, root=ROOT):
    """Every way an entry is malformed, as sentences; an empty list when they are all well formed. Expiry is not a
    problem here: `expired` reports it. A pytest id must name a file and a test function in it; a browser script id,
    its file. Dates must be real YYYY-MM-DD dates, at most MAX_DAYS apart, with the exit date after the one added."""
    out = []
    seen = set()
    for number, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            out.append(f"entry {number}: not an object")
            continue
        test_id = entry.get("id")
        name = test_id if isinstance(test_id, str) and test_id else f"entry {number}"
        for field in FIELDS:
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                out.append(f"{name}: missing {field}")
        if not isinstance(test_id, str) or not test_id:
            continue
        if test_id in seen:
            out.append(f"{name}: listed twice")
        seen.add(test_id)
        if "::" in test_id:
            file_part, _, test_name = test_id.partition("::")
            test_name = test_name.split("[", 1)[0]
            path = root / file_part
            if not file_part.endswith(".py") or not path.is_file():
                out.append(f"{name}: no such test file {file_part}")
            elif not _defined_in(path, test_name):
                out.append(f"{name}: no test function {test_name} in {file_part}")
        elif test_id.endswith(".cjs"):
            if not (root / test_id).is_file():
                out.append(f"{name}: no such browser script {test_id}")
        else:
            out.append(f"{name}: id must be path/to/test_file.py::test_name or ui/tests/name.cjs")
        added, expires = _date(entry.get("added")), _date(entry.get("expires"))
        if added is None:
            out.append(f"{name}: added must be a YYYY-MM-DD date")
        if expires is None:
            out.append(f"{name}: expires must be a YYYY-MM-DD date")
        if added and expires:
            if expires < added:
                out.append(f"{name}: expires {expires} is before added {added}")
            elif (expires - added).days > MAX_DAYS:
                out.append(f"{name}: expires {expires} is more than {MAX_DAYS} days after added {added}")
    return out


def expired(entries, on=None):
    """The well-formed entries whose exit date has passed, as sentences. They get no retry (see `active`)."""
    on = on or today()
    return [
        f"{entry.get('id')}: expired {entry['expires']}; no retry now: fix the test and remove the entry"
        for entry in entries
        if isinstance(entry, dict) and _date(entry.get("expires")) is not None and _date(entry["expires"]) < on
    ]


def active(entries, on=None):
    """The entries whose exit date has not passed: only these get their one retry."""
    on = on or today()
    return [entry for entry in entries if (_date(entry.get("expires")) or dt.date.min) >= on]


def matches(nodeid, test_id):
    """Whether a pytest node id is the listed test, or one of its parametrized cases."""
    return nodeid == test_id or nodeid.startswith(test_id + "[")


def ledger_changed(base, root=ROOT):
    """Whether this checkout's HEAD changes the ledger against `base`. Raises CalledProcessError when git cannot say,
    so a check that cannot tell fails rather than passes."""
    diff = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD", "--", LEDGER_PATH],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(diff.stdout.strip())


def main(argv=()):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base", help="the pull request's base commit; a change to the ledger against it makes expiry fail"
    )
    args = parser.parse_args(list(argv))
    try:
        entries = load(LEDGER)
    except (OSError, ValueError) as error:
        print(f"flaky ledger: cannot read {LEDGER_PATH}: {error}", file=sys.stderr)
        return 1
    found = problems(entries, root=ROOT)
    for line in found:
        print(f"flaky ledger: {line}", file=sys.stderr)
    if found:
        return 1
    stale = expired(entries)
    if stale:
        try:
            changed = bool(args.base) and ledger_changed(args.base, root=ROOT)
        except (OSError, subprocess.CalledProcessError) as error:
            print(f"flaky ledger: cannot compare the ledger with {args.base}: {error}", file=sys.stderr)
            return 1
        for line in stale:
            if changed:
                print(f"flaky ledger: {line}", file=sys.stderr)
            else:
                print(f"::warning::flaky ledger: {line}")
        if changed:
            return 1
    print(
        f"flaky ledger ok: {len(entries)} entries, each with an owner, a reason and an exit date"
        + (f"; {len(stale)} expired, warned only" if stale else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
