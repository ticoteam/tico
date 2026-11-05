"""Gate runner Git commands and report old locks without removing or moving them."""
import contextlib
import fcntl
import os
from pathlib import Path
import re
import shlex
import stat
import time

from .regular_file import SMALL, read as read_regular

OLD_S = 30 * 60
OWNED_ROOTS = ()


def owned(path):
    return any(Path(path).resolve().is_relative_to(Path(root).resolve()) for root in OWNED_ROOTS)


def metadata_roots(root):
    """Accept a linked checkout only when its admin directory points back to it."""
    try:
        root = Path(root).resolve(strict=True)
        git_dir = root / ".git"
        info = git_dir.lstat()
        if stat.S_ISDIR(info.st_mode):
            return (git_dir.resolve(strict=True),)
        if not stat.S_ISREG(info.st_mode):
            return ()
        pointer = read_regular(git_dir, SMALL).decode().strip()
        if not pointer.startswith("gitdir: "):
            return ()
        admin = (root / pointer[len("gitdir: "):]).resolve(strict=True)
        back = read_regular(admin / "gitdir", SMALL).decode().strip()
        if Path(back).resolve(strict=True) != git_dir:
            return ()
        common = (admin / read_regular(admin / "commondir", SMALL).decode().strip()).resolve(strict=True)
        if admin.parent != common / "worktrees" or not common.is_dir():
            return ()
        return admin, common
    except (OSError, ValueError, RuntimeError):
        return ()


def register_checkout(root):
    """A native install can keep its linked Git metadata outside projects and state."""
    global OWNED_ROOTS
    if owned(root):
        OWNED_ROOTS = (*OWNED_ROOTS, *metadata_roots(root))


@contextlib.contextmanager
def command_lock(root, *, wait=10):
    """All updater Git launches use the same per-repository gate as the Rust runner."""
    roots = metadata_roots(root) if owned(root) else ()
    if not roots or not all(owned(path) for path in roots):
        yield None
        return
    git_dir = roots[-1]
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
