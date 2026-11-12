import json
import math
import subprocess

import pytest

from scripts import main_red as mr
from scripts import pr_checks as checks

ROOT = mr.ROOT
RED_SHA = "b" * 40
TEST_A = "backend/tests/test_a.py::test_a"


def git(root, *args):
    return subprocess.check_output(
        ["git", "-c", "user.name=t", "-c", "user.email=t@acme.example", "-c", "commit.gpgsign=false", *args],
        cwd=root,
        text=True,
    ).strip()


def commit(root, message, files=None):
    for name, text in (files or {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD")


class FakeApi:
    """The GitHub calls scripts/main_red.py makes, kept in memory. `told` maps a pull request number to the reviews and
    comments already on it. `refuse_reviews` makes GitHub refuse a COMMENT review, as it does on a merged pull request."""

    repo = "acme/tico"

    def __init__(self, runs=(), issues=(), refuse_reviews=False, told=None, jobs=(), logs=None):
        self.runs = list(runs)
        self.open_issues = list(issues)
        self.refuse_reviews = refuse_reviews
        self.told = dict(told or {})
        self.jobs = list(jobs)
        self.logs = dict(logs or {})
        self.posts = []  # (pull request number, review) for each review POSTed
        self.comments = []  # (number or path, body) for each comment
        self.closed = []
        self.created = []  # (title, labels)
        self.issue_bodies = []  # the body of each issue opened

    def validation_runs(self):
        return self.runs

    def issues(self, label):
        return self.open_issues

    def pages(self, path, key=None, limit=10):
        return list(self.told.get(int(path.split("/")[1]), []))

    def get(self, path):
        return {"jobs": self.jobs}

    def text(self, path):
        return self.logs[int(path.split("/")[2])]

    def call(self, method, path, body=None, token=None):
        if method == "GET" and path.startswith("commits/"):
            return 200, [], ""
        if method == "POST" and path.endswith("/reviews"):
            self.posts.append((int(path.split("/")[1]), body))
            if self.refuse_reviews:
                return 422, {"message": "Can not review a merged pull request"}, ""
            return 200, {}, ""
        if method == "POST" and path.endswith("/comments"):
            self.comments.append((path, body))
            return 201, {}, ""
        return 200, {}, ""

    def comment(self, number, body):
        self.comments.append((number, body))

    def close(self, number):
        self.closed.append(number)

    def ensure_label(self, name, color, description):
        pass

    def create_issue(self, title, body, labels):
        self.created.append((title, labels))
        self.issue_bodies.append(body)
        return {"number": 9}


def run_of(conclusion, sha, created, run_id=1, branch="main", event="workflow_dispatch"):
    return {
        "id": run_id,
        "status": "completed",
        "conclusion": conclusion,
        "event": event,
        "head_branch": branch,
        "display_title": "Validate %s as v0.5.1 from v0.5.0" % sha,
        "created_at": created,
        "html_url": "https://github.com/acme/tico/actions/runs/%d" % run_id,
    }


def red_of(sha, run_id=2):
    return run_of("failure", sha, "2026-11-06T16:30:34Z", run_id=run_id)


def api_for(green, red, log, extra_runs=()):
    """A red run after a green one, with one failed job whose log is `log`."""
    return FakeApi(
        runs=[run_of("success", green, "2026-11-06T12:00:00Z", run_id=1), red_of(red), *extra_runs],
        jobs=[{"id": 5, "name": "product", "conclusion": "failure"}],
        logs={5: log},
    )


def pull_request(number, files, title="A change", author="", commits=()):
    return {
        "number": number,
        "title": title,
        "url": "https://github.com/acme/tico/pull/%d" % number,
        "author": author,
        "commits": list(commits),
        "files": set(files),
    }


def owned(owners):
    """collect() for a chain of commits: owners maps a commit to the pull requests GitHub says merged it."""

    def collect(api, git_, commits, subjects, repo):
        prs, direct = {}, []
        for sha in commits:
            numbers = owners.get(sha, [])
            if not numbers:
                direct.append({"sha": sha, "subject": subjects.get(sha, ""), "files": git_.changed(sha)})
            for number in numbers:
                entry = prs.setdefault(number, pull_request(number, [], "change %d" % number, commits=[]))
                entry["commits"].append(sha)
                entry["files"].update(git_.changed(sha))
        return prs, direct

    return collect


def chain(tmp_path, changed, count):
    """A green commit on main, then count commits that each change `changed`: shas[i] is the i-th change. The tree is
    BASE_FILES: backend/tests/test_a.py imports backend/util.py, and ui/tests/google-tab.cjs is a browser script."""
    git(tmp_path, "init", "-q", "-b", "main")
    green = commit(tmp_path, "green", BASE_FILES)
    shas = [commit(tmp_path, "change %d" % index, {changed: "value %d\n" % index}) for index in range(count)]
    return tmp_path, green, shas


def failing_from(shas, first_bad):
    """A probe for the bisect: the tests fail at shas[first_bad] and after it, and pass before it. It stands in for
    probe_commit(workdir, git, sha, tests) and for bisect()'s own probe(sha, tests): the last two arguments are used."""

    def probe(*args):
        sha, tests = args[-2:]
        return ("bad" if shas.index(sha) >= first_bad else "good"), [], "scripted"

    return probe


BASE_FILES = {
    "backend/__init__.py": "",
    "backend/util.py": "def helper():\n    return 1\n",
    "backend/unused.py": "U = 1\n",
    "backend/tests/__init__.py": "",
    "backend/tests/test_a.py": "from backend.util import helper\n\n\ndef test_a():\n    assert helper()\n",
    "backend/tests/test_b.py": "def test_b():\n    assert True\n",
    "ui/app/mapped.js": "export const mapped = 1;\n",
    "ui/app/other.js": "export const other = 2;\n",
    "ui/tests/google-tab.cjs": "// a browser script\n",
}


@pytest.fixture
def two_runs(tmp_path):
    """A tree where backend/tests/test_a.py imports backend/util.py, test_b.py imports nothing, and backend/unused.py is
    tracked and imported by nothing. A green commit, then a red change to util."""
    git(tmp_path, "init", "-q", "-b", "main")
    green = commit(tmp_path, "green", BASE_FILES)
    red = commit(tmp_path, "red: a change to util", {"backend/util.py": "def helper():\n    return 2\n"})
    return tmp_path, green, red


def test_every_pull_request_of_a_merge_queue_group_is_in_the_range(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    base = commit(tmp_path, "base", {"backend/a.py": "A = 1\n"})
    for number in range(2839, 2844):
        git(tmp_path, "checkout", "-q", "-b", "pr-%d" % number, base)
        commit(tmp_path, "pr %d work" % number, {"backend/pr%d.py" % number: "X = 1\n"})
        git(tmp_path, "checkout", "-q", "main")
        git(
            tmp_path,
            "merge",
            "-q",
            "--no-ff",
            "-m",
            "Merge pull request #%d from acme/pr-%d" % (number, number),
            "pr-%d" % number,
        )
    direct_sha = commit(tmp_path, "tweak docs without a pull request", {"backend/direct.py": "D = 1\n"})

    git_ = mr.Git(tmp_path)
    commits, capped = git_.range(base, direct_sha)
    prs, direct = mr.collect(FakeApi(), git_, commits, git_.subjects(commits), "acme/tico")

    assert sorted(prs) == [2839, 2840, 2841, 2842, 2843]
    assert prs[2841]["files"] == {"backend/pr2841.py"}
    assert [entry["sha"] for entry in direct] == [direct_sha]
    assert direct[0]["files"] == ["backend/direct.py"]
    assert not capped


def test_pull_request_numbers_come_from_merge_and_squash_subjects():
    assert mr.message_numbers("Merge pull request #2839 from acme/merge-queue") == {2839}
    assert mr.message_numbers("Fix the thing (#2746)") == {2746}
    assert mr.message_numbers("Question tasks: spec D8, step 6a") == set()


def test_failing_tests_come_from_pytest_lines_and_the_ui_scripts_line():
    log = "\n".join(
        [
            "2026-11-06T16:30:00Z FAILED backend/tests/test_page_audience_review3.py::test_leak[stored] - AssertionError",
            "2026-11-06T16:30:01Z FAILED e2e/tests/test_contract.py::test_v2_responses_match_the_goldens",
            "2026-11-06T16:31:00Z 2 of 60 UI scripts failed: google-tab.cjs, support.cjs (253s)",
            "2026-11-06T16:31:01Z unrelated line mentioning FAILED without a node id",
        ]
    )
    assert mr.failing_tests(log) == [
        "backend/tests/test_page_audience_review3.py::test_leak[stored]",
        "e2e/tests/test_contract.py::test_v2_responses_match_the_goldens",
        "google-tab.cjs",
        "support.cjs",
    ]
    # The line scripts/ui-tests.cts prints today names the total run time as "(all M took Ss)".
    assert mr.failing_tests("2 of 60 UI scripts failed: chat.cjs, docs.cjs (all 60 took 253s)\n") == [
        "chat.cjs",
        "docs.cjs",
    ]
    assert mr.test_file("backend/tests/test_page_audience_review3.py::test_leak[stored]") == (
        "backend/tests/test_page_audience_review3.py"
    )
    assert mr.test_def("backend/tests/test_page_audience_review3.py::test_leak[stored]") == "test_leak"
    assert mr.test_file("google-tab.cjs") == "ui/tests/google-tab.cjs"
    assert mr.test_file("chat.cts") == "ui/tests/chat.cts"


def test_a_test_that_passed_green_with_nothing_it_uses_changed_is_likely_flaky(two_runs):
    root, green, red = two_runs
    graph = checks.DependencyMap(root)

    kind, reason = mr.verdict("backend/tests/test_b.py::test_b", green, red, mr.Git(root), graph, {})

    assert kind == "flaky" and green[:9] in reason


def test_a_test_that_uses_a_changed_file_is_a_likely_break(two_runs):
    root, green, red = two_runs
    graph = checks.DependencyMap(root)

    kind, reason = mr.verdict("backend/tests/test_a.py::test_a", green, red, mr.Git(root), graph, {})

    assert kind == "break" and green[:9] in reason


def test_a_test_the_green_run_does_not_have_and_no_green_run_are_breaks(two_runs):
    root, green, red = two_runs
    graph = checks.DependencyMap(root)

    assert mr.verdict("backend/tests/test_b.py::test_new", green, red, mr.Git(root), graph, {})[0] == "break"
    assert mr.verdict("backend/tests/test_b.py::test_b", None, red, mr.Git(root), graph, {})[0] == "break"


def test_a_failure_on_the_green_commit_itself_is_flaky(two_runs):
    root, green, _ = two_runs
    graph = checks.DependencyMap(root)

    assert mr.verdict("backend/tests/test_a.py::test_a", green, green, mr.Git(root), graph, {})[0] == "flaky"


def test_a_ui_change_reaches_the_scripts_it_maps_and_every_script_when_it_maps_none(two_runs, monkeypatch):
    root, _, _ = two_runs
    graph = checks.DependencyMap(root)
    monkeypatch.setitem(checks.UI_MAP, "ui/app/mapped.js", ["google-tab"])

    mapped = mr.reach(graph, ["ui/app/mapped.js"], {})
    assert mr.hit("google-tab.cjs", mapped)
    assert not mr.hit("chat.cjs", mapped)
    assert not mr.hit("backend/tests/test_b.py::test_b", mapped)

    unmapped = mr.reach(graph, ["ui/app/other.js"], {})
    assert mr.hit("chat.cjs", unmapped) and mr.hit("google-tab.cjs", unmapped)
    assert not mr.hit("backend/tests/test_b.py::test_b", unmapped)


def test_a_gone_file_or_a_pin_may_reach_any_test(two_runs):
    root, _, _ = two_runs
    graph = checks.DependencyMap(root)

    assert mr.reach(graph, ["backend/removed.py"], {}) is None
    assert mr.reach(graph, ["backend/requirements.txt"], {}) is None
    assert mr.hit("backend/tests/test_b.py::test_b", mr.reach(graph, ["backend/removed.py", "ui/app/other.js"], {}))


def test_a_ui_file_a_conftest_fixture_reads_may_reach_any_test(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    files = dict(BASE_FILES)
    files["backend/tests/conftest.py"] = (
        "from pathlib import Path\n\nimport pytest\n\n\n@pytest.fixture\ndef page():\n"
        '    return Path("ui/app/other.js").read_text()\n'
    )
    files["backend/tests/test_page.py"] = "def test_page(page):\n    assert page\n"
    commit(tmp_path, "green", files)
    graph = checks.DependencyMap(tmp_path)

    assert mr.reach(graph, ["ui/app/other.js"], {}) is None


def test_a_ui_file_an_autouse_fixture_reads_may_reach_any_test(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    files = dict(BASE_FILES)
    files["backend/tests/conftest.py"] = (
        "from pathlib import Path\n\nimport pytest\n\n\n@pytest.fixture(autouse=True)\ndef page():\n"
        '    return Path("ui/app/other.js").read_text()\n'
    )
    commit(tmp_path, "green", files)
    graph = checks.DependencyMap(tmp_path)

    assert mr.reach(graph, ["ui/app/other.js"], {}) is None


def test_a_red_run_tells_each_suspect_once_and_leaves_the_others_alone(two_runs, monkeypatch):
    root, green, red = two_runs
    runs = [run_of("success", green, "2026-11-06T12:00:00Z", run_id=1)]
    api = FakeApi(
        runs=runs,
        jobs=[{"id": 5, "name": "product", "conclusion": "failure"}],
        logs={5: "FAILED backend/tests/test_a.py::test_a - AssertionError\n"},
    )
    monkeypatch.setattr(
        mr,
        "collect",
        lambda *args: (
            {
                901: pull_request(901, {"backend/util.py"}, "Changes util", "bot", [red]),
                902: pull_request(902, {"backend/unused.py"}, "Changes an unused file", "", [red]),
            },
            [],
        ),
    )
    monkeypatch.setattr(mr.Git, "range", lambda self, base, head: ([red], False))
    run = red_of(red)
    marker = "<!-- main-red-break %s -->" % green

    result = mr.alarm(api, run, root)

    assert result["baseline"] == green
    assert result["tests"]["backend/tests/test_a.py::test_a"][0] == "break"
    assert result["suspects"] == [901]
    assert [number for number, _ in api.posts] == [901]
    assert marker in api.posts[0][1]["body"]
    assert api.created == [("main is red", ["main-red"])]

    # The same red run again, with the review already on pull request 901: nothing new is posted to it.
    api.told = {901: [{"body": marker}]}
    api.posts = []
    again = mr.alarm(api, run, root)
    assert api.posts == []
    assert again["told"][901] == "already told"


def test_a_refused_review_becomes_one_comment(two_runs, monkeypatch):
    root, green, red = two_runs
    api = FakeApi(
        runs=[run_of("success", green, "2026-11-06T12:00:00Z", run_id=1)],
        refuse_reviews=True,
        jobs=[{"id": 5, "name": "product", "conclusion": "failure"}],
        logs={5: "FAILED backend/tests/test_a.py::test_a\n"},
    )
    monkeypatch.setattr(mr, "collect", lambda *args: ({902: pull_request(902, {"backend/util.py"}, commits=[red])}, []))
    monkeypatch.setattr(mr.Git, "range", lambda self, base, head: ([red], False))

    result = mr.alarm(api, red_of(red), root)

    assert result["told"][902].startswith("commented")
    assert [number for number, _ in api.posts] == [902]
    assert [path for path, _ in api.comments] == ["issues/902/comments"]
    assert "<!-- main-red-break %s -->" % green in api.comments[0][1]["body"]


def test_three_or_fewer_suspects_are_each_reviewed_once_without_a_bisect(tmp_path, monkeypatch):
    root, green, shas = chain(tmp_path, "backend/util.py", 3)
    monkeypatch.setattr(mr, "collect", owned({sha: [900 + index] for index, sha in enumerate(shas)}))
    api = api_for(green, shas[-1], "FAILED %s - AssertionError\n" % TEST_A)

    result = mr.alarm(api, red_of(shas[-1]), root)

    assert sorted(number for number, _ in api.posts) == [900, 901, 902]
    assert "needs_bisect" not in result and result["bisect"] is None


def test_more_than_three_suspects_leave_the_reviews_to_the_bisect_job(tmp_path, monkeypatch):
    root, green, shas = chain(tmp_path, "backend/util.py", 6)
    monkeypatch.setattr(mr, "collect", owned({sha: [900 + index] for index, sha in enumerate(shas)}))
    api = api_for(green, shas[-1], "FAILED %s - AssertionError\n" % TEST_A)

    result = mr.alarm(api, red_of(shas[-1]), root)

    assert result["needs_bisect"] is True and result["bisect"] == "pending"
    assert api.posts == []
    assert "Bisecting 6 suspect pull requests" in api.issue_bodies[-1]
    assert "#905" in api.issue_bodies[-1]


def test_the_bisect_reviews_only_the_pull_request_where_the_tests_first_fail(tmp_path, monkeypatch):
    root, green, shas = chain(tmp_path, "backend/util.py", 6)
    monkeypatch.setattr(mr, "collect", owned({sha: [900 + index] for index, sha in enumerate(shas)}))
    monkeypatch.setattr(mr, "probe_commit", failing_from(shas, 3))
    api = api_for(green, shas[-1], "FAILED %s - AssertionError\n" % TEST_A)

    result = mr.alarm(api, red_of(shas[-1]), root, bisecting=True)

    assert result["bisect"] == "culprit"
    assert [number for number, _ in api.posts] == [903]
    body = api.issue_bodies[-1]
    assert "First failing commit: `%s`" % shas[3][:9] in body
    assert "Reviewed: #903." in body and "Not reviewed: #900, #901, #902, #904, #905." in body
    assert "found this pull request's merge" in api.posts[0][1]["body"]


def test_a_culprit_merge_of_many_pull_requests_gets_at_most_three_reviews(tmp_path, monkeypatch):
    root, green, shas = chain(tmp_path, "backend/util.py", 6)
    owners = {sha: [900 + index] for index, sha in enumerate(shas)}
    owners[shas[3]] = [2003, 2004, 2005, 2006, 2007]
    monkeypatch.setattr(mr, "collect", owned(owners))
    monkeypatch.setattr(mr, "probe_commit", failing_from(shas, 3))
    api = api_for(green, shas[-1], "FAILED %s - AssertionError\n" % TEST_A)

    result = mr.alarm(api, red_of(shas[-1]), root, bisecting=True)

    assert result["bisect"] == "culprit"
    assert [number for number, _ in api.posts] == [2003, 2004, 2005]
    assert "#2006, #2007." in api.issue_bodies[-1]


def test_a_culprit_is_told_once_however_often_the_bisect_repeats(tmp_path, monkeypatch):
    root, green, shas = chain(tmp_path, "backend/util.py", 6)
    monkeypatch.setattr(mr, "collect", owned({sha: [900 + index] for index, sha in enumerate(shas)}))
    monkeypatch.setattr(mr, "probe_commit", failing_from(shas, 3))
    api = api_for(green, shas[-1], "FAILED %s - AssertionError\n" % TEST_A)
    mr.alarm(api, red_of(shas[-1]), root, bisecting=True)
    assert [number for number, _ in api.posts] == [903]

    api.told = {903: [{"body": "<!-- main-red-break %s -->" % green}]}
    api.posts = []
    again = mr.alarm(api, red_of(shas[-1]), root, bisecting=True)

    assert api.posts == [] and again["told"][903] == "already told"


def test_a_mixed_probe_is_flaky_and_posts_nothing(tmp_path, monkeypatch):
    root, green, shas = chain(tmp_path, "backend/util.py", 6)
    monkeypatch.setattr(mr, "collect", owned({sha: [900 + index] for index, sha in enumerate(shas)}))

    def mixed(workdir, git_, sha, tests):
        if sha == shas[2]:
            return "flaky", [TEST_A], "runs fail, pass"
        return ("bad" if shas.index(sha) >= 3 else "good"), [], "scripted"

    monkeypatch.setattr(mr, "probe_commit", mixed)
    api = api_for(green, shas[-1], "FAILED %s - AssertionError\n" % TEST_A)

    result = mr.alarm(api, red_of(shas[-1]), root, bisecting=True)

    assert result["bisect"] == "flaky"
    assert api.posts == []
    body = api.issue_bodies[-1]
    assert "ci/quarantine/" in body and "`%s`" % TEST_A in body


def test_a_red_candidate_that_passes_twice_is_flaky_and_posts_nothing(tmp_path, monkeypatch):
    root, green, shas = chain(tmp_path, "backend/util.py", 6)
    monkeypatch.setattr(mr, "collect", owned({sha: [900 + index] for index, sha in enumerate(shas)}))
    monkeypatch.setattr(mr, "probe_commit", lambda workdir, git_, sha, tests: ("good", [], "passed twice"))
    api = api_for(green, shas[-1], "FAILED %s - AssertionError\n" % TEST_A)

    result = mr.alarm(api, red_of(shas[-1]), root, bisecting=True)

    assert result["bisect"] == "flaky" and api.posts == []
    assert "passed twice at the red candidate" in api.issue_bodies[-1]


def test_an_inconclusive_probe_posts_nothing_and_says_the_bisect_did_not_finish(tmp_path, monkeypatch):
    root, green, shas = chain(tmp_path, "backend/util.py", 6)
    monkeypatch.setattr(mr, "collect", owned({sha: [900 + index] for index, sha in enumerate(shas)}))
    monkeypatch.setattr(mr, "probe_commit", lambda workdir, git_, sha, tests: ("inconclusive", [], "timed out"))
    api = api_for(green, shas[-1], "FAILED %s - AssertionError\n" % TEST_A)

    result = mr.alarm(api, red_of(shas[-1]), root, bisecting=True)

    assert result["bisect"] == "inconclusive" and api.posts == []
    assert "The bisect did not finish" in api.issue_bodies[-1]


def test_a_newer_validation_run_supersedes_the_bisect_and_leaves_the_issue_alone(tmp_path, monkeypatch):
    root, green, shas = chain(tmp_path, "backend/util.py", 6)
    monkeypatch.setattr(mr, "collect", owned({sha: [900 + index] for index, sha in enumerate(shas)}))
    api = api_for(green, shas[-1], "FAILED %s - AssertionError\n" % TEST_A)
    newer = run_of("success", shas[-1], "2026-11-06T17:00:00Z", run_id=3)

    def probe(workdir, git_, sha, tests):
        api.runs.append(newer)  # a newer run finishes while this bisect is probing
        return "bad", [], "scripted"

    monkeypatch.setattr(mr, "probe_commit", probe)

    result = mr.alarm(api, red_of(shas[-1]), root, bisecting=True)

    assert result["bisect"] == "superseded" and "skipped" in result
    assert api.posts == [] and api.issue_bodies == []


def test_a_browser_script_break_is_not_bisected_and_gets_no_reviews(tmp_path, monkeypatch):
    root, green, shas = chain(tmp_path, "ui/app/other.js", 6)
    monkeypatch.setattr(mr, "collect", owned({sha: [900 + index] for index, sha in enumerate(shas)}))

    def never(*args):
        raise AssertionError("a browser script is not probed by the bisect")

    monkeypatch.setattr(mr, "probe_commit", never)
    api = api_for(green, shas[-1], "1 of 60 UI scripts failed: google-tab.cjs (3s)\n")

    result = mr.alarm(api, red_of(shas[-1]), root)

    assert "needs_bisect" not in result and api.posts == []
    assert "Not bisected:" in api.issue_bodies[-1]


def test_merge_queue_and_stale_runs_are_skipped(tmp_path):
    api = FakeApi()
    queue = run_of(
        "failure", RED_SHA, "2026-11-06T16:30:34Z", event="merge_group", branch="gh-readonly-queue/main/pr-2839-x"
    )
    assert "skipped" in mr.alarm(api, queue, tmp_path)
    assert "skipped" in mr.alarm(api, run_of("failure", RED_SHA, "x", event="pull_request", branch="chris/x"), tmp_path)
    assert "skipped" in mr.alarm(api, run_of("cancelled", RED_SHA, "2026-11-06T16:30:34Z"), tmp_path)


def test_a_red_run_is_skipped_when_a_newer_validation_run_has_finished(two_runs):
    root, green, red = two_runs
    red_run = run_of("failure", red, "2026-11-06T16:30:34Z", run_id=2)
    api = FakeApi(runs=[red_run, run_of("success", green, "2026-11-06T17:00:00Z", run_id=3)])

    assert "skipped" in mr.alarm(api, red_run, root)


def test_a_green_run_closes_the_open_issue_and_says_which_tests_passed_again(two_runs):
    root, green, red = two_runs
    fix = commit(root, "fix util", {"backend/util.py": "def helper():\n    return 3\n"})
    state = {
        "baseline": green,
        "red": red,
        "run_url": "https://github.com/acme/tico/actions/runs/2",
        "tests": {"backend/tests/test_a.py::test_a": "break", "backend/tests/test_b.py::test_b": "break"},
    }
    issue = {"number": 7, "body": "## main is red\n\n<!-- main-red-state %s -->\n" % json.dumps(state)}
    api = FakeApi(issues=[issue])

    result = mr.alarm(api, run_of("success", fix, "2026-11-06T18:00:00Z", run_id=4), root)

    assert result["closed"] == 7 and api.closed == [7]
    text = api.comments[-1][1]
    assert "`backend/tests/test_a.py::test_a`" in text and "this was a fix" in text
    assert "`backend/tests/test_b.py::test_b`" in text and "likely flaky" in text


def test_a_green_run_with_no_open_issue_does_nothing(two_runs):
    root, green, red = two_runs
    api = FakeApi(issues=[])

    result = mr.alarm(api, run_of("success", red, "2026-11-06T18:00:00Z", run_id=4), root)

    assert result == {"state": "green", "skipped": "no open main-red issue"}
    assert api.closed == [] and api.comments == []


def test_two_runs_say_bad_good_flaky_or_inconclusive():
    assert mr.classify({"a"}, {"a"}) == ("bad", [])
    assert mr.classify(set(), set()) == ("good", [])
    assert mr.classify({"a"}, set()) == ("flaky", ["a"])
    assert mr.classify(None, set()) == ("inconclusive", [])


def test_a_pytest_run_names_each_failed_node_id():
    out = (
        "FAILED backend/tests/test_x.py::test_a[None] - AssertionError: no\n"
        "FAILED backend/tests/test_x.py::test_b - x\n"
    )
    assert set(mr.PYTEST_FAILED.findall(out)) == {
        "backend/tests/test_x.py::test_a[None]",
        "backend/tests/test_x.py::test_b",
    }
    assert mr.PYTEST_NODE.match(TEST_A) and not mr.PYTEST_NODE.match("google-tab.cjs")


def test_the_bisect_finds_the_first_bad_commit_of_seventy_in_log_steps():
    commits = ["c%02d" % index for index in range(70)]
    found = mr.bisect(commits, "g0", [TEST_A], failing_from(commits, 41), lambda: False)

    assert found["verdict"] == "culprit" and found["commit"] == "c41" and found["after"] == "c40"
    assert len(found["steps"]) <= 1 + math.ceil(math.log2(len(commits)))


def test_a_bisect_out_of_time_gives_no_verdict():
    commits = ["c%02d" % index for index in range(6)]
    ticks = iter(range(0, 10**7, 500))

    found = mr.bisect(
        commits, "g0", [TEST_A], lambda sha, tests: ("bad", [], "scripted"), lambda: False, clock=lambda: next(ticks)
    )

    assert found["verdict"] == "inconclusive" and "budget" in found["reason"]
    assert len(found["steps"]) == 1
