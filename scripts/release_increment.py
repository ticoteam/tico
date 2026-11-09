"""Incremental release validation (docs/releasing.md#incremental-validation).

At most ONE full Release validation (ci.yml's whole gate) is dispatched automatically in any 24 hours, whatever its
outcome (full_within_day). The anchor is the newest full validation of those 24 hours that ran the suite to an answer
whose commit main still contains: a green one, or a red one naming its failures. It is the anchor whichever release it
validated, and the day's other candidates run only what their diff from it selects. A cancelled full run, or a red one
with unnamed failures (a build, a VM, a startup failure), is no anchor, and it allows no second full run: the window
waits, and a person runs Release train by hand to validate again (a manual run dispatches as it does today). Once the
full run is a day old, the window may dispatch one again. A candidate after the anchor runs only what its diff from the
anchor selects (scripts/pr_checks.py selection, the merge queue's rules, never the full suite), the browser smoke set,
the factory checks when their inputs changed, the product part (the upgrade journey and the Docker smoke checks) when its
previous release differs from the anchor's, the image builds (every candidate: the Dockerfile copies the whole tree), and
the failures carried: a red anchor's, and the newest red validation's since it. A red validation is not run again until
a commit since it selects one of its failures; until then the window waits. A green validation after it (full or
incremental, of a commit main still contains) supersedes it.

The decisions here are pure. The GitHub reads (runs, job logs) come from scripts/release_train.py, and the checkout's
diff and selection from Checkout below, so the unit tests and the replay run the same code.
"""

import argparse
from collections import Counter
from datetime import timedelta
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pr_checks  # beside this module; stdlib only

ROOT = Path(__file__).resolve().parents[1]
ANCHOR_AGE = timedelta(hours=24)
FACTORY, PRODUCT, DESKTOP = "factory", "product", "desktop"
DESKTOP_INPUTS = ("app/",)  # the Tauri desktop app: its part of the gate tests it
IGNORED_JOBS = {"release-check", "test-timings"}  # a consequence of a failed part, or optional
FAILED = {"failure", "cancelled", "timed_out", "startup_failure", "action_required"}
RUNNER = "4cpu-linux-x64"
MATRIX_KEYS = ("name", "part", "shard", "workers", "runner", "binaries")
JOB_NAME = re.compile(r"(python|browser)-\d+|conformance|contracts|product|desktop")
TOKEN_PYTEST = re.compile(r"\bFAILED (\S+?)::")
TOKEN_BROWSER = re.compile(r"\bFAIL +(?:\S*/)?([\w.-]+)\.cjs\b")


def failure_tokens(text):
    """The tests a gate log names as failed: pytest files (`backend/tests/test_x.py`) and browser scripts
    (`ui/tests/x.cjs`). Empty when the log names none."""
    tokens = {match.group(1) for match in TOKEN_PYTEST.finditer(text or "")}
    tokens |= {f"ui/tests/{match.group(1)}.cjs" for match in TOKEN_BROWSER.finditer(text or "")}
    return tokens


def failed_tokens(jobs):
    """What a red validation's failed jobs name, from (job name, log text) pairs: a set of tokens, or None when some
    failure cannot be named (a build, a VM, a log with no failing test), so any commit could be the fix."""
    tokens = set()
    for name, log in jobs:
        if name in IGNORED_JOBS:
            continue
        if name.startswith("factory-sim"):
            tokens.add(FACTORY)
        elif name in (PRODUCT, DESKTOP):
            tokens.add(name)
        elif re.fullmatch(r"(python|browser)-\d+", name):
            found = failure_tokens(log)
            if not found:
                return None
            tokens |= found
        else:
            return None
    return tokens


