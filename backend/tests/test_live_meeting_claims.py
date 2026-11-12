"""Who owns a claimed live-meeting turn, and how a lost claim ends (backend/live_meetings.py claim_runner_turn,
abandon_lost_claims). A bot that claims a turn through the meeting API and loses the answer, or crashes, used to stay
busy for the rest of the meeting: the poll listed only pending turns, nothing ever aged the claim out, and the router
passed the bot over as busy. Each test drives the real routes and the router's own admission (_enqueue)."""

import json

from backend import live_meetings as LM
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.api import assign, get, headers, post, runner
from backend.tests.helpers.live_meetings import attach, live, ready  # noqa: F401


def meeting(api):
    mid = post(
        api,
        "live-meetings",
        {"title": "Synthetic lost client", "fallback_window_seconds": 30, "reply_cap": 3, "cooldown_seconds": 0},
    )["id"]
    attach(api, mid, "ops")
    return mid


def invite(api, mid, key, named=False):
    """The router's own automatic invitation of ops (what a routed window does); `named` as when a speaker names it."""
    with api.app.state.store.transaction() as c:
        return LM._enqueue(c, mid, ["ops"], source_key=key, named=named)


def turn_row(api, tid):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM live_meeting_turns WHERE id=?", (tid,)).fetchone())


def age_claim(api, tid):
    seconds = LM.CLAIM_ABANDON_SECONDS + 1
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE live_meeting_turns SET claimed_at=? WHERE id=?", (H.shift(H.now(), seconds=-seconds), tid))


def reply(api, mid, tid, text="Reviewed", token="ops-live"):
    return api.post(f"/api/v2/live-meetings/{mid}/chat", json={"text": text, "turn_id": tid}, headers=headers(token))


def sweep(api):
    api.app.state.live_meeting_routing.sweep()
    assert api.app.state.live_meeting_routing.settle(10)


def test_a_lost_claim_answer_is_shown_to_its_bot_again_and_the_same_claim_is_returned(live):
    mid = meeting(live)
    tid = invite(live, mid, "window:1")[0][0]["id"]
    first = post(live, f"live-meetings/{mid}/turns/{tid}/claim", {}, token="ops-live")
    assert first["status"] == "claimed" and first["by"] == "direct"
    # The client never saw that answer. It reconnects and polls: its own claimed turn is there, marked claimed.
    polled = get(live, f"live-meetings/{mid}/turns", token="ops-live")["turns"]
    assert [(t["id"], t["status"]) for t in polled] == [(tid, "claimed")]
    again = post(live, f"live-meetings/{mid}/turns/{tid}/claim", {}, token="ops-live")
    assert again == first
    assert reply(live, mid, tid).status_code == 200
    assert get(live, f"live-meetings/{mid}/turns", token="ops-live")["turns"] == []


def test_a_crashed_claim_is_abandoned_after_the_bound_and_automatic_invitations_resume(live):
    mid = meeting(live)
    tid = invite(live, mid, "window:1")[0][0]["id"]
    post(live, f"live-meetings/{mid}/turns/{tid}/claim", {}, token="ops-live")
    # Inside the bound the claim holds: the bot is busy, and a sweep leaves it alone.
    sweep(live)
    assert invite(live, mid, "window:2") == ([], [{"bot": "ops", "reason": "busy"}])
    assert turn_row(live, tid)["status"] == "claimed"
    # The client crashed and never comes back. Past the bound the sweep ends the turn as abandoned.
    age_claim(live, tid)
    sweep(live)
    row = turn_row(live, tid)
    assert (row["status"], row["skip_reason"]) == ("skipped", "claim_abandoned")
    # It is not run again: the turn may have acted, so no job is queued for it.
    with live.app.state.store.read() as c:
        states = {
            r[0]
            for r in c.execute(
                "SELECT j.state FROM jobs j JOIN messages m ON m.id=j.message_id "
                "WHERE json_extract(m.refs,'$.live_meeting.turn_id')=?",
                (tid,),
            )
        }
        events = [
            json.loads(r[0])
            for r in c.execute(
                "SELECT payload FROM live_meeting_events WHERE meeting_id=? AND type='meeting.bot_turn_skipped'",
                (mid,),
            )
        ]
    assert states == {"canceled"}
    assert {"turn_id": tid, "bot": "ops", "reason": "claim_abandoned"} in events
    selected, skipped = invite(live, mid, "window:3")
    assert [t["bot"] for t in selected] == ["ops"] and skipped == []


def test_the_routers_own_admission_ends_a_lost_claim_without_waiting_for_a_sweep(live):
    mid = meeting(live)
    tid = invite(live, mid, "window:1")[0][0]["id"]
    post(live, f"live-meetings/{mid}/turns/{tid}/claim", {}, token="ops-live")
    age_claim(live, tid)
    selected, skipped = invite(live, mid, "window:2")
    assert [t["bot"] for t in selected] == ["ops"] and skipped == []
    assert turn_row(live, tid)["skip_reason"] == "claim_abandoned"


def test_a_stale_reply_after_abandonment_posts_nothing(live):
    mid = meeting(live)
    tid = invite(live, mid, "window:1")[0][0]["id"]
    post(live, f"live-meetings/{mid}/turns/{tid}/claim", {}, token="ops-live")
    age_claim(live, tid)
    sweep(live)
    late = reply(live, mid, tid, "A late answer")
    assert late.status_code == 409, late.text
    claim = live.post(f"/api/v2/live-meetings/{mid}/turns/{tid}/claim", json={}, headers=headers("ops-live"))
    assert claim.status_code == 200 and claim.json()["status"] == "skipped"
    assert claim.json()["reason"] == "claim_abandoned"
    assert get(live, f"live-meetings/{mid}")["chat"] == []


