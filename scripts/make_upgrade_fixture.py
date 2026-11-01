#!/usr/bin/env python3
"""Build the old-release database that backend/tests/test_upgrade.py opens with the current code.

Run it from a checkout of the OLD release, so that release's own `backend.store` creates the file:

    git worktree add /tmp/tico-v0.2.1 v0.2.1
    cd /tmp/tico-v0.2.1 && PYTHONPATH=. python /path/to/scripts/make_upgrade_fixture.py OUT.sqlite.gz

The result is gzipped (an initialized database is about 1.2 MB, the gzip about 100 KB).

`--into DB` adds the same rows to a database a running install made instead (scripts/journey-test.sh seeds the
previous release with them before it upgrades), so it needs nothing but the standard library.
"""
import gzip
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

TS = "2026-01-01T00:00:00Z"


def seed(c, bots="INSERT"):
    """Bots, a conversation with messages and tasks, written as an older release wrote them: the rows an upgrade
    must keep."""
    for slug in ("coo", "cmo", "seo"):
        c.execute(f"{bots} INTO bots(slug,display_name,runtime,state,created_at) VALUES(?,?,?,?,?)",
                  (slug, slug.upper(), "codex", "active", TS))
    c.execute("INSERT INTO conversations(id,kind,subject,participants,created_at,last_message_at) "
              "VALUES('c1','direct','Launch plan','[\"bot:coo\",\"human:ana\"]',?,?)", (TS, TS))
    for n in (1, 2):
        c.execute("INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,created_at) "
                  "VALUES(?,?,?,?,?,?,?)", (f"m{n}", "c1", "human:ana", "bot:coo", "message", f"hello {n}", TS))
    for n in (1, 2, 3):
        c.execute("INSERT INTO tasks(id,title,body,requester,owner,status,conversation_id,created_at,updated_at) "
                  "VALUES(?,?,?,?,?,?,?,?,?)", (f"t{n}", f"Task {n}", "", "human:ana", "bot:coo", "open", "c1", TS, TS))


def build(out):
    from backend.config import Settings
    from backend.store import Store
    work = Path(tempfile.mkdtemp())
    db = work / "hub.sqlite"
    Store(Settings(db_path=db, registry_dir=Path("registry"))).initialize(seed_market=False)
    c = sqlite3.connect(db)
    seed(c)
    c.commit()
    c.execute("VACUUM")
    c.close()
    with open(db, "rb") as source, gzip.GzipFile(out, "wb", mtime=0) as target:
        shutil.copyfileobj(source, target)


def seed_into(db):
    c = sqlite3.connect(db, timeout=30)   # the install's server is running and writes too
    with c:
        seed(c, bots="INSERT OR IGNORE")   # a fresh install already has the starter bots coo, cmo and seo
    c.close()


if __name__ == "__main__":
    if sys.argv[1:2] == ["--into"]:
        seed_into(sys.argv[2])
    else:
        build(sys.argv[1])
