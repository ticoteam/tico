"""A checkout follows its server's release: the update process (python -m runner_support.release_update) against a temp repository."""

import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from runner_support import release_update as ru
from runner_support import checkout_links


@pytest.fixture
def old_follower(tmp_path):
    # Exercise the released follower itself: copying its due rules would let the compatibility test drift.
    source = subprocess.check_output(["git", "show", "v0.3.45:runner/release_update.py"], cwd=ru.ROOT, text=True)
    scope = {"__file__": ru.__file__, "__name__": "released_update"}
    exec(compile(source, "<v0.3.45 follower>", "exec"), scope)
    now = [1_800_000_000.0]
    follower = scope["Follower"](
        {},
        tmp_path,
        None,
        env={"TICO_RUNNER_KIND": "linux"},
        run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
        clock=lambda: now[0],
        wall=lambda: now[0],
    )
    follower.release, follower.desired = "0.3.44", "0.3.45"
    return follower, now


@pytest.mark.slow
def test_deferral_statuses_against_the_released_followers_due_and_claim_rules(old_follower):
    follower, now = old_follower
    cases = json.loads((Path(__file__).parent / "fixtures/update-status-cases.json").read_text())
    for case in cases:
        ru.write_status(
            follower.directory,
            state=case["state"],
            target="0.3.45",
            error="test cause",
            at=now[0] - case["age"],
            **({"not_before": now[0] + case["retry_in"]} if "retry_in" in case else {}),
        )
        follower.pending_since = now[0] - 21 * 60
        follower.decide()
        assert (follower.pending is not None) == case["python_due"], case
        assert follower.blocks_claims(True) == (case["python_due"] or case["state"] == "updating"), case
        if not case["python_due"] and case["state"] != "updating":
            assert not follower.blocks_claims(False), case
            assert follower.pending_since is None, case


@pytest.mark.slow
def test_old_runners_release_claims_for_the_legacy_fallback(old_follower, monkeypatch):
    monkeypatch.delenv("TICO_UPDATE_RETRY", raising=False)
    follower, now = old_follower
    for reason in (
        "the update lock cannot be checked",
        "another update holds the checkout lock",
        "this computer still has work running",
    ):
        result = ru.defer_update(follower.directory, "v0.3.45", reason, clock=lambda: now[0], transient=True)
        assert result["state"] == "failed" and reason in result["error"]
        assert "not_before" not in ru.read_status(follower.directory)
        for elapsed in (0, 6 * 3600 - 1):
            follower.pending_since = now[0] - 21 * 60
            now[0] += elapsed
            follower.decide()
            assert follower.pending is None and not follower.running
            assert not follower.blocks_claims(True) and not follower.blocks_claims(False)
            now[0] -= elapsed
        now[0] += 6 * 3600
        follower.decide()
        assert follower.pending == "0.3.45"
        ru.write_status(follower.directory, state="updating", target="0.3.45", error="", at=now[0])


def test_transient_deferrals_grow_to_an_hour_without_becoming_blocked(tmp_path, monkeypatch):
    reason = "active work"
    monkeypatch.setenv("TICO_UPDATE_RETRY", "short")
    now = [1000.0]
    for delay in (120, 300, 900, 1800, 3600, 3600, 3600):
        result = ru.defer_update(tmp_path, "0.3.45", reason, clock=lambda: now[0], transient=True)
        status = ru.read_status(tmp_path)
        assert result["state"] == "idle" and status["not_before"] == now[0] + delay
        assert status["cause_count"] == 0
        now[0] += delay
        ru.write_status(tmp_path, state="updating", target="0.3.45", at=now[0])
    # A new release or an actual outcome starts a fresh schedule.
    ru.defer_update(tmp_path, "0.3.46", reason, clock=lambda: now[0], transient=True)
    assert ru.read_status(tmp_path)["deferrals"] == 1
    ru.write_status(tmp_path, state="healthy", target="0.3.46", at=now[0])
    ru.defer_update(tmp_path, "0.3.46", reason, clock=lambda: now[0], transient=True)
    assert ru.read_status(tmp_path)["deferrals"] == 1


def test_only_persistent_causes_become_blocked_and_transient_work_resets_them(tmp_path, monkeypatch):
    monkeypatch.setenv("TICO_UPDATE_RETRY", "short")
    now = [1000.0]
    for reason in ("binary missing", "other cause", "binary missing"):
        assert ru.defer_update(tmp_path, "0.3.45", reason, clock=lambda: now[0])["state"] == "idle"
        now[0] += 60
        ru.write_status(tmp_path, state="updating", target="0.3.45", at=now[0])
    for _ in range(2):
        result = ru.defer_update(tmp_path, "0.3.45", "binary missing", clock=lambda: now[0])
        ru.write_status(tmp_path, state="updating", target="0.3.45", at=now[0])
    assert result["state"] == "blocked" and "3 tries" in result["error"]
    ru.defer_update(tmp_path, "0.3.45", "active work", clock=lambda: now[0], transient=True)
    now[0] += 3 * 3600
    assert ru.defer_update(tmp_path, "0.3.45", "binary missing", clock=lambda: now[0])["state"] == "idle"
    now[0] += ru.DEFERRAL_LIMIT_S
    result = ru.defer_update(tmp_path, "0.3.45", "other cause", clock=lambda: now[0])
    assert result["state"] == "blocked" and "two hours" in result["error"]
    assert ru.read_status(tmp_path)["not_before"] == now[0] + 3600


