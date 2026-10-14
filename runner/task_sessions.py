"""A task lane's own checkout of the bot's repository (clients/sessions.py names the lanes).

Turns on different tasks of one bot run at the same time, so each task gets a git worktree of the
bot's main checkout at `<projects_dir>/sessions/bot-<slug>/<task-id>` on branch `tico-session/<task-id>`,
tracking the same upstream as the main checkout. The path never changes for a task: Claude finds a
session to resume by its working directory.

A turn starts by rebasing the worktree onto its upstream and ends by committing what it left in
memory/ and knowledge/, stamping its commits with `Tico-Run:` and rebasing them onto the upstream, so the
push lands on the bot's default branch beside what the other lanes pushed. Nothing here forces, drops
a commit, or touches the main checkout's working tree.
"""

import re
import shlex
import subprocess
from pathlib import Path

from . import isolation, safe_git

SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
BRANCH = "tico-session/"
CLOSED = ("done", "closed", "declined")
# Problems no wait clears: the bot has no checkout, or one with no upstream for a worktree to share. A task's turn
# then runs in the main checkout; any other problem is worth waiting out first (runner/service.py WORKTREE_WAIT_S).
MISSING = "the bot's checkout is missing"
NO_UPSTREAM = "the bot's checkout has no upstream to share between tasks"
LASTING = (MISSING, NO_UPSTREAM)


def task_of(lane):
    """The task id a `task:<id>` lane names, or "" for the main lane or an id unfit for a path."""
    task = lane[len("task:"):] if (lane or "").startswith("task:") else ""
    return task if SLUG.fullmatch(task) and ".." not in task else ""


def path_for(projects_dir, bot, task):
    return Path(projects_dir) / "sessions" / ("bot-" + bot) / task


def _git(path, *args, env, timeout=15):
    return isolation.run([*safe_git.prefix(path), "-C", str(path), *args], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, env=env, timeout=timeout)


