"""A bot's last run that ended without finishing is said plainly, never as a bare "Crashed" (Oct 22: BotOps showed a
red Crashed for a run its computer took and never started, with nothing lost and nothing to do)."""
from backend.tests.test_api import api, claim, expire, get, post, ready, setup_attempt  # noqa: F401 (fixture)
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
                                 "action": None, "text": NOT_STARTED, "attempt_id": lapsed["id"]}
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
        c.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (lapsed["job_id"],))
    line = status(api)
    assert line["state"] == "idle" and line["last_stop"] is None


def test_a_computer_hands_back_a_run_it_could_not_start_at_once(api):
    from backend.execution import HANDED_BACK
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
    ready(api, machine, ["ops"])
    assert claim(api, machine)["job_id"] == attempt["job_id"]     # no 90 s wait for the lease


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
