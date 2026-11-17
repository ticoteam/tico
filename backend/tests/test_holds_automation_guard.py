"""Migration 20261117112358_holds_automation_guard, on a database an older server left: each automation_guard_holds row
becomes a `runaway` hold on its automation, open while it held and released at its cleared_at otherwise, and the table
goes. Running it again changes nothing. The run guard then reads the moved hold as before: its held runs stay held, a
release queues them, and its last release is where the guard counts from."""

import json

from backend import automations, holds
from backend.migrations import DIRECTORY, statements
from backend.tests.helpers.api import post  # noqa: F401  (the api fixture)
from backend.tests.helpers.automation_runs import make, queued, sql

MIGRATION = DIRECTORY / "20261117112358_holds_automation_guard.sql"
HELD = "2026-11-16T12:00:00.000000Z"
CLEARED = "2026-11-16T13:00:00.000000Z"
OLD = (
    "CREATE TABLE automation_guard_holds(automation_id TEXT PRIMARY KEY, rule TEXT NOT NULL, held_at TEXT NOT NULL, "
    "arrivals INTEGER NOT NULL DEFAULT 0, dropped INTEGER NOT NULL DEFAULT 0, task_id TEXT, cleared_at TEXT)"
)
COLUMNS = (
    "id,kind,holdable_type,holdable_id,bot_id,reason,details,upkeep_task_id,created_by_id,created_at,updated_at,"
    "released_at,released_by_id,released_how"
)
STEPS = [{"id": "tell", "type": "action", "do": "wake_bot", "args": {"bot": "ops", "text": "Hook"}}]


def _migrate(c):
    for statement in statements(MIGRATION.read_text()):
        c.execute(statement)


def _rows(c):
    return {
        r["holdable_id"]: dict(r)
        for r in c.execute(f"SELECT {COLUMNS} FROM holds WHERE holdable_type='automation' ORDER BY id")
    }


def _event(c, id, action, target, actor, at):
    c.execute(
        "INSERT INTO events(id,created_at,actor,action,target,detail) VALUES(?,?,?,?,?,'{}')",
        (id, at, actor, action, target),
    )


def test_guard_holds_move_into_holds_and_running_it_again_changes_nothing(api):
    held, by_bot, by_nobody = (make(api, STEPS) for _ in range(3))
    runs = [queued(api, held, {"n": n}) for n in range(2)]
    with api.app.state.store.transaction() as c:
        c.execute(OLD)  # the table as a release before this one left it
        c.executemany(
            "INSERT INTO automation_guard_holds(automation_id,rule,held_at,arrivals,dropped,task_id,cleared_at) "
            "VALUES(?,?,?,?,?,?,?)",
            [
                (held, "flood", HELD, 3, 1, "task-1", None),  # holding now
                (by_bot, "sustained", HELD, 0, 0, None, CLEARED),  # a bot dropped what it held
                (by_nobody, "flood", HELD, 2, 0, "task-2", CLEARED),  # cleared, no event on record
            ],
        )
        c.execute("UPDATE automation_runs SET status='held' WHERE automation_id=?", (held,))
        _event(c, "e1", "automation.hold_released", by_bot, "human:ana", "2026-11-16T12:30:00.000000Z")
        _event(c, "e2", "automation.hold_dropped", by_bot, "bot:ops", CLEARED)  # the later one

        _migrate(c)
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='automation_guard_holds'").fetchone()
        moved = _rows(c)
        _migrate(c)  # again: the older table comes back empty and goes
        assert _rows(c) == moved

        common = {"kind": "runaway", "holdable_type": "automation", "bot_id": None, "created_by_id": "keeper"}
        assert moved[held] == {
            **common,
            "id": f"runaway:automation:{held}",
            "holdable_id": held,
            "reason": "flood",
            "details": json.dumps({"arrivals": 3, "dropped": 1}, separators=(",", ":")),
            "upkeep_task_id": "task-1",
            "created_at": HELD,
            "updated_at": HELD,
            "released_at": None,
            "released_by_id": None,
            "released_how": None,
        }
        released = {k: moved[by_bot][k] for k in ("reason", "released_at", "released_by_id", "released_how")}
        assert released == {
            "reason": "sustained",
            "released_at": CLEARED,
            "released_by_id": "bot:ops",
            "released_how": "botops",
        }
        assert (moved[by_nobody]["released_by_id"], moved[by_nobody]["released_how"]) == ("keeper", "person")

        # The guard reads them as before: the open one holds its runs, each cleared one is its count's floor.
        assert automations.hold_of(c, held) == {
            "rule": "flood",
            "since": HELD,
            "held": 2,
            "dropped": 1,
            "task": "task-1",
        }
        assert automations.hold_of(c, by_bot) is None
        assert c.execute(automations.LAST_RELEASE_SQL, (by_bot,)).fetchone()[0] == CLEARED
        assert automations._unhold(c, held, "human:ana", "release") == 2
        after = holds.get(c, holds.RUNAWAY, "automation", held)
        assert after is None
    assert {r["status"] for r in sql(api, "SELECT status FROM automation_runs WHERE id IN (?,?)", tuple(runs))} == {
        "queued"
    }
    released = sql(api, "SELECT released_by_id,released_how FROM holds WHERE id=?", (f"runaway:automation:{held}",))
    assert [dict(r) for r in released] == [{"released_by_id": "human:ana", "released_how": "person"}]


def test_a_hold_turned_off_with_its_automation_ends_moot_and_the_next_one_is_a_new_row(api):
    aid = make(api, STEPS)
    with api.app.state.store.transaction() as c:
        automations._hold(c, dict(c.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone()), "flood")
        assert automations._unhold(c, aid, "human:ana", "drop", moot=True) == 0
        automations._hold(c, dict(c.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone()), "sustained")
        rows = [
            dict(r)
            for r in c.execute(
                "SELECT reason,released_how FROM holds WHERE holdable_type='automation' AND holdable_id=? "
                "ORDER BY released_at IS NULL",
                (aid,),
            )
        ]
    assert rows == [{"reason": "flood", "released_how": "moot"}, {"reason": "sustained", "released_how": None}]