@pytest.mark.parametrize("policy,expected", [("short", "idle"), ("legacy", "failed")])
def test_a_drain_that_never_finishes_defers_before_doing_any_checkout_work(tmp_path, monkeypatch, policy, expected):
    monkeypatch.setenv("TICO_UPDATE_RETRY", policy)
    now = [0.0]
    monkeypatch.setattr(ru.time, "time", lambda: now[0])
    monkeypatch.setattr(ru.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setattr(ru, "in_flight", lambda directory: True)
    monkeypatch.setattr(ru, "apply", lambda *a, **kw: pytest.fail("drain timeout must not start Git work"))
    args = SimpleNamespace(state_dir=str(tmp_path), version="0.3.45")
    for attempt in range(1, ru.MAX_DEFERRALS + 1):
        assert ru.run_update(args) == 1
        status = ru.read_status(tmp_path)
        assert status["deferrals"] == attempt and status["state"] == expected
        assert "work running" in status["error"]
    assert now[0] == 600 * ru.MAX_DEFERRALS


def test_recovery_file_reads_refuse_fifos_symlinks_devices_and_oversized_files(tmp_path):
    # A child deadline catches a blocking open/read without hanging pytest's worker shutdown.
    subprocess.run(
        [
            sys.executable,
            "-c",
            r'''
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from runner_support import regular_file as rf
root = Path(sys.argv[1])
path = root / "fifo"
os.mkfifo(path)
holder = os.open(path, os.O_RDWR | os.O_NONBLOCK)
target = root / "target"
target.write_bytes(b"keep")
link = root / "link"
link.symlink_to(target)
large = root / "large"
large.write_bytes(b"x" * (rf.SMALL + 1))
for path in (path, link, Path("/dev/null"), large):
    try:
        rf.read(path, rf.SMALL)
    except OSError:
        pass
    else:
        raise AssertionError(f"accepted unsafe file: {path}")
    try:
        with rf.lock(path):
            pass
    except OSError:
        pass
    else:
        raise AssertionError(f"accepted unsafe lock file: {path}")
# A file growing after fstat must still be read with a byte limit.
info = large.stat()
rf.os.fstat = lambda fd: SimpleNamespace(st_mode=info.st_mode, st_size=0)
try:
    rf.read(large, rf.SMALL)
except OSError:
    pass
else:
    raise AssertionError("accepted a file growing past the read limit")
os.close(holder)
assert target.read_bytes() == b"keep"
''',
            str(tmp_path),
        ],
        check=True,
        timeout=10,
    )


def test_instruction_and_registration_reads_refuse_replaced_fifos(tmp_path):
    subprocess.run(
        [
            sys.executable,
            "-c",
            r'''
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from runner_support import release_update as ru, git_locks
root = Path(sys.argv[1]).resolve()
metadata = root / ".git"
metadata.mkdir()
path = root / "AGENTS.md"
path.write_text("CLAUDE.md")
target = root / "CLAUDE.md"
target.write_text("rules")
def git(*args):
    stdout = ""
    if args[0] == "rev-parse": stdout = str(metadata)
    elif args[0] == "ls-files": stdout = "120000 " + "a" * 40 + " 0\tAGENTS.md\0"
    elif args[0] == "cat-file": stdout = "CLAUDE.md"
    return SimpleNamespace(returncode=0, stdout=stdout)
read = ru.read_regular
def replace_before_open(file):
    if file == target:
        target.unlink()
        os.mkfifo(target)
    return read(file)
ru.read_regular = replace_before_open
assert not ru.repair_instruction_links(root, git)
assert path.read_text() == "CLAUDE.md" and not path.is_symlink()
ru.read_regular = read
saved = metadata / "tico-instruction-backups"
saved.mkdir()
backup = saved / "AGENTS.md.tico-backup-legacy"
backup.write_text("rules")
path.unlink()
path.symlink_to("CLAUDE.md")
assert backup.name in ru.instruction_backup_note(root, metadata)
assert backup.read_text() == "rules"
admin = metadata / "worktrees/task"
admin.mkdir(parents=True)
task = root / "task"
task.mkdir()
pointers = [(task / ".git", f"gitdir: {admin}\n"),
            (admin / "gitdir", f"{task / '.git'}\n"), (admin / "commondir", "../..\n")]
for path, content in pointers:
    path.write_text(content)
assert git_locks.metadata_roots(task) == (admin, metadata)
for path, content in pointers:
    path.unlink()
    os.mkfifo(path)
    assert not git_locks.metadata_roots(task)
    path.unlink()
    path.write_text(content)
git_locks.OWNED_ROOTS = (root,)
assert not git_locks.report(root, f"fatal: Unable to create '{metadata}/index.lock': File exists", metadata)
''',
            str(tmp_path),
        ],
        check=True,
        timeout=10,
    )


def test_a_second_updater_exits_before_drain_or_checkout_work_and_leaves_status_alone(tmp_path, monkeypatch):
    ru.write_status(tmp_path, state="updating", target="0.2.0", error="", at=1)
    before = ru.read_status(tmp_path)
    calls = []

    def run(args):
        with ru.updater_lock(tmp_path) as acquired:
            assert not acquired  # held through the whole update, before any drain wait
        calls.append(args.version)
        return 0

    monkeypatch.setattr(ru, "run_update", run)
    argv = ["--version", "0.3.0", "--state-dir", str(tmp_path), "--pid", "42"]
    with ru.updater_lock(tmp_path) as acquired:
        assert acquired
        assert ru.main(argv) == 0 and calls == []
        assert ru.read_status(tmp_path) == before
    assert ru.main(argv) == 0 and calls == ["0.3.0"]


def test_an_updater_releases_its_process_lock_when_it_fails(tmp_path, monkeypatch):
    def fail(args):
        raise RuntimeError("update failed")

    monkeypatch.setattr(ru, "run_update", fail)
    assert ru.main(["--version", "0.2.0", "--state-dir", str(tmp_path), "--pid", "42"]) == 1
    assert "update failed" in ru.read_status(tmp_path)["error"]
    with ru.updater_lock(tmp_path) as acquired:
        assert acquired


@pytest.mark.slow
@pytest.mark.parametrize("policy", ["short", "legacy"])
@pytest.mark.parametrize(
    "step",
    [
        "process_open",
        "process_size",
        "process_symlink",
        "process_flock",
        "config_json",
        "config_shape",
        "registration",
        "drain_read",
        "drain_db",
        "drain_sleep",
        "kind",
        "systemd_unit",
        "git_lookup",
        "git_gate",
        "checkout_open",
        "checkout_size",
        "checkout_symlink",
        "checkout_flock",
        "status_read",
        "status_write",
        "early_exit",
        "exit_zero",
        "cli_exit",
        "interrupt",
    ],
)
def test_every_pre_apply_failure_defers_and_releases_claims(old_follower, monkeypatch, policy, step):
    from runner_support import git_locks

    follower, now = old_follower
    now[0] = time.time()
    state = follower.directory
    root = state / "checkout"
    metadata = root / ".git"
    metadata.mkdir(parents=True)
    process = state / ru.PROCESS_LOCK
    checkout = metadata / ru.LOCK_FILE
    unsafe = process if step.startswith("process_") else checkout
    target = state / "keep"
    target.write_text("keep this inode and content")
    if step == "drain_db":
        (state / "runner.sqlite").write_text("cannot read this database")
    if step.endswith("_open"):
        unsafe.mkdir()
    elif step.endswith("_size"):
        unsafe.write_bytes(b"x" * 4097)
    elif step.endswith("_symlink"):
        unsafe.symlink_to(target)
    before = unsafe.lstat() if unsafe.exists() else None
    applied = []
    original_git, original_read, original_write = ru.git, ru.read_status, ru.write_status
    original_flock, original_defer, original_in_flight = fcntl.flock, ru.defer_update, ru.in_flight
    cause = f"cannot finish {step}"

    def fail(*a, **kw):
        raise OSError(cause)

    for attempt in range(1, ru.MAX_DEFERRALS + 1):
        with monkeypatch.context() as patch:
            patch.setenv("TICO_UPDATE_RETRY", policy)
            patch.setenv("TICO_GIT_OWNED_ROOTS", "[]")
            patch.setattr(git_locks, "OWNED_ROOTS", ())
            patch.setattr(git_locks, "register_checkout", lambda root: None)
            patch.setattr(ru, "in_flight", lambda directory: False)
            patch.setattr(ru, "kind", lambda: "linux")
            patch.setattr(ru, "systemd_unit", lambda: "")
            patch.setattr(ru, "git", lambda *a, **kw: subprocess.CompletedProcess([], 0, ".git\n", ""))
            patch.setattr(ru, "update", lambda *a, **kw: applied.append(True))
            patch.setattr(ru, "defer_update", lambda *a, **kw: original_defer(*a, clock=lambda: now[0], **kw))
            if step == "config_json":
                patch.setenv("TICO_GIT_OWNED_ROOTS", "[")
            elif step == "config_shape":
                patch.setenv("TICO_GIT_OWNED_ROOTS", '{"bad": "paths"}')
            elif step == "registration":
                patch.setattr(git_locks, "register_checkout", fail)
            elif step == "drain_read":
                patch.setattr(ru, "in_flight", fail)
            elif step == "drain_db":
                patch.setattr(ru, "in_flight", original_in_flight)
            elif step == "drain_sleep":
                patch.setattr(ru, "in_flight", lambda directory: True)
                patch.setattr(ru.time, "sleep", fail)
            elif step in ("kind", "systemd_unit", "git_lookup"):
                patch.setattr(ru, "git" if step == "git_lookup" else step, fail)
            elif step == "git_gate":
                patch.setattr(ru, "git", original_git)
                patch.setattr(git_locks, "command_lock", fail)
            elif step in ("early_exit", "exit_zero", "interrupt"):

                def exit_early(*a, **kw):
                    if step == "exit_zero":
                        raise SystemExit(0)
                    raise SystemExit(cause) if step == "early_exit" else KeyboardInterrupt(cause)

                patch.setattr(ru, "in_flight", exit_early)
            if step.endswith("_flock") or step.startswith("status_"):

                def flock(handle, operation):
                    selected = process if step == "process_flock" else checkout
                    if (
                        operation != fcntl.LOCK_UN
                        and selected.exists()
                        and os.fstat(handle.fileno()).st_ino == selected.stat().st_ino
                    ):
                        if step.startswith("status_"):
                            raise BlockingIOError("checkout lock held")
                        fail()
                    return original_flock(handle, operation)

                patch.setattr(fcntl, "flock", flock)
            if step == "status_read":
                reads = []

                def read(directory, **kw):
                    reads.append(True)
                    if len(reads) == 1:
                        fail()
                    return original_read(directory, **kw)

                patch.setattr(ru, "read_status", read)
            elif step == "status_write":

                def write(directory, **fields):
                    if fields.get("state") == "updating":
                        fail()
                    return original_write(directory, **fields)

                patch.setattr(ru, "write_status", write)
            argv = ["--root", str(root), "--version", "v0.3.45", "--state-dir", str(state), "--pid", "42"]
            assert ru.main(argv + (["--unknown"] if step == "cli_exit" else [])) == 1
        status = original_read(state)
        assert status["state"] == (
            "blocked" if attempt == ru.MAX_DEFERRALS else "idle" if policy == "short" else "failed"
        )
        assert status["cause_count"] == attempt and status["deferrals"] == attempt
        assert status["deferral_cause"] in status["error"] and len(status["error"]) <= 300
        if step == "drain_db":
            assert "cannot check running work" in status["deferral_cause"]
        elif step in ("exit_zero", "cli_exit"):
            assert "the update stopped before it finished" in status["deferral_cause"]
        elif step not in ("config_json", "config_shape") and not step.endswith(("_open", "_size", "_symlink")):
            assert cause in status["deferral_cause"]
        if policy == "short":
            assert status["not_before"] > now[0]
        else:
            follower.pending_since = now[0] - 21 * 60
            follower.decide()
            assert follower.pending is None and not follower.running
            assert not follower.blocks_claims(True) and not follower.blocks_claims(False)
        if not step.startswith("process_") or step == "process_flock":
            with ru.updater_lock(state) as acquired:
                assert acquired
        assert applied == [] and target.read_text() == "keep this inode and content"
        if before:
            assert unsafe.lstat().st_ino == before.st_ino
        now[0] += 1


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, name, text="x", message=None):
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text)
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", message or name)
    return sh(repo, "git", "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """A public repo with releases 0.1.0 and 0.2.0 (0.2.0 changes the dependencies) plus a checkout on main at 0.1.0."""
    from runner_support import git_locks

    monkeypatch.setattr(git_locks, "OWNED_ROOTS", (tmp_path,))
    public = tmp_path / "public"
    public.mkdir()
    sh(public, "git", "init", "-q", "-b", "main")
    commit(public, "app.py", "one")
    commit(public, "backend/requirements.txt", "httpx==1\n")
    sh(public, "git", "tag", "v0.1.0")
    commit(public, "app.py", "two")
    commit(public, "backend/requirements.txt", "httpx==2\n")
    sh(public, "git", "tag", "v0.2.0")
    commit(public, "app.py", "unreleased main")
    checkout = tmp_path / "checkout"
    sh(tmp_path, "git", "clone", "-q", str(public), "checkout")
    sh(checkout, "git", "reset", "-q", "--hard", "v0.1.0")
    sh(checkout, "git", "tag", "-d", "v0.2.0")  # the checkout has not seen that release yet
    state = tmp_path / "state"
    state.mkdir()
    return public, checkout, state


class Calls:
    def __init__(self, healthy=(True,), install_error="", prepare_error=""):
        self.restarts, self.installs, self.checks, self.prepared = 0, [], [], []
        self.answers, self.install_error, self.prepare_error = list(healthy), install_error, prepare_error

    def prepare(self, root):
        self.prepared.append(sh(root, "git", "rev-parse", "HEAD"))
        return self.prepare_error

    def restart(self):
        self.restarts += 1

    def install(self, root, old, new):
        self.installs.append((old, new))
        return self.install_error if len(self.installs) == 1 else ""

    def healthy(self, commit, since):
        self.checks.append(commit)
        return self.answers.pop(0) if self.answers else True


def run_apply(checkout, state, version="0.2.0", calls=None, **kw):
    calls = calls or Calls()
    result = ru.apply(
        checkout,
        version,
        state,
        install=calls.install,
        prepare=calls.prepare,
        restart=calls.restart,
        healthy=calls.healthy,
        **kw,
    )
    return result, calls


def head(repo):
    return sh(repo, "git", "rev-parse", "HEAD")


def test_update_switches_to_the_servers_tag_not_main(repos):
    public, checkout, state = repos
    result, calls = run_apply(checkout, state)
    assert result["state"] == "healthy" and ru.checkout_release(checkout) == "0.2.0"
    assert (
        head(checkout) == sh(public, "git", "rev-parse", "v0.2.0") != head(public)
    )  # main moved on; the runner did not
    assert calls.restarts == 1 and calls.installs == [(sh(checkout, "git", "rev-parse", "v0.1.0"), head(checkout))]
    assert calls.checks == [head(checkout)] and calls.prepared == [
        head(checkout)
    ]  # the new tree's binary, before the restart
    assert ru.read_status(state)["state"] == "healthy" and ru.read_status(state)["target"] == "0.2.0"


@pytest.mark.slow
@pytest.mark.parametrize("branch,version", [("tico/b427e9ee-fix-runner", "0.2.0"), ("tico-session/task-123", "0.1.0")])
def test_bot_branch_recovery_keeps_both_refs_and_detaches_even_on_the_same_commit(repos, capsys, branch, version):
    from runner_support import release_branch

    _, checkout, state = repos
    sh(checkout, "git", "checkout", "-qb", branch)
    before = head(checkout)
    inspected = release_branch.inspect(checkout, lambda *a: ru.git(checkout, *a))
    assert inspected["recover"] and branch in inspected["error"] and "automatically" in inspected["error"]
    assert not sh(checkout, "git", "for-each-ref", "refs/tico/recovered/")
    result, calls = run_apply(checkout, state, version)
    assert result["state"] == "healthy" and calls.restarts == 1
    assert head(checkout) == sh(checkout, "git", "rev-parse", "v" + version)
    assert not sh(checkout, "git", "branch", "--show-current")
    assert sh(checkout, "git", "rev-parse", branch) == before
    saved = sh(checkout, "git", "for-each-ref", "--format=%(refname)", "refs/tico/recovered/")
    assert saved.endswith("-" + branch) and sh(checkout, "git", "rev-parse", saved) == before
    assert (
        result["note"]
        == ru.read_status(state)["note"]
        == f"moved the install off bot branch {branch} (kept at {saved})"
    )
    assert result["note"] in capsys.readouterr().out


@pytest.mark.slow
@pytest.mark.parametrize("case", ["person", "dirty", "staged", "merge", "rebase", "cherry-pick", "bisect", "untracked"])
def test_unsafe_branch_recovery_leaves_the_install_and_its_work_alone(repos, case):
    _, checkout, state = repos
    branch = "chris/my-work" if case == "person" else "tico/b427e9ee-fix-runner"
    sh(checkout, "git", "checkout", "-qb", branch)
    operations = {
        "merge": "MERGE_HEAD",
        "rebase": "rebase-merge",
        "cherry-pick": "CHERRY_PICK_HEAD",
        "bisect": "BISECT_START",
    }
    if case in ("dirty", "staged"):
        (checkout / "app.py").write_text("keep this work")
        if case == "staged":
            sh(checkout, "git", "add", "app.py")
    elif case in operations:
        marker = checkout / ".git" / operations[case]
        if case == "rebase":
            marker.mkdir()
        else:
            marker.write_text(head(checkout) + "\n")
    elif case == "untracked":
        # The release adds this file; a non-colliding untracked file does not block an update.
        public = repos[0]
        commit(public, "new.txt", "release content")
        sh(public, "git", "tag", "v0.3.0")
        (checkout / "new.txt").write_text("keep this untracked work")
    before = head(checkout)
    index = (checkout / ".git/index").read_bytes()
    result, calls = run_apply(checkout, state, "0.3.0" if case == "untracked" else "0.2.0")
    assert result["state"] == "blocked" and calls.restarts == 0
    assert branch in result["error"]
    assert head(checkout) == before and sh(checkout, "git", "branch", "--show-current") == branch
    assert sh(checkout, "git", "rev-parse", branch) == before
    assert not sh(checkout, "git", "for-each-ref", "refs/tico/recovered/")
    assert (checkout / ".git/index").read_bytes() == index
    if case == "untracked":
        assert (checkout / "new.txt").read_text() == "keep this untracked work"
        from runner_support import release_branch

        inspected = release_branch.inspect(checkout, lambda *a: ru.git(checkout, *a), "0.3.0")
        assert not inspected["recover"] and "local files are in the way" in inspected["error"]
    elif case in operations:
        assert case in result["error"]
    elif case in ("dirty", "staged"):
        assert "changed file" in result["error"] and (checkout / "app.py").read_text() == "keep this work"


@pytest.mark.slow
def test_recovered_branch_rolls_back_without_losing_the_saved_commit(repos):
    _, checkout, state = repos
    branch = "tico/b427e9ee-fix-runner"
    sh(checkout, "git", "checkout", "-qb", branch)
    before = commit(checkout, "unpublished.txt", "keep my commit")
    result, calls = run_apply(checkout, state, calls=Calls(healthy=(False, True)))
    assert result["state"] == "rolled_back" and calls.restarts == 2 and head(checkout) == before
    assert sh(checkout, "git", "branch", "--show-current") == branch
    assert sh(checkout, "git", "rev-parse", branch) == before
    assert before in sh(checkout, "git", "for-each-ref", "--format=%(objectname)", "refs/tico/recovered/")
    assert "kept at refs/tico/recovered/" in ru.read_status(state)["note"]


@pytest.mark.slow
def test_recovery_waits_for_writers_and_holds_the_update_lock(repos, monkeypatch):
    _, checkout, state = repos
    sh(checkout, "git", "checkout", "-qb", "tico/b427e9ee-fix-runner")
    result, _ = run_apply(checkout, state, busy=lambda: True)
    assert result["state"] in ("idle", "failed") and "work running" in result["error"]
    assert not sh(checkout, "git", "for-each-ref", "refs/tico/recovered/")
    original = ru.git
    checked = []

    def git(root, *args, **kw):
        if args[0] in ("update-ref", "checkout"):
            with ru.lock_path(root).open("a") as handle:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            checked.append(args[0])
        return original(root, *args, **kw)

    monkeypatch.setattr(ru, "git", git)
    (checkout / "safe-untracked.txt").write_text("keep this too")
    result, _ = run_apply(checkout, state, busy=lambda: False)
    assert result["state"] == "healthy" and checked == ["update-ref", "checkout"]
    assert (checkout / "safe-untracked.txt").read_text() == "keep this too"


@pytest.mark.slow
@pytest.mark.parametrize("change", ["branch", "dirty", "operation"])
def test_recovery_rechecks_the_checkout_after_fetching(repos, change):
    _, checkout, state = repos
    sh(checkout, "git", "checkout", "-qb", "tico/b427e9ee-fix-runner")

    def busy():
        if change == "branch":
            sh(checkout, "git", "checkout", "-q", "main")
        elif change == "dirty":
            (checkout / "app.py").write_text("keep this late edit")
        else:
            (checkout / ".git/MERGE_HEAD").write_text(head(checkout) + "\n")
        return False

    result, calls = run_apply(checkout, state, busy=busy)
    assert result["state"] == "blocked" and result["error"] and calls.restarts == 0
    assert not sh(checkout, "git", "for-each-ref", "refs/tico/recovered/")


def test_a_checkout_with_changes_is_refused_and_left_alone(repos):
    public, checkout, state = repos
    (checkout / "app.py").write_text("my local edit")
    before = head(checkout)
    result, calls = run_apply(checkout, state)
    assert (
        result["state"] == "blocked"
        and "1 changed file" in result["error"]
        and result["error"].endswith("updates itself: app.py")
    )
    assert (checkout / "app.py").read_text() == "my local edit" and head(checkout) == before
    assert calls.restarts == 0 and ru.read_status(state)["state"] == "blocked"
    assert sh(checkout, "git", "branch", "--show-current") == "main"


def instruction_links(checkout):
    commit(checkout, "CLAUDE.md", "Repository rules\n")
    commit(checkout, "templates/employee-repo/AGENT.md", "Bot rules\n")
    for name, target in [
        ("AGENTS.md", "CLAUDE.md"),
        ("templates/employee-repo/AGENTS.md", "AGENT.md"),
        ("templates/employee-repo/CLAUDE.md", "AGENT.md"),
    ]:
        (checkout / name).symlink_to(target)
    commit(checkout, "notes.txt", "keep the local branch\n")


@pytest.mark.slow
def test_flattened_instruction_links_do_not_block_a_release_update(repos):
    _, checkout, state = repos
    instruction_links(checkout)
    # A repair interrupted by shutdown leaves only ignored Git metadata, never another dirty file.
    (checkout / ".git/tico-instruction-link-0123456789abcdef0123456789abcdef").symlink_to("CLAUDE.md")
    before = head(checkout)
    for name, text in [
        ("AGENTS.md", "Repository rules\n"),
        ("templates/employee-repo/AGENTS.md", "Bot rules\n"),
        ("templates/employee-repo/CLAUDE.md", "Bot rules\n"),
    ]:
        (checkout / name).unlink()
        (checkout / name).write_text(text)
    ru.repair_instruction_links(checkout, lambda *args: ru.git(checkout, *args))
    assert sh(checkout, "git", "status", "--porcelain") == ""
    assert (checkout / "AGENTS.md").is_symlink()
    result, calls = run_apply(checkout, state)
    assert result["state"] == "healthy" and calls.restarts == 1
    assert sh(checkout, "git", "rev-parse", "main") == before  # unpublished work retains its branch


@pytest.mark.slow
def test_instruction_edits_and_deliberately_staged_copies_stay_and_are_named(repos):
    _, checkout, state = repos
    instruction_links(checkout)
    edited = checkout / "AGENTS.md"
    edited.unlink()
    edited.write_text("A bot's new rules\n")
    staged = checkout / "templates/employee-repo/CLAUDE.md"
    staged.unlink()
    staged.write_text("Bot rules\n")
    sh(checkout, "git", "add", "templates/employee-repo/CLAUDE.md")
    index = (checkout / ".git/index").read_bytes()
    before = head(checkout)
    result, calls = run_apply(checkout, state)
    assert result["state"] == "blocked" and calls.restarts == 0
    assert "AGENTS.md" in result["error"] and "templates/employee-repo/CLAUDE.md" in result["error"]
    assert edited.read_text() == "A bot's new rules\n" and not edited.is_symlink()
    assert staged.read_text() == "Bot rules\n" and not staged.is_symlink()
    assert (checkout / ".git/index").read_bytes() == index and head(checkout) == before


@pytest.mark.slow
def test_instruction_link_repair_preserves_edits_to_its_target_and_external_targets(repos):
    _, checkout, _ = repos
    instruction_links(checkout)
    external = checkout.parent / "private.md"
    external.write_text("Outside the checkout\n")
    link = checkout / "nested/AGENTS.md"
    link.parent.mkdir()
    link.symlink_to(external)
    commit(checkout, "notes.txt", "another commit\n")
    edited = checkout / "AGENTS.md"
    edited.unlink()
    edited.write_text("Repository rules\n")
    link.unlink()
    link.write_text(external.read_text())
    # Commit only the fixture links; keep the real target edit uncommitted.
    (checkout / "CLAUDE.md").write_text("Bot target edit\n")
    ru.repair_instruction_links(checkout, lambda *args: ru.git(checkout, *args))
    assert not edited.is_symlink() and edited.read_text() == "Repository rules\n"
    assert not link.is_symlink() and link.read_text() == external.read_text()
    assert (checkout / "CLAUDE.md").read_text() == "Bot target edit\n"


@pytest.mark.slow
def test_updater_reports_old_index_config_and_ref_locks_without_retry_or_removal(repos):
    from runner_support import git_locks

    _, checkout, _ = repos
    for name, command in [
        ("index.lock", ("add", "app.py")),
        ("config.lock", ("config", "--local", "repair.test", "yes")),
        ("refs/heads/blocked.lock", ("update-ref", "refs/heads/blocked", "HEAD")),
    ]:
        lock = checkout / ".git" / name
        lock.write_text("still owned")
        assert ru.git(checkout, *command).returncode != 0 and lock.exists()
        old = time.time() - git_locks.OLD_S * 2
        os.utime(lock, (old, old))
        before = lock.stat()
        with lock.open():
            result = ru.git(checkout, *command)
            assert result.returncode != 0 and lock.exists()
        result = ru.git(checkout, *command)
        assert result.returncode != 0
        assert str(checkout) in result.stderr and str(lock) in result.stderr
        assert "minutes old" in result.stderr and "Check no Git is running, then run: rm --" in result.stderr
        assert lock.stat().st_ino == before.st_ino and lock.read_text() == "still owned"
        lock.unlink()
    assert not (checkout / ".git/tico-lock-backups").exists()


@pytest.mark.slow
def test_old_locks_reach_the_update_status_with_the_manual_command(repos):
    from runner_support import git_locks

    _, checkout, state = repos
    head = sh(checkout, "git", "rev-parse", "HEAD")
    for name in ("refs/tags/v0.2.0.lock", "index.lock"):
        lock = checkout / ".git" / name
        lock.write_text("still owned")
        old = time.time() - git_locks.OLD_S * 2
        os.utime(lock, (old, old))
        result, calls = run_apply(checkout, state)
        assert result["state"] == "failed" and calls.restarts == 0
        error = ru.read_status(state)["error"]
        assert "minutes old" in error and "run: rm --" in error and str(lock) in error
        assert lock.read_text() == "still owned"
        assert sh(checkout, "git", "rev-parse", "HEAD") == head
        lock.unlink()


def test_a_changed_file_named_like_a_disk_error_is_named_not_read_as_one(repos):
    public, checkout, state = repos
    commit(checkout, "tests/test_enospc.py", "one")
    (checkout / "tests/test_enospc.py").write_text("two")
    result, _ = run_apply(checkout, state)
    assert result["state"] == "blocked" and result["error"].endswith("updates itself: tests/test_enospc.py")


@pytest.fixture
def link_repos(repos):
    public, checkout, state = repos
    commit(public, "CLAUDE.md", "# Rules\n\nKeep work.\n")
    (public / "AGENTS.md").symlink_to("CLAUDE.md")
    commit(public, "templates/employee-repo/AGENT.md", "# Bot rules\n")
    for name in ("AGENTS.md", "CLAUDE.md"):
        (public / "templates/employee-repo" / name).symlink_to("AGENT.md")
    commit(public, "note.txt", "before")
    sh(public, "git", "tag", "v0.3.0")
    sh(checkout, "git", "fetch", "-q", "origin", "tag", "v0.3.0")
    sh(checkout, "git", "reset", "-q", "--hard", "v0.3.0")
    commit(public, "app.py", "new release")
    sh(public, "git", "tag", "v0.4.0")
    return public, checkout, state


def flatten(checkout, name="AGENTS.md"):
    path = checkout / name
    content = path.read_bytes()
    path.unlink()
    path.write_bytes(content)


@pytest.mark.slow
@pytest.mark.parametrize("symlinks", ["true", "false"])
def test_only_exact_copies_of_link_targets_are_restored_under_the_update_lock(
    link_repos, symlinks, capsys, monkeypatch
):
    _, checkout, state = link_repos
    sh(checkout, "git", "config", "core.symlinks", symlinks)
    names = ["AGENTS.md", "templates/employee-repo/AGENTS.md", "templates/employee-repo/CLAUDE.md"]
    for name in names:
        flatten(checkout, name)
    before = {name: (checkout / name).read_bytes() for name in names}
    assert checkout_links.inspect(checkout)["only_flattened"]

    symlink = checkout_links.os.symlink
    swapped = []

    def locked_symlink(source, destination):
        with ru.lock_path(checkout).open("a") as handle:
            with pytest.raises(BlockingIOError):
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        swapped.append(destination.relative_to(checkout).as_posix())
        return symlink(source, destination)

    monkeypatch.setattr(checkout_links.os, "symlink", locked_symlink)
    result, _ = run_apply(checkout, state, "0.4.0")
    assert result["state"] == "healthy" and ru.checkout_release(checkout) == "0.4.0"
    assert result["note"] == ru.read_status(state)["note"]
    prefix = "restored 3 flattened links; originals kept in "
    assert result["note"].startswith(prefix)
    backup = Path(result["note"].removeprefix(prefix))
    assert backup.parent == checkout / ".git/tico-flattened-links"
    assert all((backup / name).read_bytes() == before[name] for name in names)
    assert result["note"] in capsys.readouterr().out
    assert all((checkout / name).is_symlink() and (checkout / name).read_bytes() == before[name] for name in names)
    assert not sh(checkout, "git", "status", "--porcelain", "--untracked-files=no")
    assert sh(checkout, "git", "config", "core.symlinks") == symlinks
    assert swapped == names
    assert not list(checkout.rglob(".*.tico-link-*"))


@pytest.mark.slow
@pytest.mark.parametrize("policy,paused", [("short", "idle"), ("legacy", "failed")])
def test_link_repair_keeps_the_existing_busy_work_deferrals(link_repos, monkeypatch, policy, paused):
    _, checkout, state = link_repos
    monkeypatch.setenv("TICO_UPDATE_RETRY", policy)
    before = head(checkout)
    now = 1_800_000_000.0
    for attempt in range(1, 4):
        flatten(checkout)
        result, calls = run_apply(checkout, state, "0.4.0", busy=lambda: True, clock=lambda: now)
        status = ru.read_status(state)
        assert result["state"] == status["state"] == paused
        assert status["deferrals"] == attempt and status["cause_count"] == 0
        assert "this computer still has work running" in result["error"]
        if policy == "short":
            assert status["not_before"] == now + ru.DEFERRAL_DELAYS_S[attempt - 1]
        else:
            assert "not_before" not in status
        assert head(checkout) == before and (checkout / "AGENTS.md").is_symlink()
        assert not sh(checkout, "git", "status", "--porcelain", "--untracked-files=no")
        assert calls.restarts == 0 and calls.installs == calls.prepared == []
        now += 6 * 3600
    result, _ = run_apply(checkout, state, "0.4.0")
    assert result["state"] == "healthy" and "deferrals" not in ru.read_status(state)


@pytest.mark.slow
@pytest.mark.parametrize(
    "point,action",
    [
        ("inspected", "edit"),
        ("inspected", "stage"),
        ("inspected", "edit and stage"),
        ("renamed", "edit"),
        ("renamed", "stage"),
        ("renamed", "replace"),
        ("linked", "edit"),
        ("linked", "stage"),
        ("linked", "replace"),
        ("index", "edit"),
        ("index", "stage"),
    ],
)
def test_link_repair_keeps_edits_and_staging_at_each_swap_step(link_repos, monkeypatch, point, action):
    _, checkout, state = link_repos
    names = ["AGENTS.md", "templates/employee-repo/CLAUDE.md"]
    for name in names:
        flatten(checkout, name)
    path = checkout / names[-1]
    original = path.read_bytes()
    inode = path.stat().st_ino
    before = head(checkout)
    edited, staged = b"new local rules\n", b"staged local rules\n"
    oid = (
        subprocess.run(
            ["git", "-C", str(checkout), "hash-object", "-w", "--stdin"], input=staged, capture_output=True, check=True
        )
        .stdout.decode()
        .strip()
    )
    fired = False
    index = b""
    inspect, set_aside, symlink = checkout_links._inspect, checkout_links._set_aside, checkout_links.os.symlink

    def cached():
        return subprocess.run(
            ["git", "-C", str(checkout), "diff", "--cached", "--binary"], capture_output=True, check=True
        ).stdout

    with path.open("r+b", buffering=0) as editor:

        def inject(step):
            nonlocal fired, index
            if step != point or fired:
                return
            fired = True
            if "edit" in action:
                editor.seek(0)
                editor.write(edited)
                editor.truncate()
            if action == "replace":
                path.unlink(missing_ok=True)
                path.write_bytes(edited)
            if action == "stage":
                # Stage the regular file even while its worktree name is absent or now a symlink.
                sh(checkout, "git", "update-index", "--cacheinfo", f"100644,{oid},{names[-1]}")
            elif action == "edit and stage":
                sh(checkout, "git", "add", names[-1])
            index = cached()

        def inspected(*args):
            found = inspect(*args)
            inject("inspected")
            return found

        def renamed(destination, saved):
            backup = set_aside(destination, saved)
            if destination == path:
                inject("renamed")
            return backup

        def linked(source, destination):
            symlink(source, destination)
            if destination == path:
                inject("linked")

        def run(args, **kw):
            if "--cached" in args and path.is_symlink():
                inject("index")
            return subprocess.run(args, **kw)

        monkeypatch.setattr(checkout_links, "_inspect", inspected)
        monkeypatch.setattr(checkout_links, "_set_aside", renamed)
        monkeypatch.setattr(checkout_links.os, "symlink", linked)
        result, calls = run_apply(checkout, state, "0.4.0", run=run)

    assert fired and result["state"] == "blocked" and calls.restarts == 0
    assert "note" not in result and head(checkout) == before and cached() == index
    assert not path.is_symlink() and path.stat().st_ino == inode
    assert path.read_bytes() == (edited if "edit" in action else original)
    assert not (checkout / names[0]).is_symlink()
    assert (checkout / names[0]).read_bytes() == (checkout / "CLAUDE.md").read_bytes()
    (folder,) = (checkout / ".git/tico-flattened-links").iterdir()
    backup = folder / names[-1]
    assert backup.stat().st_ino == inode and backup.read_bytes() == path.read_bytes()
    replacements = list(folder.rglob(".tico-link-replacement-*"))
    if action == "replace":
        assert len(replacements) == 1 and replacements[0].read_bytes() == edited
    else:
        assert replacements == []


@pytest.mark.slow
def test_link_repair_keeps_writes_after_an_original_passes_validation(link_repos, monkeypatch):
    _, checkout, state = link_repos
    first, second = "AGENTS.md", "templates/employee-repo/CLAUDE.md"
    for name in (first, second):
        flatten(checkout, name)
    path = checkout / first
    inode = path.stat().st_ino
    read_bytes = Path.read_bytes
    backups = checkout / ".git/tico-flattened-links"
    edited = b"edited after validation\n"
    fired = False

    with path.open("r+b", buffering=0) as editor:

        def read(saved):
            nonlocal fired
            content = read_bytes(saved)
            if backups in saved.parents and saved.as_posix().endswith(second) and not fired:
                # The first backup already passed its check. Its inode is still open in an editor.
                fired = True
                editor.seek(0)
                editor.write(edited)
                editor.truncate()
            return content

        monkeypatch.setattr(Path, "read_bytes", read)
        result, _ = run_apply(checkout, state, "0.4.0")
        assert fired and result["state"] == "healthy"
        folder = Path(result["note"].split("originals kept in ", 1)[1])
        saved = folder / first
        assert saved.stat().st_ino == inode and saved.read_bytes() == edited
        assert path.is_symlink() and not sh(checkout, "git", "status", "--porcelain")
        editor.seek(0, 2)
        editor.write(b"and after the repair\n")
        assert saved.read_bytes() == edited + b"and after the repair\n"


@pytest.mark.slow
def test_link_backups_use_the_worktrees_git_directory_and_keep_the_newest_five(link_repos):
    _, checkout, _ = link_repos
    worktree = checkout.parent / "linked worktree"
    sh(checkout, "git", "worktree", "add", "-q", "-b", "linked", str(worktree))
    git_dir = Path(sh(worktree, "git", "rev-parse", "--absolute-git-dir"))
    assert (worktree / ".git").is_file()
    folders = []
    for _ in range(6):
        flatten(worktree)
        with ru.lock_path(worktree).open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            found = checkout_links.restore(worktree)
        assert found["restored"] == ["AGENTS.md"]
        folder = Path(found["backup"])
        assert folder.parent == git_dir / "tico-flattened-links"
        folders.append(folder)
    assert set(folders[-5:]) == set((git_dir / "tico-flattened-links").iterdir())
    assert not folders[0].exists()
    assert all((folder / "AGENTS.md").read_bytes() == (worktree / "CLAUDE.md").read_bytes() for folder in folders[-5:])
    assert not sh(worktree, "git", "status", "--porcelain")


@pytest.mark.slow
@pytest.mark.parametrize("change", ["link text", "target", "staged link", "staged other", "partial", "other edit"])
def test_any_other_change_blocks_every_link_repair(link_repos, change):
    _, checkout, state = link_repos
    flatten(checkout)
    if change == "link text":
        (checkout / "AGENTS.md").write_text("my own rules\n")
    elif change == "target":
        (checkout / "CLAUDE.md").write_text("changed target\n")
    elif change == "staged link":
        sh(checkout, "git", "add", "AGENTS.md")
    elif change == "staged other":
        (checkout / "note.txt").write_text("staged")
        sh(checkout, "git", "add", "note.txt")
    elif change == "partial":
        flatten(checkout, "templates/employee-repo/AGENTS.md")
        (checkout / "templates/employee-repo/AGENTS.md").write_text("different")
    else:
        (checkout / "note.txt").write_text("unfinished")
    before = head(checkout)
    index = sh(checkout, "git", "diff", "--cached", "--raw")
    contents = (checkout / "AGENTS.md").read_bytes()
    result, calls = run_apply(checkout, state, "0.4.0")
    assert result["state"] == "blocked" and "commit or stash" in result["error"]
    assert calls.restarts == 0 and head(checkout) == before
    assert not (checkout / "AGENTS.md").is_symlink() and (checkout / "AGENTS.md").read_bytes() == contents
    assert sh(checkout, "git", "diff", "--cached", "--raw") == index
    if change in ("staged other", "partial", "other edit"):
        assert "AGENTS.md (flattened link)" in result["error"]


@pytest.mark.slow
def test_a_target_hidden_from_git_status_still_blocks_repair(link_repos):
    _, checkout, state = link_repos
    flatten(checkout)
    sh(checkout, "git", "update-index", "--assume-unchanged", "CLAUDE.md")
    (checkout / "CLAUDE.md").write_text("changed target")
    result, _ = run_apply(checkout, state, "0.4.0")
    assert result["state"] == "blocked" and not (checkout / "AGENTS.md").is_symlink()


@pytest.mark.slow
def test_a_tag_that_moved_upstream_is_not_trusted(repos):
    public, checkout, state = repos
    run_apply(checkout, state)  # the checkout now has v0.2.0
    sh(checkout, "git", "checkout", "-q", "main")
    sh(public, "git", "tag", "-f", "v0.2.0", "main")  # someone re-points the release
    result, _ = run_apply(checkout, state)
    assert result["state"] == "failed" and "differs" in result["error"]


def test_rollback_when_the_new_release_does_not_come_back(repos):
    public, checkout, state = repos
    before = head(checkout)
    result, calls = run_apply(checkout, state, calls=Calls(healthy=(False, True)))
    assert result["state"] == "rolled_back" and "did not report in" in result["error"]
    assert head(checkout) == before and sh(checkout, "git", "branch", "--show-current") == "main"
    assert calls.restarts == 2 and len(calls.installs) == 2  # forward, then the old dependencies back
    assert ru.read_status(state)["state"] == "rolled_back"


def test_a_release_whose_rust_runner_cannot_be_had_goes_back_before_any_restart(repos):
    public, checkout, state = repos
    before = head(checkout)
    result, calls = run_apply(
        checkout, state, calls=Calls(prepare_error="could not download the Rust runner for 0.2.0")
    )
    assert result["state"] == "rolled_back" and result["error"].startswith("could not download the Rust runner")
    assert head(checkout) == before and calls.restarts == 1 and calls.checks == [before]  # the restart is the way back


def runner_release(public, checkout):
    """Release 0.3.0 changes the runner's sources, so its binary is a new build."""
    commit(public, "runner-rs/src/main.rs", "fn main() {}")
    sh(public, "git", "tag", "v0.3.0")


def test_a_release_whose_new_runner_binary_is_not_published_yet_waits_without_moving(repos):
    public, checkout, state = repos
    runner_release(public, checkout)
    before, asked = head(checkout), []
    result, calls = run_apply(
        checkout, state, version="0.3.0", which=lambda name: None, listed=lambda version: asked.append(version) or False
    )
    assert result["state"] == "failed" and "does not have this computer's runner binary yet" in result["error"]
    assert asked == ["0.3.0"] and head(checkout) == before and calls.restarts == 0 and calls.prepared == []
    assert ru.read_status(state)["state"] == "failed"
    # Once release.yml adds the binary, the next try moves.
    result, calls = run_apply(checkout, state, version="0.3.0", which=lambda name: None, listed=lambda version: True)
    assert result["state"] == "healthy" and ru.checkout_release(checkout) == "0.3.0"


@pytest.mark.parametrize("policy,paused", [("short", "idle"), ("legacy", "failed")])
def test_a_runner_binary_that_never_comes_is_blocked_after_bounded_retries(repos, monkeypatch, policy, paused):
    monkeypatch.setenv("TICO_UPDATE_RETRY", policy)
    public, checkout, state = repos
    runner_release(public, checkout)
    now = [1000.0]
    wait = dict(version="0.3.0", which=lambda name: None, listed=lambda version: False, clock=lambda: now[0])
    for attempt in range(1, ru.MAX_DEFERRALS + 1):
        result, calls = run_apply(checkout, state, **wait)
        assert result["state"] == ("blocked" if attempt == ru.MAX_DEFERRALS else paused)
        assert ru.read_status(state)["deferrals"] == attempt
        assert calls.restarts == 0
        now[0] += ru.DEFERRAL_DELAYS_S[attempt - 1]
    assert "runner binary" in result["error"] and "3 tries" in result["error"]


def test_only_a_changed_runner_without_cargo_asks_whether_its_binary_is_published(repos):
    public, checkout, state = repos
    asked = []
    listed = lambda version: asked.append(version) or False
    result, _ = run_apply(checkout, state, which=lambda name: None, listed=listed)  # 0.2.0: same runner sources
    assert result["state"] == "healthy" and asked == []
    runner_release(public, checkout)
    result, _ = run_apply(checkout, state, version="0.3.0", which=lambda name: "/usr/bin/cargo", listed=listed)
    assert result["state"] == "healthy" and asked == []  # cargo builds one


def test_a_release_whose_checksums_cannot_be_read_updates_as_before(repos):
    public, checkout, state = repos
    runner_release(public, checkout)
    result, _ = run_apply(checkout, state, version="0.3.0", which=lambda name: None, listed=lambda version: None)
    assert result["state"] == "healthy"


def test_the_prepare_step_is_a_new_process_of_the_new_tree(tmp_path, monkeypatch):
    seen = []

    def run(cmd, **kw):
        seen.append((cmd, kw["cwd"]))
        return subprocess.CompletedProcess(
            cmd, 1, "", "Tico runner: no Rust runner for this checkout: cargo is not on PATH\n"
        )

    assert ru.prepare_binary(tmp_path, run=run) == "" and not seen  # a tree from before the shim gets its own
    monkeypatch.setattr(ru.sys, "executable", "/opt/env-python/bin/python3")
    # Going back to a release from before runner_support/: its engine is still runner/engine.py.
    (tmp_path / "runner").mkdir()
    (tmp_path / "runner" / "engine.py").write_text("")
    assert "cargo is not on PATH" in ru.prepare_binary(tmp_path, run=run)
    assert seen.pop() == (["/opt/env-python/bin/python3", "-m", "runner.engine", "prepare"], str(tmp_path))
    (tmp_path / "runner_support").mkdir()
    (tmp_path / "runner_support" / "engine.py").write_text("")
    assert "cargo is not on PATH" in ru.prepare_binary(tmp_path, run=run)
    assert seen == [(["/opt/env-python/bin/python3", "-m", "runner_support.engine", "prepare"], str(tmp_path))]


# -- runners that share one checkout -------------------------------------------------------------


def test_a_checkout_already_on_the_release_restarts_a_runner_that_started_on_other_code(repos):
    public, checkout, state = repos
    started = head(checkout)
    run_apply(checkout, state)
    on_release = head(checkout)
    restarted = []
    result, calls = run_apply(checkout, state, started=started, helpers=lambda: restarted.append("all"))
    assert result["state"] == "healthy" and ru.read_status(state)["state"] == "healthy"
    assert calls.restarts == 1 and calls.checks == [on_release] and restarted == ["all"]
    assert calls.installs == [(started, on_release)]  # this runner's interpreter may not have the new dependencies
    assert head(checkout) == on_release
    result, calls = run_apply(checkout, state, calls=Calls(healthy=(False,)), started=started)
    assert result["state"] == "failed" and "did not report in" in result["error"] and calls.restarts == 1
    assert head(checkout) == on_release  # no going back from a restart


def test_a_failed_install_on_a_checkout_already_moved_leaves_the_runner_running(repos):
    public, checkout, state = repos
    started = head(checkout)
    run_apply(checkout, state)
    result, calls = run_apply(
        checkout, state, calls=Calls(install_error="pip install failed: no network"), started=started
    )
    assert result["state"] == "failed" and result["error"] == "pip install failed: no network"
    assert calls.restarts == 0 and ru.read_status(state)["state"] == "failed"


def test_the_install_step_uses_the_update_process_interpreter(repos, monkeypatch):
    public, checkout, state = repos
    started = head(checkout)
    run_apply(checkout, state)
    pips = []

    def run(cmd, **kw):
        if cmd[0] == "git":
            return subprocess.run(cmd, **kw)
        pips.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ru.sys, "executable", "/opt/env-python/bin/python3")
    assert ru.pip_install(checkout, started, head(checkout), run=run) == ""
    assert pips[0][:4] == ["/opt/env-python/bin/python3", "-m", "pip", "install"]
    assert pips[0][-1] == str(checkout / "backend" / "requirements.txt")


