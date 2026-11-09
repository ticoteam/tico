"""Helpers that other tests share, moved out of test_run_view.py so they import no test file."""

from backend.store import H
from backend.tests.helpers.api import claim, post


def task_attempt(api, machine, title="Count the words"):
    task = post(api, "tasks", {"owner": "ops", "title": title, "body": "Count them."})
    attempt = claim(api, machine)
    assert attempt["task"]["id"] == task["id"]
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": title}, machine["token"])
    return task["id"], attempt


def events(api, machine, aid, rows, first=1):
    post(
        api,
        f"attempts/{aid}/events",
        {"events": [{"seq": first + i, "kind": k, "payload": p} for i, (k, p) in enumerate(rows)]},
        machine["token"],
    )


DEV_563_NOTE = (
    "Run finished its work before the 60-min limit: PR 1596 pushed at 8cf2a8e, hosted checks passed, "
    "Review 1 requested. Nothing to redo."
)


def botops_decides(api, job_id, attempt_id, decision, note, at):
    """BotOps' own review of a held run (execution.reconcile_job, as `bot:botops`), saved at `at`."""
    from types import SimpleNamespace
    from backend.auth import Identity

    with api.app.state.store.transaction() as c:
        if not H.bot(c, H.FLEET_MAINTAINER):
            c.execute(
                "INSERT INTO bots(id, slug, display_name, state) VALUES(?, ?, 'BotOps', 'active')",
                (H.FLEET_MAINTAINER, H.FLEET_MAINTAINER),
            )
        api.app.state.execution.reconcile_job(
            c,
            Identity("bot:" + H.FLEET_MAINTAINER, "bot"),
            job_id,
            SimpleNamespace(run_id=attempt_id, decision=decision, note=note, acknowledge_uncertain_effects=True),
        )
        c.execute("UPDATE job_recovery SET created_at=? WHERE job_id=?", (at, job_id))
