"""A bot's last run that ended without finishing is said plainly, never as a bare "Crashed" (Oct 22: BotOps showed a
red Crashed for a run its computer took and never started, with nothing lost and nothing to do)."""
from backend.tests.test_api import api, claim, expire, get, post, ready, setup_attempt  # noqa: F401 (fixture)
from backend.store import H
from backend.tests.test_execution_review import interrupted


def lapse(api, attempt):
    """Its lease runs out and the server's sweep sees it."""
    expire(api, attempt["id"])
    with api.app.state.store.transaction() as c:
        api.app.state.execution.expire(c)


def status(api, bot="ops"):
    return get(api, f"status?bot={bot}")["status"]


def finish(api, machine, attempt):
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "next-" + attempt["id"]}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."},
         machine["token"])


def test_a_run_whose_lease_lapsed_before_it_started_is_a_soft_line_not_a_crash(api):
    from backend.execution import NOT_STARTED
    machine, _, lapsed = setup_attempt(api)
    lapse(api, lapsed)                            # back in the queue
    ready(api, machine, ["ops"])
    line = status(api)
    assert line["state"] == "idle"
    assert line["last_stop"] == {**line["last_stop"], "kind": "not_started", "tone": "soft", "needs_person": False,
                                 "action": None, "text": NOT_STARTED, "run_id": lapsed["id"]}
    assert "lease ran out" in line["last_stop"]["reason"]
    again = claim(api, machine)
    assert again["job_id"] == lapsed["job_id"]
    assert status(api)["last_stop"] is None      # running again: nothing to explain
    finish(api, machine, again)
    assert status(api)["state"] == "idle" and status(api)["last_stop"] is None


def test_the_line_clears_when_the_run_that_did_not_start_is_no_longer_waiting(api):
    machine, _, lapsed = setup_attempt(api)
    lapse(api, lapsed)
    with api.app.state.store.transaction() as c:  # its work was done elsewhere first (an obsolete notice, Oct 22)
        c.execute("UPDATE jobs SET state='canceled' WHERE id=?", (lapsed["job_id"],))
    line = status(api)
    assert line["state"] == "idle" and line["last_stop"] is None


def test_covering_a_queued_notice_clears_its_status_and_notifies_open_headers(api):
    import json
    machine, message, lapsed = setup_attempt(api)
    lapse(api, lapsed)
    task = post(api, "tasks", {"owner": "human:ana", "title": "Already handled", "body": "Handled elsewhere."})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (task["id"],))
        c.execute("UPDATE messages SET sender_id=?,kind='notice',refs=? WHERE id=?",
                  (H.KEEPER, json.dumps({"task": task["id"]}), message["id"]))
        cursor = c.execute("SELECT coalesce(max(seq),0) FROM changes").fetchone()[0]
    assert claim(api, machine) is None
    line = status(api)
    assert line["last_stop"] is None and line["focus"] == ""
    with api.app.state.store.read() as c:
        assert c.execute("SELECT 1 FROM changes WHERE seq>? AND topic='bots' AND bot='ops'", (cursor,)).fetchone()


