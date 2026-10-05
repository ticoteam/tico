"""Independent Lead probes for assignment lifecycle capacity safety."""

from backend.tests.test_assignment_branches import (
    api, create, delivery_task, prepare_source, set_task_status,
)


def test_resume_from_review_cannot_overbook_reserved_assignment_capacity(api):
    prepare_source(api)
    tasks = [delivery_task(api, f"Capacity review {i}") for i in range(3)]
    rows = [create(api, task, key=f"review-{i}", idem=f"review-create-{i}")
            for i, task in enumerate(tasks)]
    first = rows[0]

    def transition(revision, phase, key, **extra):
        return api.patch("/api/v2/assignment-branches/" + first["id"], json={
            "expected_revision": revision, "phase": phase,
            "checkpoint": {"commit": "synthetic-safe-checkpoint"}, **extra,
        }, headers={"Authorization": "Bearer ana-test", "Idempotency-Key": key})

    paused = transition(first["revision"], "paused", "review-pause")
    assert paused.status_code == 200, paused.text
    working = transition(paused.json()["revision"], "working", "review-working")
    assert working.status_code == 200, working.text
    set_task_status(api, tasks[0]["id"], "review")
    waiting = transition(working.json()["revision"], "waiting_review", "review-wait")
    assert waiting.status_code == 200, waiting.text
    create(api, delivery_task(api, "Replacement active slot"), key="replacement",
           idem="review-replacement")
    set_task_status(api, tasks[0]["id"], "doing")

    resumed = transition(waiting.json()["revision"], "working", "review-overbook")
    with api.app.state.store.read() as c:
        active = c.execute("SELECT count(*) FROM assignment_branches WHERE phase NOT IN "
                           "('waiting_review','waiting_release','archived','cancelled')").fetchone()[0]
    assert resumed.status_code == 409 and active == 3, (
        f"Resume must reserve capacity atomically: status={resumed.status_code}, "
        f"active={active}, response={resumed.text}"
    )
