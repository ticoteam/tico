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
import subprocess
import sys
import time
import uuid

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
        # lsof may silently omit another user's descriptors; never unlink an instruction backup on that proof.
        if strict:
            return False
        try:
            out = subprocess.run(["/usr/sbin/lsof", "-F", "p", "--", str(path)], capture_output=True, timeout=5, check=False)
            if out.returncode != 1 or out.stdout or out.stderr:
                return False
            processes = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True, timeout=5, check=False)
            if processes.returncode or processes.stderr:
                return False
            for line in processes.stdout.splitlines():
                pid, command = line.strip().split(None, 1)
                if not Path(command).name.startswith("git"):
                    continue
                cwd = subprocess.run(["/usr/sbin/lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"], capture_output=True, text=True, timeout=5, check=False)
                args = subprocess.run(["ps", "eww", "-p", pid, "-o", "command="], capture_output=True, text=True, timeout=5, check=False)
                names = [line[1:] for line in cwd.stdout.splitlines() if line.startswith("n")]
                if cwd.returncode or cwd.stderr or args.returncode or args.stderr or len(names) != 1 or not args.stdout.strip():
                    return False
                # ps cannot unambiguously quote argv/environment containing spaces. Refuse those commands.
                words = args.stdout.split()
                if any(c in args.stdout for c in "\"'\\") or related(Path(names[0]), words, words, roots):
                    return False
                # Environment strings from ps have no boundaries; uncertainty keeps the lock.
                if any("GIT_DIR=" in word or "GIT_COMMON_DIR=" in word or word in ("-C", "--git-dir") or word.startswith("--git-dir=") for word in words):
                    return False
            return True
        except (OSError, ValueError, StopIteration, subprocess.SubprocessError):
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
    """Keep the inode even if a Git writer replaces the path during our rename."""
    directory = archive if archive is not None else path.parent
    directory.mkdir(parents=True, exist_ok=True)
    if directory.resolve() != directory:
        return False
    backup = directory / f"{path.name}.stale-{int(time.time())}-{uuid.uuid4().hex}"
    path.rename(backup)
    after = backup.lstat()
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        # link() restores without replacing a new lock. Both names stay if another writer won.
        # A failed restore keeps both names; the failed command reports them in Health.
        with contextlib.suppress(OSError):
            os.link(backup, path, follow_symlinks=False)
            backup.unlink()
        raise RecoveryConflict(f"repository copy needs attention: Git lock changed during recovery; inspect {path} and {backup}")
    return True


def recover(root, stderr):
    """Serialize with Rust on the common metadata's sidecar; never recover outside owned roots."""
    if "File exists" not in stderr or not owned(root):
        return False
    git_dir = Path(root) / ".git"
    if git_dir.is_symlink() or not git_dir.is_dir() or not owned(git_dir):
        return False
    git_dir = git_dir.resolve()
    names = re.findall(r"'([^'\n]+\.lock)'", stderr)
    names += [name.strip("'") + ".lock" for name in re.findall(r"could not lock config file ([^\n]+): File exists", stderr)]
    try:
        fd = os.open(git_dir / "tico-lock-recovery", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(fd, "w") as sidecar:
            sidecar_stat = os.fstat(sidecar.fileno())
            if not stat.S_ISREG(sidecar_stat.st_mode) or sidecar_stat.st_nlink != 1:
                return False
            fcntl.flock(sidecar, fcntl.LOCK_EX | fcntl.LOCK_NB)
            roots = [Path(root).resolve(), git_dir]
            worktrees = git_dir / "worktrees"
            if worktrees.exists():
                for admin in worktrees.iterdir():
                    roots.append(Path((admin / "gitdir").read_text().strip()).parent.resolve(strict=True))
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
