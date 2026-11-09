"""Main is red: tell the people and bots whose merges likely broke a red Release validation run on main.

.github/workflows/main-red.yml runs this when a Release validation run (ci.yml, dispatched on main) finishes. A run on
another branch, such as a merge queue group on gh-readonly-queue/*, is skipped. For a red run of candidate C, with G
the newest green run on main before it whose candidate is an ancestor of C:

- The suspect range is G..C: each first-parent and merge commit in it, mapped to its pull requests (GitHub's
  commits/<sha>/pulls, else the number in the commit message). A merge queue group lists each pull request it merged.
- The failing tests come from the failed jobs' logs: pytest FAILED lines and the UI scripts line. A failed job that
  names no test is not attributed to anything.
- A test is likely flaky when G has it and nothing it uses changed from G to C, by scripts/pr_checks.py's measured
  dependency map. Anything else is a likely break, and so is a test that G does not have.
- At most three suspects (pull requests that changed a file a likely break uses) each get one review (COMMENT, never a
  request for changes). Tico ingests pull request reviews (backend/github.py, pull_request_review), and a bot's review
  becomes a note that wakes the pull request's owner. A review GitHub refuses becomes one comment instead.
- More than three suspects, and every likely break a pytest node id: the alarm leaves the reviews to the bisect job
  (.github/workflows/main-red.yml, on RunsOn). The bisect runs the failing tests at the red candidate, then at the
  midpoints of the first-parent commits in G..C, each run twice: both fail is bad, both pass is good, a mix is flaky.
  Only the pull request of the first bad commit gets a review, at most three. A mix ends the bisect as flaky: no
  review, and the test is named as a candidate for ci/quarantine/ (scripts/flake_quarantine.py). A probe that cannot say, the 15 minute
  budget running out, or a newer validation run finishing ends the bisect with no review.
- The review marker names G, so a pull request is told once per break, however often the red run repeats.
- One open issue, labelled main-red, shows the current state and is edited in place. The next green run on main
  closes it and says which of the failing tests passed again with nothing they use changed.

Run it by hand against a run id, with --dry-run to print what it would post:
  GITHUB_REPOSITORY=ticoteam/tico GITHUB_TOKEN=... python3 scripts/main_red.py --run-id N --workdir DIR --dry-run
The bisect alone, offline (no GitHub calls, no issue, no reviews), prints each probe:
  python3 scripts/main_red.py --bisect-local GOOD RED --test NODE_ID --workdir DIR
"""

import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import pr_checks as checks
from scripts import release_train as rt

ISSUE_TITLE = "main is red"
ISSUE_LABEL = "main-red"
RED = {"failure", "timed_out", "startup_failure"}
GREEN = {"success"}
AGGREGATE_JOBS = {"release-check", "test-timings"}  # they only compare the parts; the parts name the failures
MAX_COMMITS = 150
NO_BASELINE_COMMITS = 50
MAX_REVIEWS = 3  # reviews per break: the culprit's pull requests, or every suspect when there are this few
BISECT_OVER = 3  # more suspects than this, with every likely break a pytest node id: bisect instead of reviewing all
REPEATS = 2  # runs of the failing tests at each commit the bisect probes
RUN_LIMIT = 55  # seconds one pytest run may take
BUILD_LIMIT = 10  # seconds the UI build may take
STEP_LIMIT = REPEATS * RUN_LIMIT + BUILD_LIMIT  # seconds one probe may take: 120, two minutes
BISECT_LIMIT = 15 * 60  # seconds a whole bisect may take
ALL_UI = "ui/tests/*"  # any browser script: a UI file UI_MAP does not name
PYTEST_FAILED = re.compile(r"\bFAILED (\S+::\S+)")
PYTEST_NODE = re.compile(r"\S+\.py::\S+$")
# scripts/ui-tests.cts ends a red run with "N of M UI scripts failed: a.cjs, b.cjs (all M took Ss)".
UI_FAILED = re.compile(r"\d+ of \d+ UI scripts failed: (.+?)(?: \((?:all \d+ took )?\d+s\))?\s*$")
MESSAGE_PR = re.compile(r"Merge pull request #(\d+)\b|\(#(\d+)\)\s*$")
STATE = re.compile(r"<!-- main-red-state (\{[^\n]*\}) -->")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_PLAIN = urllib.request.build_opener(_NoRedirect)


