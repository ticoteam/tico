"""Independent Lead probes for assignment lifecycle capacity safety."""

from concurrent.futures import ThreadPoolExecutor

from backend.tests.test_assignment_branches import (
    api, create, delivery_task, prepare_source, set_task_status,
)


def _transition(api, row, revision, phase, key):
    return api.patch("/api/v2/assignment-branches/" + row["id"], json={
        "expected_revision": revision, "phase": phase,
        "checkpoint": {"commit": "synthetic-safe-checkpoint"},
    }, headers={"Authorization": "Bearer ana-test", "Idempotency-Key": key})


def _wait_for_review(api, task, row, key):
    paused = _transition(api, row, row["revision"], "paused", key + "-pause")
    assert paused.status_code == 200, paused.text
    working = _transition(api, row, paused.json()["revision"], "working", key + "-working")
    assert working.status_code == 200, working.text
    set_task_status(api, task["id"], "review")
    waiting = _transition(api, row, working.json()["revision"], "waiting_review", key + "-wait")
    assert waiting.status_code == 200, waiting.text
    return waiting.json()


def _active_count(api):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM assignment_branches WHERE phase NOT IN "
                         "('waiting_review','waiting_release','archived','cancelled')").fetchone()[0]


def test_resume_from_review_cannot_overbook_reserved_assignment_capacity(api):
    prepare_source(api)
    tasks = [delivery_task(api, f"Capacity review {i}") for i in range(3)]
    rows = [create(api, task, key=f"review-{i}", idem=f"review-create-{i}")
            for i, task in enumerate(tasks)]
    first = rows[0]

    waiting = _wait_for_review(api, tasks[0], first, "review")
    create(api, delivery_task(api, "Replacement active slot"), key="replacement",
           idem="review-replacement")
    set_task_status(api, tasks[0]["id"], "doing")

    resumed = _transition(api, first, waiting["revision"], "working", "review-overbook")
    active = _active_count(api)
    assert resumed.status_code == 409 and active == 3, (
        f"Resume must reserve capacity atomically: status={resumed.status_code}, "
        f"active={active}, response={resumed.text}"
    )


def test_waiting_release_to_verifying_reserves_capacity_too(api):
    prepare_source(api)
    tasks = [delivery_task(api, f"Release capacity {i}") for i in range(3)]
    rows = [create(api, task, key=f"release-capacity-{i}", idem=f"release-capacity-{i}")
            for i, task in enumerate(tasks)]
    waiting_review = _wait_for_review(api, tasks[0], rows[0], "release-capacity-review")
    set_task_status(api, tasks[0]["id"], "ready")
    waiting_release = _transition(api, rows[0], waiting_review["revision"], "waiting_release",
                                  "release-capacity-wait")
    assert waiting_release.status_code == 200, waiting_release.text
    create(api, delivery_task(api, "Replacement while awaiting release"), key="release-capacity-replacement",
           idem="release-capacity-replacement")

    verifying = _transition(api, rows[0], waiting_release.json()["revision"], "verifying",
                            "release-capacity-verifying")
    assert verifying.status_code == 409 and verifying.json()["error"]["code"] == "assignment_capacity"
    assert _active_count(api) == 3


def test_concurrent_resume_and_create_share_the_three_slot_transaction(api):
    prepare_source(api)
    tasks = [delivery_task(api, f"Concurrent resume/create {i}") for i in range(3)]
    rows = [create(api, task, key=f"resume-create-{i}", idem=f"resume-create-{i}")
            for i, task in enumerate(tasks)]
    waiting = _wait_for_review(api, tasks[0], rows[0], "resume-create-review")
    set_task_status(api, tasks[0]["id"], "doing")
    competing_task = delivery_task(api, "Competing new slot")

    def resume():
        return _transition(api, rows[0], waiting["revision"], "working", "concurrent-resume")

    def allocate():
        return api.post("/api/v2/bots/cpo/assignment-branches", json={
            "assignment_key": "resume-create-competitor", "generation": 1,
            "task_id": competing_task["id"], "display_name": "Pro Workflow Engineer",
        }, headers={"Authorization": "Bearer ana-test", "Idempotency-Key": "resume-create-competitor"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        resumed, created = pool.submit(resume), pool.submit(allocate)
        results = [resumed.result(), created.result()]
    assert sorted(response.status_code for response in results) == [200, 409]
    assert _active_count(api) == 3


def test_concurrent_resumes_cannot_overbook_the_last_slot(api):
    prepare_source(api)
    tasks = [delivery_task(api, f"Concurrent resumes {i}") for i in range(3)]
    rows = [create(api, task, key=f"resume-resume-{i}", idem=f"resume-resume-{i}")
            for i, task in enumerate(tasks)]
    waiting = [_wait_for_review(api, tasks[i], rows[i], f"resume-resume-review-{i}") for i in (0, 1)]
    create(api, delivery_task(api, "Two active slots"), key="resume-resume-third",
           idem="resume-resume-third")
    for task in tasks[:2]:
        set_task_status(api, task["id"], "doing")

    def resume(index):
        return _transition(api, rows[index], waiting[index]["revision"], "working",
                           f"concurrent-resume-{index}")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(resume, (0, 1)))
    assert sorted(response.status_code for response in results) == [200, 409]
    assert _active_count(api) == 3
