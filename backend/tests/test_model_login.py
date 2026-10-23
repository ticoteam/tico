"""Signing a model in from the browser: operator access, a small state machine, and no secrets kept."""

import json

from backend.tests.test_api import api, get, headers, post, ready, runner  # noqa: F401


def online(api):
    r = runner(api)
    ready(api, r, [])
    return r


def start(api, r, runtime="codex", **kw):
    return post(api, f"runners/{r['runner_id']}/logins", {"runtime": runtime}, **kw)


def report(api, r, lid, **body):
    return post(api, f"runner-logins/{lid}/report", body, token=r["token"])


def stored(api):
    with api.app.state.store.read() as c:
        return [dict(row) for row in c.execute("SELECT * FROM model_logins")]


def test_other_members_cannot_start_read_or_cancel_a_login(api):
    r = online(api)
    base = f"runners/{r['runner_id']}/logins"
    post(api, base, {"runtime": "codex"}, token="cara-test", expected=403)
    lid = start(api, r)["id"]
    get(api, f"{base}/{lid}", token="cara-test", expected=403)
    post(api, f"{base}/{lid}/cancel", {}, token="cara-test", expected=403)
    post(api, f"{base}/{lid}/code", {"code": "abcdef#ghijkl"}, token="cara-test", expected=403)
    # A runner cannot drive the owner's endpoints, nor another runner's login.
    post(api, base, {"runtime": "codex"}, token=r["token"], expected=403)
    other = online(api)
    post(api, f"runner-logins/{lid}/report", {"state": "failed"}, token=other["token"], expected=404)
    assert api.get("/api/v2/runner-logins", headers=headers("ana-test")).status_code == 403


def test_nothing_token_like_is_stored_or_shown(api):
    r = online(api)
    login = start(api, r)
    jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlc2lnbmF0dXJl"
    secret = "sk-ant-oat01-" + "A1b2C3d4" * 6
    blob = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU2Nzg5"
    report(api, r, login["id"], state="waiting", url="http://insecure.example/x", code="not a code!!",
           lines=[f"token: {jwt}", f"Your key {secret} is here", f"blob {blob}", "Enter code AB12-CD345",
                  "line\x1b[31m with control"], message=f"failed {jwt}")
    dump = json.dumps(stored(api)) + json.dumps(get(api, f"runners/{r['runner_id']}/logins/{login['id']}"))
    for leaked in (jwt, secret, blob, "eyJ", "sk-ant"):
        assert leaked not in dump
    shown = get(api, f"runners/{r['runner_id']}/logins/{login['id']}")
    assert shown["url"] == "" and shown["code"] == ""            # http and free text are refused
    assert "Enter code AB12-CD345" in shown["lines"]
    assert "\x1b" not in dump


def test_grok_and_cursor_computer_logins_can_be_controlled(api):
    r = online(api)
    post(api, "runners/heartbeat", {"version": "test", "platform": "linux", "readiness": {},
                                 "capabilities": ["model_sign_out_v1"]}, token=r["token"])
    for runtime in ("grok", "cursor"):
        base = f"computers/{r['runner_id']}"
        login = post(api, f"{base}/logins", {"runtime": runtime})
        assert login["profile"] == "" and login["state"] == "requested"
        report(api, r, login["id"], state="signed_in")
        switch = post(api, f"{base}/logins", {"runtime": runtime, "switch": True})
        assert switch["kind"] == "switch"
        post(api, f"{base}/logins/{switch['id']}/cancel", {})
        logout = post(api, f"{base}/logouts", {"runtime": runtime})
        assert logout["kind"] == "sign_out"
        report(api, r, logout["id"], state="signed_out")
        refused = post(api, f"{base}/logins", {"runtime": runtime, "profile": "other"}, expected=422)
        assert refused["error"]["code"] == "one_login"
