"""Task settings projects existing state without granting writes or exposing private task rules."""
from backend import hubdb as H
from backend.tests.test_api import api, get  # noqa: F401
from backend.tests.test_rules import rule_row


def test_task_settings_keeps_scope_permissions_and_private_task_filtering(api):
    public = rule_row(api, event="step_entered", when={"step": "In QA"})["rule"]
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ana", "Private fixture", "Synthetic", "bot:inbox", private=True)
    private = rule_row(api, token="ana-test", scope_kind="task", scope_id=task["id"])["rule"]
    owner = get(api, "rules")
    reader = get(api, "rules", token="cara-test")
    assert private["id"] in {r["id"] for r in owner["rules"]}
    assert private["id"] not in {r["id"] for r in reader["rules"]}
    for data, editable in ((owner, True), (reader, False)):
        row = next(r for r in data["rules"] if r["id"] == public["id"])
        assert row["task_trigger"] == "step" and row["can_change"] is editable
        assert all(not r["can_change"] and r["enabled"] for r in data["task_builtins"])


def test_task_settings_pages_keep_live_triggers_and_exclude_non_task_events(api):
    task = rule_row(api, event="due")["rule"]
    other = rule_row(api, event="routine")["rule"]
    offset, found = 0, {}
    while True:
        page = get(api, f"rules?limit=1&offset={offset}")
        found.update((r["id"], r) for r in page["rules"])
        if not page["more"]:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert found[task["id"]]["task_trigger"] == "open"
    assert found[other["id"]]["task_trigger"] is None
    assert found["task_done"]["task_trigger"] == "finished"
    # Delivery filters and engine fallbacks do nothing to a task; a filter with task words still shows.
    for engine in ("default_note", "own_write", "caller_quiet", "no_run", "duplicate_drop", "same_state"):
        assert found[engine]["task_trigger"] is None, engine
    assert found["bot_to_bot"]["task_trigger"] == "open"


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