def anchor_of(records, now, contained, named):
    """The anchor: the newest validation, full or incremental, that finished within the last 24 hours (by finish time),
    whose commit main still contains, and that ran the suite to an answer: green, or red naming its failures. A green
    incremental validation covers its own diff from its anchor, so the chain of incrementals anchors the next one.
    `named(record)` gives a red record's failed tokens (a set; None when a failure cannot be named). A cancelled run, and a red one with unnamed
    failures (a build, a VM, a startup failure), is no anchor; full_within_day still counts it, so no second full run is
    dispatched on its account. Returns the record with its "failed" tokens (empty when green), or None. `records` are
    every release's validations (validation_record in release_train): the anchor may have validated an earlier
    release."""
    candidates = [
        record
        for record in records
        if record["status"] == "completed"
        and record["conclusion"] != "cancelled"
        and record["finished"] is not None
        and now - record["finished"] <= ANCHOR_AGE
    ]
    for record in sorted(candidates, key=lambda item: item["finished"], reverse=True):
        if not contained(record["sha"]):
            continue
        if record["conclusion"] == "success":
            return {**record, "failed": set()}
        if record["conclusion"] in FAILED:
            failed = named(record)
            if failed is not None:
                return {**record, "failed": failed}
    return None


def full_within_day(records, now):
    """The newest full validation that finished within the last 24 hours, whatever its outcome (a cancelled one, or a red
    one with unnamed failures, too), or None. While it exists the window dispatches no full validation automatically:
    a person runs Release train by hand to validate again. `records` are every release's validations, as anchor_of
    reads them."""
    finished = [
        record
        for record in records
        if not record["incremental"]
        and record["status"] == "completed"
        and record["finished"] is not None
        and now - record["finished"] <= ANCHOR_AGE
    ]
    return max(finished, key=lambda item: item["finished"], default=None)


def anchor_open(records, anchor, contained):
    """Whether the anchor is a red validation that no later green one supersedes (full or incremental, of a commit main
    still contains): its failures are then carried, and red_rule applies to it. `records` are newest first."""
    if anchor is None or anchor["conclusion"] not in FAILED:
        return False
    for record in records:
        if record["id"] == anchor["id"]:
            return True
        if record["status"] == "completed" and record["conclusion"] == "success" and contained(record["sha"]):
            return False
    return False


def red_since(records, anchor, contained):
    """The newest red validation that no later green one supersedes, or None. Reds count back to the anchor when there is
    one, else back to the newest green validation of any release. A green validation (full or incremental, of a commit
    main still contains) supersedes the reds older than it. Reds carry across releases: a red nothing later passed is
    still main's failure, and dropping it at the next window would leave its failure unrun. `records` are newest first,
    as anchor_of reads them."""
    for record in records:
        if anchor is not None and record["id"] == anchor["id"]:
            return None
        if record["status"] == "completed" and record["conclusion"] == "success" and contained(record["sha"]):
            return None
        if record["status"] == "completed" and record["conclusion"] in FAILED:
            return record
    return None


def names(items):
    items = sorted(items)
    return ", ".join(items[:3]) + (" and %d more" % (len(items) - 3) if len(items) > 3 else "")


def red_rule(red, head, since_red):
    """Whether the window runs head again after a red validation, and the line it logs.

    `red` is the red validation (sha, url, conclusion, failed: a set of tokens, or None when unnamed); `since_red` is the
    tokens the commits since red select (None when their diff could not be read). A red whose head is still main's head
    waits for a push. One with no named failure runs again on the next commit. Otherwise it runs when a commit since red
    selects one of its failures, and waits (saying so) when none does."""
    short = red["sha"][:12]
    if red["sha"] == head:
        return False, "main's head %s ended %s (%s): the next push to main validates it again" % (
            head[:12],
            red["conclusion"],
            red["url"],
        )
    failed = red.get("failed")
    if not failed:
        return True, "main moved on from %s (%s), which names no failure a commit could fix: validating %s" % (
            short,
            red["url"],
            head[:12],
        )
    if since_red is None:
        return True, "main moved on from %s (%s) and its diff could not be read: validating %s" % (
            short,
            red["url"],
            head[:12],
        )
    hits = failed & since_red
    if hits:
        return True, "a commit since %s selects what failed there (%s): validating %s" % (
            short,
            names(hits),
            head[:12],
        )
    return False, "no commit since %s selects what failed there (%s): the window waits for such a commit" % (
        short,
        names(failed),
    )


