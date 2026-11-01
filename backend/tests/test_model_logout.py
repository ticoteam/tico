"""Sign out and Switch account from the app: the sign-in relay's other kinds, and a login signed out on purpose stays
signed out, even when the company gives every computer a model key."""

from backend.store import H
from backend.tests.test_api import api, get, headers, post, runner  # noqa: F401
from backend.tests.test_computer_signin import computer_asks, grant, new_computer, store, vault
from backend.tests.test_getting_started import add_bot
from backend.tests.test_onboarding import environment, signed_in  # noqa: F401

CAPABLE = ["model_sign_out_v1"]


def online(api, capabilities=CAPABLE, token=None):
    r = runner(api)
    beat(api, r["token"], capabilities)
    return r


def beat(api, token, capabilities=CAPABLE, auth=None):
    import uuid
    r = api.post("/api/v2/runners/heartbeat", json={"version": "test", "platform": "test", "readiness": {},
                 "capabilities": capabilities}, headers={**(auth or headers(token)), "Idempotency-Key": uuid.uuid4().hex})
    assert r.status_code == 200, r.text


def as_computer(api, token, path, body):
    import uuid
    r = api.post("/api/v2/" + path, json=body, headers={"Authorization": "Bearer " + token,
                                                        "Idempotency-Key": uuid.uuid4().hex})
    assert r.status_code == 200, r.text
    return r.json()


def report(api, r, lid, **body):
    return post(api, f"runner-logins/{lid}/report", body, token=r["token"])


def pending(api, r):
    return get(api, "runner-logins", token=r["token"])["logins"]


def cleared(api, rid):
    with api.app.state.store.read() as c:
        return [tuple(row) for row in c.execute("SELECT runtime, profile FROM model_login_cleared WHERE runner_id=?",
                                                (rid,))]


def test_only_the_operator_or_an_admin_signs_out_and_an_old_runner_is_never_asked(api):
    r = online(api)
    rid = r["runner_id"]
    post(api, f"runners/{rid}/logouts", {"runtime": "codex"}, token="cara-test", expected=403)
    post(api, f"runners/{rid}/logins", {"runtime": "codex", "switch": True}, token="cara-test", expected=403)
    get(api, f"runners/{rid}/logouts/bots?runtime=codex", token="cara-test", expected=403)
    post(api, f"runners/{rid}/logouts", {"runtime": "codex"}, token=r["token"], expected=403)
    assert cleared(api, rid) == []
    # A runner that does not know these kinds would start a sign-in instead: it is told to update.
    old = online(api, capabilities=[])
    refused = post(api, f"runners/{old['runner_id']}/logouts", {"runtime": "codex"}, expected=409)
    assert "Update" in refused["error"]["detail"]
    post(api, f"runners/{old['runner_id']}/logins", {"runtime": "codex", "switch": True}, expected=409)
    assert pending(api, old) == [] and cleared(api, old["runner_id"]) == []
    # A plain sign-in still works there.
    post(api, f"runners/{old['runner_id']}/logins", {"runtime": "codex"})
    assert [row.get("kind", "sign_in") for row in pending(api, old)] == ["sign_in"]


def test_a_sign_out_marks_the_login_signed_out_until_a_person_signs_in_again(api):
    r = online(api)
    rid = r["runner_id"]
    out = post(api, f"runners/{rid}/logouts", {"runtime": "codex"})
    assert out["kind"] == "sign_out" and out["state"] == "requested"
    assert pending(api, r) == [{"id": out["id"], "runtime": "codex", "profile": "", "kind": "sign_out",
                                "expires_at": out["expires_at"]}]
    assert cleared(api, rid) == [("codex", "")]
    # Asking again returns the same request; a sign-in meanwhile would race it on the same files.
    assert post(api, f"runners/{rid}/logouts", {"runtime": "codex"})["id"] == out["id"]
    post(api, f"runners/{rid}/logins", {"runtime": "codex"}, expected=409)
    # A sign-out ends signed out, never signed in.
    post(api, f"runner-logins/{out['id']}/report", {"state": "signed_in"}, token=r["token"], expected=422)
    assert report(api, r, out["id"], state="signed_out", message="Signed out") == {"state": "signed_out"}
    assert get(api, f"runners/{rid}/logins/{out['id']}")["state"] == "signed_out"
    assert get(api, "operations")["computers"][0]["signed_out"] == {"codex": [""]}
    assert get(api, "operations")["computers"][0]["key_cleared"] == [], "a sign-out is not a cleared key"
    # A switch is a sign-in on the side; when it finishes, the login is no longer signed out.
    switch = post(api, f"runners/{rid}/logins", {"runtime": "codex", "switch": True})
    assert switch["kind"] == "switch" and pending(api, r)[0]["kind"] == "switch"
    assert cleared(api, rid) == [("codex", "")]
    report(api, r, switch["id"], state="signed_in")
    assert cleared(api, rid) == []


