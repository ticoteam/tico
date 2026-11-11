"""A refused setup step leaves a category, a step, a status and an id on the server, and no secret
(backend/setup_failures.py)."""

import json

from backend import setup_failures as SF
from backend.tests.helpers.api import headers, post, runner

SECRET = "ghs_PLANTEDsecret0123456789abcdef"


HARNESS = {
    "name": "Codex",
    "runtime": "codex",
    "installed": True,
    "version": "0.158.0",
    "managed": True,
    "source": "tools",
    "pinned": False,
    "pin": "",
    "authenticated": "ready",
    "update_available": True,
    "latest": "0.160.0",
    "wanted": True,
    "state": "idle",
    "detail": "",
}


def ask(api, r):
    return post(api, f"runners/{r['runner_id']}/harness-actions", {"harness": "codex", "action": "update"})


def failed(api, action=SF.ACTION):
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT * FROM events WHERE action=? ORDER BY created_at", (action,)).fetchall()
    return [dict(row, detail=json.loads(row["detail"])) for row in rows]


def stored(api):
    """Every event row as text: what a planted secret must not be in."""
    with api.app.state.store.read() as c:
        return json.dumps([dict(row) for row in c.execute("SELECT * FROM events")])


def test_a_refused_github_return_keeps_its_category_step_and_reference_and_no_secret(api):
    params = {"code": SECRET, "state": "state-" + SECRET}
    refused = api.get("/api/v2/github/app/callback", params=params, headers=headers())
    assert refused.status_code == 400
    error = refused.json()["error"]
    (row,) = failed(api)
    assert row["actor"] == "human:ana" and row["target"] == "GET /api/v2/github/app/callback"
    assert row["detail"] == {
        "category": "github_state",
        "step": "GET /api/v2/github/app/callback",
        "status": 400,
        "ref": error["ref"],
    }
    assert error["code"] == "github_state" and error["ref"].startswith("sf-")
    # The same refusal again within the minute is the same row, counted, with the same reference.
    again = api.get("/api/v2/github/app/callback", params=params, headers=headers())
    (row,) = failed(api)
    assert row["detail"]["count"] == 2 and again.json()["error"]["ref"] == error["ref"]
    assert SECRET not in stored(api)


def test_a_refused_write_names_the_ids_its_route_carries_and_never_the_request(api):
    r = runner(api)
    body = {"harness": "codex", "action": "update"}
    path = f"/api/v2/runners/{r['runner_id']}/harness-actions?note={SECRET}"
    refused = api.post(path, json=body, headers={**headers("ben-test"), "X-Note": SECRET})
    assert refused.status_code == 403
    (row,) = failed(api)
    assert row["actor"] == "human:ben" and row["detail"]["category"] == "forbidden"
    assert row["detail"]["computer"] == r["runner_id"] and row["detail"]["status"] == 403
    assert SECRET not in stored(api)


def test_a_computers_refused_enrollment_code_is_kept_and_an_ended_session_is_not(api):
    body = {"code": SECRET, "label": "Cloud", "platform": "test"}
    assert api.post("/api/v2/runners/enroll", json=body).status_code == 401
    (row,) = failed(api)
    assert (row["actor"], row["detail"]["category"]) == ("anonymous", "enrollment")
    # No session: nobody started a setup step.
    assert api.post("/api/v2/github/app/finish", json={"state": SECRET}).status_code == 401
    assert len(failed(api)) == 1 and SECRET not in stored(api)


def test_a_fault_in_a_setup_step_keeps_its_class_and_never_its_message(api, monkeypatch):
    from backend import harness_actions

    def broken(*a, **kw):
        raise RuntimeError("the installer printed " + SECRET)

    monkeypatch.setattr(harness_actions, "request", broken)
    r = runner(api)
    try:
        answer = api.post(
            f"/api/v2/runners/{r['runner_id']}/harness-actions",
            json={"harness": "codex", "action": "update"},
            headers=headers(),
        )
        assert answer.status_code == 500
    except RuntimeError:
        pass  # the test client re-raises a server fault
    (row,) = failed(api)
    assert (row["detail"]["category"], row["detail"]["error"]) == ("server_error", "RuntimeError")
    assert SECRET not in stored(api)