def selection_tokens(selected, changed, factory_root=pr_checks.ROOT):
    """What a selection runs, as the same tokens failure_tokens reads from a log. Smoke scripts are not included: they
    run on every incremental validation, so they cannot be the commit that fixes a failure."""
    tokens = set(selected["python"]) | {f"ui/tests/{name}.cjs" for name in selected["browser"]}
    if selected.get("contracts"):
        tokens |= set(pr_checks.CONTRACT_TESTS)
    if selected.get("conformance"):
        tokens.add(pr_checks.CONFORMANCE)
    if selected.get("caddy"):
        tokens.add(PRODUCT)
    if any(name.startswith(DESKTOP_INPUTS) for name in changed):
        tokens.add(DESKTOP)
    if pr_checks.factory_reason(list(changed), root=factory_root) is not None:
        tokens.add(FACTORY)
    return tokens


def load_tables(path):
    """The measured seconds the groups balance with (scripts/test-timings.json): `seconds` for python files (the default
    run's and the release gate's), `browser` for scripts, and `slow` the python files only the release gate collects."""
    table = json.loads(Path(path).read_text())
    default, release, browser = (dict(table.get(key, {})) for key in ("default", "release", "browser"))
    return {"seconds": {**release, **default}, "browser": browser, "slow": set(release) - set(default)}


def has_slow_tests(path):
    """Whether a python test file marks a test slow: its slow tests run only with `-m slow`, the contracts lane."""
    try:
        return "mark.slow" in Path(path).read_text(errors="replace")
    except OSError:
        return False


def job(name, part, files=(), workers="4", **extra):
    return {
        "name": name,
        "part": part,
        "shard": "",
        "workers": workers,
        "runner": RUNNER,
        "files": list(files),
        **extra,
    }


def plan_for(
    selected,
    changed,
    carried,
    exists,
    smoke_browser,
    tables,
    has_slow=lambda path: False,
    previous_changed=False,
    factory_root=pr_checks.ROOT,
):
    """The incremental plan: what the selection runs, the browser smoke set, and the carried failures (`carried`, a set
    of tokens from the newest red validation since the anchor). `exists(path)` tells whether a path is in the candidate's
    tree (a deleted test is not run). Returns the plan; `jobs` lists the gate's VMs in the shape ci.yml's matrix takes.
    `has_slow(path)` tells whether a test file marks slow tests: a carried file that does runs them in the contracts lane.
    `previous_changed`: the candidate's previous release is not the anchor's, so the product part runs (its upgrade
    journey starts from the previous release)."""
    carried = set(carried or ())
    slow = tables["slow"]
    python = set(selected["python"])
    browser = set(selected["browser"]) | set(smoke_browser)
    conformance = bool(selected.get("conformance"))
    contracts = bool(selected.get("contracts"))
    dropped = []
    carried_python = set()
    for token in sorted(carried):
        if token in (FACTORY, PRODUCT, DESKTOP):
            continue
        if token.startswith("ui/tests/") and token.endswith(".cjs"):
            browser.add(token[len("ui/tests/") : -len(".cjs")])
        elif token == pr_checks.CONFORMANCE:
            conformance = True
        elif token.endswith(".py") and exists(token):
            python.add(token)
            carried_python.add(token)
        else:
            dropped.append(token)  # a test file that is gone since: nothing to run
    python = sorted(name for name in python if exists(name))
    # The python lane runs `-m "not slow"`: a failed file's slow tests run in the contracts lane (-m slow) as well.
    contract_files = sorted(
        ({*pr_checks.CONTRACT_TESTS} if contracts else set())
        | {n for n in python if n in slow}
        | {n for n in carried_python if has_slow(n)}
    )
    python = [name for name in python if name not in slow]
    browser = sorted(name for name in browser if exists(f"ui/tests/{name}.cjs"))
    product = bool(selected.get("caddy")) or PRODUCT in carried or bool(previous_changed)
    desktop = any(name.startswith(DESKTOP_INPUTS) for name in changed) or DESKTOP in carried
    factory = pr_checks.factory_reason(list(changed), root=factory_root) is not None or FACTORY in carried
    jobs = build_jobs(
        python, browser, contract_files, conformance, selected.get("rust") or [], product, desktop, tables
    )
    return {
        "python": python,
        "browser": browser,
        "contracts": contract_files,
        "conformance": conformance,
        "product": product,
        "desktop": desktop,
        "factory": factory,
        "carried": sorted(carried),
        "dropped": dropped,
        "reason": selected.get("reason", ""),
        "uncertain": list(selected.get("uncertain") or [])[:20],
        "jobs": jobs,
        "estimated_seconds": sum(item.get("seconds", 0) for item in jobs),
    }