def test_a_sign_out_that_did_not_take_leaves_the_login_as_it_was(api):
    r = online(api)
    rid = r["runner_id"]
    out = post(api, f"runners/{rid}/logouts", {"runtime": "claude", "profile": "work"})
    assert cleared(api, rid) == [("claude", "work")]
    report(api, r, out["id"], state="failed", message="Claude is still signed in")
    assert cleared(api, rid) == []


def test_a_cancelled_switch_keeps_the_mark_and_the_old_login(api):
    r = online(api)
    rid = r["runner_id"]
    report(api, r, post(api, f"runners/{rid}/logouts", {"runtime": "codex"})["id"], state="signed_out")
    switch = post(api, f"runners/{rid}/logins", {"runtime": "codex", "switch": True})
    post(api, f"runners/{rid}/logins/{switch['id']}/cancel", {})
    assert pending(api, r) == [] and cleared(api, rid) == [("codex", "")]


def put_config(api, bot, runtime):
    import json
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_config(bot, config, operator) VALUES(?, ?, 'ana') ON CONFLICT(bot) DO UPDATE "
                  "SET config=excluded.config", (bot, json.dumps({"runtime": runtime})))


def test_a_signed_out_computer_is_not_given_the_shared_key_until_a_person_says_so(environment):
    """Cleared stays cleared: the company's "Every computer" key does not sign a signed-out login back in."""
    api = environment()
    vault(api)
    grant(api, store(api, "OPENAI_API_KEY", "sk-team-key-0123456789"), "computers")
    machine = new_computer(api)
    rid, token = machine["runner_id"], machine["token"]
    beat(api, token, auth={"Authorization": "Bearer " + token})
    assert computer_asks(api, token).json()["credentials"]
    r = api.post(f"/api/v2/runners/{rid}/logouts", json={"runtime": "codex"}, headers=signed_in())
    assert r.status_code == 200, r.text
    assert computer_asks(api, token).json() == {"credentials": [], "signed_out": True}
    # Only the computer's own login is affected, and only for that runtime.
    assert "signed_out" not in computer_asks(api, token, "claude").json()
    as_computer(api, token, f"runner-logins/{r.json()['id']}/report", {"state": "signed_out"})
    assert computer_asks(api, token).json()["credentials"] == []
    # "Use the shared key" is the person saying so.
    used = api.post(f"/api/v2/runners/{rid}/shared-key", json={"runtime": "codex"}, headers=signed_in())
    assert used.status_code == 200, used.text
    assert used.json()["kind"] == "use_shared_key"
    assert computer_asks(api, token).json()["credentials"][0]["value"] == "sk-team-key-0123456789"
    # Nothing to restore once it is no longer signed out.
    as_computer(api, token, f"runner-logins/{used.json()['id']}/report", {"state": "done"})
    again = api.post(f"/api/v2/runners/{rid}/shared-key", json={"runtime": "codex"}, headers=signed_in())
    assert again.status_code == 409