class Api(rt.GitHub):
    """The REST calls of scripts/release_train.py's client, plus a job's plain-text log."""

    def text(self, path):
        """GitHub answers a log with a redirect to storage. The token stays with GitHub: storage is read without it."""
        url = "%s/repos/%s/%s" % (self.api, self.repo, path.lstrip("/"))
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + self.token,
            "User-Agent": "tico-main-red",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            with _PLAIN.open(urllib.request.Request(url, headers=headers), timeout=60) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                raise
            location = exc.headers["Location"]
            exc.close()
            with urllib.request.urlopen(location, timeout=120) as response:
                raw = response.read()
        return raw.decode("utf-8", "replace")


class Git:
    """Read-only queries on the repository history. A candidate's worktree shares the history with the checkout."""

    def __init__(self, cwd):
        self.cwd = str(cwd)

    def out(self, *args):
        return subprocess.run(["git", *args], cwd=self.cwd, capture_output=True, text=True, check=True).stdout

    def ok(self, *args):
        return subprocess.run(["git", *args], cwd=self.cwd, capture_output=True, check=False).returncode == 0

    def range(self, base, head):
        """Commits after base up to head, oldest first: the first-parent line and every merge commit, so each pull
        request of a merge queue group is listed. Without a base, the newest NO_BASELINE_COMMITS of the line. Returns
        (commits, capped): when capped, only the newest MAX_COMMITS are kept."""
        if base:
            line = self.out("rev-list", "--first-parent", "--reverse", f"{base}..{head}").split()
            merges = self.out("rev-list", "--merges", "--reverse", f"{base}..{head}").split()
        else:
            line = self.out("rev-list", "--first-parent", "--reverse", "-n", str(NO_BASELINE_COMMITS), head).split()
            merges = []
        commits = list(dict.fromkeys(line + merges))
        return commits[-MAX_COMMITS:], len(commits) > MAX_COMMITS

    def line(self, base, head):
        """The first-parent commits after base up to head, oldest first: one for each change that landed on main. The
        head is the last one."""
        commits = self.out("rev-list", "--first-parent", "--reverse", f"{base}..{head}").split()
        return commits if commits[-1:] == [head] else commits + [head]

    def subjects(self, commits):
        if not commits:
            return {}
        rows = self.out("log", "--no-walk=unsorted", "--format=%H%x00%s", *commits).splitlines()
        return dict(row.split("\x00", 1) for row in rows if "\x00" in row)

    def changed(self, commit):
        """What a commit changed on main, against its first parent: a merge's whole pull request, or a direct commit."""
        return self.files(commit + "^1", commit)

    def files(self, a, b):
        return [path for path in self.out("diff", "--name-only", a, b).split("\n") if path]

    def has(self, sha, path):
        return self.ok("cat-file", "-e", f"{sha}:{path}")

    def text(self, sha, path):
        return self.out("show", f"{sha}:{path}")

    def is_ancestor(self, ancestor, descendant):
        return self.ok("merge-base", "--is-ancestor", ancestor, descendant)


def candidate(run):
    """The commit a Release validation run validated (its run name, `Validate <sha> as <version> from <previous>`)."""
    found = rt.TITLE.match(run.get("display_title") or "")
    return found.group(1) if found else None


def short(sha):
    return (sha or "none")[:9]


def newer_finished(run, runs):
    """Whether a validation run created after this one has finished: its own run decides the issue, not this one."""
    return any(
        other["id"] != run["id"] and other.get("status") == "completed" and other["created_at"] > run["created_at"]
        for other in runs
    )


def test_file(test):
    """The repository file a failing test is defined in: a UI script's file, or a pytest node id's path."""
    return "ui/tests/" + test if test.endswith((".cjs", ".cts")) else test.split("::", 1)[0]


def ui_test_files(stem):
    """Both files a browser script stem may be: ui/tests/<stem>.cjs or .cts (only one exists)."""
    return {"ui/tests/%s.cjs" % stem, "ui/tests/%s.cts" % stem}


def test_def(test):
    """The function a pytest node id names, without its parameters (None for a UI script)."""
    return test.rsplit("::", 1)[-1].split("[", 1)[0] if "::" in test else None


