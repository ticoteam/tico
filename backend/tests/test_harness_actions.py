"""Keeping a computer's model CLIs current from Settings: owner-only, audited, and relayed to the
runner the way the browser sign-in is."""

from backend.tests.test_api import api, assign, get, post, put, runner  # noqa: F401
from backend.tests.test_model_login import stored  # noqa: F401  (a row reader shared with the sign-in tests)

HARNESS = {"name": "Codex", "runtime": "codex", "installed": True, "version": "0.158.0", "managed": True,
           "source": "tools", "pinned": False, "pin": "", "authenticated": "ready", "update_available": True,
           "latest": "0.160.0", "wanted": True, "state": "idle", "detail": ""}


def beat(api, r, **harnesses):
    body = {"version": "test", "platform": "test",
            "readiness": {"schema_version": 1, "runtimes": {}, "bots": {}, "harnesses": harnesses}}
    return post(api, "runners/heartbeat", body, token=r["token"])


def online(api, **overrides):
    r = runner(api)
    beat(api, r, codex={**HARNESS, **overrides})
    return r


def ask(api, r, action="update", harness="codex", **kw):
    return post(api, f"runners/{r['runner_id']}/harness-actions", {"harness": harness, "action": action}, **kw)


def events(api, action):
    with api.app.state.store.read() as c:
        return [dict(row) for row in c.execute("SELECT * FROM events WHERE action=?", (action,))]


def rows(api):
    with api.app.state.store.read() as c:
        return [dict(row) for row in c.execute("SELECT * FROM runner_harness_actions")]


def test_only_the_owner_asks_and_a_runner_cannot_ask_for_itself(api):
    r = online(api)
    ask(api, r, token="ben-test", expected=403)
    ask(api, r, token=r["token"], expected=403)
    assert rows(api) == []
    # Nor may another person read the pending requests through the owner's page.
    # (An admin sees the company's computers, but only the owner sees or asks for their tool actions.)
    assert [m["harness_actions"] for m in get(api, "operations", token="ben-test")["machines"]] == [[]]


def test_an_action_is_relayed_to_the_runner_reported_and_audited(api):
    r = online(api)
    first = ask(api, r)
    assert (first["state"], first["action"], first["harness"]) == ("requested", "update", "codex")
    assert ask(api, r)["id"] == first["id"]                     # one at a time per action
    assert get(api, "runner-harness-actions", token=r["token"])["actions"] == [
        {"id": first["id"], "harness": "codex", "action": "update"}]
    post(api, f"runner-harness-actions/{first['id']}/report", {"state": "running"}, token=r["token"])
    assert get(api, "operations")["machines"][0]["harness_actions"][0]["state"] == "running"
    post(api, f"runner-harness-actions/{first['id']}/report",
         {"state": "done", "message": "Codex is at 0.160.0"}, token=r["token"])
    assert get(api, "runner-harness-actions", token=r["token"])["actions"] == []
    # Final: a late report cannot reopen it.
    assert post(api, f"runner-harness-actions/{first['id']}/report", {"state": "failed"}, token=r["token"])["state"] == "done"
    done = get(api, "operations")["machines"][0]["harness_actions"][0]
    assert (done["state"], done["message"]) == ("done", "Codex is at 0.160.0")
    assert [e["actor"] for e in events(api, "runner.harness.update")] == ["human:ana"]
    assert [e["actor"] for e in events(api, "runner.harness.done")] == ["runner:" + r["runner_id"]]


def test_one_runner_cannot_report_on_or_read_anothers_requests(api):
    r, other = online(api), online(api)
    request = ask(api, r)
    post(api, f"runner-harness-actions/{request['id']}/report", {"state": "done"}, token=other["token"], expected=404)
    assert get(api, "runner-harness-actions", token=other["token"])["actions"] == []



def test_the_owners_on_off_per_harness_is_kept_per_computer_and_handed_to_its_runner(api):
    r, other = runner(api), online(api)
    beat(api, r, codex=HARNESS, grok={**HARNESS, "name": "Grok Build", "runtime": "grok", "installed": False})
    assert get(api, "runner-harness-actions", token=r["token"])["choices"] == {}
    choose = lambda harness, enabled, **kw: post(api, f"runners/{r['runner_id']}/harness-choices",
                                                 {"harness": harness, "enabled": enabled}, **kw)
    choose("grok", True, token="ben-test", expected=403)
    choose("nothing", True, expected=404)                         # a harness the computer never reported
    for loose in ("yes", "off", 1, 0, None):                      # a real true/false only, never "off" read as off
        choose("grok", loose, expected=422)
    assert choose("grok", True)["enabled"] is True
    choose("codex", False)
    choose("grok", False)
    assert get(api, "runner-harness-actions", token=r["token"])["choices"] == {"grok": False, "codex": False}
    assert get(api, "runner-harness-actions", token=other["token"])["choices"] == {}
    machine = next(m for m in get(api, "operations")["machines"] if m["id"] == r["runner_id"])
    assert machine["harness_choices"] == {"grok": False, "codex": False}
    assert [e["actor"] for e in events(api, "runner.harness.off")] == ["human:ana", "human:ana"]


