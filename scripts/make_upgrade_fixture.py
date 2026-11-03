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


def _cols(c, table, *names):
    """`names` as this database spells them: each is a (current, before the cleanup renames) pair or one name, and the
    pair's older spelling is used when the table has no column by the current one (journey-test.sh seeds the previous
    release, which may predate the renames)."""
    have = {row[1] for row in c.execute(f"PRAGMA table_info({table})")}
    return ",".join(n if isinstance(n, str) else (n[0] if n[0] in have else n[1]) for n in names)


def seed(c, bots="INSERT"):
    """Bots, a conversation with messages and tasks, written as an older release wrote them: the rows an upgrade
    must keep."""
    created = ("created_at", "created")
    keyed = "id" in {row[1] for row in c.execute("PRAGMA table_info(bots)")}   # bots gained an id in the cleanup
    coo = "bot:coo"
    for slug in ("coo", "cmo", "seo"):
        have = c.execute(f"SELECT {'id' if keyed else 'slug'} FROM bots WHERE slug=?", (slug,)).fetchone()
        if bots != "INSERT" and have:
            # an install's own starter bot: since the cleanup it has its own id, and the slug/id trigger aborts an
            # INSERT OR IGNORE of id=slug (a RAISE is not a conflict OR IGNORE skips), so leave it as the install made it
            # and address the seeded rows to that id (messages queue jobs whose bot_id must name a bot)
            if slug == "coo":
                coo = f"bot:{have[0]}"
            continue
        # a release before migration 0042 has bots.runtime (its value moves into the bot's config); later ones do not
        runtime = ["runtime"] if "runtime" in {row[1] for row in c.execute("PRAGMA table_info(bots)")} else []
        names = _cols(c, "bots", *(["id"] if keyed else []), "slug", "display_name", *runtime, "state", created)
        # a bot's id is its slug (hubdb.new_bot_id for product bots), and actors are `bot:<id>`, so `bot:coo` resolves
        values = ([slug] if keyed else []) + [slug, slug.upper(), *(["codex"] if runtime else []), "active", TS]
        c.execute(f"{bots} INTO bots({names}) VALUES({','.join('?' * len(values))})", values)
    names = _cols(c, "conversations", "id", "kind", "subject", ("participants", "participants_json"), created,
                  "last_message_at")
    c.execute(f"INSERT INTO conversations({names}) VALUES('c1','direct','Launch plan',?,?,?)",
              (f'["{coo}","human:ana"]', TS, TS))
    names = _cols(c, "messages", "id", "conversation_id", ("sender_id", "from_actor"), ("recipient_id", "to_actor"), "kind",
                  "body", created)
    for n in (1, 2):
        c.execute(f"INSERT INTO messages({names}) VALUES(?,?,?,?,?,?,?)",
                  (f"m{n}", "c1", "human:ana", coo, "message", f"hello {n}", TS))
    names = _cols(c, "tasks", "id", "title", "body", ("requester_id", "requester"), ("owner_id", "owner"), "status",
                  "conversation_id", created, ("updated_at", "updated"))
    for n in (1, 2, 3):
        c.execute(f"INSERT INTO tasks({names}) VALUES(?,?,?,?,?,?,?,?,?)",
                  (f"t{n}", f"Task {n}", "", "human:ana", coo, "open", "c1", TS, TS))


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
