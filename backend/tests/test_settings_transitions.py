
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


# ------------------------------------------------------------------ go-live for a bot its computer has not cloned yet
def _new_bot(api, monkeypatch, github):
    """cpo as a newly built bot: planned, waiting for its setup, its repository named, and GitHub answering `github`."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='planned' WHERE slug='cpo'")
        c.execute("UPDATE bot_config SET repo='acme/bot-cpo',onboarding_state='needs_setup' WHERE bot='cpo'")
    asked = []
    monkeypatch.setattr(api.app.state.github_app, "repository_state", lambda name: asked.append(name) or github)
    return asked


def test_go_live_keeps_the_placement_and_the_readiness_report_finishes_it(api, monkeypatch):
    asked = _new_bot(api, monkeypatch, ("present", None))
    machine = runner(api, label="Studio Mac")
    ready(api, machine, [])
    routine = post(api, "bots/cpo/routines", {"key": "review", "title": "Weekday review", "text": "Review the week.",
                                             "cron": "0 9 * * 1-5", "timezone": "America/Los_Angeles", "enabled": True})
    expected = [{"id": routine["routine"]["id"], "title": "Weekday review", "cron": "0 9 * * 1-5",
                 "timezone": "America/Los_Angeles", "enabled": True}]
    waiting = post(api, "bots/cpo/go-live", {"computer": "Studio Mac", "routines": expected}, expected=202)
    assert asked == ["acme/bot-cpo"]
    assert (waiting["state"], waiting["computer"], waiting["activated"]) == ("waiting_for_repository", "Studio Mac", False)
    assert "Studio Mac" in waiting["message"]
    # The placement stands, so the computer is told about the bot and clones it.
    assert _placed(api, "cpo") == machine["runner_id"]
    assert [a["bot"] for a in get(api, "runners/assignments", machine["token"])] == ["cpo"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM bots WHERE slug='cpo'").fetchone()[0] == "planned"
    # The report that says the computer has the repository finishes it, with nobody calling again.
    ready(api, machine, ["cpo"])
    ready(api, machine, ["cpo"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM bots WHERE slug='cpo'").fetchone()[0] == "active"
        said = c.execute("SELECT from_actor,body FROM messages WHERE to_actor='bot:cpo'").fetchall()
        assert [(m["from_actor"], m["body"]) for m in said] == [("human:ana", "Let's set you up.")]
        assert c.execute("SELECT count(*) FROM events WHERE action='bot.go_live_finished' AND target='cpo'").fetchone()[0] == 1
        assert not c.execute("SELECT 1 FROM registry_metadata WHERE key='go-live-pending:cpo'").fetchone()


def test_go_live_that_cannot_finish_tells_the_person_once(api, monkeypatch):
    _new_bot(api, monkeypatch, ("present", None))
    machine = runner(api, label="Studio Mac")
    ready(api, machine, [])
    routine = post(api, "bots/cpo/routines", {"key": "review", "title": "Weekday review", "text": "Review the week.",
                                             "cron": "0 9 * * 1-5", "timezone": "UTC", "enabled": True})
    post(api, "bots/cpo/go-live", {"setup": False, "routines": [
        {"id": routine["routine"]["id"], "title": "Weekday review", "cron": "0 9 * * 1-5", "timezone": "UTC"}]}, expected=202)
    post(api, f"routines/{routine['routine']['id']}", {"cron": "0 10 * * 1-5"})       # changed while it waited
    ready(api, machine, ["cpo"])
    ready(api, machine, ["cpo"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM bots WHERE slug='cpo'").fetchone()[0] == "planned"
        told = c.execute("SELECT body FROM messages WHERE to_actor='human:ana' AND body LIKE '%going live could not finish%'").fetchall()
        assert len(told) == 1 and "hub bot go-live cpo" in told[0]["body"]


def test_go_live_for_a_repository_github_does_not_have_names_the_fix(api, monkeypatch):
    from backend.store import Problem
    _new_bot(api, monkeypatch, ("missing", Problem(
        "github_repo_missing", "The repository acme/bot-cpo does not exist yet on GitHub. "
        "Create it with BotOps: `hub bot repo-create cpo`.", 409)))
    machine = runner(api, label="Studio Mac")
    ready(api, machine, [])
    refused = post(api, "bots/cpo/go-live", {"computer": "Studio Mac"}, expected=409)["error"]
    assert refused["code"] == "repository_missing" and not refused["retryable"]
    assert "hub bot repo-create cpo" in refused["detail"] and "hub bot go-live cpo" in refused["detail"]
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM assignments WHERE bot='cpo'").fetchone()


def test_a_bad_routines_list_shows_the_expected_shape(api, tmp_path, capsys):
    bad = post(api, "bots/cpo/go-live", {"routines": [{"id": "cpo:review", "title": "Review"}]}, expected=422)["error"]
    assert "routines.0.timezone" in bad["detail"] and '"cron": "0 9 * * 1-5"' in bad["detail"]
    from clients import hubcli
    path = tmp_path / "routines.json"
    path.write_text('[{"id": "cpo:review", "title": "Review", "timezone": "UTC", "schedule": "daily"}]')
    try:
        hubcli.read_routines_file(path)
        raise AssertionError("accepted")
    except hubcli.CliError as exc:
        assert "[0].schedule is not a field" in str(exc) and hubcli.ROUTINES_EXAMPLE in str(exc)
    path.write_text('{"id": "cpo:review"}')
    try:
        hubcli.read_routines_file(path)
        raise AssertionError("accepted")
    except hubcli.CliError as exc:
        assert "not an array" in str(exc)
    assert hubcli.main(["bot", "go-live", "--help"]) == 0
    shown = capsys.readouterr().out
    assert hubcli.ROUTINES_EXAMPLE.replace(" ", "") in shown.replace(" ", "").replace("\n", "")
