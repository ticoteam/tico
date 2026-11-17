"""The holds table (backend/holds.py): one writer that keeps one open hold of a kind on a thing, a release that keeps it
as history, and migration 20261117061834_holds_runaway, which moves every bot_budget row (run budgets) into it."""

from backend import general_manager, holds, maintenance_health, turn_budgets
from backend.migrations import DIRECTORY, statements
from backend.store import H
from backend.tests.helpers.api import get  # noqa: F401  (the api fixture)

MIGRATION = DIRECTORY / "20261117061834_holds_runaway.sql"
HELD = "2026-10-24T12:00:00.000000Z"
EARLIER = "2026-10-23T09:00:00.000000Z"
OLD = (
    "CREATE TABLE IF NOT EXISTS bot_budget(bot TEXT PRIMARY KEY, held_at TEXT, window TEXT, runs INTEGER, "
    "budget INTEGER, task_id TEXT, released_at TEXT, released_by TEXT)"
)


def _migrate(c):
    for statement in statements(MIGRATION.read_text()):
        c.execute(statement)


def _rows(c):
    return [
        dict(r)
        for r in c.execute(
            "SELECT id,kind,holdable_type,holdable_id,bot_id,reason,details,upkeep_task_id,created_by_id,created_at,"
            "released_at,released_by_id,released_how FROM holds ORDER BY id"
        )
    ]


def test_every_bot_budget_row_moves_open_and_released_and_running_it_again_changes_nothing(api):
    with api.app.state.store.transaction() as c:
        c.execute(OLD)  # an older database's table, as a release before this one left it
        c.executemany(
            "INSERT INTO bot_budget(bot,held_at,window,runs,budget,task_id,released_at,released_by) "
            "VALUES(?,?,?,?,?,?,?,?)",
            [
                ("ops", HELD, "hour", 300, 300, "task-1", None, None),  # held now
                ("cpo", None, "day", 2600, 2500, None, EARLIER, "human:ana"),  # released by a person
                ("finance", HELD, "hour", 310, 300, None, EARLIER, "bot:botops"),  # held again after a release
            ],
        )
        _migrate(c)
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='bot_budget'").fetchone()
        moved = _rows(c)
        _migrate(c)  # again: the older table comes back empty and goes
        assert _rows(c) == moved
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='bot_budget'").fetchone()

        by_id = {row.pop("id"): row for row in moved}
        common = {"kind": "runaway", "holdable_type": "bot", "bot_id": None, "created_by_id": "keeper"}
        assert by_id["bot_budget-ops"] == {
            **common,
            "holdable_id": "ops",
            "reason": "Tico stopped ops: 300 runs in the last hour. This looks like a runaway loop, not normal work. "
            "Check its last runs, then release it or raise its budget.",
            "details": '{"window":"hour","runs":300,"budget":300}',
            "upkeep_task_id": "task-1",
            "created_at": HELD,
            "released_at": None,
            "released_by_id": None,
            "released_how": None,
        }
        assert by_id["bot_budget-cpo-released"] == {
            **common,
            "holdable_id": "cpo",
            "reason": "",
            "details": '{"window":"day","runs":2600,"budget":2500}',
            "upkeep_task_id": None,
            "created_at": EARLIER,
            "released_at": EARLIER,
            "released_by_id": "human:ana",
            "released_how": "person",
        }
        assert by_id["bot_budget-finance-released"]["details"] == "{}"  # its numbers are the hold open now
        assert by_id["bot_budget-finance-released"]["released_how"] == "botops"
        assert by_id["bot_budget-finance"]["released_at"] is None
        assert set(by_id) == {
            "bot_budget-ops",
            "bot_budget-cpo-released",
            "bot_budget-finance",
            "bot_budget-finance-released",
        }

        # The run budget reads the moved holds as it read the rows.
        assert turn_budgets.get(c, "ops")["held_at"] == HELD and turn_budgets.get(c, "cpo") == {}
        assert turn_budgets.words(c, "ops").startswith("Tico stopped ops: 300 runs in the last hour.")
        assert [hold["bot"] for hold in turn_budgets.held(c)] == ["finance", "ops"]
        assert {f["key"] for f in maintenance_health._stop_checks(c)} >= {"turn_budget:ops", "turn_budget:finance"}
        said = [item["why"] for item in general_manager._held(c)]
        assert any("ops is held by its turn budget" in why and "300 runs in the last hour" in why for why in said)


def test_put_keeps_one_open_hold_and_release_keeps_it_as_history(api):
    with api.app.state.store.transaction() as c:
        assert holds.release(c, holds.RUNAWAY, "bot", "ops", actor="human:ana", how="person") is None
        first = holds.put(c, holds.RUNAWAY, "bot", "ops", actor=H.KEEPER, details={"runs": 300})
        again = holds.put(c, holds.RUNAWAY, "bot", "ops", actor=H.KEEPER, reason="Again.", details={"runs": 301})
        assert again["id"] == first["id"] and again["reason"] == "Again." and again["details"] == '{"runs": 301}'
        # The same kind on one bot's task is another hold.
        other = holds.put(c, holds.RUNAWAY, "task", "task-1", actor=H.KEEPER, bot_id="ops")
        assert other["id"] != first["id"]
        assert holds.get(c, holds.RUNAWAY, "task", "task-1", "ops")["id"] == other["id"]
        ended = holds.release(c, holds.RUNAWAY, "bot", "ops", actor="human:ana", how="person")
        assert ended and ended["id"] == first["id"] and ended["released_how"] == "person"
        assert holds.get(c, holds.RUNAWAY, "bot", "ops") is None
        assert holds.put(c, holds.RUNAWAY, "bot", "ops", actor=H.KEEPER)["id"] != first["id"]  # a new hold
        kept = c.execute("SELECT count(*) FROM holds WHERE holdable_id='ops' AND released_at IS NOT NULL").fetchone()
        assert kept[0] == 1


def test_a_budget_raised_above_the_count_releases_the_hold_as_raised(api):
    with api.app.state.store.transaction() as c:
        holds.put(c, holds.RUNAWAY, "bot", "ops", actor=H.KEEPER, details={"window": "hour", "runs": 3, "budget": 3})
        assert turn_budgets._release_if_room(c, "human:ana", "ops", {"turns_per_hour": 50})
        how = c.execute("SELECT released_how, released_by_id FROM holds WHERE holdable_id='ops'").fetchone()
        assert tuple(how) == ("raised", "human:ana")
        assert turn_budgets.release(c, "bot:botops", "ops") is False  # nothing open now
