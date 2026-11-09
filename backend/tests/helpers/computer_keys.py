"""Helpers that other tests share, moved out of test_computer_keys.py so they import no test file."""

import uuid
from backend.tests.helpers.computer_signin import vault
from backend.tests.helpers.onboarding import signed_in


CAPABLE = ["model_sign_out_v1", "model_keys_v1"]


SHARED = "sk-team-key-0123456789"


def computer(api, label, operator="morgan", source=None, capabilities=CAPABLE, beat=True, **key):
    """An enrolled computer; `source` is the key it reports signing Codex in with ("shared", "terminal", ...), or
    `runtime` with key `name`."""
    code = api.post("/api/v2/enrollments", json={"operator": operator}, headers=signed_in()).json()["code"]
    made = api.post("/api/v2/runners/enroll", json={"code": code, "label": label, "platform": "linux"}).json()
    if beat:
        heartbeat(api, made["token"], source, capabilities, **key)
    return made


def heartbeat(api, token, source=None, capabilities=CAPABLE, runtime="codex", name="OPENAI_API_KEY"):
    row = {"installed": True, "authenticated": "ready" if source else "missing"}
    if source:
        row["key"] = {"name": name, "source": source}
    r = api.post(
        "/api/v2/runners/heartbeat",
        headers={"Authorization": "Bearer " + token, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "version": "test",
            "platform": "linux",
            "capabilities": capabilities,
            "readiness": {"schema_version": 1, "runtimes": {runtime: row}, "bots": {}},
        },
    )
    assert r.status_code == 200, r.text


def call(api, method, path, body=None, who=None, expected=200):
    r = api.request(method, "/api/v2/" + path, json=body, headers=who or signed_in())
    assert r.status_code == expected, r.text
    return r.json()


def report(api, machine, lid, state, message=""):
    r = api.post(
        f"/api/v2/runner-logins/{lid}/report",
        json={"state": state, "message": message},
        headers={"Authorization": "Bearer " + machine["token"], "Idempotency-Key": uuid.uuid4().hex},
    )
    assert r.status_code == 200, r.text


def pending(api, machine):
    r = api.get("/api/v2/runner-logins", headers={"Authorization": "Bearer " + machine["token"]})
    assert r.status_code == 200, r.text
    return r.json()["logins"]


def fetch(api, machine, login="", runtime="codex"):
    params = {"runtime": runtime, **({"login": login} if login else {})}
    r = api.get(
        "/api/v2/runner-model-credentials", params=params, headers={"Authorization": "Bearer " + machine["token"]}
    )
    assert r.status_code == 200, r.text
    return r.json()


def listed(api, machine):
    return next(m for m in call(api, "GET", "operations")["computers"] if m["id"] == machine["runner_id"])


def setup(environment):
    api = environment()
    vault(api)
    return api
