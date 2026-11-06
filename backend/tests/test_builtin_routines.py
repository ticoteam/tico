"""A built-in bot's template routines reach an install from before they existed, once: the Assistant's daily sweep.

A routine a person deleted, changed or moved is never put back. The fixture is the onboarding one (a real app, a
catalog on disk); a restart runs the startup path (backend/app.py lifespan) again.
"""

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from backend import routines
from backend.app import create_app
from backend.tests.test_onboarding import ASSISTANT_AGENT, ASSISTANT_CARD, BOTOPS_CARD, environment  # noqa: F401  (the fixture)

COO = {"coo": {"name": "Coo", "status": "active", "template": "assistant"}}
SWEEP = "coo:daily-sweep"
BOOKKEEPER = {"bk": {"name": "Bk", "status": "active", "template": "bookkeeper"}}
BOOKKEEPER_CARD = {"template": "bookkeeper", "slug": "bk", "name": "Bookkeeper", "required": False, "bootstrap": True,
                   "summary": "Keeps the books.", "owns": ["the books"], "never": ["pays bills"],
                   "reasoning_effort": "high", "recommend_when": ["always"]}
BOOKKEEPER_AGENT = "# {{bot_name}}\n\nYou keep {{company_name}}'s books.\n"
MONTHLY = {"id": "monthly-report", "title": "Monthly finance report", "cron": "0 9 3 * *",
           "timezone": "America/Los_Angeles", "template": "playbooks/monthly-report.md"}


def _write_assistant_routines(api):
    folder = Path(api.app.state.store.settings.catalog_dir) / "assistant"
    (folder / "playbooks").mkdir(exist_ok=True)
    (folder / "bot.yaml").write_text(yaml.safe_dump({"name": "assistant", "routines": [{
        "id": "daily-sweep", "title": "Daily sweep of each person's queue", "cron": "0 8 * * *",
        "template": "playbooks/daily-sweep.md"}]}))
    (folder / "playbooks" / "daily-sweep.md").write_text("Sweep {{company_name}}'s queue.\n")


def _rows(api):
    """Every schedule row the key has (a deleted or moved one stays, with deleted_at set)."""
    with api.app.state.store.read() as c:
        return [dict(row) for row in c.execute(
            "SELECT id, cron, deleted_at FROM schedules WHERE bot='coo' AND routine_key='daily-sweep'")]


def _row(api):
    """The routine as the person sees it: a routine written the new way moves to an automation at once
    (redesign_migration.adopt), and its schedule row is then soft-deleted; `routines.row` reads the automation."""
    with api.app.state.store.read() as c:
        return routines.row(c, SWEEP)


def _created(api):
    with api.app.state.store.read() as c:
        return c.execute("SELECT COUNT(*) FROM events WHERE action='routine.created' AND target=?",
                         (SWEEP,)).fetchone()[0]


def _ensure(api):
    with api.app.state.store.transaction() as c:
        return api.app.state.onboarding.ensure_builtin_routines(c)


def _restart(api):
    """Stop the app and start it again on the same database: the startup path, lifespan included."""
    settings = api.app.state.store.settings
    api.__exit__(None, None, None)
    client = TestClient(create_app(settings))
    client.__enter__()
    return client


def test_the_assistant_gets_its_daily_sweep_once_and_an_edit_a_delete_or_a_lost_row_survive_restarts(environment):
    """The key is also kept in the bot's config (`routines_seeded`), so no schedule row going missing brings it back.
    Once moved, a routine's on/off and its words are the automation's: a person's edit there stays through a restart."""
    from backend import automations
    api = environment(seed=COO)
    _write_assistant_routines(api)
    assert _rows(api) == []
    assert _ensure(api) == [("coo", "daily-sweep")]
    assert len(_rows(api)) == 1
    row = _row(api)
    assert row["id"] == SWEEP and row["cron"] == "0 8 * * *" and row["deleted_at"] is None and row["enabled"] == 1
    # A second startup creates nothing.
    created = _created(api)
    api = _restart(api)
    assert _ensure(api) == []
    assert len(_rows(api)) == 1 and _created(api) == created
    # A person-edited routine is untouched.
    with api.app.state.store.transaction() as c:
        who = api.app.state.auth.owner_identity(c)
        auto_id = _row(api)["automation"]
        before = automations.flow_of(_automation(c, auto_id))
        automations._save(c, who.actor, auto_id, {**before, "name": "My sweep"}, before, via="routine")
        routines.update(c, who.actor, SWEEP, {"enabled": False})     # after the rename, so it does not undo it
    with api.app.state.store.read() as c:
        auto = _automation(c, auto_id)
    api = _restart(api)
    with api.app.state.store.read() as c:
        assert _automation(c, auto_id) == auto               # the same automation, unchanged by startup
        assert routines.row(c, SWEEP)["enabled"] == 0
    assert auto["name"] == "My sweep" and auto["enabled"] == 0
    assert len(_rows(api)) == 1
    # After the person deletes it, startup does not recreate it.
    with api.app.state.store.transaction() as c:
        who = api.app.state.auth.owner_identity(c)
        routines.remove(c, who.actor, SWEEP)
    api = _restart(api)
    assert len(_rows(api)) == 1 and _row(api)["deleted_at"] is not None     # still deleted, no new row
    # A schedule row gone (its config row goes with it) is still not recreated.
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM schedule_config WHERE schedule_id=?", (SWEEP,))
        c.execute("DELETE FROM schedules WHERE id=?", (SWEEP,))
    api = _restart(api)
    assert _rows(api) == []
    api.__exit__(None, None, None)


def test_at_startup_only_the_assistant_gets_a_routine_a_bootstrap_bot_with_routines_gets_none(environment):
    """The backfill is the Assistant's daily sweep alone. The bookkeeper's monthly report (enabled in its template) is
    set up with the bot, so an install whose bookkeeper has none keeps none: the startup path does not switch it on."""
    api = environment(cards=[(ASSISTANT_CARD, ASSISTANT_AGENT), (BOTOPS_CARD, ""), (BOOKKEEPER_CARD, BOOKKEEPER_AGENT)],
                      seed={**COO, **BOOKKEEPER})
    _write_assistant_routines(api)
    folder = Path(api.app.state.store.settings.catalog_dir) / "bookkeeper"
    (folder / "playbooks").mkdir(exist_ok=True)
    (folder / "bot.yaml").write_text(yaml.safe_dump({"name": "bookkeeper", "routines": [MONTHLY]}))
    (folder / "playbooks" / "monthly-report.md").write_text("Report {{company_name}}'s month.\n")
    assert _ensure(api) == [("coo", "daily-sweep")]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM schedules WHERE bot='bk'").fetchone()[0] == 0
    api = _restart(api)
    assert _ensure(api) == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM schedules WHERE bot='bk'").fetchone()[0] == 0
    api.__exit__(None, None, None)


def _automation(c, aid):
    return dict(c.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone())