def test_a_switch_past_its_commit_point_cannot_be_cancelled_and_one_cancelled_first_is_never_put_in_place(api):
    r = online(api)
    rid = r["runner_id"]
    # Only a switch has a commit point.
    sign_in = post(api, f"runners/{rid}/logins", {"runtime": "claude"})
    post(api, f"runner-logins/{sign_in['id']}/report", {"state": "finishing"}, token=r["token"], expected=422)
    out = post(api, f"runners/{rid}/logouts", {"runtime": "claude", "profile": "work"})
    post(api, f"runner-logins/{out['id']}/report", {"state": "finishing"}, token=r["token"], expected=422)
    # The runner asks to put the new login in place; the server agrees, and from then on refuses a cancel and gives it
    # time of its own to finish in.
    switch = post(api, f"runners/{rid}/logins", {"runtime": "codex", "switch": True})
    report(api, r, switch["id"], state="waiting", url="https://auth.openai.com/codex/device", code="ABCD-1234")
    with api.app.state.store.transaction() as c:  # nearly out of time
        c.execute("UPDATE model_logins SET expires_at=? WHERE id=?", (H.shift(H.now(), seconds=5), switch["id"]))
    assert report(api, r, switch["id"], state="finishing") == {"state": "finishing"}
    row = get(api, f"runners/{rid}/logins/{switch['id']}")
    assert row["state"] == "finishing" and row["url"] == "" and row["code"] == ""
    with api.app.state.store.read() as c:
        expires = c.execute("SELECT expires_at FROM model_logins WHERE id=?", (switch["id"],)).fetchone()[0]
    assert expires >= H.shift(H.now(), seconds=100), "finishing gets about two minutes of its own"
    refused = post(api, f"runners/{rid}/logins/{switch['id']}/cancel", {}, expected=409)
    assert "can't be cancelled" in refused["error"]["detail"]
    assert switch["id"] in [p["id"] for p in pending(api, r)], "still the computer's to finish"
    assert report(api, r, switch["id"], state="signed_in") == {"state": "signed_in"}
    # Cancelled before the commit point: the runner's finishing gets cancelled back, so it keeps the old login.
    late = post(api, f"runners/{rid}/logins", {"runtime": "codex", "switch": True})
    post(api, f"runners/{rid}/logins/{late['id']}/cancel", {})
    assert report(api, r, late["id"], state="finishing") == {"state": "cancelled"}
    assert get(api, f"runners/{rid}/logins/{late['id']}")["state"] == "cancelled"


def test_a_mac_is_never_asked_to_switch_claude_code_which_keeps_its_login_in_the_keychain(api):
    r = online(api)
    rid = r["runner_id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET platform='darwin' WHERE id=?", (rid,))
    refused = post(api, f"runners/{rid}/logins", {"runtime": "claude", "switch": True}, expected=422)
    assert "keychain" in refused["error"]["detail"] and "Sign out, then sign in" in refused["error"]["detail"]
    assert pending(api, r) == []
    # Codex keeps its login in a file there too, and Claude Code still signs out and in.
    assert post(api, f"runners/{rid}/logins", {"runtime": "codex", "switch": True})["kind"] == "switch"
    assert post(api, f"runners/{rid}/logouts", {"runtime": "claude"})["kind"] == "sign_out"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET platform='linux' WHERE id=?", (rid,))
    post(api, f"runners/{rid}/logins", {"runtime": "claude", "switch": True}, expected=409)  # the sign-out is still running


def test_only_a_heartbeat_moves_readiness_at_so_the_page_can_tell_a_report_from_after_a_change(api):
    r = online(api)
    rid = r["runner_id"]

    def machine():
        return next(m for m in get(api, "operations")["machines"] if m["id"] == rid)
    first = machine()["readiness_at"]
    assert first
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET readiness_at='2000-01-01T00:00:00+00:00' WHERE id=?", (rid,))
    pending(api, r)  # other contact moves last_seen, never readiness_at
    assert machine()["readiness_at"] == "2000-01-01T00:00:00+00:00"
    beat(api, r["token"])
    assert machine()["readiness_at"] > "2000-01-01T00:00:00+00:00"


SWITCHES_TYPE = CAPABLE + ["model_switch_type_v1"]


def key_off(api, rid):
    with api.app.state.store.read() as c:
        return [tuple(row) for row in c.execute("SELECT runtime, profile FROM model_login_key_off WHERE runner_id=?",
                                                (rid,))]


