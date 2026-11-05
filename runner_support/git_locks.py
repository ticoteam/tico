"""Gate runner Git commands and report old locks without removing or moving them."""
import contextlib
import fcntl
import os
from pathlib import Path
import re
import shlex
import stat
import time

from .regular_file import SMALL

OLD_S = 30 * 60
OWNED_ROOTS = ()


def owned(path):
    return any(Path(path).resolve().is_relative_to(Path(root).resolve()) for root in OWNED_ROOTS)


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


def report(root, stderr, git_dir):
    """Age is only a reason to report a lock; a writer may still own or replace it."""
    if "File exists" not in stderr or git_dir is None:
        return ""
    names = re.findall(r"'([^'\n]+\.lock)'", stderr)
    names += [name.strip("'") + ".lock" for name in re.findall(r"could not lock config file ([^\n]+): File exists", stderr)]
    for name in names:
        path = Path(name)
        if not path.is_absolute():
            path = Path(root).resolve() / path
        try:
            if not path.parent.resolve().is_relative_to(git_dir):
                continue
            metadata = path.lstat()
            age = time.time() - metadata.st_mtime
            if not stat.S_ISREG(metadata.st_mode) or age < OLD_S:
                continue
            return (f"repository copy needs attention: Git lock is {int(age // 60)} minutes old. "
                    f"Check no Git is running, then run: rm -- {shlex.quote(str(path))}")
        except OSError:
            continue
    return ""
