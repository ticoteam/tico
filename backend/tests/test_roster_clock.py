"""The roster's whole-table reads shared between snapshots (backend/roster_clock.py): read once per change, and never
older or newer than the reader's own snapshot."""

from backend import read_cache
from backend.store import H
from backend.tests.helpers.api import post  # noqa: F401  (fixture)
from backend import bot_settings as BS

CONFIGS = BS.ROW_SQL


def snapshot_rows(store, statement=CONFIGS):
    """The statement's rows in a fresh read snapshot, and how many times SQLite ran it."""
    ran = []
    with store.read() as c:
        c.set_trace_callback(lambda sql: ran.append(sql) if sql == statement else None)
        c.execute("BEGIN")
        found = [dict(r) for r in read_cache.rows(c, statement)]
        direct = [dict(r) for r in c.execute(statement)]
        c.set_trace_callback(None)
        c.commit()
    assert found == direct  # identical to reading the table in the same snapshot
    return found, len(ran) - 1  # less the direct read


def test_later_snapshots_share_the_rows_until_a_roster_table_changes(api):
    store = api.app.state.store
    first, ran = snapshot_rows(store)
    assert first and ran == 1
    assert snapshot_rows(store) == (first, 0)
    with store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot=?", (first[0]["bot"],))
    changed, ran = snapshot_rows(store)
    assert ran == 1 and changed[0]["reports_to"] == "human:ben"
    bots, ran = snapshot_rows(store, "SELECT * FROM bots ORDER BY slug")
    assert ran == 1 and snapshot_rows(store, "SELECT * FROM bots ORDER BY slug") == (bots, 1)  # never shared
    assert snapshot_rows(store) == (changed, 0)
    with store.transaction() as c:
        c.execute("UPDATE bots SET display_name='Renamed' WHERE slug=?", (bots[0]["slug"],))
    renamed, ran = snapshot_rows(store, "SELECT * FROM bots ORDER BY slug")
    assert ran == 1 and renamed[0]["display_name"] == "Renamed"
    assert snapshot_rows(store)[1] == 1  # a bots column other than last_turn_at moves the clock
    with store.read() as c:
        assert H.bots(c)[0]["display_name"] == "Renamed"


def test_an_uncommitted_change_is_never_shared_even_when_a_later_write_reuses_its_count(api):
    """A write rolled back leaves the count where it was; the next write reaches the count the rolled-back one had."""
    store = api.app.state.store
    bot = snapshot_rows(store)[0][0]["bot"]
    with store.read() as c:
        c.execute("BEGIN IMMEDIATE")
        c.write_transaction = True
        c.execute("UPDATE bot_config SET reports_to='human:gone' WHERE bot=?", (bot,))
        mine = {r["bot"]: r["reports_to"] for r in read_cache.configs(c)}
        assert mine[bot] == "human:gone"
        c.rollback()
    with store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:kept' WHERE bot=?", (bot,))
    assert {r["bot"]: r["reports_to"] for r in snapshot_rows(store)[0]}[bot] == "human:kept"


def test_a_reader_in_an_older_snapshot_keeps_its_own_rows(api):
    store = api.app.state.store
    bot = snapshot_rows(store)[0][0]["bot"]
    with store.read() as old:
        old.execute("BEGIN")
        old.execute("SELECT 1 FROM bots").fetchone()  # the snapshot starts here
        with store.transaction() as c:
            c.execute("UPDATE bot_config SET reports_to='human:new' WHERE bot=?", (bot,))
        # A newer snapshot reads, and shares, the new rows first.
        assert {r["bot"]: r["reports_to"] for r in snapshot_rows(store)[0]}[bot] == "human:new"
        assert {r["bot"]: r["reports_to"] for r in read_cache.rows(old, CONFIGS)}[bot] != "human:new"
        old.commit()


def test_entries_are_built_once_across_snapshots_and_rebuilt_after_a_bot_config_write(api, monkeypatch):
    """The entries every bot list asks for are built from bot_config alone, so later snapshots share the built value."""
    from backend import views

    store = api.app.state.store
    built, real = [], views._entries
    monkeypatch.setattr(views, "_entries", lambda c, owner="": built.append(owner) or real(c, owner))

    def read_entries():
        with store.read() as c:
            c.execute("BEGIN")
            found = views.entries(c, shared=True)
            c.commit()
        return found

    slug = snapshot_rows(store)[0][0]["bot"]
    first = read_entries()
    assert read_entries() is first and len(built) == 1
    with store.read() as c:  # a caller that changes its copy changes nothing shared
        mine = views.entries(c)
        mine[slug]["repo"] = "changed-by-a-caller"
        assert views.entries(c)[slug]["repo"] != "changed-by-a-caller"
    with store.transaction() as c:
        c.execute("UPDATE bot_config SET repo=? WHERE bot=?", ("emp-renamed", slug))
    third = read_entries()
    assert len(built) == 2 and third[slug]["repo"] == "emp-renamed"
    assert read_entries() is third and len(built) == 2


