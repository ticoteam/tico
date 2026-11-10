"""Helpers that other tests share, moved out of test_skill_usage.py so they import no test file."""

from datetime import datetime, timedelta, timezone
from backend.store import H


NOW = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)


def ts(at):
    return at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def skill_id(api, name="triage"):
    with api.app.state.store.read() as c:
        return c.execute("SELECT id FROM skills WHERE name=?", (name,)).fetchone()[0]


def use(
    api,
    actor=("Human", "cara"),
    *,
    days=0.0,
    trigger="model",
    created=None,
    name="triage",
    task_id=None,
    thread_id=None,
    requested_by=None,
    source="helper",
):
    """One raw use, as the runner, the helper or a typed /name writes it."""
    at = NOW - timedelta(days=days)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO skill_usages (skill_id, sha256, actor_type, actor_id, requested_by_id, trigger, source, session_key, "
            "tool_use_id, task_id, thread_id, used_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                skill_id(api, name),
                None,
                actor[0],
                actor[1],
                requested_by,
                trigger,
                source,
                H.new_id(),
                H.new_id(),
                task_id,
                thread_id,
                ts(at),
                ts(created or at),
            ),
        )