def test_a_runner_already_on_the_release_is_not_restarted(repos):
    public, checkout, state = repos
    run_apply(checkout, state)
    for started in (head(checkout), ""):  # "": a runner from before --started
        result, calls = run_apply(checkout, state, started=started)
        assert result["state"] == "healthy" and calls.restarts == 0 and calls.checks == []


def hold(checkout):
    handle = open(ru.lock_path(checkout), "a")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


@pytest.mark.slow
def test_alternating_deferrals_release_old_runners_without_changing_the_checkout(repos, old_follower, monkeypatch):
    monkeypatch.delenv("TICO_UPDATE_RETRY", raising=False)
    public, checkout, state = repos
    runner_release(public, checkout)
    before, index = head(checkout), (checkout / ".git/index").read_bytes()
    follower, now = old_follower
    follower.directory, follower.release, follower.desired = state, "0.1.0", "0.3.0"
    for attempt, cause in enumerate(("lookup", "checkout_lock", "active_work", "binary"), 1):
        with monkeypatch.context() as patch:
            held = None
            if cause == "lookup":
                patch.setattr(ru, "lock_path", lambda *a: None)
            elif cause == "checkout_lock":
                held = hold(checkout)
                patch.setattr(ru.time, "time", lambda: now[0])
                patch.setattr(ru.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
            try:
                result, calls = run_apply(
                    checkout,
                    state,
                    version="0.3.0",
                    clock=lambda: now[0],
                    lock_wait=1,
                    busy=lambda: cause == "active_work",
                    which=lambda name: None,
                    listed=lambda version: False,
                )
            finally:
                if held:
                    held.close()
        status = ru.read_status(state)
        assert status["state"] == result["state"] == "failed"
        assert status["deferrals"] == attempt
        assert status["error"] == result["error"] and status["error"]
        follower.decide()
        assert not follower.blocks_claims(True) and not follower.blocks_claims(False)
        assert follower.pending_since is None
        assert head(checkout) == before and (checkout / ".git/index").read_bytes() == index
        assert calls.installs == calls.prepared == calls.checks == [] and calls.restarts == 0
        now[0] += 6 * 3600

    result, _ = run_apply(checkout, state, version="0.3.0", listed=lambda version: True)
    assert result["state"] == "healthy" and "deferrals" not in ru.read_status(state)


@pytest.mark.slow
@pytest.mark.parametrize("policy,paused", [("short", "idle"), ("legacy", "failed")])
def test_a_git_gate_timeout_defers_even_when_the_gate_clears_before_checkout(repos, monkeypatch, policy, paused):
    monkeypatch.setenv("TICO_UPDATE_RETRY", policy)
    _, checkout, state = repos
    before = head(checkout)
    index = (checkout / ".git/index").read_bytes()
    commands, waited = [], []
    git = ru.git

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.run(command, **kwargs)

    # A keeps the checkout lock through install and health, but releases the Git gate after its fetch.
    with hold(checkout), (checkout / ".git/tico-lock-recovery").open("w") as gate:
        fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def release_after_lookup(root, *args, **kwargs):
            started = time.monotonic()
            done = git(root, *args, **kwargs)
            if args == ("rev-parse", "--git-common-dir"):
                waited.append(time.monotonic() - started)
                assert done.returncode != 0
                fcntl.flock(gate, fcntl.LOCK_UN)
            return done

        monkeypatch.setattr(ru, "git", release_after_lookup)
        result, calls = run_apply(checkout, state, run=run, lock_wait=0)
        assert result["state"] == paused and len(waited) == 1 and waited[0] >= 10
        assert ru.read_status(state)["state"] == paused
        assert commands == []  # B never fetched or checked out without A's lock
        assert calls.installs == [] and calls.prepared == [] and calls.checks == [] and calls.restarts == 0
        assert head(checkout) == before and (checkout / ".git/index").read_bytes() == index

    monkeypatch.setattr(ru, "git", git)
    result, calls = run_apply(checkout, state)
    assert result["state"] == "healthy" and calls.restarts == 1  # the deferred update can retry normally


@pytest.mark.parametrize("policy,paused", [("short", "idle"), ("legacy", "failed")])
def test_an_unresolved_update_lock_exits_with_retryable_status(tmp_path, monkeypatch, policy, paused):
    monkeypatch.setenv("TICO_UPDATE_RETRY", policy)

    def git(root, *args, **kwargs):
        assert args == ("rev-parse", "--git-common-dir")
        return subprocess.CompletedProcess([], 1, "", "cannot read Git metadata")

    monkeypatch.setattr(ru, "git", git)
    monkeypatch.setattr(ru, "in_flight", lambda directory: False)
    ru.write_status(tmp_path, state="updating", target="0.2.0", error="", at=1)
    assert ru.main(["--root", str(tmp_path), "--version", "v0.2.0", "--state-dir", str(tmp_path), "--pid", "42"]) == 1
    status = ru.read_status(tmp_path)
    assert status["state"] == paused and status["target"] == "0.2.0" and status["at"] > 1
    assert "cannot read Git metadata" in status["error"] and status["cause_count"] == 1


def test_an_update_of_a_shared_checkout_waits_for_another_under_way(repos):
    public, checkout, state = repos
    before = head(checkout)
    held = hold(checkout)  # another environment's update holds the checkout
    result, calls = run_apply(checkout, state, lock_wait=0)
    assert result["state"] == "failed" and ru.read_status(state)["state"] == "failed"
    assert head(checkout) == before and calls.restarts == 0 and calls.installs == []

    def release():
        time.sleep(1.5)
        held.close()

    threading.Thread(target=release).start()
    result, calls = run_apply(checkout, state, lock_wait=30)
    assert result["state"] == "healthy" and ru.checkout_release(checkout) == "0.2.0" and calls.restarts == 1


# -- helper jobs follow the release --------------------------------------------------------------


@pytest.mark.slow
def test_a_healthy_update_restarts_the_helpers_and_a_rollback_does_not(repos):
    public, checkout, state = repos
    restarted = []
    result, _ = run_apply(checkout, state, helpers=lambda: restarted.append("all"))
    assert result["state"] == "healthy" and restarted == ["all"]
    sh(checkout, "git", "reset", "-q", "--hard", "v0.1.0")
    restarted.clear()
    result, _ = run_apply(checkout, state, calls=Calls(healthy=(False, True)), helpers=lambda: restarted.append("all"))
    assert result["state"] == "rolled_back" and restarted == []


class Launchctl:
    def __init__(self, fail_kickstart=()):
        self.calls, self.fail_kickstart = [], set(fail_kickstart)

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        bad = cmd[1] == "kickstart" and cmd[-1].rsplit("/", 1)[-1] in self.fail_kickstart
        return subprocess.CompletedProcess(cmd, 1 if bad else 0, "", "")


def install_plists(agents, *labels):
    agents.mkdir(parents=True, exist_ok=True)
    for label in labels:
        (agents / f"{label}.plist").write_text("<plist/>")


def test_only_installed_helpers_are_restarted(tmp_path):
    agents = tmp_path / "agents"
    install_plists(agents, "team.tico.tico-bot", "team.tico.tico-connectors", "team.tico.tico-importers")
    launchctl, said = Launchctl(), []
    done = ru.restart_helpers({}, launchctl, agents, uid=501, say=said.append)
    assert done == ["connectors", "importers"]  # close-calls has no plist; the bot job is not touched here
    assert launchctl.calls == [
        ["launchctl", "kickstart", "-k", "gui/501/team.tico.tico-connectors"],
        ["launchctl", "kickstart", "-k", "gui/501/team.tico.tico-importers"],
    ]
    assert len(said) == 2 and "team.tico.tico-connectors" in said[0]


def test_an_installed_helper_that_is_not_loaded_is_bootstrapped(tmp_path):
    agents = tmp_path / "agents"
    install_plists(agents, "team.tico.tico-connectors")
    launchctl = Launchctl(fail_kickstart={"team.tico.tico-connectors"})
    done = ru.restart_helpers({}, launchctl, agents, uid=501, say=lambda line: None)
    assert done == ["connectors"] and launchctl.calls[-1] == [
        "launchctl",
        "bootstrap",
        "gui/501",
        str(agents / "team.tico.tico-connectors.plist"),
    ]


# -- a Linux checkout supervised by systemd user units -----------------------------------------------

SYSTEMD = {"TICO_SYSTEMD_UNIT": "tico-bot.service", "PATH": "/usr/bin", "HOME": "/home/ana"}


class Systemctl:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1 if cmd[-1] in self.fail else 0, "", "")


