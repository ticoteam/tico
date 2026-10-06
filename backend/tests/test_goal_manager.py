"""The Goal Manager (templates/catalog/goal-manager, docs/goals-and-kpis.md) is built in like the Librarian:
a required bootstrap card, routines that start paused, a company from before it gets it on update, nobody
archives it, only the owner edits it, and its daily pass starts once the first KPI exists."""

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.test_onboarding import (ASSISTANT_AGENT, ASSISTANT_CARD, BOTOPS_CARD, as_person, draft,  # noqa: F401
                                           environment, machine, signed_in)

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "catalog" / "goal-manager"
BASE = [(ASSISTANT_CARD, ASSISTANT_AGENT), (BOTOPS_CARD, "")]


def with_goal_manager(api):
    """The catalog a release ships: the real template, copied next to the test's own cards."""
    shutil.copytree(TEMPLATE, Path(api.app.state.store.settings.catalog_dir) / "goal-manager")


def routines(api):
    with api.app.state.store.read() as c:
        return {r["id"]: dict(r) for r in c.execute(
            "SELECT s.id, coalesce(sc.enabled,1) AS enabled FROM schedules s "
            "LEFT JOIN schedule_config sc ON sc.schedule_id=s.id WHERE s.bot='goal-manager' AND s.deleted_at IS NULL")}


def states(api):
    with api.app.state.store.read() as c:
        return {row["slug"]: row["state"] for row in c.execute("SELECT slug,state FROM bots")}


def built(environment):
    api = environment(seed={}, cards=BASE)
    with_goal_manager(api)
    machine(api)
    draft(api)
    record = api.post("/api/v2/onboarding/complete", json={}, headers=signed_in()).json()
    return api, record


def test_it_is_built_for_every_company_with_both_routines_paused(environment):
    api, record = built(environment)
    assert sorted(row["slug"] for row in record["bots"]) == ["botops", "coo", "goal-manager"]
    assert all(row["setup_task_id"] is None for row in record["bots"])          # the runner sets it up, not BotOps
    assert states(api)["goal-manager"] == "active"
    assert {k: v["enabled"] for k, v in routines(api).items()} == {"goal-manager:kpi-pass": 0, "goal-manager:goals-review": 0}
    api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert len(routines(api)) == 2                                              # twice is still two


def test_a_company_from_before_gets_it_on_update_once_a_computer_and_model_exist(environment):
    api = environment(seed={}, cards=BASE)
    machine(api)
    draft(api)
    api.post("/api/v2/onboarding/complete", json={}, headers=signed_in())
    assert "goal-manager" not in states(api)                                    # this release's catalog has no card yet
    with_goal_manager(api)
    with TestClient(create_app(api.app.state.store.settings)):                  # a restart is an update
        pass
    assert states(api)["goal-manager"] == "active" and len(routines(api)) == 2
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE slug='goal-manager'")
    with TestClient(create_app(api.app.state.store.settings)):
        pass
    assert states(api)["goal-manager"] == "paused"                              # a person who paused it keeps it paused


def test_nobody_archives_it_and_only_the_owner_edits_it(environment):
    api, _ = built(environment)
    with api.app.state.store.read() as c:
        revision = c.execute("SELECT revision FROM bot_config WHERE bot='goal-manager'").fetchone()[0]
    gone = api.post("/api/v2/bots/goal-manager/archive", json={"expected_revision": revision}, headers=signed_in())
    assert gone.status_code == 409 and gone.json()["error"]["code"] == "system_bot"
    edit = {"description": "Somebody else's", "expected_revision": revision}
    admin = api.post("/api/v2/bots/goal-manager/definition", json=edit, headers=as_person(api, "riley"))   # an Admin
    assert admin.status_code == 403
    assert api.post("/api/v2/bots/goal-manager/definition", json=edit, headers=signed_in()).status_code == 200

