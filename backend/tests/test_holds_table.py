"""The holds table (backend/holds.py): one writer that keeps one open hold of a kind on a thing, a release that keeps it
as history, the one backoff and probe, and migrations 20261117061834_holds_runaway and 20261117072000_holds_failing,
which move every bot_budget row (run budgets) and every run_backoff row (failed runs) into it."""

from backend import general_manager, holds, invariants, maintenance_health, run_failures, turn_budgets
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
        common = {"kind": "runaway", "holdable_type": "bot", "created_by_id": "keeper"}
        assert by_id["bot_budget:open:ops"] == {
            **common,
            "holdable_id": "ops",
            "bot_id": "ops",
            "reason": "Tico stopped ops: 300 runs in the last hour. This looks like a runaway loop, not normal work. "
            "Check its last runs, then release it or raise its budget.",
            "details": '{"window":"hour","runs":300,"budget":300}',
            "upkeep_task_id": "task-1",
            "created_at": HELD,
            "released_at": None,
            "released_by_id": None,
            "released_how": None,
        }
        assert by_id["bot_budget:released:cpo"] == {
            **common,
            "holdable_id": "cpo",
            "bot_id": "cpo",
            "reason": "",
            "details": '{"window":"day","runs":2600,"budget":2500}',
            "upkeep_task_id": None,
            "created_at": EARLIER,
            "released_at": EARLIER,
            "released_by_id": "human:ana",
            "released_how": "person",
        }
        assert by_id["bot_budget:released:finance"]["details"] == "{}"  # its numbers are the hold open now
        assert by_id["bot_budget:released:finance"]["released_how"] == "botops"
        assert by_id["bot_budget:open:finance"]["released_at"] is None
        assert set(by_id) == {
            "bot_budget:open:ops",
            "bot_budget:released:cpo",
            "bot_budget:open:finance",
            "bot_budget:released:finance",
        }

        # The run budget reads the moved holds as it read the rows.
        assert turn_budgets.get(c, "ops")["held_at"] == HELD and turn_budgets.get(c, "cpo") == {}
        assert turn_budgets.words(c, "ops").startswith("Tico stopped ops: 300 runs in the last hour.")
        assert [hold["bot"] for hold in turn_budgets.held(c)] == ["finance", "ops"]
        assert {f["key"] for f in maintenance_health._stop_checks(c)} >= {"turn_budget:ops", "turn_budget:finance"}
        said = [item["why"] for item in general_manager._held(c)]
        assert any("ops is held by its turn budget" in why and "300 runs in the last hour" in why for why in said)


def test_a_bot_named_like_another_bots_released_row_keeps_every_row(api):
    with api.app.state.store.transaction() as c:
        c.execute(OLD)
        c.executemany(
            "INSERT INTO bot_budget(bot,held_at,window,runs,budget,released_at,released_by) VALUES(?,?,?,?,?,?,?)",
            [
                ("ops", HELD, "hour", 300, 300, EARLIER, "human:ana"),  # held, and released before
                ("ops-released", HELD, "day", 2600, 2500, None, None),  # its open hold's id once matched ops's release
            ],
        )
        _migrate(c)
        carried = {(r["holdable_id"], r["released_at"] is None) for r in _rows(c)}
        assert carried == {("ops", True), ("ops", False), ("ops-released", True)}


def test_put_keeps_one_open_hold_and_release_keeps_it_as_history(api):
    with api.app.state.store.transaction() as c:
        assert holds.release(c, holds.RUNAWAY, "bot", "ops", actor="human:ana", how="person") is None
        first = holds.put(c, holds.RUNAWAY, "bot", "ops", actor=H.KEEPER, details={"runs": 300})
        assert first["bot_id"] == "ops"  # a bot's own hold names it: every open hold of a bot is one read (holds_bot)
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


FAILING = DIRECTORY / "20261117072000_holds_failing.sql"
OLD_FAILING = (
    "CREATE TABLE IF NOT EXISTS run_backoff(bot TEXT PRIMARY KEY, fails INTEGER NOT NULL DEFAULT 0, wait_until TEXT, "
    "error_class TEXT, error TEXT, last_failed_at TEXT, held_at TEXT, task_id TEXT, released_at TEXT, released_by TEXT)"
)
LATER = "2026-10-24T12:30:00.000000Z"


