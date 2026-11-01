"""A builder hand-off from Tico is the first of the bot's queued jobs a claim gives out (backend/verdict_moves.py)."""
from backend.store import H
from backend.tests.test_api import api, assign, claim, post, ready, runner  # noqa: F401
from backend import verdict_moves as V


def claimed_refs(api, attempt):
    with api.app.state.store.read() as c:
        row = c.execute("SELECT m.refs FROM attempts a JOIN jobs j ON j.id=a.job_id "
                        "JOIN messages m ON m.id=j.message_id WHERE a.id=?", (attempt["id"],)).fetchone()
    return H._json(row[0], {})


def test_a_hand_off_is_claimed_before_the_bots_older_higher_ranked_job(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    with api.app.state.store.transaction() as c:
        first = H.task_create(c, "bot:coo", "Sort the invoices", "Sort them.", "bot:ops", lint=False)
        later = H.task_create(c, "bot:coo", "Fix the import", "Fix it.", "bot:ops", lint=False)
        H.task_update(c, "bot:ops", first["id"], rank=1)
        H.task_update(c, "bot:ops", later["id"], rank=9)
        c.execute("DELETE FROM jobs")
        H._wake(c, H.task(c, first["id"]), "bot:ops", "Look at the invoices again.", {"run_now": True})
        H._wake(c, H.task(c, later["id"]), "bot:ops", "Review 2 gave changes.", {V.HANDOFF: "verdict"})
        queued = c.execute("SELECT count(*) FROM jobs WHERE bot_id='ops' AND state='queued' AND held=0").fetchone()[0]
    assert queued == 2
    assert claimed_refs(api, claim(api, r)).get(V.HANDOFF) == "verdict"


def test_a_hand_off_always_wakes_and_is_never_held_for_a_batch(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=json_set(config,'$.wake_preset','token_saver') WHERE bot='ops'")
        task = H.task_create(c, "bot:coo", "Fix the import", "Fix it.", "bot:ops", lint=False)
        c.execute("DELETE FROM jobs")
        H._wake(c, H.task(c, task["id"]), "bot:ops", "Checks finished: failed.",
                {"wake": "pr_checks_done", "pr_head": "a" * 40, "pr_result": "fail", V.HANDOFF: "failed_check"})
        job = c.execute("SELECT j.state,j.held,m.refs FROM jobs j JOIN messages m ON m.id=j.message_id "
                        "WHERE j.bot_id='ops'").fetchone()
    assert job is not None and (job["state"], job["held"]) == ("queued", 0)
    assert H._json(job["refs"], {})["rule"] == "builder_handoff"
