"""A task moved back and forth an unusual number of times is flagged in Health and to its people once; never blocked."""
from backend import task_loops
from backend.tests.test_api import api, headers, post  # noqa: F401
from backend.store import H


def test_a_looping_task_is_named_in_health_and_its_people_are_told_once(api):
    tid = post(api, "tasks", {"owner_id": "ops", "title": "Ship the importer", "body": "x"})["id"]
    with api.app.state.store.transaction() as c:
        for i in range(task_loops.LOOP_CHANGES):
            H.task_update(c, H.KEEPER if i % 2 else "bot:ops", tid, status="waiting" if i % 2 else "review", mover=True)
        assert [t["task_id"] for t in task_loops.looping(c)] == [tid]
        assert task_loops.flag(c) == [tid] and task_loops.flag(c) == []
        told = c.execute("SELECT recipient_id FROM messages WHERE kind='notice' AND body LIKE 'This task changed status%'").fetchall()
        assert sorted(r[0] for r in told) == ["bot:ops", "human:ana"]
    health = api.get("/api/v2/health", headers=headers()).json()
    check = next(c for c in health["checks"] if c["id"] == "task_loops")
    assert "Ship the importer" in check["summary"] and "Tico's automation" in check["summary"] and "ops" in check["summary"]
    # nothing is blocked: it still moves
    with api.app.state.store.transaction() as c:
        assert H.task_update(c, "bot:ops", tid, status="doing")["status"] == "doing"