def test_switch_to_subscription_keeps_the_key_until_the_subscription_works_then_keeps_the_shared_key_off(environment):
    """Switch to subscription (Computers part 2): the computer's login moves from the API key to a subscription only once
    the sign-in has worked; until then, and after a cancel or a failure, the key stays in use. Once switched, the
    company's "Every computer" key is not handed to it again, not even after a later sign-in, until a person picks
    Use the shared key."""
    api = environment()
    vault(api)
    grant(api, store(api, "OPENAI_API_KEY", "sk-team-key-0123456789"), "computers")
    machine = new_computer(api)
    rid, token = machine["runner_id"], machine["token"]
    auth = {"Authorization": "Bearer " + token}
    beat(api, token, SWITCHES_TYPE, auth=auth)
    start = lambda body, who=None: api.post(f"/api/v2/runners/{rid}/logins", json=body, headers=who or signed_in())
    has_key = lambda: computer_asks(api, token).json()["credentials"][0]["value"] == "sk-team-key-0123456789"
    assert has_key()

    # Cancelled: nothing changes, the key is still what it runs on.
    first = start({"runtime": "codex", "to": "subscription"})
    assert first.status_code == 200, first.text
    assert first.json()["kind"] == "to_subscription"
    assert [p.get("kind") for p in get(api, "runner-logins", token=token)["logins"]] == ["to_subscription"]
    as_computer(api, token, f"runner-logins/{first.json()['id']}/report", {"state": "waiting", "url": "https://auth.openai.com/codex/device"})
    assert has_key(), "until the subscription works the key stays in use"
    api.post(f"/api/v2/runners/{rid}/logins/{first.json()['id']}/cancel", json={}, headers=signed_in())
    assert has_key() and key_off(api, rid) == []

    # Failed after the commit point (the new login did not work in place): the key stays too.
    failed = start({"runtime": "codex", "to": "subscription"}).json()
    assert as_computer(api, token, f"runner-logins/{failed['id']}/report", {"state": "finishing"}) == {"state": "finishing"}
    as_computer(api, token, f"runner-logins/{failed['id']}/report", {"state": "failed", "message": "did not work"})
    assert has_key() and key_off(api, rid) == []

    # Worked: the shared key is kept off this login from then on.
    done = start({"runtime": "codex", "to": "subscription"}).json()
    as_computer(api, token, f"runner-logins/{done['id']}/report", {"state": "finishing"})
    assert has_key(), "finishing is not yet switched"
    assert as_computer(api, token, f"runner-logins/{done['id']}/report", {"state": "signed_in"}) == {"state": "signed_in"}
    assert key_off(api, rid) == [("codex", "")]
    assert computer_asks(api, token).json() == {"credentials": [], "key_off": True}
    assert "key_off" not in computer_asks(api, token, "claude").json(), "only that tool's login"
    # A later Switch account signs in to another subscription; the key stays off.
    later = start({"runtime": "codex", "switch": True}).json()
    as_computer(api, token, f"runner-logins/{later['id']}/report", {"state": "finishing"})
    as_computer(api, token, f"runner-logins/{later['id']}/report", {"state": "signed_in"})
    assert computer_asks(api, token).json()["credentials"] == []
    # Use the shared key is the person saying so.
    used = api.post(f"/api/v2/runners/{rid}/shared-key", json={"runtime": "codex"}, headers=signed_in())
    assert used.status_code == 200, used.text
    assert key_off(api, rid) == [] and has_key()


def test_switch_to_subscription_is_for_the_operator_or_an_admin_on_a_runner_that_knows_it(api):
    r = online(api, SWITCHES_TYPE)
    rid = r["runner_id"]
    post(api, f"runners/{rid}/logins", {"runtime": "codex", "to": "subscription"}, token="cara-test", expected=403)
    post(api, f"runners/{rid}/logins", {"runtime": "codex", "to": "subscription"}, token=r["token"], expected=403)
    # The computer's own login only, and not both a switch of account and of type at once.
    post(api, f"runners/{rid}/logins", {"runtime": "codex", "profile": "work", "to": "subscription"}, expected=422)
    post(api, f"runners/{rid}/logins", {"runtime": "codex", "switch": True, "to": "subscription"}, expected=422)
    post(api, f"runners/{rid}/logins", {"runtime": "codex", "to": "api_key"}, expected=422)
    assert pending(api, r) == []
    # A runner that does not know it would run a plain sign-in and leave the key on: it is told to update.
    old = online(api)
    refused = post(api, f"runners/{old['runner_id']}/logins", {"runtime": "codex", "to": "subscription"}, expected=409)
    assert "Update" in refused["error"]["detail"]
    assert pending(api, old) == []
    # Claude Code on a Mac keeps its login in the keychain: no side to sign in on, as for Switch account.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET platform='darwin' WHERE id=?", (rid,))
    refused = post(api, f"runners/{rid}/logins", {"runtime": "claude", "to": "subscription"}, expected=422)
    assert "keychain" in refused["error"]["detail"]


REMOVES = CAPABLE + ["model_remove_v1"]


def with_profile(api, r, profile="work", runtimes=None):
    import uuid
    body = {"version": "test", "platform": "test", "readiness": {}, "capabilities": REMOVES,
            "profiles": [{"name": profile, "runtimes": runtimes or {"codex": {"signed_in": True}}}]}
    out = api.post("/api/v2/runners/heartbeat", json=body, headers={**headers(r["token"]), "Idempotency-Key": uuid.uuid4().hex})
    assert out.status_code == 200, out.text