def defined_at(git, sha, test):
    """Whether a commit has the test: its file, and the function a pytest node id names."""
    path, name = test_file(test), test_def(test)
    return git.has(sha, path) and (not name or bool(re.search(r"\bdef %s\b" % re.escape(name), git.text(sha, path))))


def failing_tests(log):
    """The failing tests a job's log names, in order: pytest FAILED lines and the UI scripts line."""
    found = []
    for line in log.splitlines():
        match = PYTEST_FAILED.search(line)
        if match:
            found.append(match.group(1))
        match = UI_FAILED.search(line)
        if match:
            found += [name.strip() for name in match.group(1).split(",") if name.strip()]
    return list(dict.fromkeys(found))


def message_numbers(subject):
    return {int(first or second) for first, second in MESSAGE_PR.findall(subject)}


def reach(graph, changed, cache):
    """The files that run or read a changed file, by the measured map. None when the change is too broad to say (an
    unmapped, deleted or shared file): then any test may use it."""
    key = tuple(sorted(set(changed)))
    if key not in cache:
        cache[key] = set() if not key else _reach(graph, list(key))
    return cache[key]


def _reach(graph, changed):
    # A UI file (or the browser runner) that UI_MAP does not name may break any browser script, and no Python test
    # beyond what the map measures. selection() counts it uncertain (its rule 4), so it is judged here, not there.
    unmapped = {
        name
        for name in changed
        if (name.startswith("ui/") or name == checks.UI_RUNNER)
        and checks.ui_map_key(name) not in checks.UI_MAP
        and (graph.root / name).is_file()
    }
    rest = [name for name in changed if name not in unmapped]
    try:
        if rest and checks.selection(rest, "pull_request", root=graph.root, graph=graph, core=())["uncertain"]:
            return None
    except (OSError, ValueError, KeyError):  # selection could not read what it needs: say nothing about the change
        return None
    data = [name for name in changed if checks.read_only(name) and not graph.code(name)]
    via = set()  # files that reach tests only through a conftest fixture, which the map does not follow
    found = set(graph.affected([name for name in changed if name not in data], via=via))
    if data:
        found |= set(graph.read_by_tests(data, via=via))
    # A conftest the change reaches (an autouse fixture reads or runs it) may change any test below it.
    if via or any(Path(name).name == "conftest.py" for name in found):
        return None
    # A UI file runs the browser scripts that UI_MAP names for it. One it does not map runs every script.
    for name in changed:
        if checks.ui_map_key(name) in checks.UI_MAP:
            found |= {path for script in checks.UI_MAP[checks.ui_map_key(name)] for path in ui_test_files(script)}
    if unmapped:
        found.add(ALL_UI)
    return found


def hit(test, used):
    if used is None or test_file(test) in used:
        return True
    return test.endswith((".cjs", ".cts")) and ALL_UI in used


def verdict(test, baseline, red, git, graph, cache):
    """(kind, reason) for one failing test: "flaky" when the baseline green run has it and nothing it uses changed
    from there to the red candidate; "break" otherwise."""
    if baseline is None:
        return "break", "no green run on main to compare with"
    if not defined_at(git, baseline, test):
        return "break", "not in green run %s (new or renamed)" % short(baseline)
    if hit(test, reach(graph, git.files(baseline, red), cache)):
        return "break", "a change since green run %s touches what it uses" % short(baseline)
    return "flaky", "passed in green run %s and nothing it uses changed since" % short(baseline)


def baseline(run, runs, git, red):
    """The newest green validation run on main before this one whose candidate is an ancestor of the red candidate."""
    for other in runs:
        if other["id"] == run["id"] or other.get("status") != "completed" or other.get("conclusion") not in GREEN:
            continue
        if other["created_at"] >= run["created_at"]:
            continue
        sha = candidate(other)
        if sha and git.is_ancestor(sha, red):
            return sha
    return None