def _migrate_failing(c, rows):
    c.execute(OLD_FAILING)  # an older database's table, as a release before this one left it
    c.executemany(
        "INSERT INTO run_backoff(bot,fails,wait_until,error_class,error,last_failed_at,held_at,task_id,released_at,"
        "released_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    for statement in statements(FAILING.read_text()):
        c.execute(statement)


def _failing_rows(c):
    return {
        r["id"]: dict(r)
        for r in c.execute(
            "SELECT id,holdable_id,bot_id,reason,details,streak,until_at,probe_at,upkeep_task_id,created_at,released_at,"
            "released_by_id,released_how FROM holds WHERE kind='failing' AND holdable_type='bot'"
        )
    }


def test_every_run_backoff_row_moves_open_and_released_and_running_it_again_changes_nothing(api, monkeypatch):
    monkeypatch.setattr(H, "now", lambda: "2026-10-24T12:01:00.000000Z")
    with api.app.state.store.transaction() as c:
        _migrate_failing(
            c,
            [
                ("ops", 5, LATER, "sign_in", "401 unauthorized", HELD, HELD, "task-1", None, None),  # held now
                ("cpo", 2, LATER, "other", "boom", HELD, None, None, None, None),  # waits after two failures
                ("finance", 0, None, "other", "boom", EARLIER, None, None, EARLIER, "human:ana"),  # released
                ("inbox", 6, None, None, None, HELD, HELD, None, EARLIER, "bot:botops"),  # held again, no next try kept
                ("assistant", 0, None, "other", "boom", EARLIER, None, None, None, None),  # ran normally since
                ("gone", 1, LATER, "other", "boom", HELD, None, None, None, None),  # a bot since removed
            ],
        )
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='run_backoff'").fetchone()
        moved = _failing_rows(c)
        _migrate_failing(c, [])  # again: the older table comes back empty and goes
        assert _failing_rows(c) == moved

        assert set(moved) == {
            "run_backoff:open:ops",
            "run_backoff:open:cpo",
            "run_backoff:released:finance",
            "run_backoff:open:inbox",
            "run_backoff:released:inbox",
            "run_backoff:open:gone",
        }
        ops = moved["run_backoff:open:ops"]
        assert ops["reason"] == (
            "Tico stopped ops: its last 5 runs failed within a minute (the AI sign-in was refused). Tico tries one run "
            "on its own at growing intervals and releases it when one completes. Check its last runs, or release it now."
        )
        assert (ops["bot_id"], ops["streak"], ops["until_at"], ops["probe_at"], ops["upkeep_task_id"]) == (
            "ops",
            5,
            None,
            LATER,
            "task-1",
        )
        cpo = moved["run_backoff:open:cpo"]
        assert (cpo["reason"], cpo["streak"], cpo["until_at"], cpo["probe_at"]) == ("", 2, LATER, None)
        assert moved["run_backoff:open:inbox"]["probe_at"] == "2026-10-24T12:30:00.000000Z"  # 30 minutes after the hold
        assert moved["run_backoff:open:gone"]["bot_id"] is None
        assert (
            moved["run_backoff:released:finance"]["released_by_id"],
            moved["run_backoff:released:finance"]["released_how"],
        ) == ("human:ana", "person")
        assert moved["run_backoff:released:inbox"]["released_how"] == "botops"

        # The pages, Health and the claim read the moved holds as they read the rows.
        held = run_failures.get(c, "ops")
        assert held["details"] == {
            "error_class": "sign_in",
            "error": "401 unauthorized",
            "last_failed_at": HELD,
            "held_at": HELD,
        }
        assert holds.why(c, "ops") == holds.RUN_HOLD and run_failures.words(c, "ops") == ops["reason"]
        assert holds.why(c, "cpo") == holds.BACKOFF and "cpo is pausing 1740 s" in run_failures.words(c, "cpo")
        assert holds.why(c, "finance") is None and run_failures.get(c, "assistant") == {}
        assert [hold["holdable_id"] for hold in run_failures.held(c)] == ["inbox", "ops"]
        assert {f["key"] for f in maintenance_health._stop_checks(c)} >= {"run_hold:ops", "run_hold:inbox"}
        said = [item["why"] for item in general_manager._held(c)]
        assert any("ops is held after five fast failures in a row (sign_in)" in why for why in said)
        assert invariants.sign_in_held(c, None) == ["ops"]
        assert turn_budgets.view(c, "ops", {"config": "{}", "revision": 1})["run_hold"] == {
            "fails": 5,
            "error_class": "sign_in",
            "since": HELD,
            "next_try": LATER,
            "words": ops["reason"],
        }


def test_ids_name_the_rows_kind_before_the_bot_so_no_two_bots_rows_share_one(api):
    with api.app.state.store.transaction() as c:
        _migrate_failing(
            c,
            [
                ("ops", 5, LATER, "other", "boom", HELD, HELD, None, EARLIER, "human:ana"),  # held, and released before
                ("released:ops", 1, LATER, "other", "boom", HELD, None, None, None, None),
                ("open:ops", 0, None, "other", "boom", HELD, None, None, EARLIER, "human:ana"),
            ],
        )
        carried = {(r["holdable_id"], r["released_at"] is None) for r in _failing_rows(c).values()}
        assert carried == {("ops", True), ("ops", False), ("released:ops", True), ("open:ops", False)}


def test_the_one_backoff_doubles_to_its_cap_and_a_held_kind_probes_at_its_own_gap():
    assert [holds.wait(holds.FAILING, n) for n in range(1, 5)] == [15, 30, 60, 120]
    assert [holds.wait(holds.FAILING, n) for n in range(5, 12)] == [1800, 3600, 7200, 14400, 28800, 28800, 28800]


def test_a_probe_is_taken_once_and_never_while_a_run_of_the_bot_is_out(api, monkeypatch):
    monkeypatch.setattr(H, "now", lambda: LATER)
    with api.app.state.store.transaction() as c:
        hold = holds.put(c, holds.FAILING, "bot", "ops", actor=H.KEEPER, streak=5, probe_at=HELD)
        c.execute("INSERT INTO attempts(id,bot_id,state,created_at) VALUES('a-out','ops','running',?)", (HELD,))
        assert not holds.take_probe(c, hold, "job-1")  # a run of the bot is still out
        c.execute("DELETE FROM attempts WHERE id='a-out'")
        assert holds.take_probe(c, hold, "job-1")
        taken = holds.get(c, holds.FAILING, "bot", "ops")
        assert (taken["probe_job_id"], taken["probe_at"]) == ("job-1", H.shift(LATER, seconds=3600))
        assert not holds.take_probe(c, taken, "job-2")  # the next try is an hour out
