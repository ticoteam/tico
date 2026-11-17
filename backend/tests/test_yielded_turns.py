"""A turn that yields for an update goes back in the queue; one stopped before it started ends unstarted."""

from backend.tests.helpers.api import assign, get, post, runner  # noqa: F401


def test_a_turn_that_gave_its_place_up_for_an_update_goes_back_in_the_queue_unanswered_and_not_held_for_review(api):
    from backend.tests.helpers.api import claim, setup_attempt

    r, origin, a = setup_attempt(api)
    post(api, f"attempts/{a['id']}/started", {"thread_id": "thread"}, r["token"])
    line = "Paused at the computer's drain time limit; work and session saved, queued to continue"
    post(
        api,
        f"attempts/{a['id']}/events",
        {"events": [{"seq": 1, "kind": "status", "payload": {"yielded": line}}]},
        r["token"],
    )
    post(
        api,
        f"attempts/{a['id']}/complete",
        {
            "outcome": "interrupted",
            "yielded": True,
            "last_seq": 1,
            "text": "Pushed my work in progress; next: the summary table.",
        },
        r["token"],
    )
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE message_id=?", (origin["id"],)).fetchone()[0] == "queued"
        assert not c.execute("SELECT 1 FROM messages WHERE in_reply_to=?", (origin["id"],)).fetchone()
    # The bot is idle with the pause line until its continuation starts, not crashed, and Health raises nothing.
    [status] = [s for s in get(api, "status")["bots"] if s and s["bot"] == a["bot"]]
    assert (status["state"], status["focus"]) == ("idle", line)
    assert not [i for i in get(api, "operations")["issues"] if i.get("bot") == a["bot"]]
    # It continues on the next claim, as the same work.
    again = claim(api, r)
    assert again["job_id"] == a["job_id"] and again["id"] != a["id"]


def test_a_person_stopping_a_turn_before_it_started_ends_it_unstarted(api):
    from backend.tests.helpers.api import claim, ready

    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    post(api, "tasks", {"owner": "ops", "title": "Count the words", "body": "Count them."})
    a = claim(api, r)
    post(api, "bots/ops/stop", {"run_id": a["id"]})
    assert post(api, f"attempts/{a['id']}/renew", {}, r["token"])["stop"] is True
    # The runner stops before it acknowledges the start (waiting for a place, or preparing): nothing ran.
    post(api, f"attempts/{a['id']}/complete", {"outcome": "interrupted", "last_seq": 0}, r["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (a["job_id"],)).fetchone()[0] == "canceled"
        assert c.execute("SELECT 1 FROM messages WHERE kind='notice' AND body='Stopped by Ana'").fetchone()