def collect(api, git, commits, subjects, repo):
    """Each commit's pull requests (GitHub's record, else the numbers in its message), and the commits with none."""
    prs, direct = {}, []
    for sha in commits:
        subject = subjects.get(sha, "")
        found = {}
        code, data, _ = api.call("GET", "commits/%s/pulls" % sha)
        for pr in data if code == 200 and isinstance(data, list) else []:
            found[pr["number"]] = pr
        for number in message_numbers(subject):
            found.setdefault(number, None)
        changed = git.changed(sha)
        if not found:
            direct.append({"sha": sha, "subject": subject, "files": changed})
        for number, pr in found.items():
            entry = prs.setdefault(
                number,
                {
                    "number": number,
                    "title": subject,
                    "url": "https://github.com/%s/pull/%d" % (repo, number),
                    "author": "",
                    "commits": [],
                    "files": set(),
                },
            )
            if pr:
                entry.update(
                    title=pr.get("title") or subject,
                    url=pr.get("html_url") or entry["url"],
                    author=(pr.get("user") or {}).get("login") or "",
                )
            entry["commits"].append(sha)
            entry["files"].update(changed)
    return prs, direct


def failed_jobs(api, run_id):
    jobs = api.get("actions/runs/%d/jobs?filter=latest&per_page=100" % run_id)["jobs"]
    return [
        job for job in jobs if job.get("conclusion") in ("failure", "timed_out") and job["name"] not in AGGREGATE_JOBS
    ]


def read_state(body):
    found = STATE.search(body or "")
    try:
        return json.loads(found.group(1)) if found else None
    except ValueError:
        return None


def classify(first, second):
    """What two runs of the failing tests say about one commit: bad when both fail, good when both pass, flaky when
    one fails and the other passes. None is a run that could not say, and makes the probe inconclusive."""
    if first is None or second is None:
        return "inconclusive", []
    if not first and not second:
        return "good", []
    if first and second:
        return "bad", []
    return "flaky", sorted(first | second)


def describe(run):
    return "no answer" if run is None else ("pass" if not run else "fail")


def run_tests(workdir, tests):
    """One pytest run of the tests, the way the python suite runs them (scripts/pr_checks.py): the node ids that
    failed, or None when the run cannot say, because it ran past RUN_LIMIT or pytest stopped before the tests ran."""
    # -m "" undoes pytest.ini's -m "not slow": release validation runs the slow tests too, and a probe names them.
    command = [sys.executable, "-m", "pytest", "-q", "-n", str(checks.WORKERS), *checks.PYTEST_FLAGS, "-m", "", *tests]
    try:
        done = subprocess.run(command, cwd=workdir, capture_output=True, text=True, timeout=RUN_LIMIT, check=False)
    except subprocess.TimeoutExpired:
        return None
    if done.returncode == 0:
        return set()
    failed = set(PYTEST_FAILED.findall(done.stdout + done.stderr))
    return failed if done.returncode == 1 and failed else None


