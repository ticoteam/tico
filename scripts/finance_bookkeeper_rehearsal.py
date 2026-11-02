#!/usr/bin/env python3
"""Bookkeeper rehearsal: the built-in Bookkeeper's checks 1-7, live on a local demo and in-process.

This is a rehearsal on synthetic data. It is not the Mercury run, and it never touches a real install, a real bank,
a card processor, or a paid model.

  1 built path and guards   live: the demo refuses archive (409 system_bot) and group move (422 built_in); code: test_c1_*
  2 tools and refusals      code: test_c2_* (the bot's own tools/list, denied and held calls)
  3 triggers and quiet runs code: test_c3_* (seeded routines, a quiet check, known gaps)
  4 instructions vs code    code: test_c4_* (hub commands and kinds the playbooks name)
  5 approvals               code: test_c5_* (partial: the bot holds pay and cannot decide approvals)
  6 failures                code: test_c6_* (a failed sync reaching the owner is a known gap)
  7 demo identity           live: the demo serves the built Bookkeeper with its default rights

Support tests (backend/tests/test_bookkeeper.py, test_finance_bookkeeper_turns.py) and the static gates
(scripts/check_types.sh, scripts/finance_lint.py) must pass too. A strict xfail is a known gap and counts as not passed.

Start the demo by hand first, from the repository root:
  TICO_MERCURY_BASE=http://127.0.0.1:18832/api/v1 python3 -m backend.demo --port 18831
with the fake bank on its own port: python3 scripts/fake_mercury.py --port 18832

Exit 0 only if every check passes.
"""

import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = os.environ.get("BOOKKEEPER_REHEARSAL_DEMO", "http://127.0.0.1:18831")
BOOKKEEPER = "bookkeeper"
ACTOR = "bot:bookkeeper"
DEFAULT_RIGHTS = {"finance.book", "finance.ap", "finance.ar", "finance.pay", "finance.cards"}
TESTS = ["backend/tests/test_finance_bookkeeper_rehearsal.py", "backend/tests/test_bookkeeper.py",
         "backend/tests/test_finance_bookkeeper_turns.py"]
TITLES = {1: "built path and guards", 2: "tools and refusals", 3: "triggers and quiet runs",
          4: "instructions vs code", 5: "approvals", 6: "failures visible", 7: "demo identity"}


def _request(opener, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(DEMO + path, data=data, method=method, headers=headers)
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def live():
    """Checks 1 and 7 against the demo. Returns ({check: [(passed, detail)]}, None) or (None, why it could not run)."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        opener.open(DEMO + "/", timeout=15).read()     # the first visit signs the loopback owner in
    except OSError as exc:
        return None, f"demo not reachable at {DEMO}: {exc}"
    out = {1: [], 7: []}
    status, text = _request(opener, "GET", "/api/v2/bots/" + BOOKKEEPER)
    bot = json.loads(text) if status == 200 else {}
    active = status == 200 and bot.get("state") == "active"
    out[7].append((active, f"GET bot -> {status}, state {bot.get('state')!r}"))
    revision = bot.get("revision", 0)
    status, text = _request(opener, "POST", "/api/v2/bots/" + BOOKKEEPER + "/archive",
                            {"expected_revision": revision, "successor": None, "revoke_agent": True})
    out[1].append((status == 409 and "system_bot" in text, f"archive -> {status}"))
    status, text = _request(opener, "PATCH", "/api/v2/groups/leadership",
                            {"add": {"bots": [BOOKKEEPER], "people": []}})
    out[1].append((status == 422 and "built_in" in text, f"group move -> {status}"))
    out[1].append((active, "bot still active after the refusals"))
    status, text = _request(opener, "GET", "/api/v2/finance/access")
    rights = set()
    if status == 200:
        for row in json.loads(text).get("teammates", []):
            if row.get("teammate") == ACTOR:
                rights = set(row.get("rights", []))
    out[7].append((rights == DEFAULT_RIGHTS, f"finance access -> {status}, {ACTOR} rights {sorted(rights)}"))
    status, _ = _request(opener, "GET", "/api/v2/finance/inbox")
    out[7].append((status == 200, f"finance inbox -> {status}"))
    return out, None


def pytest_checks():
    """Runs the test files once and sorts each result by its check number (test_cN_ prefix)."""
    scratch = Path(tempfile.mkdtemp(prefix="bookkeeper-rehearsal-"))
    try:
        subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-q", "-p", "no:cacheprovider",
                        f"--junitxml={scratch / 'junit.xml'}"], cwd=ROOT, capture_output=True, text=True, timeout=1800)
        tree = ET.parse(scratch / "junit.xml")
    except (OSError, ET.ParseError) as exc:
        return None, f"pytest did not produce a report: {exc}"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    sorted_by = {}
    for case in tree.iter("testcase"):
        name = case.get("name", "")
        kind = "pass"
        for child in case:
            if child.tag in ("failure", "error"):
                kind = "fail"
            elif child.tag == "skipped":
                kind = "xfail" if child.get("type") == "pytest.xfail" else "skip"
        match = re.match(r"test_c(\d)_", name)
        group = int(match.group(1)) if match else "support"
        sorted_by.setdefault(group, []).append((kind, name))
    return sorted_by, None


def gates():
    """The static gates the change must keep green."""
    results = {}
    for label, cmd in (("check_types", ["bash", "scripts/check_types.sh"]),
                       ("finance_lint", [sys.executable, "scripts/finance_lint.py"])):
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=1800)
        results[label] = proc.returncode
    return results


def main():
    failing = []
    live_out, live_err = live()
    tests, test_err = pytest_checks()
    static = gates()

    for number in range(1, 8):
        mode = []
        ok_parts = []
        if live_out and number in live_out:
            for passed, detail in live_out[number]:
                mode.append("live")
                ok_parts.append(passed)
                if not passed:
                    print(f"  check {number} live: FAIL {detail}")
        if tests is not None and number in tests:
            kinds = [k for k, _ in tests[number]]
            ok_parts.append(bool(kinds) and all(k == "pass" for k in kinds))
            mode.append("code")
            for kind, name in tests[number]:
                if kind != "pass":
                    print(f"  check {number} code: {kind.upper()} {name}")
        if not ok_parts:
            reason = live_err if number in (1, 7) and live_err else (test_err or "no tests")
            print(f"check {number} {TITLES[number]}: NOT RUN ({reason})")
            failing.append(number)
            continue
        all_ok = all(bool(p) for p in ok_parts)
        label = "PASS" if all_ok else "NOT PASSED"
        print(f"check {number} {TITLES[number]}: {label} ({'+'.join(sorted(set(mode)))})")
        if not all_ok:
            failing.append(number)

    if tests is not None and "support" in tests:
        kinds = [k for k, _ in tests["support"]]
        support_ok = all(k == "pass" for k in kinds) and bool(kinds)
        print(f"support tests: {'PASS' if support_ok else 'NOT PASSED'} "
              f"({kinds.count('pass')} passed, {kinds.count('xfail')} known gaps, {kinds.count('fail')} failed)")
        for kind, name in tests["support"]:
            if kind != "pass":
                print(f"  support: {kind.upper()} {name}")
        if not support_ok:
            failing.append("support")
    for label, code in static.items():
        print(f"gate {label}: {'PASS' if code == 0 else 'FAILED (exit ' + str(code) + ')'}")
        if code != 0:
            failing.append(label)
    print("RESULT: every check passed" if not failing else f"RESULT: not passed: {failing}")
    return 0 if not failing else 1


if __name__ == "__main__":
    sys.exit(main())
