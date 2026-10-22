"""A computer can add different AI tools, but cannot add another subscription for the same tool."""

from concurrent.futures import ThreadPoolExecutor

from backend.tests.test_api import api, headers, post, runner  # noqa: F401
from backend.tests.test_model_login import report, stored


def heartbeat(api, machine, *, runtimes=None, profiles=None):
    body = {"version": "test", "platform": "linux", "readiness": {},
            "capabilities": ["model_sign_out_v1", "model_switch_type_v1"]}
    if runtimes is not None:
        body["readiness"] = {"runtimes": runtimes}
    if profiles is not None:
        body["profiles"] = profiles
    post(api, "runners/heartbeat", body, token=machine["token"])


def sign_in(api, machine, profile="", runtime="claude", **kwargs):
    return post(api, f"runners/{machine['runner_id']}/logins", {"runtime": runtime, "profile": profile}, **kwargs)


def test_a_connected_tool_refuses_a_second_subscription_but_other_tools_and_computers_work(api):
    machine = runner(api)
    heartbeat(api, machine, runtimes={"claude": {"installed": True, "authenticated": "ready"}})
    error = sign_in(api, machine, "second", expected=409)["error"]
    assert error["code"] == "subscription_exists" and "Switch account" in error["detail"]
    assert "Claude Code" in error["detail"] and stored(api) == []
    browser = api.post(f"/api/v2/computers/{machine['runner_id']}/logins",
                       json={"runtime": "claude", "profile": "second"}, headers=headers())
    assert browser.status_code == 409 and browser.json()["error"]["code"] == "subscription_exists"
    assert sign_in(api, machine, "first-codex", runtime="codex")["state"] == "requested"
    another = runner(api)
    heartbeat(api, another)
    assert sign_in(api, another, "first-claude")["state"] == "requested"
    # Choosing the existing connection for an account switch is still available.
    switch = post(api, f"runners/{machine['runner_id']}/logins", {"runtime": "claude", "switch": True})
    assert switch["kind"] == "switch"


def test_concurrent_profile_names_reserve_one_tool_and_completion_bridges_the_heartbeat_gap(api):
    machine = runner(api)
    heartbeat(api, machine)
    path = f"/api/v2/runners/{machine['runner_id']}/logins"
    def request(profile):
        return api.post(path, json={"runtime": "codex", "profile": profile}, headers=headers())
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(request, ("one", "two")))
    assert sorted(r.status_code for r in responses) == [200, 409]
    first = next(r.json() for r in responses if r.status_code == 200)
    assert sign_in(api, machine, first["profile"], runtime="codex")["id"] == first["id"]
    assert len(stored(api)) == 1
    report(api, machine, first["id"], state="signed_in")
    sign_in(api, machine, "three", runtime="codex", expected=409)
    # Neither Switch account nor an API-key-to-subscription request may create a second slot.
    post(api, path.removeprefix("/api/v2/"), {"runtime": "codex", "profile": "three", "switch": True}, expected=409)
    post(api, path.removeprefix("/api/v2/"), {"runtime": "codex", "to": "subscription"}, expected=409)


def test_legacy_profiles_keep_their_maintenance_but_do_not_allow_another_subscription(api):
    machine = runner(api)
    heartbeat(api, machine, profiles=[
        {"name": name, "runtimes": {"claude": {"signed_in": True}}} for name in ("one", "two")])
    sign_in(api, machine, "three", expected=409)
    sign_in(api, machine, expected=409)  # The computer login cannot be a second subscription either.
    assert sign_in(api, machine, "one")["profile"] == "one"
    switch = post(api, f"runners/{machine['runner_id']}/logins", {"runtime": "claude", "profile": "two", "switch": True})
    assert switch["kind"] == "switch"


def test_a_cancelled_or_signed_out_subscription_does_not_occupy_the_tool_and_keys_are_unchanged(api):
    machine = runner(api)
    heartbeat(api, machine, runtimes={"codex": {"installed": True, "authenticated": "ready",
              "key": {"name": "OPENAI_API_KEY", "source": "computer"}}})
    first = sign_in(api, machine, "cancel-me", runtime="codex")
    post(api, f"runners/{machine['runner_id']}/logins/{first['id']}/cancel", {})
    second = sign_in(api, machine, "keep", runtime="codex")
    report(api, machine, second["id"], state="signed_in")
    out = post(api, f"runners/{machine['runner_id']}/logouts", {"runtime": "codex", "profile": "keep"})
    sign_in(api, machine, "too-early", runtime="codex", expected=409)
    report(api, machine, out["id"], state="failed")
    sign_in(api, machine, "still-connected", runtime="codex", expected=409)
    out = post(api, f"runners/{machine['runner_id']}/logouts", {"runtime": "codex", "profile": "keep"})
    report(api, machine, out["id"], state="signed_out")
    # Even if an old heartbeat still says signed in, explicit sign-out wins.
    heartbeat(api, machine, profiles=[{"name": "keep", "runtimes": {"codex": {"signed_in": True}}}])
    assert sign_in(api, machine, "replacement", runtime="codex")["state"] == "requested"