def test_the_update_restarts_the_runner_through_systemctl_when_a_unit_runs_it(monkeypatch):
    killed = []
    monkeypatch.setattr(ru.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    systemctl = Systemctl()
    ru.restart_runner(4242, env=SYSTEMD, run=systemctl)
    assert systemctl.calls == [["systemctl", "--user", "restart", "tico-bot.service"]] and killed == []


def test_a_failed_systemctl_falls_back_to_stopping_the_process(monkeypatch):
    killed = []

    def kill(pid, sig):
        killed.append(sig)
        if sig == 0:
            raise ProcessLookupError

    monkeypatch.setattr(ru.os, "kill", kill)
    ru.restart_runner(4242, env=SYSTEMD, run=Systemctl(fail={"tico-bot.service"}))
    assert killed[0] == ru.signal.SIGTERM  # Restart=always starts it again


def test_only_installed_helper_units_are_restarted_through_systemctl(tmp_path):
    units = tmp_path / "units"
    units.mkdir()
    for name in ("tico-bot.service", "tico-connectors.service", "tico-importers.service"):
        (units / name).write_text("[Service]\n")
    systemctl, said = Systemctl(), []
    done = ru.restart_helpers_systemd(SYSTEMD, systemctl, unit_dir=units, say=said.append)
    assert done == ["connectors", "importers"]  # close-calls has no unit; the runner's own is not touched here
    assert systemctl.calls == [
        ["systemctl", "--user", "restart", "tico-connectors.service"],
        ["systemctl", "--user", "restart", "tico-importers.service"],
    ]
    (units / "tico-acme-close-calls.service").write_text("[Service]\n")
    assert ru.restart_helpers_systemd(
        {"TICO_SYSTEMD_UNIT": "tico-acme-bot.service"}, Systemctl(), unit_dir=units, say=lambda line: None
    ) == ["close-calls"]


@pytest.mark.slow
def test_instruction_backup_keeps_different_link_text_and_names_it_in_health(repos, capsys):
    _, checkout, state = repos
    instruction_links(checkout)
    path = checkout / "AGENTS.md"
    path.unlink()
    path.write_text("CLAUDE.md")
    result, calls = run_apply(checkout, state)
    backups = list((checkout / ".git/tico-instruction-backups").rglob("AGENTS.md.tico-backup-*"))
    assert len(backups) == 1 and backups[0].read_text() == "CLAUDE.md"
    assert path.is_symlink() and path.read_text() == "Repository rules\n"
    assert result["state"] == "blocked" and calls.restarts == 0
    assert str(backups[0].relative_to(checkout)) in ru.read_status(state)["error"]
    assert "bot instruction edit to review" in capsys.readouterr().err


@pytest.mark.slow
def test_retained_instruction_baseline_survives_release_target_changes_and_removal(repos):
    _, checkout, _ = repos
    instruction_links(checkout)
    path = checkout / "AGENTS.md"
    path.unlink()
    path.write_text("Repository rules\n")
    g = lambda *args: ru.git(checkout, *args)
    assert not ru.repair_instruction_links(checkout, g)
    (backup,) = (checkout / ".git/tico-instruction-backups").glob("AGENTS.md.tico-backup-*")
    assert "-sha256-" in backup.name
    commit(checkout, "CLAUDE.md", "Updated release rules\n")
    assert not ru.repair_instruction_links(checkout, g)
    sh(checkout, "git", "rm", "-q", "AGENTS.md", "CLAUDE.md")
    sh(checkout, "git", "-c", "user.name=t", "-c", "user.email=t@acme.example", "commit", "-qm", "removed instructions")
    assert not ru.repair_instruction_links(checkout, g)
    assert backup.read_text() == "Repository rules\n"
    backup.write_text("Late bot edit\n")
    assert backup.name in ru.repair_instruction_links(checkout, g)
    assert backup.read_text() == "Late bot edit\n"


def test_legacy_instruction_backup_gets_a_baseline_only_while_unchanged(tmp_path):
    metadata = tmp_path / ".git"
    saved = metadata / "tico-instruction-backups"
    saved.mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("accepted rules\n")
    backup = saved / "AGENTS.md.tico-backup-1"
    backup.write_text("accepted rules\n")
    assert not ru.instruction_backup_note(tmp_path, metadata)
    (retained,) = saved.iterdir()
    assert "-sha256-" in retained.name and not backup.exists()
    (tmp_path / "AGENTS.md").unlink()
    assert not ru.instruction_backup_note(tmp_path, metadata)
    unknown = saved / "AGENTS.md.tico-backup-2"
    unknown.write_text("unverified old contents")
    assert unknown.name in ru.instruction_backup_note(tmp_path, metadata)
    assert unknown.read_text() == "unverified old contents"


@pytest.mark.slow
def test_open_instruction_writer_lands_in_backup_and_is_reported_on_next_update(repos):
    _, checkout, state = repos
    instruction_links(checkout)
    path = checkout / "AGENTS.md"
    path.unlink()
    path.write_text("Repository rules\n")
    with path.open("r+") as writer:
        assert not ru.repair_instruction_links(checkout, lambda *args: ru.git(checkout, *args))
        backups = list((checkout / ".git/tico-instruction-backups").rglob("AGENTS.md.tico-backup-*"))
        assert len(backups) == 1
        writer.seek(0)
        writer.write("Late bot edit\n")
        writer.truncate()
        writer.flush()
    assert backups[0].read_text() == "Late bot edit\n"
    assert path.read_text() == "Repository rules\n"
    result, _ = run_apply(checkout, state)
    assert result["state"] == "blocked" and backups[0].name in result["error"]


def test_lock_outside_runner_root_is_never_touched(tmp_path, monkeypatch):
    from runner_support import git_locks

    checkout = tmp_path / "outside"
    metadata = checkout / ".git"
    metadata.mkdir(parents=True)
    lock = metadata / "index.lock"
    lock.write_text("unpublished")
    old = time.time() - git_locks.OLD_S * 2
    os.utime(lock, (old, old))
    monkeypatch.setattr(git_locks, "OWNED_ROOTS", (tmp_path / "runner",))
    assert not git_locks.report(checkout, f"Unable to create '{lock}': File exists", None)
    assert lock.read_text() == "unpublished"
    assert not (metadata / "tico-lock-recovery").exists()


@pytest.mark.slow
@pytest.mark.parametrize("linked", [False, True])
def test_native_updater_registers_its_checkout_and_reports_an_old_index_lock(repos, monkeypatch, linked):
    from runner_support import git_locks

    _, checkout, state = repos
    base = checkout
    base_index = (base / ".git/index").read_bytes()
    if linked:
        checkout = state.parent / "native-linked"
        sh(base, "git", "worktree", "add", "--detach", str(checkout), "v0.1.0")
    metadata = Path(sh(checkout, "git", "rev-parse", "--absolute-git-dir"))
    common = (base / ".git").resolve()
    projects = state.parent / "projects with 'quotes' and : colons"
    projects.mkdir()
    monkeypatch.setattr(git_locks, "OWNED_ROOTS", (state, projects))
    assert not git_locks.owned(checkout) and not git_locks.owned(metadata) and not git_locks.owned(common)
    lock = metadata / "index.lock"
    lock.write_text("still owned")
    old = time.time() - git_locks.OLD_S * 2
    os.utime(lock, (old, old))
    inode = lock.stat().st_ino
    head = sh(checkout, "git", "rev-parse", "HEAD")

    def update(args):
        result, calls = run_apply(checkout, state)
        assert result["state"] == "failed"
        assert str(lock) in result["error"] and "minutes old" in result["error"] and "run: rm --" in result["error"]
        assert calls.restarts == 0
        return 1

    monkeypatch.setattr(ru, "run_update", update)
    monkeypatch.setenv("TICO_GIT_OWNED_ROOTS", json.dumps([str(projects)]))
    assert ru.main(["--root", str(checkout), "--version", "0.2.0", "--state-dir", str(state), "--pid", "42"]) == 1
    assert git_locks.owned(checkout) and git_locks.owned(state) and git_locks.owned(projects)
    assert git_locks.owned(metadata) and git_locks.owned(common)
    with git_locks.command_lock(checkout) as gate:
        assert gate == common
        with (common / "tico-lock-recovery").open("w") as other:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert str(lock) in ru.read_status(state)["error"]
    assert lock.stat().st_ino == inode and lock.read_text() == "still owned"
    assert sh(checkout, "git", "rev-parse", "HEAD") == head
    assert (base / ".git/index").read_bytes() == base_index


def test_native_checkout_registration_refuses_a_mismatched_backlink(tmp_path, monkeypatch):
    from runner_support import git_locks

    checkout = tmp_path / "native"
    common = tmp_path / "outside/.git"
    admin = common / "worktrees/native"
    admin.mkdir(parents=True)
    checkout.mkdir()
    (checkout / ".git").write_text(f"gitdir: {admin}\n")
    (admin / "gitdir").write_text(str(tmp_path / "other/.git"))
    (admin / "commondir").write_text("../..\n")
    monkeypatch.setattr(git_locks, "OWNED_ROOTS", (checkout,))
    git_locks.register_checkout(checkout)
    assert not git_locks.owned(admin) and not git_locks.owned(common)
    with git_locks.command_lock(checkout) as gate:
        assert gate is None
    assert not (common / "tico-lock-recovery").exists()


def test_updater_git_holds_the_repository_gate_and_never_retries_a_locked_command(tmp_path, monkeypatch):
    from runner_support import git_locks

    metadata = tmp_path / ".git"
    metadata.mkdir()
    lock = metadata / "index.lock"
    lock.write_text("abandoned")
    old = time.time() - git_locks.OLD_S * 2
    os.utime(lock, (old, old))
    monkeypatch.setattr(git_locks, "OWNED_ROOTS", (tmp_path,))
    attempts = []

    def run(command, **kwargs):
        with (metadata / "tico-lock-recovery").open("w") as other:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        attempts.append(command)
        return subprocess.CompletedProcess(command, int(lock.exists()), "", f"Unable to create '{lock}': File exists")

    result = ru.git(tmp_path, "add", "draft", run=run)
    assert result.returncode == 1 and "minutes old" in result.stderr
    assert len(attempts) == 1
    assert lock.read_text() == "abandoned"


def test_instruction_backup_cleanup_never_follows_a_metadata_symlink(tmp_path):
    checkout, outside = tmp_path / "repo", tmp_path / "outside"
    metadata = checkout / ".git"
    metadata.mkdir(parents=True)
    outside.mkdir()
    (checkout / "AGENTS.md").write_text("same bytes")
    backup = outside / "AGENTS.md.tico-backup-1"
    backup.write_text("same bytes")
    (metadata / "tico-instruction-backups").symlink_to(outside)
    assert "cannot inspect instruction backups" in ru.instruction_backup_note(checkout, metadata)
    assert backup.read_text() == "same bytes"


def test_lock_reporting_is_read_only_and_rechecks_replacement_locks(tmp_path, monkeypatch):
    from runner_support import git_locks

    metadata = tmp_path / ".git"
    metadata.mkdir()
    lock = metadata / "index.lock"
    lock.write_text("recent")
    monkeypatch.setattr(git_locks, "OWNED_ROOTS", (tmp_path,))
    error = f"Unable to create '{lock}': File exists"
    assert not git_locks.report(tmp_path, error, metadata)
    old = time.time() - git_locks.OLD_S * 2
    os.utime(lock, (old, old))
    assert "minutes old" in git_locks.report(tmp_path, error, metadata)
    lock.rename(metadata / "original")
    lock.write_text("replacement writer")
    assert not git_locks.report(tmp_path, error, metadata)
    assert lock.read_text() == "replacement writer"
    assert not (metadata / "tico-lock-recovery").exists()
    assert not (metadata / "tico-lock-backups").exists()


def test_old_lock_command_quotes_shell_paths(tmp_path):
    import shlex
    from runner_support import git_locks

    root = tmp_path / "repo with spaces"
    metadata = root / ".git"
    metadata.mkdir(parents=True)
    lock = metadata / "config.lock"
    lock.write_text("old")
    old = time.time() - git_locks.OLD_S * 2
    os.utime(lock, (old, old))
    note = git_locks.report(root, "error: could not lock config file .git/config: File exists", metadata)
    assert shlex.split(note.split("run: ", 1)[1]) == ["rm", "--", str(lock)]
    assert lock.read_text() == "old"