def test_remove_takes_a_leftover_subscription_off_the_computer(api):
    """Remove: a named subscription left from before one login per computer (#1259) comes off the computer, with
    nothing of it left behind. No bot runs on one any more."""
    r = online(api, REMOVES)
    rid = r["runner_id"]
    with_profile(api, r)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO model_login_cleared(runner_id,runtime,profile,cleared_at,cleared_by) VALUES(?, 'codex', 'work', '2026-10-17T00:00:00+00:00', 'human:ana')", (rid,))
    put = api.put("/api/v2/subscriptions/weekly", json={"runner_id": rid, "profile": "work", "runtime": "codex",
                  "used_percent": 40, "resets_at": None}, headers={**headers("ana-test"), "Idempotency-Key": "w1"})
    assert put.status_code == 200, put.text
    out = post(api, f"computers/{rid}/removals", {"runtime": "codex", "profile": "work"})   # the page's own path
    assert out["kind"] == "remove" and out["state"] == "requested"
    assert pending(api, r) == [{"id": out["id"], "runtime": "codex", "profile": "work", "kind": "remove",
                                "expires_at": out["expires_at"]}]
    # A remove ends done or failed, never signed in.
    post(api, f"runner-logins/{out['id']}/report", {"state": "signed_in"}, token=r["token"], expected=422)
    assert report(api, r, out["id"], state="done", message="Removed work") == {"state": "done"}
    # Nothing of it is left to fill a line again: no sign-out mark, no manual reading.
    assert cleared(api, rid) == []
    from backend.subscriptions import weekly_key
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM registry_metadata WHERE key=?", (weekly_key(rid, "work", "codex"),)).fetchone()


def test_remove_is_for_a_named_subscription_on_this_computer_by_its_operator_or_an_admin_on_a_runner_that_knows_it(api):
    r = online(api, REMOVES)
    rid = r["runner_id"]
    with_profile(api, r)
    post(api, f"runners/{rid}/removals", {"runtime": "codex", "profile": "work"}, token="cara-test", expected=403)
    post(api, f"runners/{rid}/removals", {"runtime": "codex", "profile": "work"}, token=r["token"], expected=403)
    post(api, f"runners/{rid}/removals", {"runtime": "codex", "profile": ""}, expected=422)
    post(api, f"runners/{rid}/removals", {"runtime": "codex", "profile": "other"}, expected=404)
    post(api, f"runners/{rid}/removals", {"runtime": "claude", "profile": "work"}, expected=404)
    assert pending(api, r) == []
    # A runner that does not know Remove would start a sign-in instead: it is told to update.
    old = online(api)
    with_profile(api, old)
    beat(api, old["token"], CAPABLE)
    refused = post(api, f"runners/{old['runner_id']}/removals", {"runtime": "codex", "profile": "work"}, expected=409)
    assert "Update" in refused["error"]["detail"]
    assert pending(api, old) == []


def test_botops_never_removes_a_subscription_even_with_a_persons_rights(api):
    """Remove takes a login off a computer, so like Sign out it is a person's, checked before anything else; BotOps'
    allow-list does not reach the route either."""
    import pytest
    from backend.auth import Identity
    from backend.model_login import remove
    from backend.store import Problem
    r = online(api, REMOVES)
    with_profile(api, r)
    with api.app.state.store.transaction() as c:
        with pytest.raises(Problem) as no:
            remove(c, Identity("human:ana", "owner", via="botops", confirmed=True), r["runner_id"], "codex", "work", {})
    assert no.value.status == 403 and "BotOps cannot remove a subscription" in str(no.value)
    assert pending(api, r) == []


def test_a_write_kept_from_the_lock_answers_busy_with_retry_after_and_a_beat_rewrites_only_what_changed(api):
    import uuid
    r = online(api)
    store = api.app.state.store
    with store.read() as c:
        before = c.execute("SELECT readiness,last_seen_at FROM runners WHERE id=?", (r["runner_id"],)).fetchone()
    beat(api, r["token"])
    with store.read() as c:
        after = c.execute("SELECT readiness,last_seen_at FROM runners WHERE id=?", (r["runner_id"],)).fetchone()
    assert after[0] == before[0] and after[1] >= before[1]
    store.writer.acquire()
    timeout, store.writer.timeout = store.writer.timeout, 0.05
    try:
        res = api.post("/api/v2/runners/heartbeat", json={"version": "test", "platform": "test", "readiness": {},
                       "capabilities": CAPABLE}, headers={**headers(r["token"]), "Idempotency-Key": uuid.uuid4().hex})
    finally:
        store.writer.timeout = timeout
        store.writer.release()
    assert res.status_code == 503 and res.headers["retry-after"] == "2", res.text
    assert res.json()["error"] == {"code": "busy", "detail": "Tico is busy; retry shortly", "retryable": True}
