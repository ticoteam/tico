"""The Factory Manager (backend/factory_manager.py): plain-code passes every 30 minutes that wake its bot only for judgment
items, wake once for the same bottleneck, open no second BotOps task for a dead pipeline manager, and never move a card."""

from datetime import datetime, timedelta, timezone

from backend import hubdb as H
from backend import factory_manager as FM
from backend import pipeline_manager as PM
from backend.tests.test_api import api, get  # noqa: F401
from backend.tests.test_pipeline_manager import ANA, botops_roster, now, pipeline, stamp, step, ticket, visit

FACTORY = "Product development"


def factory(c, manager="ops"):
    """A factory with a manager. Track A adds factories.manager_id; a test database made before it gets the column here."""
    if "manager_id" not in {r[1] for r in c.execute("PRAGMA table_info(factories)")}:
        c.execute("ALTER TABLE factories ADD COLUMN manager_id TEXT")
    base = stamp(now())
    c.execute(
        "INSERT INTO factories (id, name, owner_id, position, created_at, updated_at, manager_id) VALUES (?,?,?,?,?,?,?)",
        ("fac-product", FACTORY, ANA, 1, base, base, manager),
    )
    return "fac-product"


def checks(c):
    return c.execute("SELECT COUNT(*) FROM tasks WHERE title=?", (FM.CHECK_PREFIX + FACTORY,)).fetchone()[0]


def runs(c):
    return [
        dict(r)
        for r in c.execute(
            "SELECT * FROM factory_manager_runs WHERE factory_id='fac-product' ORDER BY started_at, id"
        ).fetchall()
    ]


def bot_wakes(woken):
    """The wakes sent to a bot (a person's task note wakes the person, not a model)."""
    return [w for w in woken if str(w[2]).startswith("bot:")]


def botops(c):
    return c.execute(
        "SELECT COUNT(*) FROM tasks WHERE owner_id='bot:botops' AND title='Pipeline manager stopped: Launch'"
    ).fetchone()[0]


def test_a_pass_with_nothing_to_judge_wakes_nobody_and_makes_no_model_call(api, monkeypatch):
    store = api.app.state.store
    woken = []
    monkeypatch.setattr(H, "_wake", lambda *a, **k: woken.append(a))
    t0 = now()
    with store.transaction() as c:
        fid = factory(c)
        pipeline(c, manager=None)
        c.execute("UPDATE task_types SET factory_id=? WHERE name='Launch'", (fid,))
    FM.tick(store, t0)
    with store.read() as c:
        [run] = runs(c)
        assert (run["status"], run["woke"], run["model_called"], run["judgment_count"]) == ("ok", 0, 0, 0)
        assert checks(c) == 0
    assert bot_wakes(woken) == []


def test_the_same_bottleneck_on_two_passes_wakes_the_manager_once(api):
    store = api.app.state.store
    t0 = now()
    with store.transaction() as c:
        fid = factory(c)
        type_id = pipeline(c, manager=None)
        c.execute("UPDATE task_types SET factory_id=? WHERE id=?", (fid, type_id))
        c.execute("UPDATE task_steps SET limit_minutes=60 WHERE id=?", (step(c, type_id, "Building"),))
        ticket(c, type_id, created=t0 - timedelta(hours=3))
        before = c.execute("SELECT step_id FROM tasks WHERE type_id=?", (type_id,)).fetchone()[0]

    FM.tick(store, t0)
    with store.read() as c:
        assert checks(c) == 0
    FM.tick(store, t0 + timedelta(minutes=30))
    FM.tick(store, t0 + timedelta(minutes=60))
    with store.read() as c:
        assert checks(c) == 1
        woke = [r["woke"] for r in runs(c)]
        assert woke == [0, 1, 0]
        assert [r["model_called"] for r in runs(c)] == [0, 1, 0]
        assert c.execute("SELECT COUNT(*) FROM factory_manager_actions WHERE action='ask_manager'").fetchone()[0] == 1
        proposed = c.execute("SELECT note FROM factory_manager_actions WHERE action='propose_rule'").fetchall()
        assert len(proposed) == 1 and proposed[0][0].startswith("Rule proposal: Building")
        # The proposal is delivered: a task for the factory's owner that names the stage and the rule.
        [delivered] = c.execute("SELECT owner_id, body FROM tasks WHERE title LIKE 'Rule proposal:%'").fetchall()
        assert delivered[0] == "human:ana" and "add a requirement to Building" in delivered[1]
        assert c.execute("SELECT step_id FROM tasks WHERE type_id=?", (type_id,)).fetchone()[0] == before
        assert c.execute("SELECT COUNT(*) FROM stage_decisions").fetchone()[0] == 0


