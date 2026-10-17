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


def test_the_confirmation_names_the_bots_on_that_login(api):
    r = online(api)
    rid = r["runner_id"]
    for bot, runtime in (("writer", "codex"), ("helper", "claude")):
        add_bot(api, bot)
        put_config(api, bot, runtime)
        post(api, f"bots/{bot}/assignment", {"runner_id": rid, "expected_generation": 0})
    add_bot(api, "sleeper", state="paused")
    put_config(api, "sleeper", "codex")
    post(api, "bots/sleeper/assignment", {"runner_id": rid, "expected_generation": 0})
    assert [b["bot"] for b in get(api, f"runners/{rid}/logouts/bots?runtime=codex")["bots"]] == ["writer"]
    assert [b["bot"] for b in get(api, f"runners/{rid}/logouts/bots?runtime=claude")["bots"]] == ["helper"]
    # A named subscription's bots are the ones assigned to it.
    assert get(api, f"runners/{rid}/logouts/bots?runtime=codex&profile=work")["bots"] == []


def put_config(api, bot, runtime):
    import json
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bot_config(bot, config_json, operator) VALUES(?, ?, 'ana') ON CONFLICT(bot) DO UPDATE "
                  "SET config_json=excluded.config_json", (bot, json.dumps({"runtime": runtime})))


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
