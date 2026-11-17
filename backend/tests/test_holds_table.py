"""The holds table (backend/holds.py): one writer that keeps one open hold of a kind on a thing, a release that keeps it
as history, and the migrations that move each kind into it: 20261117061834_holds_runaway (bot_budget, run budgets) and
20261117073216_holds_paused (the company's pause and each paused bot)."""

from backend import company_pause, general_manager, holds, maintenance_health, turn_budgets
from backend.migrations import DIRECTORY, statements
from backend.store import H
from backend.tests.helpers.api import get, post, setup_attempt  # noqa: F401  (the api fixture)

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
                ("finance", HELD, "hour", 310, 300, None, EARLIER, "bot:ops"),  # held again after a release
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
        assert turn_budgets.release(c, "bot:ops", "ops") is False  # nothing open now


PAUSES = DIRECTORY / "20261117073216_holds_paused.sql"


def _pauses(c):
    return {
        r["id"]: (r["holdable_type"], r["holdable_id"], r["bot_id"], r["created_by_id"], r["created_at"])
        for r in c.execute("SELECT * FROM holds WHERE kind='paused' ORDER BY id")
    }


def test_the_company_pause_and_each_paused_bot_move_to_open_paused_holds_and_running_it_again_changes_nothing(api):
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO registry_metadata(key,value) VALUES('company_pause',?)",
            ('{"paused_by": "human:ben", "paused_at": "' + HELD + '"}',),
        )
        c.execute("UPDATE bots SET state='paused' WHERE id IN ('ops','cpo','finance')")
        events = [
            ("e1", EARLIER, "human:ana", "bot.pause", "ops"),  # paused by ana
            ("e2", EARLIER, "human:ana", "bot.pause", "cpo"),
            ("e3", HELD, "human:ana", "bot.resume", "cpo"),  # resumed after: paused since some other way
            ("e4", EARLIER, "human:ana", "bot.pause", "botops"),  # an active bot: no pause to move
        ]
        c.executemany("INSERT INTO events(id,created_at,actor,action,target,detail) VALUES(?,?,?,?,?,'{}')", events)
        for statement in statements(PAUSES.read_text()):
            c.execute(statement)
        moved = _pauses(c)
        for statement in statements(PAUSES.read_text()):
            c.execute(statement)
        assert _pauses(c) == moved
        assert not c.execute("SELECT 1 FROM registry_metadata WHERE key='company_pause'").fetchone()

        assert moved["company_pause"] == ("company", "", None, "human:ben", HELD)
        assert moved["paused:bot:ops"] == ("bot", "ops", "ops", "human:ana", EARLIER)
        assert moved["paused:bot:cpo"][3] == "keeper" and moved["paused:bot:finance"][3] == "keeper"
        assert set(moved) == {"company_pause", "paused:bot:ops", "paused:bot:cpo", "paused:bot:finance"}
        # The bots stay paused: bots.state is the gate the rest of Tico reads.
        assert c.execute("SELECT count(*) FROM bots WHERE state='paused'").fetchone()[0] == 3

        # The readers read the moved holds.
        assert company_pause.record(c)["paused_by"] == "human:ben"
        assert holds.why(c, "ops", kinds=(holds.PAUSED,)) == holds.PAUSED
        assert holds.why(c, "botops", kinds=(holds.PAUSED,)) is None


def test_a_company_pause_row_with_no_time_was_no_pause_and_is_dropped(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO registry_metadata(key,value) VALUES('company_pause','{\"paused_by\": \"human:ben\"}')")
        for statement in statements(PAUSES.read_text()):
            c.execute(statement)
        assert _pauses(c) == {} and company_pause.record(c) is None
        assert not c.execute("SELECT 1 FROM registry_metadata WHERE key='company_pause'").fetchone()


def test_every_way_a_bot_is_paused_or_resumed_opens_or_releases_its_hold_and_the_api_still_says_paused(api):
    def hold():
        with api.app.state.store.read() as c:
            return holds.get(c, holds.PAUSED, "bot", "ops")

    setup_attempt(api)  # a computer with the bot's repository, so resuming may activate it
    revision = get(api, "bots/ops/pause-state")["revision"]
    post(api, "bots/ops/control", {"action": "pause", "expected_revision": revision})
    assert hold()["created_by_id"] == "human:ana"
    assert get(api, "bots/ops/pause-state")["state"] == "paused"
    post(api, "bots/ops/control", {"action": "resume", "expected_revision": revision + 1})
    assert hold() is None and get(api, "bots/ops/pause-state")["state"] == "active"
    with api.app.state.store.read() as c:
        how = c.execute("SELECT released_how FROM holds WHERE kind='paused' AND holdable_id='ops'").fetchone()[0]
    assert how == "person"

    with api.app.state.store.transaction() as c:  # the status route, and what Tico sets itself
        H.status_set(c, "bot:ops", "ops", state="paused", reason="Paused for upkeep")
    assert hold()["reason"] == "Paused for upkeep" and hold()["created_by_id"] == "bot:ops"
    with api.app.state.store.transaction() as c:
        H.status_set(c, "bot:ops", "ops", state="active")
        assert H.bot(c, "ops")["state"] == "active"
    assert hold() is None

    for status in ("paused", "planned"):  # a definition's status, as Settings saves it
        revision = get(api, "bots/ops/pause-state")["revision"]
        post(api, "bots/ops/definition", {"status": status, "expected_revision": revision})
        assert bool(hold()) == (status == "paused")
    with api.app.state.store.read() as c:
        assert H.bot(c, "ops")["state"] == "planned"


def test_a_bots_pause_hold_is_its_state_switch_but_never_wakes_an_archived_or_quarantined_bot(api):
    """holds.STATE_KINDS: the open hold sets bots.state 'paused' on an active bot only, and its release sets it active
    only while the bot is still 'paused'."""
    with api.app.state.store.transaction() as c:
        for bot, state in (("ops", "active"), ("cpo", "archived"), ("finance", "quarantined")):
            c.execute("UPDATE bots SET state=? WHERE id=?", (state, bot))
            company_pause.pause_bot(c, "human:ana", bot)
        states = {bot: H.bot(c, bot)["state"] for bot in ("ops", "cpo", "finance")}
        assert states == {"ops": "paused", "cpo": "archived", "finance": "quarantined"}
        for bot in states:
            company_pause.resume_bot(c, "human:ana", bot)
            assert not holds.get(c, holds.PAUSED, "bot", bot)
        assert {bot: H.bot(c, bot)["state"] for bot in states} == {
            "ops": "active",
            "cpo": "archived",
            "finance": "quarantined",
        }
        company_pause.resume_bot(c, "human:ana", "ops")  # nothing open: nothing changes
        assert H.bot(c, "ops")["state"] == "active"
