"""Task settings projects existing state without granting writes. (The wake rules listing it once described went with
the /rules routes; rules made today live in automations.)"""
from backend.tests.test_api import api, get  # noqa: F401


def test_task_settings_routines_preserve_actual_state_and_do_not_run_jobs(api):
    with api.app.state.store.read() as c:
        before = [tuple(r) for r in c.execute("SELECT * FROM system_runs")]
    owner = get(api, "system-routines")
    reader = get(api, "system-routines", token="cara-test")
    by_key = lambda data: {r["key"]: r for r in data["jobs"]}
    mine, theirs = by_key(owner), by_key(reader)
    assert mine["task-rule-close-after-done"]["task_trigger"] == "after"
    assert mine["due_reminders"]["task_trigger"] == "open"
    assert mine["health_scan"]["task_trigger"] is None
    assert mine["stale_review"]["can_change"] and not theirs["stale_review"]["can_change"]
    assert mine["stale_review"]["on"] == theirs["stale_review"]["on"]
    with api.app.state.store.read() as c:
        assert [tuple(r) for r in c.execute("SELECT * FROM system_runs")] == before
