"""`credentials.usable` (a Finance connection's key check, run at connect and for every connection on every sync) reads
only the one credential's rows by key. Its first version scanned credentials (`owned`), bot_config (`effective_grant`'s
join) and humans (the owner by email); these plans keep those scans from coming back."""

import sqlite3

import pytest

from backend import credentials
from backend.auth import Identity
from backend.config import Settings
from backend.store import Store

AT = "2026-10-15T12:00:00.000000Z"
WATCHED = ("credentials", "humans", "bot_config", "credential_grants", "k", "g", "p", "bc")


@pytest.fixture
def c(tmp_path):
    path = tmp_path / "hub.sqlite"
    Store(Settings(db_path=path)).initialize(seed_market=False)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO credentials(id,name,kind,created_at,updated_at,updated_by,created_by) "
        "VALUES('key','key','token',?,?,'human:ana','human:ana')",
        (AT, AT),
    )
    conn.execute(
        "INSERT INTO credential_grants(id,credential_id,subject,granted_by,created_at) "
        "VALUES('g1','key','bot:books','human:ana',?),('g2','key','human:ben','human:ana',?)",
        (AT, AT),
    )
    yield conn
    conn.close()


def plans(c, run):
    seen: list[str] = []
    c.set_trace_callback(seen.append)
    try:
        run()
    finally:
        c.set_trace_callback(None)
    return {
        sql: [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql)]
        for sql in dict.fromkeys(seen)
        if sql.lstrip().upper().startswith("SELECT")
    }


def scans(found):
    return {
        sql: plan for sql, plan in found.items() if any(p.startswith(tuple(f"SCAN {t}" for t in WATCHED)) for p in plan)
    }


def test_effective_grant_seeks_the_grant_and_its_bot_by_key(c):
    found = plans(c, lambda: credentials.effective_grant(c, "key", "bot:books"))
    [plan] = found.values()
    assert any(p.startswith("SEARCH g USING INDEX credential_grant_active") for p in plan), plan
    assert any(p.startswith("SEARCH bc USING") and "(bot=?)" in p for p in plan), plan
    assert not scans(found), found


@pytest.mark.parametrize(
    "who",
    [
        Identity("human:ben", "human", email="ben@acme.example"),
        Identity("human:cara", "human", email="cara@acme.example"),
        Identity("human:ana", "owner", email="ana@acme.example"),
        Identity("bot:books", "bot"),
        Identity("bot:other", "bot"),
    ],
    ids=["person-granted", "person-not", "owner", "bot-granted", "bot-not"],
)
def test_usable_reads_only_this_credentials_rows(c, who):
    found = plans(c, lambda: credentials.usable(c, who, "key", ("ana@acme.example",)))
    assert found, "usable ran no statement"
    assert not scans(found), scans(found)
