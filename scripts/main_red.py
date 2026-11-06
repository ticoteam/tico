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
- Each pull request in the range that changed a file a likely break uses gets one review (COMMENT, never a request for
  changes). Tico ingests pull request reviews (backend/github.py, pull_request_review), and a bot's review becomes a
  note that wakes the pull request's owner. A review GitHub refuses becomes one comment instead. The marker names G,
  so a pull request is told once per break, however often the red run repeats.
- One open issue, labelled main-red, shows the current state and is edited in place. The next green run on main
  closes it and says which of the failing tests passed again with nothing they use changed.

Run it by hand against a run id, with --dry-run to print what it would post:
  GITHUB_REPOSITORY=ticoteam/tico GITHUB_TOKEN=... python3 scripts/main_red.py --run-id N --workdir DIR --dry-run
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
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
UI_TEST = "ui/tests/%s.cjs"  # a browser script that UI_MAP names for a UI file
ALL_UI = "ui/tests/*"  # any browser script: a UI file UI_MAP does not name
PYTEST_FAILED = re.compile(r"\bFAILED (\S+::\S+)")
UI_FAILED = re.compile(r"\d+ of \d+ UI scripts failed: (.+?)(?: \(\d+s\))?\s*$")
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


def test_file(test):
    """The repository file a failing test is defined in: a UI script's file, or a pytest node id's path."""
    return "ui/tests/" + test if test.endswith(".cjs") else test.split("::", 1)[0]


def test_def(test):
    """The function a pytest node id names, without its parameters (None for a UI script)."""
    return test.rsplit("::", 1)[-1].split("[", 1)[0] if "::" in test else None


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
    if "uncertain" in checks.selection(changed, "pull_request", root=graph.root, graph=graph, core=()):
        return None
    data = [name for name in changed if checks.read_only(name) and not graph.code(name)]
    found = set(graph.affected([name for name in changed if name not in data]))
    if data:
        found |= set(graph.read_by_tests(data))
    # A UI file runs the browser scripts that UI_MAP names for it. One it does not map runs every script, as CI does.
    for name in changed:
        if name in checks.UI_MAP:
            found |= {UI_TEST % script for script in checks.UI_MAP[name]}
        elif name.startswith("ui/"):
            found.add(ALL_UI)
    return found


def hit(test, used):
    if used is None or test_file(test) in used:
        return True
    return test.endswith(".cjs") and ALL_UI in used


def verdict(test, baseline, red, git, graph, cache):
    """(kind, reason) for one failing test: "flaky" when the baseline green run has it and nothing it uses changed
    from there to the red candidate; "break" otherwise."""
    if baseline is None:
        return "break", "no green run on main to compare with"
    path, name = test_file(test), test_def(test)
    if not git.has(baseline, path) or (
        name and not re.search(r"\bdef %s\b" % re.escape(name), git.text(baseline, path))
    ):
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
    if report["suspects"]:
        lines += ["", "### Suspect pull requests (each got one review from the alarm)"]
        lines += [
            "- [#%d](%s) %s%s" % (n, s["url"], s["title"], " (by %s)" % s["author"] if s["author"] else "")
            for n, s in sorted(report["suspects"].items())
        ]
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


def review_body(report, test_names, marker):
    tests = "\n".join("- `%s`" % test for test in test_names)
    return (
        "Release validation for main failed on `%s` ([run](%s)). This pull request changed a file that the failing "
        "test(s) use, so it is a likely break:\n\n%s\n\n"
        "Last green run on main: `%s`. This is the main-red alarm's note (scripts/main_red.py), not a request: nothing "
        "needs a reply. The issue \"%s\" stays open until main is green.\n\n%s"
        % (short(report["red"]), report["run_url"], tests, short(report["baseline"]), ISSUE_TITLE, marker)
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


def red(api, run, runs, git, root, dry_run):
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
    }
    result = {"state": "red", "red": sha, "baseline": base, "tests": verdicts, "suspects": sorted(suspects)}
    result["issue"] = upsert(api, render(report), dry_run)
    result["told"] = {}
    for number, pr in sorted(suspects.items()):
        marker = "<!-- main-red-break %s -->" % (base or "none")
        body = review_body(report, pr["tests"], marker)
        if dry_run:
            result["told"][number] = "dry run"
            continue
        try:
            result["told"][number] = tell(api, number, body, marker)
        except rt.Fail as exc:
            result["told"][number] = "failed: %s" % exc
            result.setdefault("errors", []).append(str(exc))
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
        exists = git.has(sha, test_file(test)) and (
            not test_def(test) or re.search(r"\bdef %s\b" % re.escape(test_def(test)), git.text(sha, test_file(test)))
        )
        if not exists or not state.get("red"):
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


def alarm(api, run, root, *, dry_run=False):
    """What one finished Release validation run changes: the red run's issue and its tests, or the green run that
    closes it. Returns a summary (also printed by main)."""
    if run.get("event") != "workflow_dispatch" or run.get("head_branch") != "main":
        return {"skipped": "not a main validation run: %s on %s" % (run.get("event"), run.get("head_branch"))}
    if run.get("conclusion") not in RED | GREEN:
        return {"skipped": "conclusion %s" % run.get("conclusion")}
    if not candidate(run):
        return {"skipped": "the run name names no candidate commit"}
    runs = api.validation_runs()
    if any(
        other["id"] != run["id"] and other.get("status") == "completed" and other["created_at"] > run["created_at"]
        for other in runs
    ):
        return {"skipped": "a newer validation run has finished; its own run tells the issue"}
    git = Git(root)
    if run["conclusion"] in GREEN:
        return green(api, run, git, root, dry_run)
    return red(api, run, runs, git, root, dry_run)


def main(argv=None, env=None):
    env = os.environ if env is None else env
    parser = argparse.ArgumentParser(description="Tell the owners of a red Release validation run on main.")
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--workdir", required=True, help="where to check out the candidate (a git worktree)")
    parser.add_argument("--dry-run", action="store_true", help="print what would be posted; post nothing")
    args = parser.parse_args(argv)
    api = Api(env["GITHUB_REPOSITORY"], env.get("GITHUB_TOKEN") or env.get("GH_TOKEN") or "")
    run = api.get("actions/runs/%d" % args.run_id)
    if run.get("event") != "workflow_dispatch" or run.get("head_branch") != "main" or not candidate(run):
        print(json.dumps({"skipped": "not a candidate run on main"}))
        return 0
    subprocess.run(["git", "worktree", "add", "--detach", "--force", args.workdir, candidate(run)], check=True)
    try:
        result = alarm(api, run, args.workdir, dry_run=args.dry_run)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", args.workdir], capture_output=True, check=False)
    print(json.dumps(result, indent=2, sort_keys=True, default=sorted))
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
