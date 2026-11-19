"""Resident sessions (backend/residents.py): a runner keeps a session's process between turns, and every turn stays an
ordinary claim, start and completion."""

from backend import residents
from backend.store import H
from backend.tests.helpers.api import assign, claim, get, headers, post, ready, runner
from backend.tests.helpers.task_lanes import RUNNER_CAPABILITIES

RESIDENT = [*RUNNER_CAPABILITIES, residents.CAPABILITY]


def machine(api, capabilities=RESIDENT):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"], capabilities=capabilities)
    return r


def turn(api, r, text):
    post(api, "chat/ops", {"text": text})
    return claim(api, r)


def complete(api, r, aid, text="Done.", outcome="completed"):
    return post(
        api,
        f"attempts/{aid}/complete",
        {"outcome": outcome, "last_seq": 0, "text": text, "usage": {"input_tokens": 120, "output_tokens": 30}},
        r["token"],
    )


def row(api, sql, *args):
    with api.app.state.store.read() as c:
        return c.execute(sql, args).fetchone()


def first_turn(api, r):
    """An ordinary turn that registers its process as the session's resident, then finishes."""
    a1 = turn(api, r, "What did Acme order last week?")
    post(api, f"attempts/{a1['id']}/started", {"thread_id": "acme-thread"}, r["token"])
    held = post(api, f"attempts/{a1['id']}/resident", {"provider_session_id": "acme-thread"}, r["token"])
    complete(api, r, a1["id"], "Acme ordered 12 crates.")
    return a1, held


def test_a_second_message_reuses_the_first_turns_process_and_both_settle_on_their_own(api):
    r = machine(api)
    a1, held = first_turn(api, r)
    assert held["state"] == "live" and held["session_key"].startswith("root:") and not held["stop"]

    # Between turns the resident holds no lane, start place or busy turn: nothing is leased or running.
    assert row(api, "SELECT count(*) FROM attempts WHERE state IN ('leased','running')")[0] == 0
    places = (
        "SELECT count(*) FROM start_places p JOIN attempts a ON a.id=p.attempt_id WHERE a.state IN ('leased','running')"
    )
    assert row(api, places)[0] == 0
    idle = post(api, f"residents/{held['id']}/renew", {"state": "dormant"}, r["token"])
    assert idle["state"] == "dormant" and not idle["stop"]

    a2 = turn(api, r, "And the week before?")
    bound = post(api, f"attempts/{a2['id']}/resident-bind", {"resident_id": held["id"]}, r["token"])
    assert bound["id"] == held["id"] and bound["state"] == "live" and bound["attempt_id"] == a2["id"]
    post(api, f"attempts/{a2['id']}/started", {"thread_id": "acme-thread"}, r["token"])
    complete(api, r, a2["id"], "Acme ordered 9 crates.")

    jobs = [
        row(api, "SELECT j.state FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", a)[0]
        for a in (a1["id"], a2["id"])
    ]
    assert jobs == ["completed", "completed"]
    for text in ("Acme ordered 12 crates.", "Acme ordered 9 crates."):
        assert row(api, "SELECT count(*) FROM messages WHERE body=?", text)[0] == 1
    turns = row(
        api, "SELECT count(*) FROM resident_turns WHERE resident_session_id=? AND fenced_at IS NULL", held["id"]
    )
    assert turns[0] == 2


def runner_call(api, r, path, body):
    return api.post("/api/v2/" + path, json=body, headers=headers(r["token"]))


def revoke_ops(api, reason="credential_revoked"):
    with api.app.state.store.transaction() as c:
        return residents.revoke(c, reason, bot=H.resolve_bot(c, "ops")["id"])


def test_a_revocation_between_claim_and_start_refuses_the_bound_turn(api):
    r = machine(api)
    _, held = first_turn(api, r)
    a2 = turn(api, r, "Send Acme the invoice")
    post(api, f"attempts/{a2['id']}/resident-bind", {"resident_id": held["id"]}, r["token"])
    assert get(api, "me", a2["token"])["actor"] == "bot:ops"  # the turn holds its authority until revoked

    assert revoke_ops(api) == [a2["id"]]
    stopped = row(api, "SELECT state, stop_reason, revoked_at FROM resident_sessions WHERE id=?", held["id"])
    assert stopped["state"] == "closed" and stopped["stop_reason"] == "credential_revoked" and stopped["revoked_at"]
    # No start, no tool authority, nothing handed to it, and the runner hears stop on its next renewals.
    refused = runner_call(api, r, f"attempts/{a2['id']}/started", {"thread_id": "acme-thread"})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "stale_lease"
    assert api.get("/api/v2/me", headers={"Authorization": "Bearer " + a2["token"]}).status_code == 409
    assert post(api, f"attempts/{a2['id']}/renew", {}, r["token"])["stop"] is True
    assert post(api, f"residents/{held['id']}/renew", {"state": "live"}, r["token"])["stop"] is True
    complete(api, r, a2["id"], "", outcome="interrupted")
    job = row(api, "SELECT j.state, j.id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", a2["id"])
    assert job["state"] == "queued"  # it never started: its message runs again, fresh
    assert "a change of access" in row(api, "SELECT note FROM job_recovery WHERE job_id=?", job["id"])["note"]
    # A bind to the stopped resident is refused, so the retry starts the ordinary way.
    a3 = claim(api, r)
    gone = runner_call(api, r, f"attempts/{a3['id']}/resident-bind", {"resident_id": held["id"]})
    assert gone.status_code == 409 and gone.json()["error"]["code"] == "resident_stopped"