def build_jobs(python, browser, contract_files, conformance, rust, product, desktop, tables):
    """The gate's VMs for these files, grouped by measured seconds the way pr_checks.prepare groups them."""
    jobs = []
    if python:
        split, loads = pr_checks.groups(Counter(dict.fromkeys(python, 1)), tables["seconds"])
        jobs += [
            job(f"python-{i}", "python", files, seconds=load)
            for i, (files, load) in enumerate(zip(split, loads, strict=True), 1)
            if files
        ]
    if browser:
        split, loads = pr_checks.groups(
            Counter(dict.fromkeys(browser, 1)),
            tables["browser"],
            kind="browser",
            fallback=pr_checks.UNTIMED_SCRIPT_SECONDS,
        )
        jobs += [
            job(f"browser-{i}", "browser", files, workers="2", binaries="no", seconds=load)
            for i, (files, load) in enumerate(zip(split, loads, strict=True), 1)
            if files
        ]
    if conformance:
        jobs.append(job("conformance", "python", [pr_checks.CONFORMANCE], crates=list(rust)))
    if contract_files:
        jobs.append(job("contracts", "python", contract_files))
    if product:
        # The product and desktop parts run hosted_release_check's native_binaries, so their VMs place the release binaries.
        jobs.append(job("product", "product", workers="2"))
    if desktop:
        jobs.append(job("desktop", "desktop", workers="2"))
    return jobs


def plan_json(plan):
    """The plan as JSON text (one line). Release validation makes the plan itself from its checkout, so its size is no
    limit: only the anchor, the head and the carried names are dispatched."""
    return json.dumps(plan, separators=(",", ":"), sort_keys=True)


def full_matrix():
    """The full validation's gate VMs: the product part, four browser shards, 28 python shards and the desktop part."""
    jobs = [{"name": "product", "part": "product", "shard": "", "workers": "2", "runner": RUNNER}]
    jobs += [
        {
            "name": f"browser-{k}",
            "part": "browser",
            "shard": f"{k}/4",
            "workers": "2",
            "runner": RUNNER,
            "binaries": "no",
        }
        for k in range(1, 5)
    ]
    jobs += [
        {
            "name": f"python-{k}",
            "part": "python",
            "shard": f"{k}/28",
            "workers": "4",
            "runner": RUNNER,
            **({} if k <= 2 else {"binaries": "no"}),
        }
        for k in range(1, 29)
    ]
    jobs.append({"name": "desktop", "part": "desktop", "shard": "", "workers": "2", "runner": RUNNER})
    return jobs


