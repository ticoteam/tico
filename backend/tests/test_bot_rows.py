"""A bot's queue counts (backend/bot_rows.py `job_queues`) and Health's split of the same queue
(`health.queue_counts`): a queued job that can never be handed out is dead, not queued, and both say so alike."""

from backend import bot_rows, health
from backend import hubdb as H
from backend.tests.helpers.api import assign, claim, post, ready, runner  # noqa: F401  (api is a fixture)


def _interrupted(api):
    """ops has one queued job behind a run the computer lost mid-way, and one chat it can start."""
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    task = post(api, "tasks", {"owner": "ops", "title": "Ship the fix", "body": "Please ship it."})
    attempt = claim(api, r)
    with api.app.state.store.transaction() as c:
        # The computer restarted mid-run: the run may have acted, so its task waits for a person's review.
        c.execute(
            "UPDATE attempts SET state='interrupted', finished_at=?, result=? WHERE id=?",
            (H.now(), '{"text": "Runner restarted during execution"}', attempt["id"]),
        )
        c.execute("UPDATE jobs SET state='uncertain' WHERE id=?", (attempt["job_id"],))
    post(api, f"tasks/{task['id']}/comments", {"text": "Any news?", "needs_action": True})
    post(api, "chat/ops", {"text": "Separate question"})  # a person's own chat is never held by it
    return r


def test_a_job_behind_an_unreviewed_interrupted_run_is_dead_and_health_counts_it_the_same(api):
    r = _interrupted(api)
    assert claim(api, r) is not None  # the chat starts; the task's job never does while the run waits
    who = api.app.state.store.settings.test_identities["ana-test"]
    with api.app.state.store.read_transaction() as c:
        mine = bot_rows.job_queues(c, who, ["ops"])["ops"]
        split = health.queue_counts(c, ["ops"])["ops"]
    assert (mine["dead"], mine["uncertain"]) == (1, 1) and len(mine["dead_ids"]) == 1
    assert (split["queued"], split["dead"], split["uncertain"]) == (mine["queued"], mine["dead"], mine["uncertain"])


def test_job_queues_checks_privacy_for_every_message_in_one_batch(api, monkeypatch):
    from backend import task_privacy as privacy

    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    for i in range(8):
        post(api, "chat/ops", {"text": f"Queue item {i}"})
    calls = []
    real = privacy.readable_messages

    def once(c, actor, messages):
        calls.append(len(list(messages)))
        return real(c, actor, messages)

    monkeypatch.setattr(privacy, "readable_messages", once)
    who = api.app.state.store.settings.test_identities["ana-test"]
    with api.app.state.store.read_transaction() as c:
        queues = bot_rows.job_queues(c, who, ["ops"])
    assert queues["ops"]["queued"] == 8
    assert calls.count(8) == 1
