"""Throwaway probes for PR 559 review (not part of the PR)."""

from backend.tests.test_api import api, headers, post  # noqa: F401
from backend.tests.test_assistant import assistant_turn


def create(api, token, **kw):
    body = {"owner": "human:cara", "title": "Review the plan", "body": "Please review the plan."}
    body.update(kw)
    r = api.post("/api/v2/tasks", json=body, headers=headers(token))
    return r.status_code, r.json()


def test_probe_edges(api):
    r, attempt = assistant_turn(api, "ben-test")
    token = attempt["token"]
    # Unknown / malformed person ids: gate may pass them, the route must refuse and create nothing.
    for owner in ("human:nobody", "human:", "human: cara", "HUMAN:cara", "human:cara "):
        code, out = create(api, token, owner=owner, title=f"Probe {owner!r}")
        print("OWNER", repr(owner), code, out.get("error", {}).get("code"), out.get("owner"))
    with api.app.state.store.read() as c:
        n = c.execute("SELECT count(*) FROM tasks WHERE title LIKE 'Probe %'").fetchone()[0]
        print("PROBE TASKS CREATED", n)
    # Settling / odd fields on a create for another person.
    for extra in ({"status": "done"}, {"close": True}, {"next_run": True}, {"private": True},
                  {"owner": "human:cara", "request_id": "turn"}):
        code, out = create(api, token, title=f"Field {sorted(extra)}", **extra)
        print("FIELD", extra, code, out.get("error", {}).get("code"), out.get("error", {}).get("detail", "")[:110])
    # Plain-ask lint: a jargon/id-heavy first paragraph an ordinary bot would be refused for.
    code, out = create(api, token, title="backend/assistant.py write_allowed 972cdadd",
                        body="Run hub task update 972cdadd-8fb3-48d9-8307 on backend/assistant.py, check "
                             "s3://bucket/key and set status: done via confirm_required in write_allowed.")
    print("JARGON", code, out.get("error", {}).get("code"), out.get("warnings"), out.get("id") is not None)
    # A subtask for another person under ben's own task, and under a task ben cannot see.
    mine = post(api, "tasks", {"owner": "human:ben", "title": "Ben parent", "body": "Parent."}, token=token)
    code, out = create(api, token, title="Cara child", relations=[{"kind": "parent", "task": mine["id"]}])
    print("SUBTASK own parent", code, out.get("owner"))
    code, out = create(api, token, title="Cara orphan", relations=[{"kind": "parent", "task": "no-such-task"}])
    print("SUBTASK bad parent", code, out.get("error", {}).get("code"))


def test_probe_bot_lint_contrast(api):
    # The same jargon body from an ordinary bot actor is refused by require_person_ask (hubdb rule 7).
    from backend.store import H
    from backend.tests.test_api import assign, claim, ready, runner
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    with api.app.state.store.transaction() as c:
        t = H.task_create(c, "human:ana", "Start ops", "Please start.", "bot:ops")
    att = claim(api, r, "ops")
    body = {"owner": "human:cara", "title": "Review the plan",
            "body": "Run hub task update 972cdadd-8fb3-48d9-8307 on backend/assistant.py, check "
                    "s3://bucket/key and set status: done via confirm_required in write_allowed."}
    resp = api.post("/api/v2/tasks", json=body, headers=headers(att["token"]))
    print("BOT JARGON", resp.status_code, resp.json().get("error", {}).get("code"),
          resp.json().get("error", {}).get("detail", "")[:160])
