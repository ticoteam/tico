"""Bounded supervisor reads from bot-writable checkouts and Git metadata."""
import os
import stat
from contextlib import contextmanager

CONTENT = 1024 * 1024
SMALL = 4096


def read(path, limit=CONTENT):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise OSError("not a bounded regular file")
        content = handle.read(limit + 1)
        if len(content) > limit:
            raise OSError("regular file exceeds size limit")
        return content


@contextmanager
def lock(path):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o644)
    with os.fdopen(fd, "a") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > SMALL:
            raise OSError("not a bounded regular lock file")
        yield handle
