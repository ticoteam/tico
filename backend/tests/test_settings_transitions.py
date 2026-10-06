
from backend.store import encode

from backend.tests.test_api import api, assign, get, post, ready, runner  # noqa: F401


def _report(api, machine, bots):
    document = {"schema_version": 1,
                "runtimes": {"codex": {"installed": True, "authenticated": "ready",
                                          "models": [], "controls": ["interrupt", "new-session"]}},
                "bots": bots}
    post(api, "runners/heartbeat", {"version": "0.2.0", "platform": "linux",
                                    "capacity": 4, "readiness": document}, machine["token"])


def _placed(api, bot):
    with api.app.state.store.read() as c:
        return c.execute("SELECT runner_id FROM assignments WHERE bot=?", (bot,)).fetchone()[0]


def _company_default(api, bot, model="gpt-6-sol"):
    """Set a company Codex default while `bot` names neither setting."""
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO registry_metadata VALUES('providers',?)", (encode({
            "enabled": ["openai"], "runtime": "codex", "model": model, "revision": 1}),))
        c.execute("UPDATE bot_config SET config_json=? WHERE bot=?", (encode({"name": bot}), bot))


def test_move_refuses_a_destination_that_is_not_ready_for_the_bot(api):
    _company_default(api, "cpo")
    first = runner(api, "ana", "Ana Mac")
    assign(api, first, "cpo")
    machine = runner(api, "ben", "Ben setup Mac")
    _report(api, machine, {"cpo": {
        "ready": False, "runtime": "codex", "model": "gpt-6-sol", "repository_present": False,
        "repository_revision": "", "configuration_valid": True,
        "problems": ["Missing bot repository or AGENT.md"]}})
    failure = post(api, "bots/cpo/transitions", {
        "kind": "machine", "runner_id": machine["runner_id"], "expected_revision": 1,
        "expected_generation": 1,
    }, expected=409)
    assert failure["error"]["code"] == "runner_not_ready"
    assert "repository" in failure["error"]["detail"].lower()


def test_resume_and_restore_check_the_repository_before_activating(api):
    r = runner(api)
    assign(api, r, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE slug='ops'")
    revision = get(api, "bots/ops")["revision"]
    denied = post(api, "bots/ops/control", {"action": "resume", "expected_revision": revision}, expected=409)
    assert denied["error"]["code"] == "repository_missing"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM bots WHERE slug='ops'").fetchone()[0] == "paused"
    ready(api, r, ["ops"])
    post(api, "bots/ops/control", {"action": "resume", "expected_revision": revision})
    revision = get(api, "bots/ops")["revision"]
    post(api, "bots/ops/archive", {"expected_revision": revision})
    ready(api, r, [])
    denied = post(api, "bots/ops/restore", {}, expected=409)
    assert denied["error"]["code"] == "repository_missing"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM bots WHERE slug='ops'").fetchone()[0] == "archived"
    ready(api, r, ["ops"])
    assert post(api, "bots/ops/restore", {})["status"] == "active"
    # Built-in repositories are built by their assigned computer, even on older configs without materialize.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='coo'", (encode({"template": "assistant"}),))
        c.execute("UPDATE bots SET state='paused' WHERE slug='coo'")
    ready(api, r, [])
    pending = post(api, "bots/coo/go-live", {"setup": False})
    assert pending["state"] == "active" and pending["building"] and pending["setup_started"] is False
    with api.app.state.store.read() as c:
        assert c.execute("SELECT runner_id FROM assignments WHERE bot='coo'").fetchone()[0] == r["runner_id"]