def test_a_handoff_with_no_intake_in_the_week_wakes_once_and_clears_when_the_target_takes_cards(api):
    store = api.app.state.store
    t0 = now()
    with store.transaction() as c:
        fid = factory(c, manager="ops")
        src = pipeline(c, manager=None)
        dst = pipeline(c, manager=None, name="Handoff target")
        for type_id in (src, dst):
            c.execute("UPDATE task_types SET factory_id=? WHERE id=?", (fid, type_id))
        c.execute(
            "INSERT INTO pipeline_links (id, from_pipeline_id, from_pipeline_stage_id, to_pipeline_id, mode, created_at) "
            "VALUES ('link-1', ?, ?, ?, 'move', ?)",
            (src, step(c, src, "Building"), dst, stamp(t0)),
        )
        c.execute(
            "INSERT INTO pipeline_handoffs (id, pipeline_link_id, source_task_id, target_task_id, created_at) "
            "VALUES ('handoff-1', 'link-1', 'task-a', 'task-b', ?)",
            (stamp(t0 - timedelta(days=1)),),
        )
    FM.tick(store, t0)
    with store.read() as c:
        assert [r["woke"] for r in runs(c)] == [1]
        assert [r["judgment_count"] for r in runs(c)] == [1]
    FM.tick(store, t0 + timedelta(minutes=30))
    with store.read() as c:
        assert [r["woke"] for r in runs(c)] == [1, 0]
    with store.transaction() as c:
        visit(c, ticket(c, dst)["id"], dst, "Building", t0 + timedelta(minutes=40))
    FM.tick(store, t0 + timedelta(minutes=60))
    with store.read() as c:
        assert [r["woke"] for r in runs(c)] == [1, 0, 0]


def test_a_dead_pipeline_manager_opens_one_botops_task_and_wakes_the_factory_manager_once(api):
    store = api.app.state.store
    t0 = now()
    with store.transaction() as c:
        botops_roster(c)
        fid = factory(c)
        type_id = pipeline(c, manager="launch-bot")
        c.execute(
            "UPDATE task_types SET factory_id=?, created_at=? WHERE id=?",
            (fid, stamp(t0 - timedelta(hours=2)), type_id),
        )
    FM.tick(store, t0)
    PM.watchdog(store, t0)
    FM.tick(store, t0 + timedelta(minutes=30))
    PM.watchdog(store, t0 + timedelta(minutes=35))
    with store.read() as c:
        assert botops(c) == 1
        assert checks(c) == 1
        assert [r["woke"] for r in runs(c)] == [1, 0]
        assert c.execute("SELECT COUNT(*) FROM factory_manager_actions WHERE action='ask_manager'").fetchone()[0] == 1


def test_every_emitted_factory_action_is_allowed():
    assert set(FM.EMITTED) <= set(FM.ALLOWED)


def test_factories_without_a_manager_are_skipped(api):
    store = api.app.state.store
    with store.transaction() as c:
        factory(c, manager=None)
    FM.tick(store, now())
    with store.read() as c:
        assert runs(c) == []


def test_the_factory_gets_one_note_per_utc_day_from_plain_code(api, monkeypatch):
    store = api.app.state.store
    woken = []
    monkeypatch.setattr(H, "_wake", lambda *a, **k: woken.append(a))
    day1 = datetime(2026, 11, 7, 0, 5, tzinfo=timezone.utc)
    with store.transaction() as c:
        factory(c, manager="ops")
    for minutes in (0, 30, 60 * 12, 60 * 23 + 30):  # four passes on the first UTC day
        FM.tick(store, day1 + timedelta(minutes=minutes))
    with store.read() as c:
        notes = c.execute("SELECT title FROM tasks WHERE title LIKE ?", (FM.NOTE_PREFIX + FACTORY + "%",)).fetchall()
        assert [n[0] for n in notes] == [FM.NOTE_PREFIX + FACTORY + " 2026-11-06"]
        assert c.execute("SELECT COUNT(*) FROM factory_manager_actions WHERE action='post_note'").fetchone()[0] == 1
        assert [r["model_called"] for r in runs(c)] == [0, 0, 0, 0]
    FM.tick(store, day1 + timedelta(days=1, minutes=10))  # the next UTC day
    FM.tick(store, day1 + timedelta(days=1, minutes=40))
    with store.read() as c:
        assert (
            c.execute("SELECT COUNT(*) FROM tasks WHERE title LIKE ?", (FM.NOTE_PREFIX + FACTORY + "%",)).fetchone()[0]
            == 2
        )
        assert c.execute("SELECT COUNT(*) FROM factory_manager_actions WHERE action='post_note'").fetchone()[0] == 2
    assert bot_wakes(woken) == []
