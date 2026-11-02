"""A usage limit whose fallback could not start the turn waits for the reset instead of holding the run for a person,
and runs held that way before resume or are dismissed on their own (Oct 21: 27 of Reviewer 2's turns hopped to a
fallback that failed at start and were each held until a person dismissed it)."""
import json

from backend import login_holds
from backend.store import H
from backend.tests.test_api import claim, get, post
from backend.tests.test_api import api  # noqa: F401
from backend.tests.test_execution_review import job_state, sweep
from backend.tests.test_login_holds import at, signed_in, two_bots

WEEKLY = "You've hit your weekly limit · resets Oct 26, 8pm (UTC)"
LOCK = ("--session-id requires session logging: failed to create session lock directory for session x: "
        "Permission denied (os error 13)")


def hop_fails(api, machine, attempt):
    """What the runner sent on Oct 21 19:54Z: the bot's own login hit its weekly limit, the turn moved to the company's
    default fallback, and that fallback failed before it did anything."""
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t-" + attempt["id"]}, machine["token"])
    rows = [("message", {"text": WEEKLY}),
            ("error", {"error": WEEKLY, "kind": "turn_failed", "limit": True}),
            ("status", {"state": "idle"}),
            ("diagnostic", {"fallback": "codex", "text": "fake hit its usage limit; running this turn on codex "
                                                         "(no fallback set; another provider signed in here)"}),
            ("status", {"tool_events": False}),
            ("status", {"state": "active"}),
            ("error", {"error": LOCK, "kind": "turn_failed", "limit": False}),
            ("status", {"state": "idle"})]
    post(api, f"attempts/{attempt['id']}/events", {"events": [
        {"seq": n, "kind": kind, "payload": payload} for n, (kind, payload) in enumerate(rows, 1)]}, machine["token"])
    post(api, f"attempts/{attempt['id']}/complete", {
        "outcome": "failed", "last_seq": len(rows), "limited": False, "fallback": "codex", "primary_limited": True,
        "text": ""}, machine["token"])


def test_a_limit_whose_fallback_fails_at_start_waits_for_the_reset_not_for_a_person(api, monkeypatch):
    machine = two_bots(api, monkeypatch)
    at(monkeypatch, "2026-10-21T19:54:13.000000Z", api)
    post(api, "tasks", {"owner": "ops", "title": "Review PR 1", "body": "Please review it."})
    first = claim(api, machine)
    hop_fails(api, machine, first)
    assert job_state(api, first["job_id"]) == "queued"
    assert get(api, "bots/ops/execution-review")["jobs"] == []
    with api.app.state.store.read() as c:
        assert H.status(c, "ops")["state"] == "limited"
        hold = login_holds.get(c, machine["runner_id"], "", "fake")
        assert hold["until"].startswith("2026-10-26T20:00") and "ops" in hold["failed_fallback"]
        assert c.execute("SELECT count(*) FROM events WHERE action='attempt.limited' AND target=?",
                         (first["id"],)).fetchone()[0] == 1
    # The same fallback is not tried again in this hold, though Codex still says it is signed in.
    at(monkeypatch, "2026-10-21T21:00:00.000000Z", api)
    signed_in(api, machine, ["ops", "finance"], ["codex"])
    assert claim(api, machine, bot="ops") is None
    # A fallback the bot names now is another one: the next turn goes there.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=json_set(config,'$.fallback',json(?)) WHERE bot='ops'",
                  (json.dumps({"harness": "codex", "model": "gpt-5.5"}),))
    moved = claim(api, machine, bot="ops")
    assert moved["job_id"] == first["job_id"] and moved["config"]["primary_held"]


def test_without_another_fallback_the_job_runs_again_after_the_reset(api, monkeypatch):
    machine = two_bots(api, monkeypatch)
    at(monkeypatch, "2026-10-21T19:54:13.000000Z", api)
    post(api, "tasks", {"owner": "ops", "title": "Review PR 1", "body": "Please review it."})
    first = claim(api, machine)
    hop_fails(api, machine, first)
    at(monkeypatch, "2026-10-26T20:01:00.000000Z", api)
    signed_in(api, machine, ["ops", "finance"], ["codex"])
    again = claim(api, machine, bot="ops")
    assert again["job_id"] == first["job_id"] and not again["config"].get("primary_held")


def held_the_old_way(api, machine, attempt):
    """A run held before this change: the same turn left `uncertain`, with no hold on the login and no limit status."""
    hop_fails(api, machine, attempt)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='uncertain' WHERE id=?", (attempt["job_id"],))
        c.execute("UPDATE attempts SET result=json_remove(result,'$.fallback_failed'),"
                  "result=json_set(result,'$.limited',json('false')) WHERE id=?", (attempt["id"],))
        c.execute("DELETE FROM registry_metadata WHERE key LIKE 'login-hold:%'")
        c.execute("UPDATE bot_status SET state='idle',focus='One run stopped; saved for later' WHERE bot='ops'")