def test_a_computer_hands_back_a_run_it_could_not_start_at_once(api):
    from backend.execution import HAND_BACK_WAIT, HANDED_BACK
    machine, _, attempt = setup_attempt(api)
    post(api, f"attempts/{attempt['id']}/events", {"events": [{"seq": 1, "kind": "diagnostic", "payload": {
        "text": "APIError: execution interrupted (Tico could not confirm this request); inspect local runner"}}]},
         machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "interrupted", "last_seq": 1, "text": ""},
         machine["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0] == "expired"
        assert c.execute("SELECT state FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0] == "queued"
    line = status(api)
    assert line["state"] == "idle" and line["last_stop"]["tone"] == "soft"
    assert line["last_stop"]["reason"].startswith(HANDED_BACK)
    # Back in seconds, not at the lease's 90: a short wait, then the same job again.
    ready(api, machine, ["ops"])
    assert claim(api, machine) is None
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET finished_at=? WHERE id=?", (H.shift(H.now(), seconds=-HAND_BACK_WAIT - 1), attempt["id"]))
    assert claim(api, machine)["job_id"] == attempt["job_id"]


def test_quick_hand_backs_wait_between_tries_and_do_not_fail_the_run(api, monkeypatch):
    """A runner claims every quarter second: a step that fails fast before every start must not spend every pre-start
    try in a few seconds and fail the run."""
    from backend import execution
    monkeypatch.setattr(execution, "PRE_START_TRIES", 3)
    machine, _, attempt = setup_attempt(api)
    for _ in range(20):                           # a minute of claims, each handed straight back
        if attempt:
            post(api, f"attempts/{attempt['id']}/complete", {"outcome": "interrupted", "last_seq": 0, "text": ""},
                 machine["token"])
        ready(api, machine, ["ops"])
        attempt = claim(api, machine)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM attempts").fetchone()[0] == 1
        job = c.execute("SELECT id,state FROM jobs").fetchone()
        assert job["state"] == "queued"
        waits = []
        for n in (1, 2, 3):                       # the wait doubles with each hand-back, up to its cap
            c.execute("UPDATE attempts SET finished_at=? WHERE job_id=?", (H.now(), job["id"]))
            until = execution.handed_back_until(c, dict(c.execute("SELECT * FROM jobs").fetchone()))
            waits.append(round((H.parse_ts(until) - H.parse_ts(H.now())).total_seconds()))
            if n < 3:
                c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,"
                          "finished_at,final_text) SELECT ?,job_id,bot_id,runner_id,generation,?,state,lease_until,created_at,"
                          "finished_at,final_text FROM attempts WHERE job_id=? LIMIT 1", (f"x{n}", f"h{n}", job["id"]))
    assert [w - waits[0] for w in waits] == [0, execution.HAND_BACK_WAIT, 3 * execution.HAND_BACK_WAIT]


def test_a_stop_before_the_start_and_a_completed_report_are_not_hand_backs(api):
    machine, _, attempt = setup_attempt(api)
    # Only a run that started can finish.
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Done."},
         machine["token"], expected=409)
    # Main settles a person's Stop immediately, even before the runner acknowledged start.
    post(api, "bots/ops/stop", {"run_id": attempt["id"]})
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "interrupted", "last_seq": 0, "text": ""},
         machine["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone()[0] == "canceled"
    assert status(api)["state"] == "idle" and status(api)["last_stop"] is None


def test_a_run_that_stopped_partway_says_so_and_offers_the_review(api):
    interrupted(api)
    line = status(api)
    assert line["state"] == "crashed"
    stop = line["last_stop"]
    assert (stop["kind"], stop["tone"], stop["needs_person"], stop["action"]) == ("stopped", "alert", True, "review")
    assert stop["text"].startswith("Last run stopped partway")
    assert "lease ran out" in stop["reason"]


def test_a_run_its_computer_never_started_after_every_try_names_the_computer(api, monkeypatch):
    from backend import execution
    monkeypatch.setattr(execution, "PRE_START_TRIES", 1)
    machine, _, lapsed = setup_attempt(api)
    lapse(api, lapsed)
    line = status(api)
    assert line["state"] == "crashed"
    stop = line["last_stop"]
    assert (stop["kind"], stop["tone"], stop["needs_person"], stop["action"]) == ("never_started", "alert", True, None)
    assert stop["text"] == "Your ops couldn't start on Test Mac; check that Computer."


def test_the_latest_failed_start_is_not_hidden_by_an_older_held_run(api):
    from backend.execution import settle_status
    machine, message, old = interrupted(api)
    newer = post(api, "chat/ops", {"text": "Another request."})
    with api.app.state.store.transaction() as c:
        job = c.execute("SELECT id FROM jobs WHERE message_id=?", (newer["id"],)).fetchone()[0]
        c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,"
                  "finished_at,final_text) SELECT 'new-start',?,bot_id,runner_id,generation,'new-hash','failed',lease_until,"
                  "created_at,?,? FROM attempts WHERE id=?",
                  (job, H.now(), "Check the newer Computer", old["id"]))
        c.execute("UPDATE jobs SET state='failed',attempt_id='new-start' WHERE id=?", (job,))
        settle_status(c, "ops", "crashed", "Check the newer Computer", stop="new-start")
    stop = status(api)["last_stop"]
    assert stop["run_id"] == "new-start" and stop["kind"] == "never_started"
    assert stop["action"] is None and "newer Computer" in stop["text"]