def test_a_running_turn_fenced_mid_run_counts_its_usage_but_posts_nothing(api):
    r = machine(api)
    _, held = first_turn(api, r)
    a2 = turn(api, r, "Send Acme the invoice")
    post(api, f"attempts/{a2['id']}/resident-bind", {"resident_id": held["id"]}, r["token"])
    post(api, f"attempts/{a2['id']}/started", {"thread_id": "acme-thread"}, r["token"])
    revoke_ops(api)
    event = {"seq": 1, "kind": "delta", "payload": {"text": "Invoice sent to Acme."}}
    acked = post(api, f"attempts/{a2['id']}/events", {"events": [event]}, r["token"])
    assert acked["historical"] is True
    assert row(api, "SELECT count(*) FROM attempt_events WHERE attempt_id=?", a2["id"])[0] == 0
    assert post(api, f"attempts/{a2['id']}/inputs", {}, r["token"])["messages"] == []
    complete(api, r, a2["id"], "Invoice sent to Acme.")
    assert row(api, "SELECT count(*) FROM messages WHERE body=?", "Invoice sent to Acme.")[0] == 0
    done = row(api, "SELECT input_tokens, outcome FROM attempts WHERE id=?", a2["id"])
    assert done["input_tokens"] == 120


def test_a_paused_bot_ends_its_residents_in_the_same_write(api):
    r = machine(api)
    _, held = first_turn(api, r)
    a2 = turn(api, r, "Check Acme's balance")
    post(api, f"attempts/{a2['id']}/resident-bind", {"resident_id": held["id"]}, r["token"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE id=?", (H.resolve_bot(c, "ops")["id"],))
    assert row(api, "SELECT stop_reason FROM resident_sessions WHERE id=?", held["id"])[0] == "bot_paused"
    assert row(api, "SELECT fence_reason FROM resident_turns WHERE attempt_id=?", a2["id"])[0] == "bot_paused"
    out = post(api, "runners/me/residents/reconcile", {"residents": [{"id": held["id"], "state": "live"}]}, r["token"])
    assert out["stops"] == [{"id": held["id"], "stop_reason": "bot_paused", "revoked": True}]
    assert out["fenced_attempts"] == [a2["id"]] and out["residents"] == []


def test_a_late_release_from_a_replaced_process_leaves_its_replacement(api):
    r = machine(api)
    _, old = first_turn(api, r)
    # The runner restarted and lost the old process: the next turn registers a new incarnation in its place.
    a2 = turn(api, r, "Any news from Acme?")
    post(api, f"attempts/{a2['id']}/started", {"thread_id": "acme-thread"}, r["token"])
    taken = runner_call(api, r, f"attempts/{a2['id']}/resident", {"provider_session_id": "acme-thread"})
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "resident_taken"  # one writer per session
    new = post(
        api, f"attempts/{a2['id']}/resident", {"provider_session_id": "acme-thread", "replaces": old["id"]}, r["token"]
    )
    assert new["id"] != old["id"]

    late = post(api, f"residents/{old['id']}/release", {"reason": "exited"}, r["token"])
    assert late["stop"] and late["stop_reason"] == "replaced"
    assert row(api, "SELECT state FROM resident_sessions WHERE id=?", new["id"])[0] == "live"
    assert not post(api, f"residents/{new['id']}/renew", {"state": "live"}, r["token"])["stop"]


def test_reconcile_closes_what_the_runner_no_longer_holds(api):
    r = machine(api)
    _, held = first_turn(api, r)
    out = post(
        api, "runners/me/residents/reconcile", {"residents": [{"id": "acme-unknown", "state": "live"}]}, r["token"]
    )
    assert out["stops"] == [{"id": "acme-unknown", "stop_reason": "unknown", "revoked": False}]
    assert row(api, "SELECT stop_reason FROM resident_sessions WHERE id=?", held["id"])[0] == "not_held"


def test_a_key_outside_the_turns_audience_is_refused(api):
    r = machine(api)
    a1 = turn(api, r, "Draft the Acme note")
    out = runner_call(api, r, f"attempts/{a1['id']}/resident", {"provider_session_id": "t", "session_key": "public"})
    assert out.status_code == 409 and out.json()["error"]["code"] == "session_key_mismatch"


def test_a_cleared_session_ends_its_resident_without_stopping_a_turn(api):
    r = machine(api)
    _, held = first_turn(api, r)
    from backend.execution import reset_bot_sessions

    with api.app.state.store.transaction() as c:
        reset_bot_sessions(c, H.resolve_bot(c, "ops")["id"])
    stopped = row(api, "SELECT state, stop_reason, revoked_at FROM resident_sessions WHERE id=?", held["id"])
    assert (stopped["state"], stopped["stop_reason"], stopped["revoked_at"]) == ("closed", "session_cleared", None)


def test_a_runner_without_the_capability_sees_no_change(api):
    r = machine(api, capabilities=RUNNER_CAPABILITIES)
    a1 = turn(api, r, "What did Acme order?")
    off = runner_call(api, r, f"attempts/{a1['id']}/resident", {"provider_session_id": "acme-thread"})
    assert off.status_code == 409 and off.json()["error"]["code"] == "resident_threads_off"
    post(api, f"attempts/{a1['id']}/started", {"thread_id": "acme-thread"}, r["token"])
    complete(api, r, a1["id"], "Acme ordered 12 crates.")
    assert row(api, "SELECT count(*) FROM messages WHERE body=?", "Acme ordered 12 crates.")[0] == 1
    assert row(api, "SELECT count(*) FROM resident_sessions")[0] == 0
    # The server says it takes the capability, so a new runner may advertise it.
    assert get(api, "runners/desired", token=r["token"])["features"]["resident_threads_v1"] is True