def test_a_failed_harness_request_keeps_its_id_a_category_and_the_computers_scrubbed_lines(api):
    r = runner(api)
    beat_with_lines(api, r, ["2026-01-01T00:00:00Z codex install failed: npm ERR! token=" + SECRET, "x " + SECRET])
    asked = ask(api, r)
    report = {"state": "failed", "message": "npm said " + SECRET}
    answer = api.post(f"/api/v2/runner-harness-actions/{asked['id']}/report", json=report, headers=headers(r["token"]))
    assert answer.status_code == 200
    (row,) = failed(api, "runner.harness.failed")
    detail = row["detail"]
    assert (detail["id"], detail["category"], detail["harness"]) == (asked["id"], "harness_action_failed", "codex")
    assert len(detail["lines"]) == 2 and "install failed" in detail["lines"][0]
    assert SECRET not in stored(api)
    with api.app.state.store.read() as c:
        listed = SF.failures(c, lambda rid: "runner-1" if rid == r["runner_id"] else None)
    assert listed[0]["who"] == "runner-1" and listed[0]["computer"] == "runner-1"
    assert listed[0]["category"] == "harness_action_failed" and listed[0]["lines"] == detail["lines"]


def test_a_harness_request_the_computer_never_answers_leaves_a_failure_event(api):
    from backend import harness_actions
    from backend.store import H

    r = runner(api)
    beat_with_lines(api, r, [])
    asked = ask(api, r)
    with api.app.state.store.transaction() as c:
        late = H.shift(H.now(), hours=-harness_actions.EXPIRES_HOURS - 1)
        c.execute("UPDATE runner_harness_actions SET created_at=? WHERE id=?", (late, asked["id"]))
    assert api.get("/api/v2/runner-harness-actions", headers=headers(r["token"])).json()["actions"] == []
    (row,) = failed(api, "runner.harness.failed")
    assert (row["detail"]["id"], row["detail"]["category"]) == (asked["id"], "no_answer")


def beat_with_lines(api, r, lines):
    body = {
        "version": "test",
        "platform": "test",
        "readiness": {
            "schema_version": 1,
            "runtimes": {},
            "bots": {},
            "harnesses": {"codex": HARNESS},
            "recent_errors": lines,
        },
    }
    assert api.post("/api/v2/runners/heartbeat", json=body, headers=headers(r["token"])).status_code == 200


def test_the_failure_list_reads_only_named_keys_from_any_failure_event(api):
    """An event another module writes (a failed sign-in) shows with the keys it carries; a key nobody listed does not."""
    from backend.store import H

    with api.app.state.store.transaction() as c:
        H.event(
            c,
            "runner:r1",
            "runner.login.failed",
            "r1",
            {
                "category": "cli_stopped",
                "exit_code": 1,
                "elapsed_ms": 4200,
                "login": "login-1",
                "kind": "connect",
                "runtime": "claude",
                "lines": ["phase=exited"],
                "message": "free text " + SECRET,
                "url": "https://example.test/?code=" + SECRET,
            },
        )
        (item,) = SF.failures(c)
    assert item == {
        "at": item["at"],
        "event": "runner.login.failed",
        "who": "computer",
        "category": "cli_stopped",
        "exit_code": 1,
        "elapsed_ms": 4200,
        "login": "login-1",
        "kind": "connect",
        "runtime": "claude",
        "lines": ["phase=exited"],
    }


def test_every_setup_route_records_its_refusals(api):
    """The guard: a route in a setup module is a step `record` keeps, or is named here as a plain read. A new setup
    route that would answer a person with a refusal and keep nothing fails this until it is put on one list."""
    modules = ("backend.github_app", "backend.go_live_routes", "backend.model_subscriptions", "backend.cloud_machines")
    plain_reads = {
        "GET /api/v2/github/app",
        "GET /api/v2/github/product-repos/preview",
        "GET /api/v2/bots/{bot}/github-repos",
        "GET /api/v2/model-subscriptions",
        "GET /api/v2/computer-subscriptions",
        "GET /api/v2/cloud-machines",
        "GET /api/v2/cloud-machines/support",
    }
    # Calls a computer or a turn makes, not a person: their failures are the run's (attempt events) or Health's.
    machine_calls = {
        "POST /api/v2/github/token",
        "POST /api/v2/runner-logins/{lid}/subscription",
        "POST /api/v2/runner-model-subscription-refresh",
    }
    missing = []
    for route in api.app.routes:
        endpoint = getattr(route, "endpoint", None)
        if getattr(endpoint, "__module__", "") not in modules:
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            name = method + " " + route.path
            if not SF.step(method, route.path) and name not in plain_reads | machine_calls:
                missing.append(name)
    assert missing == []
