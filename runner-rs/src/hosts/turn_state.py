"""Snapshot only this conversation's provider state; never copy authentication or configuration.

The supervisor starts this helper with synthetic/test or leased-turn paths. Every
source and destination is pinned; links, aliases and special files are refused.
"""
import json
import os
from pathlib import Path
import stat
import sys

NAMES = {"projects", "sessions", "archived_sessions", "session_index.jsonl", "history.jsonl"}
MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024


def directory(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("invalid state path")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parts[1:]:
            next_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def copy(source, destination, source_uid, target_uid, gid):
    budget = [0, 0]

    def walk(src, dst, depth):
        if depth > 64:
            raise ValueError("state nesting limit")
        for name in os.listdir(src):
            if depth == 0 and name not in NAMES:
                continue
            budget[0] += 1
            if budget[0] > 100000:
                raise ValueError("state entry limit")
            inp = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=src)
            try:
                info = os.fstat(inp)
                if info.st_uid != source_uid:
                    raise ValueError("foreign state inode")
                if stat.S_ISDIR(info.st_mode):
                    os.mkdir(name, 0o700, dir_fd=dst)
                    out = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dst)
                    try:
                        walk(inp, out, depth + 1)
                        os.fchown(out, target_uid, gid)
                    finally:
                        os.close(out)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    if info.st_size > MAX_FILE:
                        raise ValueError("state file limit")
                    out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dst)
                    try:
                        copied = 0
                        while True:
                            raw = os.read(inp, 65536)
                            if not raw:
                                break
                            copied += len(raw)
                            budget[1] += len(raw)
                            if copied > MAX_FILE or budget[1] > MAX_TOTAL:
                                raise ValueError("state size limit")
                            view = memoryview(raw)
                            while view:
                                view = view[os.write(out, view):]
                        os.fchown(out, target_uid, gid)
                    finally:
                        os.close(out)
                else:
                    raise ValueError("state is not a plain private file")
            finally:
                os.close(inp)

    src = directory(source)
    try:
        dst = directory(destination)
        try:
            walk(src, dst, 0)
        finally:
            os.close(dst)
    finally:
        os.close(src)


if __name__ == "__main__":
    try:
        copy(**json.load(sys.stdin))
    except Exception:
        sys.exit(1)
