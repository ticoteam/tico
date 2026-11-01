#!/usr/bin/env python3
"""Which pages of a SQLite database differ between two copies of it, and a synthetic database to measure with.

Litestream uploads the pages a write changed, so a start that rewrites most of the file uploads most of it again.
Take each copy after a checkpoint (or with the server stopped), then compare:

    python3 scripts/page_churn.py before.sqlite after.sqlite

The guard test (backend/tests/test_start_page_churn.py) and scripts/litestream_restart_check.py use these functions.
"""
import hashlib
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


def page_size(path):
    """The page size from the SQLite header; the value 1 means 65536."""
    with Path(path).open("rb") as handle:
        header = handle.read(18)
    if len(header) < 18 or not header.startswith(b"SQLite format 3\x00"):
        raise ValueError(f"{path} is not a SQLite database")
    size = int.from_bytes(header[16:18], "big")
    return 65536 if size == 1 else size


def page_digests(path):
    """A SHA-256 for each page of the file, in page order."""
    size = page_size(path)
    digests = []
    with Path(path).open("rb") as handle:
        while page := handle.read(size):
            digests.append(hashlib.sha256(page).digest())
    return digests


def changed_pages(before, after):
    """(pages that differ, pages in the larger copy). A page past the end of the smaller copy counts as changed."""
    old, new = page_digests(before), page_digests(after)
    differing = sum(1 for a, b in zip(old, new, strict=False) if a != b) + abs(len(old) - len(new))
    return differing, max(len(old), len(new))


def fill_synthetic(path, messages=15000, events=5000):
    """Adds random, fictional conversations, messages and events to a Tico database, so the file is big enough to
    measure (about 35 MB at the defaults). The text is random hex, so no real content is in it."""
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def stamp(i):
        return (start + timedelta(seconds=i * 37)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def text(n):
        return os.urandom(n // 2 + 1).hex()[:n]

    conversations = 200
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA foreign_keys=OFF")
        db.executemany(
            "INSERT INTO conversations(id,kind,subject,task_id,participants,created_at,last_message_at,scope) "
            "VALUES(?,?,?,?,?,?,?,?)",
            [(f"conv-{i:06d}", "chat", "Synthetic " + text(24), None, '["human:owen"]', stamp(i), stamp(i), "direct")
             for i in range(conversations)])
        db.executemany(
            "INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,refs,created_at,public) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            [(f"msg-{i:08d}", f"conv-{i % conversations:06d}", "human:owen", "human:owen", "chat", text(1200), "{}",
              stamp(i), 0) for i in range(messages)])
        db.executemany(
            "INSERT INTO events(id,ts,actor,action,target,detail) VALUES(?,?,?,?,?,?)",
            [(f"evt-{i:08d}", stamp(i), "human:owen", "synthetic.row", f"msg-{i:08d}", '{"n": "' + text(200) + '"}')
             for i in range(events)])


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: page_churn.py BEFORE.sqlite AFTER.sqlite", file=sys.stderr)
        return 2
    differing, total = changed_pages(*argv)
    share = differing / total if total else 0.0
    print(f"{differing} of {total} pages differ ({share:.1%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
