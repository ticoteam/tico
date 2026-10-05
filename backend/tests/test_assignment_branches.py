"""Synthetic authorization, idempotency, capacity, and runner isolation for task instances."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend import shared_bots
from backend.auth import Identity
from backend.repositories import access as repository_access
from backend.store import H, Problem
from backend.tests.test_api import api, get, post, runner, put  # noqa: F401


def prepare_source(api, capability=True):
    revision = get(api, "bots/cpo")["revision"]
    post(api, "bots/cpo/definition", {"shared": True, "expected_revision": revision})
    computer = runner(api, "ana", "Assignment Mac")
    post(api, "bots/cpo/assignment", {"runner_id": computer["runner_id"], "expected_generation": 0})
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "capacity": 4,
        "capabilities": ["assignment_instances_v1", "assignment_cleanup_v1"] if capability else [],
        "readiness": {"schema_version": 1, "bots": {"cpo": {"ready": True}}}}, computer["token"])
    return computer


def delivery_task(api, title="Deliver feature"):
    return post(api, "tasks", {"owner": "cpo", "title": title, "body": "Implement this bounded delivery."})


def create(api, task, key="feature-one", name="Pro Workflow Engineer", generation=1, expected=200, idem=None):
    return post(api, "bots/cpo/assignment-branches", {
        "assignment_key": key, "generation": generation, "task_id": task["id"], "display_name": name,
    }, key=idem, expected=expected)


def set_task_status(api, task_id, status):
    current = get(api, "tasks/" + task_id)["task"]
    return post(api, "tasks/" + task_id, {"version": current["version"], "status": status})


def test_allocation_is_stable_task_bound_and_separate_from_personal_routing(api):
    computer = prepare_source(api)
    task = delivery_task(api)
    made = create(api, task, idem="assignment-create-1")
    assert made["source_bot"] == "cpo" and made["task_id"] == task["id"]
    assert made["phase"] == "preparing" and made["runner_id"] == computer["runner_id"]
    assert made["bot"] != "cpo-cara" and made["bot"].endswith("-g1")
    assert made["assignment_key"] == "feature-one"
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["owner"] == "bot:" + made["bot"]
        config = shared_bots.declared(c, made["bot"])
        assert config["assignment_branch"] is True and config["assignment_task_id"] == task["id"]
        assert shared_bots.copies(c, "cpo") == []
        assert shared_bots.route(c, "human:ana", "bot:cpo") == "bot:cpo"
        row = c.execute("SELECT * FROM assignments WHERE bot=?", (made["bot"],)).fetchone()
        assert row["runner_id"] == computer["runner_id"]
        assigned = api.app.state.execution.assigned(c, Identity("runner:" + computer["runner_id"], "runner",
                                                                runner_id=computer["runner_id"]))
        entry = next(item for item in assigned if item["bot"] == made["bot"])
        assert entry["repository"] == ""  # no source GitHub token/push path
        assert repository_access(c, made["bot"], api.app.state.store.settings.github_owner)["effective"] == []
        assert c.execute("SELECT count(*) FROM bot_repo_access WHERE bot=?", (made["bot"],)).fetchone()[0] == 0

    # The stable key replays the same actor even when the transport idempotency key changes;
    # the display label is editable metadata, not identity.
    replay = create(api, task, key="feature-one", name="Renamed in request", idem="assignment-create-retry")
    assert replay["id"] == made["id"] and replay["display_name"] == made["display_name"]
    second_task = delivery_task(api, "Another delivery")
    same_name = create(api, second_task, key="feature-two", name="Pro Workflow Engineer", idem="assignment-create-2")
    assert same_name["bot"] != made["bot"] and same_name["display_name"] == made["display_name"]


def test_old_runner_refuses_assignment_without_changing_legacy_branch_guard(api):
    computer = prepare_source(api, capability=False)
    task = delivery_task(api)
    refused = create(api, task, expected=409)
    assert refused["error"]["code"] == "runner_capability"
    assert get(api, "bots/cpo/branches")["branches"] == []
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["owner"] == "bot:cpo"


def test_assignment_actor_task_reads_are_scoped_to_its_linked_delivery(api):
    prepare_source(api)
    linked = delivery_task(api, "Assigned feature")
    made = create(api, linked, key="only-linked-task", idem="only-linked-task")
    unrelated = delivery_task(api, "Unrelated source task")
    actor = Identity("bot:" + made["bot"], "bot")
    auth = api.app.state.auth
    with api.app.state.store.read() as c:
        visible = [row["id"] for row in c.execute("SELECT id FROM tasks WHERE " + auth.task_sql(c, actor))]
        assert visible == [linked["id"]]
        assert auth.task(c, actor, linked["id"])["id"] == linked["id"]
        with pytest.raises(Problem) as denied:
            auth.task(c, actor, unrelated["id"])
        assert denied.value.status == 404


def test_assignment_actor_contact_is_limited_to_its_linked_delivery(api):
    prepare_source(api)
    linked = delivery_task(api, "Only accepted assignment task")
    made = create(api, linked, key="contact-scope", idem="contact-scope")
    unrelated = delivery_task(api, "Unrelated contact target")
    auth = api.app.state.auth
    human = Identity("human:ana", "human")
    target = "bot:" + made["bot"]
    with api.app.state.store.read() as c:
        auth.require_bot_contact(c, human, target, task_id=linked["id"], kind="comment")
        with pytest.raises(Problem) as direct:
            auth.require_bot_contact(c, human, target, kind="message")
        assert direct.value.code == "assignment_scope"
        with pytest.raises(Problem) as other:
            auth.require_bot_contact(c, human, target, task_id=unrelated["id"], kind="comment")
        assert other.value.code == "assignment_scope"
        with pytest.raises(Problem) as new_task:
            auth.require_bot_contact(c, human, target, kind="task")
        assert new_task.value.code == "assignment_scope"
    refused = post(api, "tasks", {"owner": made["bot"], "relations": [{"task": linked["id"], "kind": "parent"}],
                                   "title": "Another assigned task", "body": "Must remain on the linked task."},
                   expected=403)
    assert refused["error"]["code"] == "assignment_scope"


def test_three_active_slots_are_atomic_and_review_wait_releases_capacity(api):
    computer = prepare_source(api)
    rows = []
    tasks = []
    keys = ("slot-one", "slot-two", "slot-three")
    for index, key in enumerate(keys):
        task = delivery_task(api, f"Slot {index + 1}")
        tasks.append(task)
        rows.append(create(api, task, key=key,
                           idem=f"{key}-idem"))
    refused = create(api, delivery_task(api, "Fourth slot"), key="slot-four", expected=409)
    assert refused["error"]["code"] == "assignment_capacity"
    paused = api.patch("/api/v2/assignment-branches/" + rows[0]["id"], json={
        "expected_revision": rows[0]["revision"], "phase": "paused", "note": "Pause before review"
    }, headers={"Authorization": "Bearer ana-test", "Idempotency-Key": "slot-one-pause"})
    assert paused.status_code == 200, paused.text
    resumed = api.patch("/api/v2/assignment-branches/" + rows[0]["id"], json={
        "expected_revision": paused.json()["revision"], "phase": "working", "note": "Resume for review"
    }, headers={"Authorization": "Bearer ana-test", "Idempotency-Key": "slot-one-resume"})
    assert resumed.status_code == 200, resumed.text
    set_task_status(api, tasks[0]["id"], "review")
    waiting = api.patch("/api/v2/assignment-branches/" + rows[0]["id"], json={
        "expected_revision": resumed.json()["revision"], "phase": "waiting_review", "note": "Ready for review",
        "checkpoint": {"commit": "synthetic-review-head", "next": "review"}
    }, headers={"Authorization": "Bearer ana-test", "Idempotency-Key": "slot-one-review"})
    assert waiting.status_code == 200, waiting.text
    fourth = create(api, delivery_task(api, "Slot after review"), key="slot-four", idem="slot-idem-four")
    assert fourth["phase"] == "preparing"
    assert fourth["runner_id"] == computer["runner_id"]
    assert get(api, "bots/cpo/assignment-branches")["active"] == 3


def test_policy_requires_human_manager_and_direct_parent_actor(api):
    revision = get(api, "bots/cpo")["revision"]
    post(api, "bots/cpo/definition", {"shared": True, "expected_revision": revision})
    revision = get(api, "bots/cpo")["revision"]
    denied = put(api, "bots/cpo/assignment-branches/policy", {"enabled": True, "expected_revision": revision}, expected=409)
    assert denied["error"]["code"] == "assignment_allocator"


def test_cancellation_is_audited_and_reopen_uses_a_new_generation(api):
    prepare_source(api)
    task = delivery_task(api)
    handoff = delivery_task(api, "Follow-on task for the same feature")
    first = create(api, task, key="feature-generation", idem="generation-one")
    response = api.patch("/api/v2/assignment-branches/" + first["id"], json={
        "expected_revision": first["revision"], "phase": "cancelled", "note": "Scope changed; return to source role",
        "checkpoint": {}, "handoff_task_id": handoff["id"],
    }, headers={"Authorization": "Bearer ana-test", "Idempotency-Key": "cancel-generation-one"})
    assert response.status_code == 200, response.text
    cancelled = response.json()
    assert cancelled["phase"] == "cancelled"
    with api.app.state.store.read() as c:
        with pytest.raises(Problem) as retired:
            api.app.state.auth.require_bot_contact(
                c, Identity("human:ana", "human"), "bot:" + first["bot"],
                task_id=task["id"], kind="comment")
        assert retired.value.code == "assignment_retired"
    with api.app.state.store.read() as c:
        assert H.task(c, task["id"])["owner"] == "bot:cpo"
        assert H.task(c, handoff["id"])["owner"] == "bot:cpo"
        assert H.bot(c, first["bot"])["state"] == "archived"
        events = [json.loads(row[0]) for row in c.execute(
            "SELECT detail_json FROM assignment_branch_events WHERE assignment_id=? ORDER BY created,id", (first["id"],))]
        assert len(events) == 2 and events[-1]["handoff_task_id"] == handoff["id"]
    second = create(api, task, key="feature-generation", generation=2, idem="generation-two")
    assert second["bot"] != first["bot"] and second["generation"] == 2


def test_expired_lease_does_not_release_an_unsettled_assignment(api):
    prepare_source(api)
    task = delivery_task(api, "Lease expiry must not free this assignment")
    made = create(api, task, key="lease-expiry", idem="lease-expiry-create")
    set_task_status(api, task["id"], "review")
    now = H.now()
    with api.app.state.store.transaction() as c:
        job = c.execute("SELECT id FROM jobs WHERE bot=? AND state='queued'", (made["bot"],)).fetchone()
        assert job
        c.execute("UPDATE jobs SET state='running' WHERE id=?", (job["id"],))
        c.execute("UPDATE assignment_branches SET phase='working' WHERE id=?", (made["id"],))
        c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created,started,thread_id) "
                  "VALUES(?,?,?,?,1,?,'running',?,?,?,?)",
                  (H.new_id(), job["id"], made["bot"], made["runner_id"], "expired-lease-token-hash",
                   H.shift(now, seconds=-60), now, now, "synthetic-thread"))
    response = api.patch("/api/v2/assignment-branches/" + made["id"], json={
        "expected_revision": made["revision"], "phase": "waiting_review", "note": "Lease expired",
        "checkpoint": {"commit": "synthetic-last-safe-head", "next": "reconcile expired lease"}
    }, headers={"Authorization": "Bearer ana-test", "Idempotency-Key": "expired-lease-release"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "assignment_busy"
    assert get(api, "bots/cpo/assignment-branches")["active"] == 1
    assert get(api, "bots/cpo/assignment-branches")["assignments"][0]["phase"] == "working"


def test_pause_resume_review_release_archive_preserves_assignment_history(api):
    computer = prepare_source(api)
    task = delivery_task(api, "Release an accepted feature")
    made = create(api, task, key="release-feature", idem="release-feature-create")

    def transition(phase, key, **extra):
        current = get(api, "bots/cpo/assignment-branches")["assignments"]
        row = next(item for item in current if item["id"] == made["id"])
        response = api.patch("/api/v2/assignment-branches/" + row["id"], json={
            "expected_revision": row["revision"], "phase": phase, **extra
        }, headers={"Authorization": "Bearer ana-test", "Idempotency-Key": key})
        assert response.status_code == 200, response.text
        return response.json()

    paused = transition("paused", "release-pause", note="Pause at a safe checkpoint",
                        checkpoint={"commit": "synthetic-1", "next": "resume verification"})
    assert paused["checkpoint"]["commit"] == "synthetic-1"
    resumed = transition("working", "release-resume", note="Resume from the recorded checkpoint")
    assert resumed["phase"] == "working"
    set_task_status(api, task["id"], "review")
    review = transition("waiting_review", "release-review", note="Submitted for review")
    assert review["phase"] == "waiting_review"
    set_task_status(api, task["id"], "ready")
    waiting_release = transition("waiting_release", "release-wait", note="Approved; waiting for deployment")
    assert waiting_release["phase"] == "waiting_release"
    set_task_status(api, task["id"], "done")
    verifying = transition("verifying", "release-verifying", note="Deployment acceptance is being checked")
    assert verifying["phase"] == "verifying"
    unreviewed = api.patch("/api/v2/assignment-branches/" + made["id"], json={
        "expected_revision": verifying["revision"], "phase": "archived", "note": "Attempt archive without learning review",
        "deployed_version": "v1.2.3", "acceptance_receipt": "acceptance:42",
        "learning_receipt": "source-lesson-commit:abc123", "evidence_receipt": "test-report:9"
    }, headers={"Authorization": "Bearer ana-test", "Idempotency-Key": "archive-without-learning-review"})
    assert unreviewed.status_code == 409 and unreviewed.json()["error"]["code"] == "assignment_learning_receipt"
    reviewed = transition("verifying", "release-learning-review", note="Reviewed generalized learning",
                          reviewed_learning_note="Preserve both commits when a learning branch diverges.",
                          confirm_learning_review=True)
    assert reviewed["learning_message_id"]
    premature = api.patch("/api/v2/assignment-branches/" + made["id"], json={
        "expected_revision": reviewed["revision"], "phase": "archived", "note": "Attempt early archive",
        "deployed_version": "v1.2.3", "acceptance_receipt": "acceptance:42",
        "learning_receipt": "source-lesson-commit:abc123", "evidence_receipt": "test-report:9"
    }, headers={"Authorization": "Bearer ana-test", "Idempotency-Key": "archive-with-pending-job"})
    assert premature.status_code == 409 and premature.json()["error"]["code"] == "assignment_pending"
    with api.app.state.store.read() as c:
        assert H.bot(c, made["bot"])["state"] == "active"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='delivered' WHERE bot=? AND state='queued'", (made["bot"],))
    archived = transition("archived", "release-archive", note="Verified deployed result",
                           deployed_version="v1.2.3", acceptance_receipt="acceptance:42",
                           learning_receipt="source-lesson-commit:abc123", evidence_receipt="test-report:9")
    assert archived["phase"] == "archived"
    with api.app.state.store.read() as c:
        assert H.bot(c, made["bot"])["state"] == "archived"
        assert c.execute("SELECT 1 FROM assignment_branches WHERE id=?", (made["id"],)).fetchone()
        assert c.execute("SELECT 1 FROM assignments WHERE bot=?", (made["bot"],)).fetchone()
        assert H.task(c, task["id"])["owner"] == "bot:" + made["bot"]

    cleanup_path = "/api/v2/assignment-branches/" + made["id"] + "/cleanup"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET capabilities_json='[\"assignment_instances_v1\"]' WHERE id=?",
                  (computer["runner_id"],))
    old_runner = api.post(cleanup_path, json={"expected_revision": archived["revision"]},
                          headers={"Authorization": "Bearer ana-test", "Idempotency-Key": "cleanup-old-runner"})
    assert old_runner.status_code == 409 and old_runner.json()["error"]["code"] == "assignment_cleanup_runner"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET capabilities_json='[\"assignment_instances_v1\",\"assignment_cleanup_v1\"]' WHERE id=?",
                  (computer["runner_id"],))
        job = c.execute("SELECT id FROM jobs WHERE bot=?", (made["bot"],)).fetchone()
        now = H.now()
        c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created,started,thread_id) "
                  "VALUES(?,?,?,?,1,?,'uncertain',?,?,?,?)",
                  (H.new_id(), job["id"], made["bot"], made["runner_id"], "cleanup-uncertain-token-hash",
                   H.shift(now, seconds=-60), now, now, "synthetic-cleanup-thread"))
    leased = api.post(cleanup_path, json={"expected_revision": archived["revision"]},
                      headers={"Authorization": "Bearer ana-test", "Idempotency-Key": "cleanup-with-uncertain-attempt"})
    assert leased.status_code == 409 and leased.json()["error"]["code"] == "assignment_cleanup_busy"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed' WHERE bot=? AND state='uncertain'", (made["bot"],))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='queued' WHERE bot=? AND state='delivered'", (made["bot"],))
    busy = api.post(cleanup_path, json={"expected_revision": archived["revision"]},
                    headers={"Authorization": "Bearer ana-test", "Idempotency-Key": "cleanup-with-pending-reply"})
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "assignment_cleanup_busy"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='delivered' WHERE bot=? AND state='queued'", (made["bot"],))
    requested = api.post(cleanup_path, json={"expected_revision": archived["revision"]},
                         headers={"Authorization": "Bearer ana-test", "Idempotency-Key": "cleanup-request"})
    assert requested.status_code == 200, requested.text
    assert requested.json()["state"] == "requested"
    queue = get(api, "runners/assignment-cleanups", computer["token"])["cleanups"]
    assert len(queue) == 1 and queue[0]["assignment_id"] == made["id"] and queue[0]["attempt"] == 1
    blocked = api.post("/api/v2/runners/assignment-cleanups/" + made["id"],
                       json={"attempt": 1, "result": "blocked", "detail": "Synthetic dirty-tree refusal"},
                       headers={"Authorization": "Bearer " + computer["token"], "Idempotency-Key": "cleanup-blocked"})
    assert blocked.status_code == 200 and blocked.json()["state"] == "blocked"
    assert get(api, "bots/cpo/assignment-branches")["assignments"][0]["cleanup"]["detail"] == "Synthetic dirty-tree refusal"
    retried = api.post(cleanup_path, json={"expected_revision": archived["revision"]},
                       headers={"Authorization": "Bearer ana-test", "Idempotency-Key": "cleanup-retry"})
    assert retried.status_code == 200 and retried.json()["state"] == "requested" and retried.json()["attempt"] == 2
    assert get(api, "runners/assignment-cleanups", computer["token"])["cleanups"][0]["attempt"] == 2
    stale = api.post("/api/v2/runners/assignment-cleanups/" + made["id"],
                     json={"attempt": 1, "result": "complete", "detail": ""},
                     headers={"Authorization": "Bearer " + computer["token"], "Idempotency-Key": "cleanup-stale-attempt"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "assignment_cleanup_attempt"
    complete = api.post("/api/v2/runners/assignment-cleanups/" + made["id"],
                        json={"attempt": 2, "result": "complete", "detail": ""},
                        headers={"Authorization": "Bearer " + computer["token"], "Idempotency-Key": "cleanup-complete"})
    assert complete.status_code == 200 and complete.json()["state"] == "complete"
    assert get(api, "runners/assignment-cleanups", computer["token"])["cleanups"] == []
    with api.app.state.store.read() as c:
        assert H.bot(c, made["bot"])["state"] == "archived"
        assert H.task(c, task["id"])["owner"] == "bot:" + made["bot"]
        actions = [row[0] for row in c.execute(
            "SELECT action FROM assignment_branch_events WHERE assignment_id=?", (made["id"],))]
        assert actions.count("cleanup_requested") == 2
        assert "cleanup_blocked" in actions and "cleanup_complete" in actions


def test_reviewed_learning_reaches_source_without_the_task_transcript_and_dedupes(api):
    prepare_source(api)
    task = post(api, "tasks", {"owner": "cpo", "title": "Private delivery headline",
                                "body": "PRIVATE_TASK_BODY customer deployment details"})
    made = create(api, task, key="learning-note", idem="learning-note-create")
    endpoint = "/api/v2/assignment-branches/" + made["id"]
    unconfirmed = api.patch(endpoint, json={
        "expected_revision": made["revision"], "reviewed_learning_note": "Use a bounded retry budget."
    }, headers={"Authorization": "Bearer ana-test", "Idempotency-Key": "unconfirmed-learning"})
    assert unconfirmed.status_code == 409
    lesson = "Use bounded retries for transient API reads and retain the original operation key."
    first = api.patch(endpoint, json={"expected_revision": made["revision"],
                                      "reviewed_learning_note": lesson, "confirm_learning_review": True},
                      headers={"Authorization": "Bearer ana-test", "Idempotency-Key": "reviewed-learning-one"})
    assert first.status_code == 200, first.text
    message_id = first.json()["learning_message_id"]
    assert message_id
    with api.app.state.store.read() as c:
        message = H.message(c, message_id)
        conversation = H.conversation(c, message["conversation_id"])
        assert message["from_actor"] == "human:ana" and message["to_actor"] == "bot:cpo"
        assert conversation["task_id"] is None
        assert lesson in message["body"]
        assert "PRIVATE_TASK_BODY" not in message["body"]
        assert "Private delivery headline" not in message["body"]
        refs = json.loads(message["refs_json"])
        assert refs["assignment_learning_reviewed"] == made["id"]
        count = c.execute("SELECT count(*) FROM messages WHERE id=?", (message_id,)).fetchone()[0]
        assert count == 1
    retry = api.patch(endpoint, json={"expected_revision": first.json()["revision"],
                                      "reviewed_learning_note": lesson, "confirm_learning_review": True},
                      headers={"Authorization": "Bearer ana-test", "Idempotency-Key": "reviewed-learning-retry"})
    assert retry.status_code == 200, retry.text
    assert retry.json()["learning_message_id"] == message_id
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM messages WHERE to_actor=? AND body LIKE ?",
                         ("bot:cpo", "%" + lesson.split()[0] + "%")).fetchone()[0] == 1


def test_concurrent_retries_share_one_actor_and_concurrent_requests_cannot_overbook(api):
    prepare_source(api)
    task = delivery_task(api, "Idempotent assignment")
    payload = {"assignment_key": "concurrent-key", "generation": 1, "task_id": task["id"],
               "display_name": "Pro Workflow Engineer"}

    def send(key):
        return api.post("/api/v2/bots/cpo/assignment-branches", json=payload,
                        headers={"Authorization": "Bearer ana-test", "Idempotency-Key": key})

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(send, ("concurrent-retry-a", "concurrent-retry-b")))
    assert [reply.status_code for reply in replies] == [200, 200]
    assert replies[0].json()["id"] == replies[1].json()["id"]
    assert get(api, "bots/cpo/assignment-branches")["active"] == 1

    # The SQLite write transaction and cap check allow one racing request and reject the other
    # before it creates an actor.
    third_task = delivery_task(api, "Third slot")
    third = create(api, third_task, key="concurrent-third", idem="concurrent-third")
    candidates = [delivery_task(api, "Racing slot one"), delivery_task(api, "Racing slot two")]

    def allocate(index):
        body = {"assignment_key": f"concurrent-race-{index}", "generation": 1,
                "task_id": candidates[index]["id"], "display_name": "Racing Engineer"}
        return api.post("/api/v2/bots/cpo/assignment-branches", json=body,
                        headers={"Authorization": "Bearer ana-test", "Idempotency-Key": f"race-{index}"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        raced = list(pool.map(allocate, (0, 1)))
    assert sorted(reply.status_code for reply in raced) == [200, 409]
    assert sum(reply.status_code == 409 and reply.json()["error"]["code"] == "assignment_capacity" for reply in raced) == 1
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM assignment_branches").fetchone()[0] == 3


def test_concurrent_distinct_assignment_keys_cannot_claim_the_same_delivery_task(api):
    prepare_source(api)
    task = delivery_task(api, "Single-owner feature")

    def allocate(key):
        body = {"assignment_key": key, "generation": 1, "task_id": task["id"],
                "display_name": "Temporary Engineer"}
        return api.post("/api/v2/bots/cpo/assignment-branches", json=body,
                        headers={"Authorization": "Bearer ana-test", "Idempotency-Key": key + "-idem"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(allocate, ("same-task-one", "same-task-two")))
    assert sorted(result.status_code for result in results) == [200, 409]
    assert any(result.status_code == 409 and result.json()["error"]["code"] == "task_owner" for result in results)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM assignment_branches WHERE task_id=?", (task["id"],)).fetchone()[0] == 1
        assert H.task(c, task["id"])["owner"].startswith("bot:cpo-work-")
