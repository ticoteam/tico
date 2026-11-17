"""The holds table (backend/holds.py): one writer that keeps one open hold of a kind on a thing, a release that keeps it
as history, the one backoff and probe, and the migrations that move each kind into it: 20261117061834_holds_runaway
(bot_budget, run budgets), 20261117072000_holds_failing (run_backoff, failed runs), 20261117071838_holds_usage_limit
(registry_metadata login-hold keys, usage limits) and 20261117073216_holds_paused (the company's pause and each paused
bot)."""

import json

from backend import claim_loads, company_pause, general_manager, holds, invariants, login_holds, maintenance_health
from backend import run_failures, turn_budgets
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

        assert turn_budgets.release(c, "bot:botops", "ops") is False  # nothing open now


def test_a_quarantine_wins_over_a_pause_and_the_bot_is_active_only_once_neither_is_open(api):
    """holds.STATE_KINDS: a release sets bots.state to the stopping hold still open on the bot, quarantine over pause,
    and active only when none is; it never touches an archived or planned bot."""

    def state(c, bot="ops"):
        return H.bot(c, bot)["state"]

    def pause(c, bot="ops"):
        company_pause.pause_bot(c, "human:ana", bot)

    def quarantine(c, bot="ops"):
        holds.put(c, holds.QUARANTINE, "bot", bot, actor="bot:ops", reason="too many refusals")

    def lift(c, bot="ops"):
        holds.release(c, holds.QUARANTINE, "bot", bot, actor="human:ana", how="person")

    with api.app.state.store.transaction() as c:
        pause(c)
        quarantine(c)
        assert state(c) == "quarantined"
        lift(c)  # a quarantine released while paused: still paused
        assert state(c) == "paused"

        quarantine(c)
        company_pause.resume_bot(c, "human:ana", "ops")  # a pause released while quarantined: still quarantined
        assert state(c) == "quarantined"
        lift(c)  # both released: active
        assert state(c) == "active"

        quarantine(c)
        pause(c)  # a pause never covers a quarantine
        assert state(c) == "quarantined"
        lift(c)
        company_pause.resume_bot(c, "human:ana", "ops")
        assert state(c) == "active"

        for bot, kept in (("cpo", "archived"), ("finance", "planned")):
            c.execute("UPDATE bots SET state=? WHERE id=?", (kept, bot))
            pause(c, bot)
            quarantine(c, bot)
            lift(c, bot)
            company_pause.resume_bot(c, "human:ana", bot)
            assert state(c, bot) == kept


LOGINS = DIRECTORY / "20261117071838_holds_usage_limit.sql"
LIMIT = "You've hit your session limit · resets 2:40am (UTC)"


def test_every_login_hold_key_moves_to_an_open_usage_limit_hold_and_running_it_again_changes_nothing(api):
    own = {
        "runner_id": "r1",
        "profile": "",
        "runtime": "claude",
        "since": HELD,
        "until": "2026-10-24T14:40:00.000000Z",
        "streak": 2,
        "reset": True,
        "capacity": False,
        "attempt_id": "att-1",
        "bot": "ops",
        "error": LIMIT,
        "probe_job": "job-1",
        "probe_at": HELD,
        "cut": "2026-10-24T15:00:00.000000Z",
        "stuck": ["ops"],
    }
    plan = {"runner_id": "", "profile": "sub:s1", "runtime": "claude", "since": EARLIER, "until": HELD, "error": ""}
    with api.app.state.store.transaction() as c:
        c.executemany(
            "INSERT INTO registry_metadata(key,value) VALUES(?,?)",
            [
                (login_holds.key("r1", "", "claude"), json.dumps(own)),
                (login_holds.key("", "sub:s1", "claude"), json.dumps(plan)),
                ("login-hold:r9||codex", "not json"),  # read as no hold before, and dropped
                ("login-holding", json.dumps({"other": 1})),  # another key, left as it is
            ],
        )
        for statement in statements(LOGINS.read_text()):
            c.execute(statement)
        moved = [dict(r) for r in c.execute("SELECT * FROM holds ORDER BY id")]
        for statement in statements(LOGINS.read_text()):
            c.execute(statement)
        assert [dict(r) for r in c.execute("SELECT * FROM holds ORDER BY id")] == moved
        left = [r[0] for r in c.execute("SELECT key FROM registry_metadata WHERE key LIKE 'login-hold%'")]
        assert left == ["login-holding"]

        by_id = {row["id"]: row for row in moved}
        assert set(by_id) == {"login-hold:r1||claude", "login-hold:sub:s1|claude"}
        row = by_id["login-hold:r1||claude"]
        assert (row["kind"], row["holdable_type"], row["holdable_id"], row["bot_id"]) == (
            "usage_limit",
            "login",
            "r1||claude",
            None,
        )
        assert (row["until_at"], row["streak"], row["probe_job_id"], row["probe_at"]) == (
            own["until"],
            2,
            "job-1",
            HELD,
        )
        assert (row["created_by_id"], row["created_at"], row["released_at"]) == ("keeper", HELD, None)
        assert "until" not in json.loads(row["details"]) and json.loads(row["details"])["cut"] == own["cut"]
        assert (
            by_id["login-hold:sub:s1|claude"]["streak"] == 0
            and by_id["login-hold:sub:s1|claude"]["probe_job_id"] is None
        )

        # The login holds read the moved rows as they read the keys.
        assert login_holds.get(c, "r1", "", "claude") == own
        assert login_holds.get(c, "", "sub:s1", "claude") == {**plan, "streak": 0, "probe_job": None, "probe_at": None}
        assert login_holds.for_attempt(c, "att-1") == own
        assert [h["runtime"] for h in login_holds.every(c)] == ["claude", "claude"]
        assert [h["runner_id"] for h in login_holds.on_runner(c, "r1")] == ["r1"]


def test_a_cleared_login_hold_is_kept_as_history_and_the_next_limit_opens_another(api):
    with api.app.state.store.transaction() as c:
        first = login_holds.record(c, "r1", "", "claude", HELD, attempt_id="att-1", bot="ops", error=LIMIT)
        assert login_holds.get(c, "r1", "", "claude") == first
        login_holds.take_probe(c, "r1", "", "claude", "job-1", now=first["until"])
        assert login_holds.get(c, "r1", "", "claude")["probe_job"] == "job-1"
        login_holds.clear(c, "r1", "", "claude")
        assert login_holds.get(c, "r1", "", "claude") == {} and login_holds.for_attempt(c, "att-1") == {}
        how = c.execute("SELECT released_how FROM holds WHERE holdable_id='r1||claude'").fetchall()
        assert [r[0] for r in how] == ["probe_passed"]
        again = login_holds.record(c, "r1", "", "claude", first["until"], bot="ops", error=LIMIT)
        assert again["streak"] == 1  # a new run of limits: the cleared one is history
        loads = claim_loads.Loads(None, None, None, {}, {}, {}, {}, {}, claim_loads.open_holds(c))
        assert login_holds.get(c, "r1", "", "claude", loads) == again
        assert login_holds.get(c, "r2", "", "claude", loads) == {}


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
