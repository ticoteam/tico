"""`scripts/pr_checks.py local`: a builder runs the tests the PR checks would select, on its own computer, before it
marks a draft ready. Its last line is a receipt the review gate takes for that exact head (backend/test_receipts.py)."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from backend import test_receipts
from scripts import pr_checks as checks

PLAN = {
    "python": ["tests/test_app.py"],
    "browser": ["chat"],
    "caddy": False,
    "contracts": False,
    "conformance": False,
    "rust": [],
    "reason": "measured dependency map",
    "uncertain": [],
}


def git(root, *args):
    return subprocess.check_output(
        ["git", "-c", "user.name=t", "-c", "user.email=t@acme.example", *args], cwd=root, text=True
    ).strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A branch with one change on top of main, the selection fixed, and every command recorded instead of run."""
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "app.py").write_text("x = 1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "Start")
    git(tmp_path, "checkout", "-qb", "feature")
    (tmp_path / "app.py").write_text("x = 2\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_x(): pass\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "Change")
    seen = {}

    def select(changed, event="pull_request", **kwargs):
        seen.update(changed=changed, event=event, base=kwargs.get("base"))
        return dict(seen.get("plan", PLAN))

    monkeypatch.setattr(checks, "selection", select)
    monkeypatch.setattr(checks, "browser_scripts", lambda args, root=None: list(args))
    monkeypatch.setattr(checks, "factory_reason", lambda changed, call_path=None, root=None: None)
    commands = []
    answers = {}

    def execute(command, **kwargs):
        commands.append(command)
        key = next((k for k in answers if k in " ".join(command)), None)
        answer = answers.get(key, (0, ""))
        # A list answers each call in turn, its last item from then on.
        code, out = (answer.pop(0) if len(answer) > 1 else answer[0]) if isinstance(answer, list) else answer
        return SimpleNamespace(returncode=code, stdout=out, stderr="")

    return SimpleNamespace(
        root=tmp_path,
        head=git(tmp_path, "rev-parse", "HEAD"),
        seen=seen,
        commands=commands,
        answers=answers,
        run=lambda **kw: checks.local(base="main", root=tmp_path, execute=execute, **kw),
    )


def receipt_line(out):
    return out.strip().splitlines()[-1]


def gate(line, head):
    """What the review gate reads from this line posted on the owner's ticket."""
    task = {"id": "t1", "owner_id": "bot:builder", "messages": [{"id": "m1", "sender_id": "bot:builder", "body": line}]}
    return test_receipts.at_head(task, head)[0]


def test_a_local_pass_runs_the_ci_selection_and_prints_a_receipt_the_review_gate_takes_at_that_head(repo, capsys):
    assert repo.run() == 0
    # The same selection a pull request's plan makes: the branch's own change, as a pull request event.
    assert repo.seen["changed"] == ["app.py", "tests/test_app.py"] and repo.seen["event"] == "pull_request"
    python, browser = repo.commands
    assert python[-1] == "tests/test_app.py" and "pytest" in python and python[python.index("-n") + 1] == "2"
    assert "scripts/ui-tests.cjs" in browser and browser[-1] == "chat"
    # Heavy local runs share the computer's slots (scripts/heavy.sh), as a bot's full suite does.
    assert all(c[1].endswith("scripts/heavy.sh") for c in repo.commands)
    line = receipt_line(capsys.readouterr().out)
    assert len(repo.head) == 40 and repo.head in line
    assert test_receipts.recognise(line) == {"head": repo.head, "status": "passed", "text": line}
    assert gate(line, repo.head) == "passed"
    assert gate(line, "f" * 40) == "stale"  # a later push needs a new receipt


def test_a_local_failure_stops_and_prints_a_failed_receipt_that_blocks_the_head(repo, capsys):
    repo.answers["pytest"] = (1, "")
    assert repo.run() == 1
    assert len(repo.commands) == 1  # the browser scripts never start after a red Python run
    line = receipt_line(capsys.readouterr().out)
    assert test_receipts.recognise(line)["status"] == "failed" and gate(line, repo.head) == "failed"


def test_uncommitted_changes_get_no_receipt_and_run_nothing(repo, capsys):
    (repo.root / "app.py").write_text("x = 3\n")
    assert repo.run() == 2
    out = capsys.readouterr()
    assert repo.commands == [] and repo.head not in out.out + out.err
    assert not test_receipts.SHA.search(out.out)


def test_what_ci_alone_runs_is_named_and_the_receipt_still_passes(repo, monkeypatch, capsys):
    repo.seen["plan"] = {**PLAN, "conformance": True, "rust": ["runner-rs"], "browser": []}
    monkeypatch.setattr(checks, "factory_reason", lambda changed, call_path=None, root=None: "the sim runs app.py")
    assert repo.run() == 0
    assert len(repo.commands) == 1  # no browser scripts were selected
    line = receipt_line(capsys.readouterr().out)
    assert "conformance" in line and "factory simulation" in line
    assert gate(line, repo.head) == "passed"


def test_contract_tests_run_as_the_pull_request_runs_them(repo):
    repo.seen["plan"] = {**PLAN, "python": [], "browser": [], "contracts": True}
    assert repo.run() == 0
    (command,) = repo.commands
    assert command[command.index("-m", command.index("pytest") + 1) + 1] == "slow"
    assert command[-len(checks.CONTRACT_TESTS) :] == list(checks.CONTRACT_TESTS)


def test_a_change_that_selects_no_tests_still_gets_a_passing_receipt(repo, capsys):
    repo.seen["plan"] = {**PLAN, "python": [], "browser": []}
    assert repo.run() == 0
    assert repo.commands == []
    assert gate(receipt_line(capsys.readouterr().out), repo.head) == "passed"


def test_ready_marks_the_draft_ready_only_after_a_pass_at_the_pull_requests_own_head(repo, capsys):
    view = {"number": 7, "headRefOid": repo.head, "isDraft": True, "url": "https://github.com/acme/app/pull/7"}
    repo.answers["gh pr view"] = (0, json.dumps(view))
    assert repo.run(ready=True) == 0
    assert repo.commands[-1][:4] == ["gh", "pr", "ready", "7"]
    assert gate(receipt_line(capsys.readouterr().out), repo.head) == "passed"


def test_ready_leaves_a_pull_request_that_is_already_ready_alone(repo):
    view = {"number": 7, "headRefOid": repo.head, "isDraft": False, "url": "https://github.com/acme/app/pull/7"}
    repo.answers["gh pr view"] = (0, json.dumps(view))
    assert repo.run(ready=True) == 0
    assert not any(c[:3] == ["gh", "pr", "ready"] for c in repo.commands)


def test_ready_refuses_before_testing_when_the_pull_request_head_is_not_this_commit(repo, capsys):
    view = {"number": 7, "headRefOid": "a" * 40, "isDraft": True, "url": "https://github.com/acme/app/pull/7"}
    repo.answers["gh pr view"] = (0, json.dumps(view))
    assert repo.run(ready=True) == 1
    assert [c[:3] for c in repo.commands] == [["gh", "pr", "view"]]  # no tests, no ready
    assert "push" in capsys.readouterr().err


def test_ready_leaves_the_draft_alone_when_a_push_moved_its_head_during_the_run(repo, capsys):
    view = {"number": 7, "headRefOid": repo.head, "isDraft": True, "url": "https://github.com/acme/app/pull/7"}
    moved = {**view, "headRefOid": "b" * 40}
    repo.answers["gh pr view"] = [(0, json.dumps(view)), (0, json.dumps(moved))]
    assert repo.run(ready=True) == 1
    assert not any(c[:3] == ["gh", "pr", "ready"] for c in repo.commands)
    out = capsys.readouterr()
    assert gate(receipt_line(out.out), repo.head) == "passed"  # the receipt still holds for the head it ran
    assert "b" * 40 in out.err


def test_a_remote_run_that_never_reported_a_result_prints_no_receipt(repo, capsys):
    # scripts/remote.py (TICO_HEAVY_REMOTE=1): 75 never dispatched (an unpushed head), 76 no result. Neither is a test
    # failure, and a failed receipt would block this head for good.
    for code in (75, 76):
        repo.commands.clear()
        repo.answers["pytest"] = (code, "")
        assert repo.run() == 2
        out = capsys.readouterr()
        assert not test_receipts.SHA.search(out.out) and gate(out.out, repo.head) in ("missing", "unrecognised")


def test_ready_after_a_failure_leaves_the_draft_a_draft(repo):
    view = {"number": 7, "headRefOid": repo.head, "isDraft": True, "url": "https://github.com/acme/app/pull/7"}
    repo.answers["gh pr view"] = (0, json.dumps(view))
    repo.answers["pytest"] = (1, "")
    assert repo.run(ready=True) == 1
    assert not any(c[:3] == ["gh", "pr", "ready"] for c in repo.commands)


def test_the_command_line_runs_local_without_the_ci_only_plan_guard(monkeypatch):
    calls = []
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("TICO_PLAN_LOCAL", raising=False)
    monkeypatch.setattr(checks, "local", lambda **kwargs: calls.append(kwargs) or 0)
    monkeypatch.setattr("sys.argv", ["pr_checks.py", "local", "--base", "origin/main", "--jobs", "3", "--ready"])
    assert checks.main() == 0
    assert calls == [{"base": "origin/main", "jobs": 3, "ready": True}]
