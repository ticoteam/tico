import json
import subprocess

import pytest

from scripts import main_red as mr
from scripts import pr_checks as checks

ROOT = mr.ROOT
RED_SHA = "b" * 40


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


def pull_request(number, files, title="A change", author="", commits=()):
    return {
        "number": number,
        "title": title,
        "url": "https://github.com/acme/tico/pull/%d" % number,
        "author": author,
        "commits": list(commits),
        "files": set(files),
    }


@pytest.fixture
def two_runs(tmp_path):
    """A tree where backend/tests/test_a.py imports backend/util.py, test_b.py imports nothing, and backend/unused.py is
    tracked and imported by nothing. A green commit, then a red change to util."""
    git(tmp_path, "init", "-q", "-b", "main")
    green = commit(
        tmp_path,
        "green",
        {
            "backend/__init__.py": "",
            "backend/util.py": "def helper():\n    return 1\n",
            "backend/unused.py": "U = 1\n",
            "backend/tests/__init__.py": "",
            "backend/tests/test_a.py": "from backend.util import helper\n\n\ndef test_a():\n    assert helper()\n",
            "backend/tests/test_b.py": "def test_b():\n    assert True\n",
            "ui/app/mapped.js": "export const mapped = 1;\n",
            "ui/app/other.js": "export const other = 2;\n",
            "ui/tests/google-tab.cjs": "// a browser script\n",
        },
    )
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
    assert mr.test_file("backend/tests/test_page_audience_review3.py::test_leak[stored]") == (
        "backend/tests/test_page_audience_review3.py"
    )
    assert mr.test_def("backend/tests/test_page_audience_review3.py::test_leak[stored]") == "test_leak"
    assert mr.test_file("google-tab.cjs") == "ui/tests/google-tab.cjs"


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
    run = run_of("failure", red, "2026-11-06T16:30:34Z", run_id=2)
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

    result = mr.alarm(api, run_of("failure", red, "2026-11-06T16:30:34Z", run_id=2), root)

    assert result["told"][902].startswith("commented")
    assert [number for number, _ in api.posts] == [902]
    assert [path for path, _ in api.comments] == ["issues/902/comments"]
    assert "<!-- main-red-break %s -->" % green in api.comments[0][1]["body"]


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


def test_the_workflow_runs_only_for_a_main_dispatch_and_reads_by_default():
    text = (ROOT / ".github/workflows/main-red.yml").read_text()
    assert "workflows: [Release validation]" in text
    assert "github.event.workflow_run.event == 'workflow_dispatch'" in text
    assert "github.event.workflow_run.head_branch == 'main'" in text
    assert "permissions:\n  contents: read" in text