def runner_for_ops(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    return machine


def test_a_direct_claim_cannot_take_a_turn_the_runner_leased(live):
    mid = meeting(live)
    machine = runner_for_ops(live)
    tid = invite(live, mid, "window:1")[0][0]["id"]
    attempt = post(live, "jobs/claim", {"bot": "ops"}, token=machine["token"])["attempt"]
    assert tid in attempt["message"]["body"]
    with live.app.state.store.read() as c:
        job = c.execute("SELECT job_id FROM attempts WHERE id=?", (attempt["id"],)).fetchone()[0]
    assert turn_row(live, tid)["claim_owner"] == "job:" + job
    # The direct client of the same bot does not see the runner's turn and cannot claim or answer it.
    assert get(live, f"live-meetings/{mid}/turns", token="ops-live")["turns"] == []
    taken = live.post(f"/api/v2/live-meetings/{mid}/turns/{tid}/claim", json={}, headers=headers("ops-live"))
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "turn_claimed", taken.text
    assert reply(live, mid, tid, "Direct answer").status_code == 409
    # A runner turn is not aged out while its job lives: the job's lease is its heartbeat.
    age_claim(live, tid)
    sweep(live)
    assert turn_row(live, tid)["status"] == "claimed"
    post(live, f"attempts/{attempt['id']}/started", {"thread_id": "synthetic-turn"}, token=machine["token"])
    post(
        live,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "text": "Runner answer", "last_seq": 0},
        token=machine["token"],
    )
    assert [m["text"] for m in get(live, f"live-meetings/{mid}")["chat"]] == ["Runner answer"]


def test_a_runner_lease_racing_a_direct_claim_hands_out_nothing(live):
    mid = meeting(live)
    machine = runner_for_ops(live)
    tid = invite(live, mid, "window:1")[0][0]["id"]
    post(live, f"live-meetings/{mid}/turns/{tid}/claim", {}, token="ops-live")
    # The lease read the job before the direct claim canceled it: put it back in the queue as that lease saw it.
    with live.app.state.store.transaction() as c:
        c.execute(
            "UPDATE jobs SET state='queued' WHERE message_id IN "
            "(SELECT id FROM messages WHERE json_extract(refs,'$.live_meeting.turn_id')=?)",
            (tid,),
        )
    assert post(live, "jobs/claim", {"bot": "ops"}, token=machine["token"])["attempt"] is None
    assert turn_row(live, tid)["claim_owner"].startswith("direct:")
    with live.app.state.store.read() as c:
        assert {
            r[0]
            for r in c.execute(
                "SELECT j.state FROM jobs j JOIN messages m ON m.id=j.message_id "
                "WHERE json_extract(m.refs,'$.live_meeting.turn_id')=?",
                (tid,),
            )
        } == {"canceled"}
    assert reply(live, mid, tid, "Direct answer").status_code == 200
    assert [m["text"] for m in get(live, f"live-meetings/{mid}")["chat"]] == ["Direct answer"]


def test_detaching_the_bot_or_ending_the_meeting_during_a_claim_ends_it_and_fences_the_reply(live):
    mid = meeting(live)
    tid = invite(live, mid, "window:1")[0][0]["id"]
    post(live, f"live-meetings/{mid}/turns/{tid}/claim", {}, token="ops-live")
    removed = live.request("DELETE", f"/api/v2/live-meetings/{mid}/bots/ops", json={}, headers=headers())
    assert removed.status_code == 200 and removed.json()["canceled_turns"] == [tid]
    assert turn_row(live, tid)["skip_reason"] == "bot_detached"
    assert reply(live, mid, tid).status_code == 403

    ended = meeting(live)
    tid = invite(live, ended, "window:1")[0][0]["id"]
    post(live, f"live-meetings/{ended}/turns/{tid}/claim", {}, token="ops-live")
    post(live, f"live-meetings/{ended}/control", {"action": "end"})
    assert turn_row(live, tid)["skip_reason"] == "meeting_ended_before_reply"
    assert reply(live, ended, tid).status_code == 409
    assert get(live, f"live-meetings/{ended}")["chat"] == []


def test_a_claim_from_an_unrelated_run_of_the_bot_is_a_direct_claim(live):
    """A bot that calls the claim API from a task turn does not tie the meeting turn to that run: it claims as a direct
    client, so its claim is listed for it and ends after the bound like any direct claim."""
    machine = runner_for_ops(live)
    with live.app.state.store.transaction() as c:
        H.task_create(c, H.human_actor("ana"), "Tidy the notes", "Please tidy them.", "bot:ops")
    task_run = post(live, "jobs/claim", {"bot": "ops"}, token=machine["token"])["attempt"]
    assert "live_meeting" not in json.dumps(task_run["message"].get("refs") or {})
    live.app.state.store.settings.test_identities["ops-in-task"] = Identity(
        "bot:ops", "bot", runner_id=machine["runner_id"], attempt_id=task_run["id"]
    )
    mid = meeting(live)
    tid = invite(live, mid, "window:1", named=True)[0][0]["id"]  # busy with its task run, so named
    claim = post(live, f"live-meetings/{mid}/turns/{tid}/claim", {}, token="ops-in-task")
    assert claim["by"] == "direct" and turn_row(live, tid)["claim_owner"].startswith("direct:")
    assert [t["id"] for t in get(live, f"live-meetings/{mid}/turns", token="ops-live")["turns"]] == [tid]
    age_claim(live, tid)
    sweep(live)
    assert turn_row(live, tid)["skip_reason"] == "claim_abandoned"