def company(api, **record):
    from backend.store import encode
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM registry_metadata WHERE key='providers'")
        c.execute("INSERT INTO registry_metadata VALUES('providers',?)", (encode({"revision": 1, **record}),))


def machine(api, r):
    return next(m for m in get(api, "operations")["machines"] if m["id"] == r["runner_id"])


def test_what_a_bot_there_runs_on_its_fallback_included_cannot_be_turned_off(api):
    from backend.store import encode
    company(api, enabled=["openai", "google", "xai"], runtime="codex", model="gpt-6-sol")
    r = runner(api)
    assign(api, r, "cpo")
    # A branch bot: its fallback is its original's (shared_bots.follow), as the runner reads it.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config=? WHERE bot='finance'", (encode(
            {"name": "finance", "fallback": {"harness": "gemini", "model": "gemini-3-pro"},
             "task_model": {"harness": "grok", "runtime": "grok", "model": "grok-4.6"}}),))
        c.execute("UPDATE bot_config SET config=? WHERE bot='cpo'", (encode({"name": "cpo", "shared_from": "finance"}),))
    beat(api, r, codex=HARNESS, **{"gemini-cli": {**HARNESS, "name": "Gemini CLI", "runtime": "gemini"}},
         grok={**HARNESS, "name": "Grok Build", "runtime": "grok"})
    choose = lambda harness, enabled, **kw: post(api, f"runners/{r['runner_id']}/harness-choices",
                                                 {"harness": harness, "enabled": enabled}, **kw)
    choose("codex", False, expected=409)                          # the bot's own
    choose("gemini-cli", False, expected=409)                     # its fallback
    assert choose("gemini-cli", True)["enabled"] is True
    choose("grok", False, expected=409)                           # its inherited task model
    assert get(api, "runner-harness-actions", token=r["token"])["choices"] == {"gemini-cli": True}
    # All three are required installs; only the primary is needed for the computer to be ready (tasks can fall back).
    assert (machine(api, r)["bot_runtimes"], machine(api, r)["needed_runtimes"]) == (["codex", "gemini", "grok"], ["codex"])
    assert machine(api, runner(api))["bot_runtimes"] == []


def test_a_computer_with_no_bots_needs_only_the_one_default_it_installs(api):
    # No company default: the runner installs one runtime (config default_runtime), not every enabled provider's.
    company(api, enabled=["xai", "openai"], runtime="", model="")
    r = runner(api)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM assignments WHERE runner_id=?", (r["runner_id"],))      # the first computer's built-in bots
    assert get(api, "config")["default_runtime"] == "codex"
    assert machine(api, r)["needed_runtimes"] == ["codex"]
    company(api, enabled=["xai"], runtime="grok", model="grok-5")
    assert machine(api, r)["needed_runtimes"] == ["grok"]
    company(api, enabled=[], runtime="", model="")
    assert machine(api, r)["needed_runtimes"] == []
    # With a bot, what it runs on: here the company's first provider's.
    company(api, enabled=["xai", "openai"], runtime="", model="")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config='{\"name\": \"cpo\"}' WHERE bot='cpo'")
    assign(api, r, "cpo")
    assert machine(api, r)["needed_runtimes"] == ["grok"]


def test_a_move_to_a_computer_without_the_harness_is_not_refused_it_installs_there(api):
    from backend.readiness import can_run
    from backend.tests.test_settings_transitions import _company_default
    _company_default(api, "cpo")
    there = runner(api, "ben", "Ben Mac")
    missing = {**HARNESS, "installed": False, "version": "", "managed": False, "source": "", "wanted": False}

    def verdict(**codex):
        post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": {
            "schema_version": 1, "runtimes": {"codex": {"installed": False, "authenticated": "missing"}},
            "bots": {}, "harnesses": {"codex": {**missing, **codex}}}}, token=there["token"])
        with api.app.state.store.read() as c:
            return can_run(c, "cpo", "codex", runner=there["runner_id"])

    answer = verdict()
    assert (answer["can_run"], answer["problem"]) == (None, "Codex is installing on Ben Mac")
    answer = verdict(state="failed", detail="npm is not installed on this computer")
    assert answer["can_run"] is False and "could not be installed" in answer["problem"]


def test_choices_and_recent_batch_match_per_runner_lookups(api):
    """Operations listing should not query harness tables once per computer."""
    from backend import harness_actions
    a, b = online(api), online(api)
    ask(api, a)
    ask(api, b, action="update", harness="codex")
    post(api, f"runners/{a['runner_id']}/harness-choices", {"harness": "codex", "enabled": False})
    with api.app.state.store.read() as c:
        by_choice = harness_actions.choices_by_runner(c)
        by_recent = harness_actions.recent_by_runner(c)
        assert by_choice[a["runner_id"]] == harness_actions.choices(c, a["runner_id"])
        assert by_choice.get(b["runner_id"], {}) == harness_actions.choices(c, b["runner_id"])
        assert by_recent[a["runner_id"]] == harness_actions.recent(c, a["runner_id"])
        assert by_recent[b["runner_id"]] == harness_actions.recent(c, b["runner_id"])
