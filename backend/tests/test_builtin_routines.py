"""A built-in bot's template routines reach an install from before they existed, once: the Assistant's daily sweep.

A routine a person deleted, changed or moved is never put back. The fixture is the onboarding one (a real app, a
catalog on disk); a restart runs the startup path (backend/app.py lifespan) again.
"""

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from backend import routines
from backend.app import create_app
from backend.tests.test_onboarding import environment  # noqa: F401  (the fixture)

COO = {"coo": {"name": "Coo", "status": "active", "template": "assistant"}}
SWEEP = "coo:daily-sweep"


def _write_assistant_routines(api):
    folder = Path(api.app.state.store.settings.catalog_dir) / "assistant"
    (folder / "playbooks").mkdir(exist_ok=True)
    (folder / "bot.yaml").write_text(yaml.safe_dump({"name": "assistant", "routines": [{
        "id": "daily-sweep", "title": "Daily sweep of each person's queue", "cron": "0 8 * * *",
        "template": "playbooks/daily-sweep.md"}]}))
    (folder / "playbooks" / "daily-sweep.md").write_text("Sweep {{company_name}}'s queue.\n")


def _rows(api):
    with api.app.state.store.read() as c:
        return [dict(row) for row in c.execute(
            "SELECT id, cron, deleted_at FROM schedules WHERE bot='coo' AND routine_key='daily-sweep'")]


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


def test_the_coo_assistant_on_an_install_without_daily_sweep_gets_it_once(environment):
    api = environment(seed=COO)
    _write_assistant_routines(api)
    assert _rows(api) == []
    assert _ensure(api) == [("coo", "daily-sweep")]
    (row,) = _rows(api)
    assert row["id"] == SWEEP and row["cron"] == "0 8 * * *" and row["deleted_at"] is None


def test_a_second_startup_creates_nothing(environment):
    api = environment(seed=COO)
    _write_assistant_routines(api)
    _ensure(api)
    created = _created(api)
    api = _restart(api)
    assert _ensure(api) == []
    assert len(_rows(api)) == 1 and _created(api) == created
    api.__exit__(None, None, None)


def test_after_the_person_deletes_it_startup_does_not_recreate_it(environment):
    api = environment(seed=COO)
    _write_assistant_routines(api)
    _ensure(api)
    with api.app.state.store.transaction() as c:
        who = api.app.state.auth.owner_identity(c)
        routines.remove(c, who.actor, SWEEP)
    api = _restart(api)
    assert [r["deleted_at"] is not None for r in _rows(api)] == [True]     # still deleted, no new row
    api.__exit__(None, None, None)


def test_a_routine_whose_row_is_gone_is_still_not_recreated(environment):
    """The key is also kept in the bot's config (`routines_seeded`), so no schedule row going missing brings it back."""
    api = environment(seed=COO)
    _write_assistant_routines(api)
    _ensure(api)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM schedules WHERE id=?", (SWEEP,))
    api = _restart(api)
    assert _rows(api) == []
    api.__exit__(None, None, None)


def test_a_person_edited_routine_is_untouched(environment):
    api = environment(seed=COO)
    _write_assistant_routines(api)
    _ensure(api)
    with api.app.state.store.transaction() as c:
        who = api.app.state.auth.owner_identity(c)
        routines.update(c, who.actor, SWEEP, {"title": "My sweep", "cron": "0 7 * * 1", "enabled": False})
    with api.app.state.store.read() as c:
        before = dict(c.execute("SELECT * FROM schedules WHERE id=?", (SWEEP,)).fetchone())
    api = _restart(api)
    with api.app.state.store.read() as c:
        after = dict(c.execute("SELECT * FROM schedules WHERE id=?", (SWEEP,)).fetchone())
        enabled = routines.row(c, SWEEP)["enabled"]
    assert after == before and after["title"] == "My sweep" and after["cron"] == "0 7 * * 1"
    assert enabled == 0
    api.__exit__(None, None, None)