def matrix_of(plan_text):
    """The gate's matrix: the full one for an empty plan, else the plan's VMs. Refuses a plan that names an unknown VM."""
    if not plan_text.strip():
        return {"include": full_matrix()}
    plan = json.loads(plan_text)
    jobs = plan.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("an incremental plan lists no VMs")
    for entry in jobs:
        if not JOB_NAME.fullmatch(entry.get("name", "")) or entry.get("part") not in (
            "python",
            "browser",
            "product",
            "desktop",
        ):
            raise ValueError("an incremental plan names an unknown VM: %r" % (entry.get("name"),))
    return {"include": [{key: entry[key] for key in MATRIX_KEYS if key in entry} for entry in jobs]}


class Checkout:
    """What a commit range selects, read from the checkout (the window job's, at main's head)."""

    def __init__(self, root=ROOT):
        self.root = Path(root)
        self._graph = None

    def known(self, sha):
        return (
            subprocess.run(
                ["git", "cat-file", "-e", sha + "^{commit}"], cwd=self.root, capture_output=True, check=False
            ).returncode
            == 0
        )

    def selected(self, base, head):
        """(changed paths, selection) for base..head, selected the way a merge queue group selects."""
        if not (self.known(base) and self.known(head)):
            raise RuntimeError("%s..%s is not in the checkout" % (base[:12], head[:12]))
        changed = pr_checks.changed_files(base, head, root=self.root)
        added = pr_checks.changed_files(base, head, root=self.root, only="A")
        changed = sorted(set(changed) | set(pr_checks.script_changes(base, head, changed, root=self.root)))
        if self._graph is None:
            self._graph = pr_checks.DependencyMap(self.root)
        selected = pr_checks.selection(
            changed, "merge_group", root=self.root, graph=self._graph, base=base, added=added
        )
        return changed, selected

    def tokens_since(self, base, head):
        """The tokens the commits base..head select, or None when the diff cannot be read."""
        try:
            changed, selected = self.selected(base, head)
        except Exception:  # noqa: BLE001 - an unreadable diff is a fact the window logs, not a crash
            return None
        return selection_tokens(selected, changed, self.root)

    def plan(self, base, head, carried, previous_changed=False):
        """The incremental plan for base..head with the carried failures, as JSON text. Raises when it cannot be made (a
        commit missing from the checkout). `previous_changed`: the candidate's previous release is not the anchor's
        (plan_for runs the product part)."""
        changed, selected = self.selected(base, head)
        _, smoke_browser = pr_checks.smoke(self.root)
        tables = load_tables(self.root / "scripts" / "test-timings.json")
        plan = plan_for(
            selected,
            changed,
            carried,
            lambda path: (self.root / path).is_file(),
            smoke_browser,
            tables,
            has_slow=lambda path: has_slow_tests(self.root / path),
            previous_changed=previous_changed,
            factory_root=self.root,
        )
        return plan_json(plan)


def carried_of(text):
    """The carried failures a dispatch names: a JSON list of tokens (empty text: none)."""
    if not (text or "").strip():
        return set()
    names = json.loads(text)
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise ValueError("carried failures are a JSON list of test names")
    return set(names)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    matrix = sub.add_parser("matrix", help="print the gate's matrix for a plan (empty: the full validation's)")
    matrix.add_argument("plan", nargs="?", default="")
    plan = sub.add_parser("plan", help="print the incremental plan for anchor..head, made from a checkout")
    plan.add_argument("--root", required=True, help="the checkout that holds the candidate's tree and history")
    plan.add_argument("--base", required=True, help="the anchor commit")
    plan.add_argument("--head", required=True, help="the candidate commit")
    plan.add_argument("--carried", default="", help="JSON list of the carried failed tests")
    plan.add_argument("--anchor-previous", default="", help="the release the anchor validated against")
    plan.add_argument("--previous", required=True, help="the release this candidate is compared with")
    args = parser.parse_args(argv)
    if args.action == "plan":
        previous_changed = bool(args.anchor_previous) and args.anchor_previous != args.previous
        text = Checkout(args.root).plan(
            args.base, args.head, carried_of(args.carried), previous_changed=previous_changed
        )
        print(text)
        return 0
    print(json.dumps(matrix_of(args.plan), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
