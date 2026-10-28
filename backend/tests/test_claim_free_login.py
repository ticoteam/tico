"""A queued job whose bot's own login and codex are held, with claude signed in and free on this computer: Health
(queued_reasons) says the claim hands it out, so the claim must hand it out."""
from backend import login_holds
from backend.execution import queued_reasons
from backend.tests.test_api import api, assign, post, runner
from backend.tests.test_login_holds import at, providers_on, signed_in


def test_claim_hands_out_the_queued_job_to_the_free_claude_fallback_that_health_says_is_free(api, monkeypatch):
    providers_on(api)
    at(monkeypatch, "2026-10-20T10:30:00.000000Z", api)
    machine = runner(api)
    assign(api, machine, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET capacity=8 WHERE id=?", (machine["runner_id"],))
    signed_in(api, machine, ["ops"], ["codex", "claude"])
    hit, reset = "2026-10-20T10:00:00.000000Z", "2026-10-20T12:00:00.000000Z"
    with api.app.state.store.transaction() as c:
        login_holds.record(c, machine["runner_id"], "", "fake", hit=hit, reset=reset)
        login_holds.record(c, machine["runner_id"], "", "codex", hit=hit, reset=reset)
    post(api, "tasks", {"owner": "ops", "title": "Free work", "body": "Please do it."})
    with api.app.state.store.read() as c:
        reasons = list(queued_reasons(c, api.app.state.store.settings, machine["runner_id"], "ops"))
    assert [why for _, why in reasons] == [None], "Health should say the queued job is handed out"
    # The sim's claim body: no bot of this runner is named busy, and the runner has free capacity.
    body = {"next_run": True, "busy_bots": [], "busy_lanes": [], "start_places": True}
    reply = post(api, "jobs/claim", body, token=machine["token"])
    assert reply["attempt"] is not None, f"claim answered {reply} for work Health says is handed out"
    assert reply["attempt"]["job_id"] == reasons[0][0]["id"]
    assert reply["attempt"]["config"]["primary_held"]
