"""The roster's whole-table reads shared between snapshots (backend/roster_clock.py): read once per change, and never
older or newer than the reader's own snapshot."""
from backend import read_cache
from backend.store import H
from backend.tests.test_api import api, post  # noqa: F401  (fixture)

CONFIGS = "SELECT * FROM bot_config"
STEPS = "SELECT * FROM task_steps ORDER BY position,id"


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
    assert found == direct          # identical to reading the table in the same snapshot
    return found, len(ran) - 1      # less the direct read


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
    assert ran == 1 and snapshot_rows(store, "SELECT * FROM bots ORDER BY slug") == (bots, 0)
    with store.transaction() as c:
        c.execute("UPDATE bots SET display_name='Renamed' WHERE slug=?", (bots[0]["slug"],))
    renamed, ran = snapshot_rows(store, "SELECT * FROM bots ORDER BY slug")
    assert ran == 1 and renamed[0]["display_name"] == "Renamed"
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
        old.execute("SELECT 1 FROM bots").fetchone()          # the snapshot starts here
        with store.transaction() as c:
            c.execute("UPDATE bot_config SET reports_to='human:new' WHERE bot=?", (bot,))
        # A newer snapshot reads, and shares, the new rows first.
        assert {r["bot"]: r["reports_to"] for r in snapshot_rows(store)[0]}[bot] == "human:new"
        assert {r["bot"]: r["reports_to"] for r in read_cache.rows(old, CONFIGS)}[bot] != "human:new"
        old.commit()


def test_reads_outside_a_transaction_share_the_roster_too(api):
    """Most request reads run in a plain `store.read()`, not a read transaction: they must share the roster as well."""
    store = api.app.state.store
    snapshot_rows(store)                                          # the roster is read once and published
    ran = []
    with store.read() as c:
        c.set_trace_callback(lambda sql: ran.append(sql) if sql == CONFIGS else None)
        first = [dict(r) for r in read_cache.configs(c)]
        second = [dict(r) for r in read_cache.configs(c)]
        c.set_trace_callback(None)
        assert not c.in_transaction
    assert first == second and ran == []


def test_task_steps_are_shared_until_a_step_changes(api):
    store = api.app.state.store
    found, ran = snapshot_rows(store, STEPS)
    assert found and ran == 1
    assert snapshot_rows(store, STEPS) == (found, 0)
    with store.transaction() as c:
        c.execute("UPDATE task_steps SET name='Renamed step' WHERE id=?", (found[0]["id"],))
    changed, ran = snapshot_rows(store, STEPS)
    assert ran == 1 and changed[0]["name"] == "Renamed step"
    assert snapshot_rows(store, STEPS) == (changed, 0)


def test_type_list_reads_task_steps_from_the_shared_rows(api):
    store = api.app.state.store
    ran = []
    with store.transaction() as c:
        c.execute("UPDATE task_steps SET name='Listed step' WHERE id=(SELECT id FROM task_steps ORDER BY position,id LIMIT 1)")
    with store.read() as c:
        c.set_trace_callback(lambda sql: ran.append(sql) if sql == STEPS else None)
        listed = H.type_list(c)
        H.type_list(c)
        c.set_trace_callback(None)
    assert any(s["name"] == "Listed step" for t in listed for s in t["steps"])
    assert len(ran) <= 1
    ran.clear()
    with store.read() as c:
        c.set_trace_callback(lambda sql: ran.append(sql) if sql == STEPS else None)
        H.type_list(c)
        c.set_trace_callback(None)
    assert ran == []


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
    with store.read() as c:                     # a caller that changes its copy changes nothing shared
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
        return (c.execute("SELECT value FROM roster_clock WHERE id=1").fetchone()[0],
                [tuple(r) for r in c.execute("SELECT name,value FROM row_clock ORDER BY name")],
                c.execute("SELECT value FROM provenance_clock WHERE id=1").fetchone()[0])


def test_a_restart_that_changes_nothing_leaves_the_change_counters_where_they_were(api):
    """Every start rewrites what it checks (task step positions, the shared rows' keys), and a rewrite that changes no
    value must not move the roster, row or provenance counters: each move throws away what the caches built on it.
    The first restart applies what the seeded rows still need; the second must change nothing."""
    from fastapi.testclient import TestClient
    from backend.app import create_app
    post(api, "tasks", {"owner_id": "ops", "title": "Restart probe", "body": "Nothing changes on a restart."})
    post(api, "tasks", {"owner_id": "cpo", "title": "Private restart probe", "body": "Nor does this.", "private": True})
    settings = api.app.state.store.settings
    with TestClient(create_app(settings)):
        pass
    before = _counters(api.app.state.store)
    with TestClient(create_app(settings)):
        pass
    assert _counters(api.app.state.store) == before
