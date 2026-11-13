"""Task settings projects existing state without granting writes. (The wake rules listing it once described went with
the /rules routes; rules made today live in automations.)"""

from backend import task_settings as TS
from backend.tests.helpers.api import get  # noqa: F401


def test_task_settings_routines_preserve_actual_state_and_do_not_run_jobs(api):
    with api.app.state.store.read() as c:
        before = [tuple(r) for r in c.execute("SELECT * FROM system_runs")]
    owner = get(api, "system-routines")
    reader = get(api, "system-routines", token="cara-test")
    by_key = lambda data: {r["key"]: r for r in data["jobs"]}
    mine, theirs = by_key(owner), by_key(reader)
    assert mine["heartbeat"]["task_trigger"] == "open" and mine["heartbeat"]["task_effect"] == "Wakes"
    assert "due_reminders" not in mine
    assert "health_scan" not in mine and "health_scan" not in theirs
    assert not any(job.get("can_change") for job in theirs.values())
    with api.app.state.store.read() as c:
        assert [tuple(r) for r in c.execute("SELECT * FROM system_runs")] == before
