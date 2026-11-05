"""Conservative recovery for runner-private repositories, paired with runner-rs/src/gitlocks.rs.

Owned roots must contain copies private to one runner/PID namespace, never a bind mount shared
with another container or host. Filesystem type alone cannot establish that deployment assumption.
"""
import contextlib
import fcntl
import os
from pathlib import Path

import re
import stat
import sys
import time
import uuid

from .regular_file import read as read_regular, SMALL

OLD_S = 30 * 60
OWNED_ROOTS = ()


def owned(path):
    return any(Path(path).resolve().is_relative_to(Path(root).resolve()) for root in OWNED_ROOTS)


def inaccessible_git(process):
    try:
        return (process / "comm").read_text().strip().startswith("git")
    except FileNotFoundError:
        return False
    except OSError:
        return True


def exited(process):
    if not process.exists():
        return True
    try:
        # Linux zombies no longer own descriptors or a cwd. Unknown state remains a possible live owner.
        return (process / "stat").read_text().rsplit(") ", 1)[1][:1] in ("Z", "X")
    except (OSError, IndexError):
        return False


def related(cwd, arguments, environment, roots):
    """Unknown paths cannot establish that a live Git command belongs elsewhere."""
    def inside(value, base):
        path = (base / value).resolve(strict=True)
        return any(path.is_relative_to(root) for root in roots)

    if inside(cwd, Path("/")):
        return True
    at = cwd
    args = iter(arguments[1:])
    for arg in args:
        if arg == "-C":
            at = (at / next(args)).resolve(strict=True)
            if inside(at, cwd):
                return True
        elif arg == "--git-dir":
            if inside(next(args), at):
                return True
        elif arg.startswith("--git-dir="):
            if inside(arg.partition("=")[2], at):
                return True
    for value in environment:
        if value.startswith(("GIT_DIR=", "GIT_COMMON_DIR=")) and inside(value.partition("=")[2], at):
            return True
    return False


def unused(path, lock, roots=(), *, strict=False):
    if sys.platform != "linux":
        # lsof/ps cannot establish writer exclusion. Leave the lock for Health and a person or BotOps.
        return False
    try:
        for process in Path("/proc").iterdir():
            if not process.name.isdigit():
                continue
            if inaccessible_git(process):
                try:
                    cwd = (process / "cwd").resolve(strict=True)
                    args = (process / "cmdline").read_bytes().decode(errors="surrogateescape").rstrip("\0").split("\0")
                    environment = (process / "environ").read_bytes().decode(errors="surrogateescape").split("\0")
                    if not args[0] or related(cwd, args, environment, roots):
                        return False
                except (OSError, StopIteration):
                    if not exited(process):
                        return False
                    continue
            try:
                files = list((process / "fd").iterdir())
            except FileNotFoundError:
                continue
            except PermissionError:
                if not strict and not inaccessible_git(process):
                    continue
                return False
            for file in files:
                try:
                    opened = file.stat()
                except FileNotFoundError:
                    continue
                except PermissionError:
                    if strict or inaccessible_git(process):
                        return False
                    continue
                if (opened.st_dev, opened.st_ino) == (lock.st_dev, lock.st_ino):
                    return False
        return True
    except OSError:
        return False


class RecoveryConflict(Exception):
    """Both raced copies stay available and the failed command carries their names to Health."""


def quarantine(path, before, archive=None):
    """Move only under the repository gate, after ruling out live Git owners."""
    directory = archive if archive is not None else path.parent
    directory.mkdir(parents=True, exist_ok=True)
    if directory.resolve() != directory:
        return False
    # A changed inode must stay at its pathname; restoring it after moving cannot protect Git's rename.
    after = path.lstat()
    if (before.st_dev, before.st_ino, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_mtime_ns):
        return False
    backup = directory / f"{path.name}.stale-{int(time.time())}-{uuid.uuid4().hex}"
    path.rename(backup)
    after = backup.lstat()
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        # An exclusion violation is an error, never proof that retrying Git is safe.
        with contextlib.suppress(OSError):
            os.link(backup, path, follow_symlinks=False)
            backup.unlink()
        raise RecoveryConflict(f"repository copy needs attention: Git lock changed during recovery; inspect {path} and {backup}")
    return True


@contextlib.contextmanager
def command_lock(root, *, wait=10):
    """All updater Git launches use the same per-repository gate as the Rust runner."""
    git_dir = Path(root) / ".git"
    if not owned(root) or git_dir.is_symlink() or not git_dir.is_dir() or not owned(git_dir):
        yield None
        return
    git_dir = git_dir.resolve()
    fd = os.open(git_dir / "tico-lock-recovery", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, "w") as sidecar:
        info = os.fstat(sidecar.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > SMALL:
            raise OSError("Git work lock cannot be checked")
        until = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(sidecar, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= until:
                    raise
                time.sleep(0.025)
        yield git_dir


def recover(root, stderr):
    """Direct recovery takes the same gate as a command, without waiting behind a live writer."""
    try:
        with command_lock(root, wait=0) as common:
            return recover_locked(root, stderr, common)
    except OSError:
        return False


def recover_locked(root, stderr, git_dir):
    """The caller holds the repository gate through the failed command, scan, move and retry."""
    if "File exists" not in stderr or git_dir is None or sys.platform != "linux":
        return False
    names = re.findall(r"'([^'\n]+\.lock)'", stderr)
    names += [name.strip("'") + ".lock" for name in re.findall(r"could not lock config file ([^\n]+): File exists", stderr)]
    try:
        roots = [Path(root).resolve(), git_dir]
        worktrees = git_dir / "worktrees"
        if worktrees.exists():
            for admin in worktrees.iterdir():
                roots.append(Path(os.fsdecode(read_regular(admin / "gitdir", SMALL)).strip()).parent.resolve(strict=True))
        for name in names:
            path = Path(name)
            if not path.is_absolute():
                path = Path(root).resolve() / path
            try:
                if not path.parent.resolve().is_relative_to(git_dir):
                    continue
                before = path.lstat()
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or time.time() - before.st_mtime < OLD_S:
                    continue
                if not unused(path, before, roots):
                    continue
                after = path.lstat()
                if (before.st_dev, before.st_ino, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_mtime_ns):
                    continue
                return quarantine(path, before, git_dir / "tico-lock-backups")
            except OSError:
                continue
    except OSError:
        return False
    return False