def probe_commit(workdir, git, sha, tests):
    """(result, names, note) for one commit: the tests it defines run there REPEATS times, in the checkout at workdir.
    A commit that defines none of them passes them: the break is not there yet."""
    present = [test for test in tests if defined_at(git, sha, test)]
    if not present:
        return "good", [], "defines none of the tests"
    try:
        subprocess.run(["git", "checkout", "--detach", "--force", sha], cwd=workdir, capture_output=True, check=True)
        subprocess.run(
            ["npm", "run", "--silent", "build:ui"],
            cwd=workdir,
            capture_output=True,
            check=True,
            timeout=BUILD_LIMIT,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return "inconclusive", [], "checkout or UI build failed (%s)" % type(exc).__name__
    runs = [run_tests(workdir, present) for _ in range(REPEATS)]
    result, names = classify(*runs)
    return result, names, "runs %s" % ", ".join(describe(run) for run in runs)


def bisect(commits, good, tests, probe, superseded, clock=time.monotonic, limit=BISECT_LIMIT, step_limit=STEP_LIMIT):
    """The first of commits (first-parent, oldest first, the last one the red candidate) where the failing tests fail.
    probe(sha, tests) gives (result, names, note); superseded() says a newer validation run has finished. The candidate
    is probed first: the failure must reproduce there. Then the commits are halved until one is left, each probe good
    (both runs pass) or bad (both fail). A mix ends it as flaky. An inconclusive probe, the budget running out, or a
    superseded run ends it with no verdict on any commit."""
    started, steps = clock(), []

    def finish(verdict, **fields):
        return dict(verdict=verdict, steps=steps, **fields)

    def probe_at(sha):
        if superseded():
            return "superseded", [], "a newer validation run finished"
        if clock() - started + step_limit > limit:
            return "budget", [], "the bisect ran out of its 15 minute budget"
        began = clock()
        result, names, note = probe(sha, tests)
        steps.append({"sha": sha, "result": result, "names": names, "note": note, "seconds": round(clock() - began)})
        return result, names, note

    def stop(result, names, note, sha):
        if result == "superseded":
            return finish("superseded", reason=note)
        if result == "budget":
            return finish("inconclusive", reason=note)
        if result == "flaky":
            return finish("flaky", commit=sha, names=names, reason="one run failed and one passed at %s" % short(sha))
        if result == "good":  # only the red candidate is stopped on a pass: the failure did not come back
            return finish("flaky", commit=sha, names=list(tests), reason="the tests passed twice at the red candidate")
        return finish("inconclusive", reason="the probe at %s could not say: %s" % (short(sha), note))

    if good is None:
        return finish("inconclusive", reason="no green run on main to start from")
    if not commits:
        return finish("inconclusive", reason="no commits between the green run and the red candidate")
    lo, hi = -1, len(commits) - 1
    result, names, note = probe_at(commits[hi])
    if result != "bad":
        return stop(result, names, note, commits[hi])
    while hi - lo > 1:
        mid = (lo + hi) // 2
        result, names, note = probe_at(commits[mid])
        if result == "good":
            lo = mid
        elif result == "bad":
            hi = mid
        else:
            return stop(result, names, note, commits[mid])
    return finish("culprit", commit=commits[hi], after=commits[lo] if lo >= 0 else good, names=list(tests))


def review_body(report, test_names, marker, culprit=None):
    tests = "\n".join("- `%s`" % test for test in test_names)
    if culprit:
        why = (
            "The bisect of the red run's first-parent commits found this pull request's merge (`%s`) as the first commit "
            "where these tests fail:" % short(culprit)
        )
    else:
        why = "This pull request changed a file that the failing test(s) use, so it is a likely break:"
    return (
        "Release validation for main failed on `%s` ([run](%s)). %s\n\n%s\n\n"
        "Last green run on main: `%s`. This is the main-red alarm's note (scripts/main_red.py), not a request: nothing "
        "needs a reply. The issue \"%s\" stays open until main is green.\n\n%s"
        % (short(report["red"]), report["run_url"], why, tests, short(report["baseline"]), ISSUE_TITLE, marker)
    )


def already_told(api, number, marker):
    for path in ("pulls/%d/reviews?per_page=100" % number, "issues/%d/comments?per_page=100" % number):
        for item in api.pages(path, limit=5):
            if marker in (item.get("body") or ""):
                return True
    return False


def tell(api, number, body, marker):
    """One review (COMMENT) on the pull request; one comment when GitHub refuses the review. Nothing when told already."""
    if already_told(api, number, marker):
        return "already told"
    code, data, _ = api.call("POST", "pulls/%d/reviews" % number, {"event": "COMMENT", "body": body})
    if code in (200, 201):
        return "reviewed"
    code, data, _ = api.call("POST", "issues/%d/comments" % number, {"body": body})
    if code == 201:
        return "commented (the review was refused: %s)" % data.get("message", "")
    raise rt.Fail("telling #%d: %s %s" % (number, code, data.get("message", "")))


def upsert(api, body, dry_run):
    """Edit the one open main-red issue in place, or open it."""
    open_issues = api.issues(ISSUE_LABEL)
    if open_issues:
        number = min(issue["number"] for issue in open_issues)
        if not dry_run:
            api.call("PATCH", "issues/%d" % number, {"body": body})
        return "updated #%d" % number
    if not dry_run:
        api.ensure_label(ISSUE_LABEL, "b60205", "Release validation on main is red (scripts/main_red.py)")
        api.create_issue(ISSUE_TITLE, body, [ISSUE_LABEL])
    return "opened"


def bisect_lines(report):
    """The issue's bisect section: what the bisect found, in plain words, and each probe it made."""
    found = report["bisect"]
    if not found:
        return []
    verdict = found["verdict"]
    if verdict == "pending":
        text = [
            "Bisecting %d suspect pull requests on RunsOn; the bisect job updates this issue when it finishes."
            % len(report["suspects"])
        ]
    elif verdict == "culprit":
        pulls = ", ".join("#%d" % number for number in found.get("prs", [])) or "no pull request (a direct commit)"
        text = [
            "First failing commit: `%s`, after `%s`: %s. Found in %d probes."
            % (short(found["commit"]), short(found["after"]), pulls, len(found["steps"]))
        ]
    elif verdict == "flaky":
        text = [
            "Flaky: %s. Candidate for ci/quarantine/ (scripts/flake_quarantine.py): %s. No pull request was reviewed."
            % (found["reason"], ", ".join("`%s`" % name for name in found["names"]))
        ]
    elif verdict == "not run":
        text = ["Not bisected: %s. No pull request was reviewed." % found["reason"]]
    else:
        text = ["The bisect did not finish: %s. No pull request was reviewed." % found["reason"]]
    probes = ["- `%s` %s (%s)" % (short(step["sha"]), step["result"], step["note"]) for step in found.get("steps", ())]
    return ["", "### Bisect"] + text + ([""] + probes if probes else [])


def render(report):
    """The main-red issue's body: the state now, in plain words, and the state the next run reads back."""
    lines = ["## main is red", ""]
    lines.append("Release validation for main failed on `%s` ([run](%s))." % (short(report["red"]), report["run_url"]))
    if report["baseline"]:
        lines.append("Last green run on main: `%s`." % short(report["baseline"]))
    else:
        lines.append("No green run on main among the last validation runs; the range is the newest commits.")
    lines.append(
        "Range: %d commits, %d pull requests%s."
        % (report["commits"], report["pull_requests"], ", capped" if report["capped"] else "")
    )
    breaks = [(test, reason) for test, (kind, reason) in report["tests"].items() if kind == "break"]
    flaky = [(test, reason) for test, (kind, reason) in report["tests"].items() if kind == "flaky"]
    if breaks:
        lines += ["", "### Likely breaks"] + ["- `%s`: %s" % item for item in breaks]
    lines += bisect_lines(report)
    if report["suspects"]:
        lines += ["", "### Suspect pull requests"]
        lines += [
            "- [#%d](%s) %s%s" % (n, s["url"], s["title"], " (by %s)" % s["author"] if s["author"] else "")
            for n, s in sorted(report["suspects"].items())
        ]
        if report["reviewed"]:
            lines.append("Reviewed: %s." % ", ".join("#%d" % n for n in sorted(report["reviewed"])))
        pending = report["bisect"] and report["bisect"]["verdict"] == "pending"
        unreviewed = [n for n in sorted(report["suspects"]) if n not in report["reviewed"]]
        if unreviewed and not pending:
            lines.append("Not reviewed: %s." % ", ".join("#%d" % n for n in unreviewed))
    if report["direct"]:
        lines += ["", "### Commits with no pull request (nothing is routed to anyone)"]
        lines += ["- `%s` %s" % (c["sha"][:9], c["subject"]) for c in report["direct"]]
    if flaky:
        lines += ["", "### Likely flaky"] + ["- `%s`: %s" % item for item in flaky]
    if report["unattributed"]:
        lines += ["", "### Not attributed"] + [
            "- %s: no test named in the log" % name for name in report["unattributed"]
        ]
    state = {
        "baseline": report["baseline"],
        "red": report["red"],
        "run_url": report["run_url"],
        "tests": {test: kind for test, (kind, _) in report["tests"].items()},
    }
    lines += ["", "<!-- main-red-state %s -->" % json.dumps(state, sort_keys=True)]
    return "\n".join(lines) + "\n"


def red(api, run, runs, git, root, dry_run, bisecting=False):
    """A red run: the suspects, and the reviews. With more than BISECT_OVER suspects and only pytest node ids as likely
    breaks, the reviews wait for the bisect job (bisecting=False records that it is needed, and returns needs_bisect).
    The bisect job (bisecting=True) bisects, and reviews only the pull request of the first bad commit."""
    sha = candidate(run)
    base = baseline(run, runs, git, sha)
    commits, capped = git.range(base, sha)
    prs, direct = collect(api, git, commits, git.subjects(commits), api.repo)
    tests, unattributed = {}, []
    for job in failed_jobs(api, run["id"]):
        try:
            names = failing_tests(api.text("actions/jobs/%d/logs" % job["id"]))
        except (OSError, urllib.error.URLError) as exc:
            unattributed.append("%s (log unreadable: %s)" % (job["name"], exc))
            continue
        if names:
            tests.update(dict.fromkeys(names))
        else:
            unattributed.append(job["name"])
    graph = checks.DependencyMap(Path(root)) if tests else None
    cache = {}
    verdicts = {test: verdict(test, base, sha, git, graph, cache) for test in tests}
    breaks = [test for test, (kind, _) in verdicts.items() if kind == "break"]
    suspects = {}
    for number, pr in prs.items():
        found = [test for test in breaks if hit(test, reach(graph, pr["files"], cache))] if breaks else []
        if found:
            suspects[number] = dict(pr, tests=found)
    direct_hits = []
    for commit in direct:
        found = [test for test in breaks if hit(test, reach(graph, commit["files"], cache))] if breaks else []
        if found:
            direct_hits.append(dict(commit, tests=found))
    report = {
        "red": sha,
        "baseline": base,
        "run_url": run["html_url"],
        "commits": len(commits),
        "pull_requests": len(prs),
        "capped": capped,
        "tests": verdicts,
        "unattributed": unattributed,
        "suspects": suspects,
        "direct": direct_hits,
        "bisect": None,
        "reviewed": {},
    }
    result = {"state": "red", "red": sha, "baseline": base, "tests": verdicts, "suspects": sorted(suspects)}
    marker = "<!-- main-red-break %s -->" % (base or "none")
    targets = []  # (pull request number, the tests it is named for, the commit the bisect found or None)
    if len(suspects) <= BISECT_OVER:
        targets = [(number, suspects[number]["tests"], None) for number in sorted(suspects)]
    elif not breaks or not all(PYTEST_NODE.match(test) for test in breaks):
        report["bisect"] = {
            "verdict": "not run",
            "reason": "the bisect runs pytest node ids only, and a browser script or an unnamed job is among the breaks",
        }
    elif not bisecting:
        report["bisect"] = {"verdict": "pending"}
        result["issue"] = upsert(api, render(report), dry_run)
        result.update(bisect="pending", needs_bisect=True)
        return result
    else:

        def superseded():
            return newer_finished(run, api.validation_runs())

        def probe(commit, names):
            return probe_commit(root, git, commit, names)

        found = bisect(git.line(base, sha) if base else [], base, breaks, probe, superseded)
        if found["verdict"] == "superseded":
            return dict(result, bisect="superseded", skipped="a newer validation run finished; its own run decides")
        report["bisect"] = found
        if found["verdict"] == "culprit":
            culprits, _ = collect(api, git, [found["commit"]], git.subjects([found["commit"]]), api.repo)
            found["prs"] = sorted(culprits)
            targets = [(number, breaks, found["commit"]) for number in sorted(culprits)]
    for number, names, culprit in targets[:MAX_REVIEWS]:
        body = review_body(report, names, marker, culprit)
        if dry_run:
            report["reviewed"][number] = "dry run"
            continue
        try:
            report["reviewed"][number] = tell(api, number, body, marker)
        except rt.Fail as exc:
            report["reviewed"][number] = "failed: %s" % exc
            result.setdefault("errors", []).append(str(exc))
    result["told"] = report["reviewed"]
    result["bisect"] = report["bisect"]["verdict"] if report["bisect"] else None
    result["issue"] = upsert(api, render(report), dry_run)
    return result


def green(api, run, git, root, dry_run):
    """The next green run on main: it closes the open issue, and says which failing tests passed again."""
    issues = api.issues(ISSUE_LABEL)
    if not issues:
        return {"state": "green", "skipped": "no open main-red issue"}
    issue = min(issues, key=lambda item: item["number"])
    state = read_state(issue.get("body")) or {}
    sha = candidate(run)
    graph, cache, lines = None, {}, []
    for test in sorted(state.get("tests") or {}):
        if not defined_at(git, sha, test) or not state.get("red"):
            lines.append("- `%s`: not passing or not present at this green run" % test)
            continue
        graph = graph or checks.DependencyMap(Path(root))
        if hit(test, reach(graph, git.files(state["red"], sha), cache)):
            lines.append(
                "- `%s`: passed again here; a change it uses landed since the red run, so this was a fix" % test
            )
        else:
            lines.append("- `%s`: passed again here with nothing it uses changed: likely flaky" % test)
    comment = "main is green again on `%s` ([run](%s))." % (short(sha), run["html_url"])
    if lines:
        comment += "\n\nThe failing tests of the red run:\n" + "\n".join(lines)
    result = {"state": "green", "green": sha, "closed": issue["number"], "tests": lines}
    if not dry_run:
        api.comment(issue["number"], comment)
        api.close(issue["number"])
    return result


def alarm(api, run, root, *, dry_run=False, bisecting=False):
    """What one finished Release validation run changes: the red run's issue and its tests, or the green run that
    closes it. Returns a summary (also printed by main)."""
    if run.get("event") != "workflow_dispatch" or run.get("head_branch") != "main":
        return {"skipped": "not a main validation run: %s on %s" % (run.get("event"), run.get("head_branch"))}
    if run.get("conclusion") not in RED | GREEN:
        return {"skipped": "conclusion %s" % run.get("conclusion")}
    if not candidate(run):
        return {"skipped": "the run name names no candidate commit"}
    runs = api.validation_runs()
    if newer_finished(run, runs):
        return {"skipped": "a newer validation run has finished; its own run tells the issue"}
    git = Git(root)
    if run["conclusion"] in GREEN:
        return green(api, run, git, root, dry_run)
    return red(api, run, runs, git, root, dry_run, bisecting)


@contextlib.contextmanager
def checkout_of(workdir, sha):
    """A worktree at sha. One that already exists is used as it is (the bisect job checks it out and installs it before
    this runs); otherwise it is added here and removed afterwards."""
    if Path(workdir).exists():
        yield
        return
    subprocess.run(["git", "worktree", "add", "--detach", "--force", workdir, sha], check=True)
    try:
        yield
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", workdir], capture_output=True, check=False)


def write_outputs(env, result, sha):
    """The alarm job's outputs, for the bisect job: whether it runs, and the candidate it checks out."""
    path = env.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write("bisect=%s\ncandidate=%s\n" % ("true" if result.get("needs_bisect") else "false", sha or ""))


def main(argv=None, env=None):
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(description="Tell the owners of a red Release validation run on main.")
    parser.add_argument("--run-id", type=int, help="the Release validation run to read")
    parser.add_argument("--workdir", required=True, help="where to check out the candidate (a git worktree)")
    parser.add_argument("--dry-run", action="store_true", help="print what would be posted; post nothing")
    parser.add_argument("--bisect", action="store_true", help="the bisect job: bisect the range the alarm handed over")
    parser.add_argument(
        "--bisect-local",
        nargs=2,
        metavar=("GOOD", "RED"),
        help="offline, with no GitHub calls: bisect the first-parent commits from GOOD to RED for --test on --workdir",
    )
    parser.add_argument("--test", action="append", default=[], help="a failing test's node id, with --bisect-local")
    args = parser.parse_args(argv)
    if args.bisect_local:
        if not args.test:
            parser.error("--bisect-local needs at least one --test")
        good, red_sha = args.bisect_local
        with checkout_of(args.workdir, red_sha):
            git = Git(args.workdir)
            found = bisect(
                git.line(good, red_sha),
                good,
                args.test,
                lambda commit, names: probe_commit(args.workdir, git, commit, names),
                lambda: False,
            )
            if found["verdict"] == "culprit":
                found["subject"] = git.subjects([found["commit"]]).get(found["commit"], "")
        print(json.dumps(found, indent=2, sort_keys=True))
        return 0
    if args.run_id is None:
        parser.error("--run-id is required")
    api = Api(env["GITHUB_REPOSITORY"], env.get("GITHUB_TOKEN") or env.get("GH_TOKEN") or "")
    run = api.get("actions/runs/%d" % args.run_id)
    if run.get("event") != "workflow_dispatch" or run.get("head_branch") != "main" or not candidate(run):
        print(json.dumps({"skipped": "not a candidate run on main"}))
        return 0
    with checkout_of(args.workdir, candidate(run)):
        result = alarm(api, run, args.workdir, dry_run=args.dry_run, bisecting=args.bisect)
    write_outputs(env, result, candidate(run))
    print(json.dumps(result, indent=2, sort_keys=True, default=sorted))
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