def upstream(main, env):
    """The main checkout's upstream as a remote-tracking ref (`origin/main`), or "" when it has none."""
    done = _git(main, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", env=env)
    ref = done.stdout.strip() if done.returncode == 0 else ""
    return ref if "/" in ref else ""


def ensure(main, path, task, turn_env=None):
    """Make or reuse the task's worktree. Returns (upstream ref, problem); a problem means the turn runs
    in the main checkout instead, one at a time with the bot's other turns there."""
    main, path = Path(main), Path(path)
    env = safe_git.environment(turn_env)
    if not (main / ".git").exists():
        return "", MISSING
    try:
        tracked = upstream(main, env)
        if not tracked:
            return "", NO_UPSTREAM
        common = _git(main, "rev-parse", "--path-format=absolute", "--git-common-dir", env=env).stdout.strip()
        if (path / ".git").exists():
            mine = _git(path, "rev-parse", "--path-format=absolute", "--git-common-dir", env=env).stdout.strip()
            if not common or Path(mine).resolve() != Path(common).resolve():
                return "", f"{path} is not a worktree of the bot's checkout; left as it is"
            return tracked, ""
        if path.exists() and any(path.iterdir()):
            return "", f"{path} already exists and is not a worktree; left as it is"
        _git(main, "worktree", "prune", env=env)
        # `git worktree add` runs as the bot user, so the folder it adds the task in must be the bot's before it
        # runs, including one an older runner made as the supervisor (every task then fell back to the checkout).
        isolation.mkdir(path.parent, mode=0o755)
        isolation.chown(path.parent)
        branch = BRANCH + task
        exists = _git(main, "show-ref", "--verify", "--quiet", "refs/heads/" + branch, env=env).returncode == 0
        added = _git(main, "worktree", "add", "--quiet", *((str(path), branch) if exists else ("-b", branch, str(path), tracked)),
                     env=env, timeout=60)
        if added.returncode:
            return "", "could not add the task's worktree: " + (added.stderr.strip().splitlines() or ["git failed"])[-1][:160]
        _git(path, "branch", "--quiet", "--set-upstream-to=" + tracked, env=env)
        # `git push` from a branch named unlike its upstream is refused under the default push.default; the bot
        # pushes from here as it does from its main checkout.
        if _git(main, "config", "--get", "push.default", env=env).returncode:
            _git(main, "config", "push.default", "upstream", env=env)
        isolation.chown(path, recursive=True)
        return tracked, ""
    except (OSError, subprocess.SubprocessError) as exc:
        return "", type(exc).__name__


def finish(path, aid, turn_env=None, skip=(), timeout=45):
    """After a completed turn: commit what it left in memory/ and knowledge/ (not `skip`, files the redactor
    held back), stamp this lane's unpushed commits with the run, and rebase them onto the upstream.
    Returns a problem or ""; on a problem the commits stay here for the next turn or a person."""
    from .memory_history import FOLDERS
    path = Path(path)
    env = safe_git.committing_environment(turn_env)
    try:
        folders = [name for name in FOLDERS if (path / name).is_dir()]
        if folders:
            _git(path, "add", "-A", "--", *folders, env=env)
            held = [str(Path(p).relative_to(path)) for p in skip if Path(p).is_relative_to(path)]
            if held:
                _git(path, "reset", "-q", "--", *held, env=env)
            if _git(path, "diff", "--cached", "--quiet", env=env).returncode:
                made = _git(path, "commit", "-q", "-m", f"Memory from run {aid}\n\nTico-Run: {aid}", env=env)
                if made.returncode:
                    return "the memory commit failed"
        fetched = _git(path, "fetch", "--quiet", "--no-tags", env=env, timeout=timeout)
        if fetched.returncode:
            return "fetch failed"
        if not _git(path, "rev-list", "@{u}..HEAD", env=env).stdout.strip():
            return ""
        if _git(path, "rev-list", "--merges", "@{u}..HEAD", env=env).stdout.strip():
            # A rebase would flatten a merge and lose what was resolved in it; the push's catch-up merges instead
            # (runner/service.py catch_up), and these commits go without the trailer.
            return ""
        # The trailer is how memory history (runner/memory_history.py) tells which run made a commit once
        # other lanes' commits sit beside it; a commit that already names its run keeps it.
        stamp = shlex.join([*safe_git.prefix(path), "-c", "trailer.ifexists=doNothing", "commit", "-q", "--amend",
                            "--no-edit", "--no-verify", "--trailer", f"Tico-Run: {aid}"])
        stashed = bool(_git(path, "status", "--porcelain", "--untracked-files=no", env=env).stdout.strip())
        rebased = _git(path, "rebase", "--quiet", *(("--autostash",) if stashed else ()), "--exec", stamp, "@{u}",
                       env=env, timeout=timeout)
        if rebased.returncode == 0:
            return ""
        _git(path, "rebase", "--abort", env=env)
        return "its commits conflict with what the bot's other tasks pushed"
    except subprocess.TimeoutExpired:
        try:
            _git(path, "rebase", "--abort", env=env)
        except (OSError, subprocess.SubprocessError):
            pass
        return "git timed out"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return type(exc).__name__


def remove(main, path, task, turn_env=None):
    """Take a closed task's worktree and branch away once nothing in it is left unsaved: a clean tree and every
    commit on the remote. True when gone."""
    main, path = Path(main), Path(path)
    env = safe_git.environment(turn_env)
    if not path.exists():
        return True
    try:
        if _git(path, "status", "--porcelain", env=env).stdout.strip():
            return False
        # Reachable from a remote-tracking ref, not just from the upstream: a commit only this branch has stays.
        ahead = _git(path, "rev-list", "--count", "HEAD", "--not", "--remotes", env=env)
        if ahead.returncode or ahead.stdout.strip() != "0":
            return False
        if _git(main, "worktree", "remove", str(path), env=env, timeout=60).returncode:
            return False
        _git(main, "branch", "-D", BRANCH + task, env=env)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
