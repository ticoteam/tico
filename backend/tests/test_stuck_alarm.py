"""The stuck count and the stuck alarm (backend/stuck_alarm.py, roadmap 886): a card with no run for 45 minutes is stuck
(the one rule on pipeline_exceptions); the count shows the stuck cards of a factory; the alarm files one task to the
factory's manager for a card stuck more than 4 hours, once, and clears itself when the card moves. It ships off.
Synthetic fixtures only (test_pipeline_exceptions helpers)."""

from backend import devcopy
from backend import stuck_alarm as SA
from backend.config import Settings
from backend.store import H, Store
from backend.tests.helpers.pipeline_exceptions import board, listed, run, ticket

ENGINEERING = "engineering-pipeline-manager"


def _factory(c, fid="fac-engineering"):
    now = H.now()
    c.execute(
        "INSERT INTO factories (id, name, owner_id, position, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (fid, fid, "ana", 1, now, now),
    )
    return fid


def _engineering_bot(c):
    if not H.bot(c, H.bot_actor(ENGINEERING)):
        c.execute(
            "INSERT INTO bots (id, slug, display_name, state) VALUES (?, ?, ?, ?)",
            (ENGINEERING, ENGINEERING, "Eng PM", "active"),
        )


def _pipeline(c, fid):
    typ = board(c)
    c.execute("UPDATE task_types SET factory_id=? WHERE id=?", (fid, typ["id"]))
    return typ


def _alarm_tasks(c):
    return H._rows(c.execute("SELECT * FROM tasks WHERE title LIKE 'Stuck alarm: %' ORDER BY created_at"))


def _open_alarms(c):
    return c.execute("SELECT COUNT(*) FROM stuck_alarms WHERE cleared_at IS NULL").fetchone()[0]


def test_an_empty_factory_and_an_unknown_factory_both_count_zero(api):
    with api.app.state.store.transaction() as c:
        fid = _factory(c)
        assert SA.count(c, fid) == 0
        assert SA.count(c, "no-such-factory") == 0


def test_the_count_is_the_cards_stuck_past_45_minutes_on_the_factorys_pipeline_and_no_other(api):
    with api.app.state.store.transaction() as c:
        fid = _factory(c)
        other = _factory(c, "fac-other")
        typ = _pipeline(c, fid)
        ticket(c, typ, "Fix Cursor", minutes=50)
        ticket(c, typ, "Fix Muse", minutes=20)  # fresh: not stuck
        ticket(c, typ, "Fix Grok", minutes=300)  # stuck for hours: still one card
        assert SA.count(c, fid) == 2
        assert SA.count(c, fid) == len(listed(c, typ, "stuck")), "the count is the list's own stuck entries"
        assert SA.count(c, other) == 0


def test_the_alarm_is_off_by_default_and_files_nothing_at_five_hours(api, monkeypatch):
    monkeypatch.delenv(SA.ENV, raising=False)
    with api.app.state.store.transaction() as c:
        fid = _factory(c)
        _engineering_bot(c)
        typ = _pipeline(c, fid)
        ticket(c, typ, "Fix Cursor", minutes=300)
        assert SA.enabled() is False
        assert SA.sweep(c, H.now()) == {"raised": 0, "cleared": 0}
        assert _alarm_tasks(c) == []
        assert _open_alarms(c) == 0


def test_a_card_stuck_two_hours_is_counted_but_gets_no_alarm_yet(api, monkeypatch):
    monkeypatch.setenv(SA.ENV, "on")
    with api.app.state.store.transaction() as c:
        fid = _factory(c)
        _engineering_bot(c)
        typ = _pipeline(c, fid)
        ticket(c, typ, "Fix Cursor", minutes=120)
        assert SA.count(c, fid) == 1
        assert SA.sweep(c, H.now()) == {"raised": 0, "cleared": 0}
        assert _alarm_tasks(c) == []


def test_a_card_stuck_over_four_hours_fires_one_task_to_the_engineering_manager_and_no_duplicate(api, monkeypatch):
    monkeypatch.setenv(SA.ENV, "on")
    with api.app.state.store.transaction() as c:
        fid = _factory(c)
        _engineering_bot(c)
        typ = _pipeline(c, fid)
        ticket(c, typ, "Fix Cursor", minutes=300)
        assert SA.sweep(c, H.now()) == {"raised": 1, "cleared": 0}
        [alarm] = _alarm_tasks(c)
        assert alarm["owner_id"] == H.bot_actor(ENGINEERING)
        assert alarm["title"] == "Stuck alarm: Fix Cursor"
        assert SA.sweep(c, H.now()) == {"raised": 0, "cleared": 0}, "one task per card while the condition holds"
        assert len(_alarm_tasks(c)) == 1
        assert _open_alarms(c) == 1


def test_a_card_that_moves_clears_its_alarm_and_the_alarm_task_is_closed(api, monkeypatch):
    monkeypatch.setenv(SA.ENV, "on")
    with api.app.state.store.transaction() as c:
        fid = _factory(c)
        _engineering_bot(c)
        typ = _pipeline(c, fid)
        row = ticket(c, typ, "Fix Cursor", minutes=300)
        assert SA.sweep(c, H.now()) == {"raised": 1, "cleared": 0}
        [alarm] = _alarm_tasks(c)
        run(c, row, "completed", 5)  # the card moves: a run landed five minutes ago
        assert SA.count(c, fid) == 0
        assert SA.sweep(c, H.now()) == {"raised": 0, "cleared": 1}
        assert _open_alarms(c) == 0
        assert H.task(c, alarm["id"])["status"] not in H.ACTIVE_STATUSES
        assert SA.sweep(c, H.now()) == {"raised": 0, "cleared": 0}


def test_a_dev_copy_keeps_the_alarm_ledger_so_the_copy_files_no_second_alarm(api, monkeypatch, tmp_path):
    monkeypatch.setenv(SA.ENV, "on")
    live = api.app.state.store
    with live.transaction() as c:
        fid = _factory(c)
        _engineering_bot(c)
        typ = _pipeline(c, fid)
        ticket(c, typ, "Fix Cursor", minutes=300)
        assert SA.sweep(c, H.now()) == {"raised": 1, "cleared": 0}
        before = [tuple(row) for row in c.execute("SELECT * FROM stuck_alarms ORDER BY id")]
    assert len(before) == 1
    # Close the live store's idle connections so the copy reads a file with everything checkpointed (as in test_devcopy).
    for conn in live.pool:
        conn.close()
    live.pool.clear()
    dest = tmp_path / "copy" / "hub.sqlite"
    devcopy.build(live.settings.db_path, dest, days=30)
    copied = Store(Settings(db_path=dest))
    with copied.transaction() as c:
        assert [tuple(row) for row in c.execute("SELECT * FROM stuck_alarms ORDER BY id")] == before, (
            "the copy keeps the ledger row of the alarm already filed"
        )
        # Guard against a pass for the wrong reason: a copy that lost the card or its manager would also sweep to zero.
        assert SA.count(c, fid) == 1, "the card is still stuck in the copy"
        assert H.bot(c, H.bot_actor(ENGINEERING)), "the copy still has the factory's manager"
        assert SA.sweep(c, H.now()) == {"raised": 0, "cleared": 0}, "the copy files no second alarm"
        assert len(_alarm_tasks(c)) == 1
        assert _open_alarms(c) == 1
