"""`tico task worktree add|attach|setup` and the local MCP tools run the runner's own worktree code
(`tico-runner worktree`, runner-rs/src/worktrees/cli.rs, which holds the git tests). Here: which binary runs, what it is
given, and how its JSON answer becomes a result, a server refusal (a worktree wait keeps its fields) or a message. The
binary is a script that prints what a runner would."""

import json
import os
from pathlib import Path

import pytest

from clients import hubcli, remotecli
from clients.commands import task_work
from clients.tico import APIError
from runner_support import engine


def fake_runner(folder, out="", err="", code=0, name="tico-runner"):
    """A runner that records its arguments and TICO_ROOT, then prints `out` and `err` and exits `code`."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    (folder / "out").write_text(out)
    (folder / "err").write_text(err)
    path.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{folder}/argv"\n'
        f'printf "%s" "$TICO_ROOT" > "{folder}/root"\n'
        f'cat "{folder}/out"; cat "{folder}/err" >&2\n'
        f"exit {code}\n"
    )
    path.chmod(0o755)
    return path


@pytest.fixture
def runner(monkeypatch, tmp_path):
    """Point the command at a fake runner; returns a function that sets what it answers."""
    monkeypatch.delenv("TICO_ROOT", raising=False)

    def answer(out="", err="", code=0):
        binary = fake_runner(tmp_path / "bin", out, err, code)
        monkeypatch.setattr(task_work, "runner_binary", lambda env: binary)
        return tmp_path / "bin"

    return answer


def test_the_runner_gets_the_operation_task_and_value_and_its_last_line_is_the_answer(runner):
    folder = runner(out='Tico runner: a log line first\n{"link_id": "L1", "state": "present"}\n')
    assert task_work.worktree("add", "-odd/name", "t7") == {"link_id": "L1", "state": "present"}
    assert (folder / "argv").read_text().split("\n")[:-1] == ["worktree", "add", "--task", "t7", "--", "-odd/name"]
    # The runner keeps worktrees out of this checkout wherever its binary lives.
    assert (folder / "root").read_text() == str(engine.ROOT)
    task_work.worktree("attach", "tasks/x/product")
    assert (folder / "argv").read_text().split("\n")[:-1] == ["worktree", "attach", "--", "tasks/x/product"]


def test_a_server_refusal_keeps_its_code_status_and_extra_fields(runner):
    wait = {"id": "wait-1"}
    error = {"code": "worktree_limit", "detail": "All slots in use", "status": 409, "retryable": False, "wait": wait}
    runner(out=json.dumps({"error": error}) + "\n", code=1)
    with pytest.raises(APIError) as refused:
        task_work.worktree("add", "org/product")
    assert (refused.value.code, refused.value.detail, refused.value.status) == (
        "worktree_limit",
        "All slots in use",
        409,
    )
    assert refused.value.extra == {"wait": wait}


def test_a_worktree_wait_ends_the_cli_with_its_own_exit_status(runner, monkeypatch, capsys):
    monkeypatch.setenv("HUB_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(remotecli, "Client", lambda *args, **kwargs: object())
    error = {"code": "worktree_limit", "detail": "Waiting for a worktree", "status": 409, "wait": {"id": "w"}}
    runner(out=json.dumps({"error": error}), code=1)
    code = remotecli.main(hubcli.parser().parse_args(["task", "worktree", "add", "org/product", "--task", "t7"]))
    assert (code, capsys.readouterr().out.strip()) == (remotecli.WORKTREE_WAIT_EXIT, "Waiting for a worktree")


@pytest.mark.parametrize(
    ("out", "err", "code", "message"),
    [
        ('{"error": {"detail": "Setup did not finish"}}', "", 1, "^Setup did not finish$"),
        ('{"error": "plain words"}', "", 1, "^plain words$"),
        ("", "error: unrecognized subcommand 'worktree'", 2, "older than this Tico checkout"),
        ("", "thread 'main' panicked", 101, r"failed \(exit 101\): thread 'main' panicked"),
    ],
    ids=["message", "flat-error", "old-runner", "no-answer"],
)
def test_everything_else_is_a_message(runner, out, err, code, message):
    runner(out=out, err=err, code=code)
    with pytest.raises(ValueError, match=message):
        task_work.worktree("setup", "org/product")


def test_no_runner_binary_says_so(monkeypatch):
    monkeypatch.setattr(task_work, "runner_binary", lambda env: None)
    with pytest.raises(ValueError, match="No Tico runner binary"):
        task_work.worktree("add", "org/product")


def test_the_mcp_tools_run_the_same_command(monkeypatch):
    seen = []
    monkeypatch.setattr(task_work, "worktree", lambda *args: seen.append(args) or {"ok": True})
    assert task_work.task_worktree_add(None, {"repo": "org/product", "task": "t7"}) == {"ok": True}
    assert task_work.task_worktree_attach(None, {"path": "tasks/x/product"}) == {"ok": True}
    assert seen == [("add", "org/product", "t7"), ("attach", "tasks/x/product", None)]


def test_the_binary_is_the_checkouts_then_the_turns_runner_then_any_other(monkeypatch, tmp_path):
    current = fake_runner(tmp_path / "current")
    monkeypatch.setattr(engine, "ready", lambda env, root: current)
    assert task_work.runner_binary({}, tmp_path) == current

    monkeypatch.setattr(engine, "ready", lambda env, root: None)
    monkeypatch.setattr(engine, "candidates", lambda env, root: [tmp_path / "missing" / "tico-runner"])
    # The turn's `gh` is a link to the runner that started it.
    turn_runner = fake_runner(tmp_path / "runner")
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "gh").symlink_to(turn_runner)
    installed = fake_runner(tmp_path / "installed")
    path = f"{shim}{os.pathsep}{installed.parent}"
    assert task_work.runner_binary({"PATH": path}, tmp_path) == turn_runner.resolve()

    # A real gh is not a runner: the one on PATH is used.
    real = fake_runner(tmp_path / "real-gh", name="gh")
    assert task_work.runner_binary({"PATH": f"{real.parent}{os.pathsep}{installed.parent}"}, tmp_path) == Path(
        installed
    )
    assert task_work.runner_binary({"PATH": str(tmp_path / "nowhere")}, tmp_path) is None
