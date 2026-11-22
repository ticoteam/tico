"""`explicitly_granted` (one query per subject) answers exactly what `effective_grant` answers per credential; `granted`
adds a bot's default reads (backend/default_read.py, tested in test_default_read.py)."""

import random
import sqlite3

from backend.credentials import effective_grant, explicitly_granted


def test_granted_matches_effective_grant_for_every_credential_and_subject():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE credential_grants(id TEXT PRIMARY KEY, credential_id TEXT, subject TEXT, revoked_at TEXT, "
        "parent_id TEXT, access_level TEXT NOT NULL DEFAULT 'read_write')"
    )
    c.execute("CREATE TABLE bot_config(bot TEXT PRIMARY KEY, owner_id TEXT)")
    rng = random.Random(7)
    people, bots, creds = ["ana", "ben"], ["ops", "cmo", "fin"], [f"c{i}" for i in range(12)]
    for bot in bots:
        c.execute("INSERT INTO bot_config VALUES(?,?)", (bot, rng.choice(people)))
    subjects = ["human:" + p for p in people] + ["bot:" + b for b in bots] + ["computers"]
    for n in range(80):
        parent = rng.choice([None, None, f"g{rng.randrange(max(n, 1))}"])
        c.execute(
            "INSERT INTO credential_grants(id,credential_id,subject,revoked_at,parent_id) VALUES(?,?,?,?,?)",
            (f"g{n}", rng.choice(creds), rng.choice(subjects), rng.choice([None, None, None, "2026-10-06"]), parent),
        )
    for subject in subjects:
        assert explicitly_granted(c, subject) == {
            cid for cid in creds if effective_grant(c, cid, subject) is not None
        }, subject


def test_the_migration_lowers_a_grant_passed_on_above_its_givers():
    from pathlib import Path

    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE credential_grants(id TEXT PRIMARY KEY, credential_id TEXT, subject TEXT, revoked_at TEXT, "
        "parent_id TEXT, access_level TEXT NOT NULL DEFAULT 'read_write')"
    )
    c.executemany(
        "INSERT INTO credential_grants(id,parent_id,access_level) VALUES(?,?,?)",
        [
            ("p-read", None, "read"),
            ("c-up", "p-read", "read_write"),
            ("p-rw", None, "read_write"),
            ("c-ok", "p-rw", "read_write"),
        ],
    )
    migration = next(Path(__file__).resolve().parents[1].glob("migrations/*_delegated_grants_at_most_their_parent.sql"))
    c.executescript(migration.read_text())
    assert dict(c.execute("SELECT id, access_level FROM credential_grants")) == {
        "p-read": "read",
        "c-up": "read",
        "p-rw": "read_write",
        "c-ok": "read_write",
    }