def _counters(store):
    with store.read() as c:
        return (
            c.execute("SELECT value FROM roster_clock WHERE id=1").fetchone()[0],
            [tuple(r) for r in c.execute("SELECT name,value FROM row_clock ORDER BY name")],
            c.execute("SELECT value FROM provenance_clock WHERE id=1").fetchone()[0],
        )


def test_a_restart_that_changes_nothing_leaves_the_change_counters_where_they_were(api):
    """Every start rewrites what it checks (task step positions, the shared rows' keys), and a rewrite that changes no
    value must not move the roster, row or provenance counters: each move throws away what the caches built on it.
    The first restart applies what the seeded rows still need; the second must change nothing."""
    from fastapi.testclient import TestClient
    from backend.app import create_app

    post(api, "tasks", {"owner": "ops", "title": "Restart probe", "body": "Nothing changes on a restart."})
    post(api, "tasks", {"owner": "cpo", "title": "Private restart probe", "body": "Nor does this.", "private": True})
    settings = api.app.state.store.settings
    with TestClient(create_app(settings)):
        pass
    before = _counters(api.app.state.store)
    with TestClient(create_app(settings)):
        pass
    assert _counters(api.app.state.store) == before


def test_a_finished_turn_keeps_the_shared_rows_and_every_reader_sees_its_time(api):
    """A turn's `bots.last_turn_at` write moves no clock, so the shared settings and entries are not read or built again
    after it, and the bot list (read in each snapshot) shows the new time; any other `bots` column still moves the clock. The trigger names every column but
    `last_turn_at`, so a column added later moves the clock too or this fails."""
    from backend import hubdb, roster_clock

    store = api.app.state.store
    with store.read() as c:
        columns = {r["name"] for r in c.execute("PRAGMA table_info(bots)")}
        trigger = c.execute("SELECT sql FROM sqlite_master WHERE name='roster_clock_bots_update'").fetchone()[0]
    assert set(roster_clock.BOTS_CLOCKED) == columns - {"last_turn_at"}
    assert ", ".join(roster_clock.BOTS_CLOCKED) in trigger
    snapshot_rows(store)
    bots, _ = snapshot_rows(store, "SELECT * FROM bots ORDER BY slug")
    slug, before = bots[0]["id"], _counters(store)[0]
    entries = []
    with store.read() as c:
        from backend import views

        entries.append(views.entries(c, shared=True))
    with store.transaction() as c:
        row = hubdb.turn_start(c, "bot:" + slug, slug, trigger="message", trigger_kind="message")
        hubdb.turn_finish(c, "bot:" + slug, row["id"], "ok", "done")
        finished = c.execute("SELECT last_turn_at FROM bots WHERE id=?", (slug,)).fetchone()[0]
    assert finished and _counters(store)[0] == before
    assert snapshot_rows(store)[1] == 0
    with store.read() as c:
        assert views.entries(c, shared=True) is entries[0]
        listed = {b["id"]: b for b in H.bots(c)}
        assert listed[slug]["last_turn_at"] == finished
        assert listed == {r["id"]: dict(r) for r in c.execute("SELECT * FROM bots")}
    with store.transaction() as c:
        c.execute("UPDATE bots SET effort='high' WHERE id=?", (slug,))
    assert _counters(store)[0] == before + 1


def test_manages_reads_the_snapshot_values_and_answers_as_copies_did(api):
    """Auth.manages hands people.manages the snapshot's roster and entries as they are: it answers as it did with fresh
    copies, for every bot and person, and leaves the shared values unchanged."""
    import copy

    from backend import people as P
    from backend import views
    from backend.auth import Identity

    store, auth = api.app.state.store, api.app.state.auth
    with store.transaction() as c:
        slugs = [b["id"] for b in H.bots(c)]
        for n, slug in enumerate(slugs):
            c.execute(
                "UPDATE bot_config SET owner_id=?, reports_to=? WHERE bot=?",
                ("ana", ("human:cara" if n == 0 else slugs[n - 1]) if n % 3 else None, slug),
            )
        if len(slugs) > 2:
            c.execute("UPDATE bots SET state='archived' WHERE id=?", (slugs[2],))
    with store.read_transaction() as c:
        roster, entries = copy.deepcopy(views.roster(c, shared=True)), copy.deepcopy(views.entries(c, shared=True))
        archived = {b["id"] for b in H.bots(c) if b.get("state") == "archived"}
        for pid in ("cara", "ben", "ana"):
            who = Identity("human:" + pid, "human")
            for kind, ident in [("bot", s) for s in slugs] + [("person", p["id"]) for p in roster["people"]]:
                want = P.manages(pid, kind, ident, copy.deepcopy(roster), copy.deepcopy(entries), archived, True)
                assert auth.manages(c, who, kind, ident) == want, (pid, kind, ident)
        assert views.roster(c, shared=True) == roster and views.entries(c, shared=True) == entries
