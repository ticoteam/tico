"""Placing or moving a bot brings its repository: the server names it on the assignment, refuses a move that
would strand the only copy, forgets the old computer's report, and Health and `hub health check` say why a
bot has no repository on its computer."""
from backend.tests.test_api import api, assign, get, post, runner
from backend.tests.test_settings_transitions import _company_default

REPO = "Acme/emp-ops"
GENERIC = "Missing bot repository or AGENT.md"


def report(api, machine, present, published=None, problems=(), ready=None, repository=""):
    row = {"ready": (not problems) if ready is None else ready, "runtime": "codex", "model": "gpt-6-sol",
           "repository_present": present, "repository_revision": "abc" if present else "",
           "configuration_valid": True, "problems": list(problems), "warnings": []}
    if repository:
        row["repository"] = repository
    if published is not None:
        row["published"] = published
    post(api, "runners/heartbeat", {"version": "0.5.4", "platform": "linux", "capacity": 4, "readiness": {
        "schema_version": 1, "runtimes": {"codex": {"installed": True, "authenticated": "ready", "models": [],
                                                     "controls": ["interrupt", "new-session"]}},
        "bots": {"ops": row}}}, machine["token"])


def stored(api, machine):
    with api.app.state.store.read() as c:
        import json
        return json.loads(c.execute("SELECT readiness_json FROM runners WHERE id=?", (machine["runner_id"],)).fetchone()[0])


def two_computers(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET repo=? WHERE bot='ops'", (REPO,))
    _company_default(api, "ops")
    old, new = runner(api, "ana", "Old Mac"), runner(api, "ana", "Mail box")
    assign(api, old, "ops")
    return old, new


def test_a_move_that_would_strand_the_only_copy_is_refused_and_names_the_cause(api):
    old, new = two_computers(api)
    report(api, old, present=True, published=False)
    report(api, new, present=False, problems=[GENERIC])
    failure = post(api, "bots/ops/assignment", {"runner_id": new["runner_id"], "expected_generation": 1}, expected=409)
    assert failure["error"]["code"] == "repository_unpublished"
    assert "Old Mac" in failure["error"]["detail"] and REPO in failure["error"]["detail"] and "GitHub" in failure["error"]["detail"]
    settings_move = post(api, "bots/ops/transitions", {"kind": "machine", "runner_id": new["runner_id"],
                                                        "expected_revision": 1, "expected_generation": 1}, expected=409)
    assert settings_move["error"]["code"] == "repository_unpublished"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT runner_id FROM assignments WHERE bot='ops'").fetchone()[0] == old["runner_id"]


def test_a_published_bot_moves_and_the_old_computer_stops_claiming_it(api):
    old, new = two_computers(api)
    report(api, old, present=True, published=True)
    report(api, new, present=False, problems=[GENERIC])
    # The destination has nothing to report ready until the runner there clones it: assigning is what starts that.
    post(api, "bots/ops/transitions", {"kind": "machine", "runner_id": new["runner_id"],
                                        "expected_revision": 1, "expected_generation": 1})
    with api.app.state.store.read() as c:
        assert c.execute("SELECT runner_id FROM assignments WHERE bot='ops'").fetchone()[0] == new["runner_id"]
    assert "ops" not in stored(api, old)["bots"]                                # dropped at the move
    # Its next heartbeat still lists the bot (it could host it, and an old checkout is there), but that is not
    # this computer running it: the computers view does not show it as ready here.
    report(api, old, present=True, published=True)
    machines = {m["id"]: m for m in get(api, "operations")["machines"]}
    assert "ops" not in machines[old["runner_id"]]["readiness"]["bots"]
    assert "ops" not in machines[old["runner_id"]]["bots"]
