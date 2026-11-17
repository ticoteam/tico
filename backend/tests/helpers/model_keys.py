"""Model keys given to computers, as tests seed them: computers that report which key they sign in with, and keys
kept for computers before they became subscriptions, moved as an upgrade moves them (the two key migrations)."""

import importlib
import uuid
from datetime import datetime, timezone

from backend.tests.helpers.computer_signin import store, vault
from backend.tests.helpers.onboarding import signed_in


CAPABLE = ["model_sign_out_v1", "model_keys_v1"]
SHARED = "sk-team-key-0123456789"
MOVE = importlib.import_module("backend.migrations.20261117061243_computer_keys_to_subscriptions")
DROP = importlib.import_module("backend.migrations.20261117085930_drop_computer_keys")
# The two tables the drop migration removes, as v0.6.0-rc.1 made them (backend/migrations/baseline.sql).
LEGACY = (
    "CREATE TABLE IF NOT EXISTS computer_keys(credential_id TEXT PRIMARY KEY REFERENCES credentials(id), "
    "runner_id TEXT NOT NULL REFERENCES runners(id), runtime TEXT NOT NULL, state TEXT NOT NULL, login_id TEXT, "
    "created_at TEXT NOT NULL, created_by TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS computer_keys_by_runner ON computer_keys(runner_id,runtime,state)",
    "CREATE TABLE IF NOT EXISTS model_login_key_off(runner_id TEXT NOT NULL REFERENCES runners(id), "
    "runtime TEXT NOT NULL, profile TEXT NOT NULL DEFAULT '', set_at TEXT NOT NULL, set_by TEXT NOT NULL, "
    "PRIMARY KEY(runner_id,runtime,profile))",
)


def legacy(c):
    """Put back the tables a database had before the drop migration, to seed what an older install held."""
    for statement in LEGACY:
        c.execute(statement)


def upgrade(c):
    """Run both key migrations, as a database that held computer keys is upgraded."""
    legacy(c)
    MOVE.up(c)
    DROP.up(c)


def shared_before(api, *cids, subject="computers"):
    """Keys shared with computers in Credentials before keys became subscriptions (vault grants to `subject`; the
    team OpenAI key SHARED when none are named), moved as an upgrade moves them: given to the computers enrolled now
    that run on them. The first credential's id."""
    cids = cids or (store(api, "OPENAI_API_KEY", SHARED),)
    with api.app.state.store.transaction() as c:
        for cid in cids:
            c.execute(
                "INSERT INTO credential_grants(id,credential_id,subject,granted_by,created_at) VALUES(?,?,?,?,?)",
                (uuid.uuid4().hex, cid, subject, "human:owner", datetime.now(timezone.utc).isoformat()),
            )
        upgrade(c)
    return cids[0]


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
    return r.json()


def fetch(api, machine, login="", runtime="codex"):
    params = {"runtime": runtime, **({"login": login} if login else {})}
    r = api.get(
        "/api/v2/runner-model-credentials", params=params, headers={"Authorization": "Bearer " + machine["token"]}
    )
    assert r.status_code == 200, r.text
    return r.json()


def listed(api, machine, who=None):
    return next(m for m in call(api, "GET", "operations", who=who)["computers"] if m["id"] == machine["runner_id"])


def setup(environment):
    api = environment()
    vault(api)
    return api
