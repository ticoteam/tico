"""Release-updater counterpart of runner-rs/src/gitlocks.rs: never unlink a recent or open Git lock."""
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

OLD_S = 5 * 60


def unused(path, lock):
    if sys.platform != "linux":
        try:
            out = subprocess.run(["/usr/sbin/lsof", "-F", "p", "--", str(path)], capture_output=True, timeout=5, check=False)
            return out.returncode == 1 and not out.stdout and not out.stderr
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        for process in Path("/proc").iterdir():
            if not process.name.isdigit():
                continue
            owner = None
            try:
                owner = process.stat().st_uid
                files = list((process / "fd").iterdir())
            except FileNotFoundError:
                continue
            except PermissionError:
                if owner is not None and owner != lock.st_uid:
                    continue
                return False
            for file in files:
                try:
                    opened = file.stat()
                except FileNotFoundError:
                    continue
                if (opened.st_dev, opened.st_ino) == (lock.st_dev, lock.st_ino):
                    return False
        return True
    except OSError:
        return False


def recover(root, stderr):
    """Only Git's reported lock, inside this checkout's real metadata, with an unchanged inode."""
    if "File exists" not in stderr:
        return False
    git_dir = Path(root) / ".git"
    if git_dir.is_symlink() or not git_dir.is_dir():
        return False
    git_dir = git_dir.resolve()
    for name in re.findall(r"'([^'\n]+\.lock)'", stderr):
        path = Path(name)
        try:
            if not path.is_absolute() or not path.parent.resolve().is_relative_to(git_dir):
                continue
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or time.time() - before.st_mtime < OLD_S:
                continue
            if not unused(path, before):
                continue
            after = path.lstat()
            if (before.st_dev, before.st_ino, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_mtime_ns):
                continue
            path.unlink()
            return True
        except OSError:
            continue
    return False