def test_runs_held_after_a_limit_resume_together_oldest_first_once_it_resets(api, monkeypatch):
    machine = two_bots(api, monkeypatch)
    at(monkeypatch, "2026-10-21T19:54:13.000000Z", api)
    task = post(api, "tasks", {"owner": "ops", "title": "Review PR 1", "body": "Please review it."})
    first = claim(api, machine)
    held_the_old_way(api, machine, first)
    # A second request on the same task, held in the same turn (the runner folded it in).
    post(api, f"tasks/{task['id']}/comments", {"text": "Also check the tests."})
    with api.app.state.store.transaction() as c:
        second = c.execute("SELECT id FROM jobs WHERE bot='ops' AND state='queued' ORDER BY created_at DESC").fetchone()[0]
        c.execute("UPDATE jobs SET state='uncertain',attempt_id=? WHERE id=?", (first["id"], second))
    # Before the reset the limit names, nothing is resumed, and BotOps is not asked to review a run that did nothing.
    at(monkeypatch, "2026-10-21T20:30:00.000000Z", api)
    assert sweep(api) == []
    assert job_state(api, first["job_id"]) == job_state(api, second) == "uncertain"
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM tasks WHERE title LIKE 'Review ops%'").fetchone()
    # After it, both go back together, oldest first (a held run on the task would hold the other).
    at(monkeypatch, "2026-10-26T20:01:00.000000Z", api)
    assert [d["job_id"] for d in sweep(api)] == [first["job_id"], second]
    with api.app.state.store.read() as c:
        note = c.execute("SELECT note FROM job_recovery WHERE job_id=?", (first["job_id"],)).fetchone()[0]
        assert H.status(c, "ops")["focus"] == "Tico resumed 2 runs held by a usage limit"
    assert "stopped on a usage limit before it did anything, and the limit has reset" in note
    signed_in(api, machine, ["ops", "finance"], ["codex"])
    # The claim's own order then applies: a person's message first (Execution.candidate).
    assert claim(api, machine, bot="ops")["job_id"] == second
    assert get(api, "bots/ops/execution-review")["jobs"] == []


def test_a_run_held_after_a_limit_is_dismissed_once_a_later_turn_on_its_task_finished_but_a_chat_run_is_not(
        api, monkeypatch):
    machine = two_bots(api, monkeypatch)
    at(monkeypatch, "2026-10-21T19:54:13.000000Z", api)
    task = post(api, "tasks", {"owner": "ops", "title": "Review PR 1", "body": "Please review it."})
    first = claim(api, machine)
    held_the_old_way(api, machine, first)
    post(api, "chat/ops", {"text": "Please summarize the reviews."})
    with api.app.state.store.transaction() as c:
        chat = c.execute("SELECT id FROM jobs WHERE bot='ops' AND state='queued'").fetchone()[0]
        c.execute("UPDATE jobs SET state='uncertain',attempt_id=? WHERE id=?", (first["id"], chat))
    # Later the bot finished a turn that worked the task (from another of its rooms), and one in that chat.
    at(monkeypatch, "2026-10-21T20:00:00.000000Z", api)
    post(api, "chat/ops", {"text": "Did you get that?"})
    signed_in(api, machine, ["ops", "finance"], ["codex"])
    with api.app.state.store.transaction() as c:
        login_holds.clear(c, machine["runner_id"], "", "fake")
    later = claim(api, machine, bot="ops")
    post(api, f"attempts/{later['id']}/started", {"thread_id": "t-" + later["id"]}, machine["token"])
    post(api, f"attempts/{later['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Here it is."},
         machine["token"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE turns SET task_id=? WHERE id=?", (task["id"], later["id"]))
    at(monkeypatch, "2026-10-21T20:30:00.000000Z", api)
    assert [d["job_id"] for d in sweep(api)] == [first["job_id"]]
    assert job_state(api, first["job_id"]) == "cancelled" and job_state(api, chat) == "uncertain"
    with api.app.state.store.read() as c:
        assert "covered" in c.execute("SELECT note FROM job_recovery WHERE job_id=?", (first["job_id"],)).fetchone()[0]
        assert H.status(c, "ops")["focus"] == "Tico dismissed 1 held run a later run already covered"


def test_a_hop_that_used_an_approval_is_left_for_a_person(api, monkeypatch):
    machine = two_bots(api, monkeypatch)
    at(monkeypatch, "2026-10-21T19:54:13.000000Z", api)
    post(api, "tasks", {"owner": "ops", "title": "Pay the invoice", "body": "Please pay it."})
    first = claim(api, machine)
    with api.app.state.store.transaction() as c:
        message = c.execute("SELECT message_id FROM jobs WHERE id=?", (first["job_id"],)).fetchone()[0]
        c.execute("INSERT INTO approvals(id,kind,message_id,requested_by,created_at,consumed_at) VALUES(?,?,?,?,?,?)",
                  (H.new_id(), "spend", message, "bot:ops", H.now(), H.now()))
    hop_fails(api, machine, first)
    assert job_state(api, first["job_id"]) == "uncertain"
    at(monkeypatch, "2026-10-26T20:01:00.000000Z", api)
    assert sweep(api) == [] and job_state(api, first["job_id"]) == "uncertain"


def test_a_run_held_after_a_limit_resumes_when_the_bot_moves_to_another_model(api, monkeypatch):
    machine = two_bots(api, monkeypatch)
    at(monkeypatch, "2026-10-21T19:54:13.000000Z", api)
    post(api, "tasks", {"owner": "ops", "title": "Review PR 1", "body": "Please review it."})
    first = claim(api, machine)
    held_the_old_way(api, machine, first)
    at(monkeypatch, "2026-10-21T20:30:00.000000Z", api)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_transitions(id,bot,kind,target,requested_by,expected_revision,state,created_at,"
                  "updated_at,applied_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (H.new_id(), "ops", "model", "{}", "human:ana", 0, "applied", H.now(), H.now(), H.now()))
    assert [d["state"] for d in sweep(api)] == ["queued"]
    with api.app.state.store.read() as c:
        assert "moved to another model" in c.execute("SELECT note FROM job_recovery WHERE job_id=?",
                                                     (first["job_id"],)).fetchone()[0]
