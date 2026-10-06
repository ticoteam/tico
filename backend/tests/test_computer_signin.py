"""A new computer signs its model in from the team's model credential, and says so on Health when it cannot."""

from backend.credentials import CredentialCipher
from backend.tests.test_credentials import FakeKMS
from backend.tests.test_getting_started import add_bot, heartbeat  # noqa: F401
from backend.tests.test_onboarding import OWNER_EMAIL, environment, signed_in  # noqa: F401



def vault(api):
    settings = api.app.state.store.settings
    settings.credential_kms_key, settings.credential_admins = "test-key", (OWNER_EMAIL,)
    api.app.state.vault.cipher = CredentialCipher("test-key", FakeKMS())


def store(api, env, value, **kwargs):
    r = api.post("/api/v2/credentials", json={"name": env, "env": env, "secret": value, **kwargs}, headers=signed_in())
    assert r.status_code == 200, r.text
    return r.json()["id"]


def grant(api, cid, subject, expected=200):
    r = api.post(f"/api/v2/credentials/{cid}/grants", json={"subject": subject}, headers=signed_in())
    assert r.status_code == expected, r.text
    return r


def computer_asks(api, token, runtime="codex"):
    return api.get("/api/v2/runner-model-credentials", params={"runtime": runtime}, headers={"Authorization": "Bearer " + token})


def new_computer(api, label="Tico Team mail"):
    code = api.post("/api/v2/enrollments", json={"operator": "morgan"}, headers=signed_in()).json()["code"]
    return api.post("/api/v2/runners/enroll", json={"code": code, "label": label, "platform": "linux"}).json()


def test_a_computer_gets_the_team_model_key_and_nothing_a_bot_holds(environment):
    api = environment()
    vault(api)
    add_bot(api, "mailer")
    team = store(api, "OPENAI_API_KEY", "sk-team-key-0123456789")
    private = store(api, "OPENAI_API_KEY", "sk-only-for-mailer-999999", name="Mailer's own key")
    other = store(api, "POSTHOG_API_KEY", "phx-mailer-secret-123456")
    grant(api, team, "computers")
    grant(api, private, "bot:mailer")
    grant(api, other, "bot:mailer")
    machine = new_computer(api)
    got = computer_asks(api, machine["token"])
    assert got.status_code == 200
    assert got.json() == {"credentials": [{"env": "OPENAI_API_KEY", "value": "sk-team-key-0123456789"}]}
    assert computer_asks(api, machine["token"], "claude").json() == {"credentials": []}
    assert computer_asks(api, machine["token"], "gemini").status_code == 422
    # Only a computer asks, and the event that records it never holds the key.
    assert api.get("/api/v2/runner-model-credentials", params={"runtime": "codex"}, headers=signed_in()).status_code == 403
    with api.app.state.store.read() as c:
        dump = str([tuple(r) for r in c.execute("SELECT * FROM events WHERE action='credential.sent_to_computer'")])
    assert machine["runner_id"] in dump and "sk-team-key" not in dump
    # Once the runner has signed in, Health has nothing to say about it.
    heartbeat(api, machine["runner_id"], runtimes={"codex": {"installed": True, "authenticated": "ready"}})
    checks = {c["id"]: c for c in api.get("/api/v2/health", headers=signed_in()).json()["checks"]}
    assert "computer_signin" not in checks


def test_only_a_model_key_can_go_to_every_computer_and_only_an_administrator_can_say_so(environment):
    api = environment()
    vault(api)
    grant(api, store(api, "POSTHOG_API_KEY", "phx-mailer-secret-123456"), "computers", expected=422)
    grant(api, store(api, "OPENAI_API_KEY", "sk-a-password", name="Pass", kind="password"), "computers", expected=422)
    grant(api, store(api, "OPENAI_API_KEY", "sk-x", name="Empty-ish"), "human:nobody", expected=422)
    machine = new_computer(api)
    assert computer_asks(api, machine["token"]).json() == {"credentials": []}
    cid = store(api, "ANTHROPIC_API_KEY", "sk-ant-team-0123456789", name="Claude key")
    grant(api, cid, "computers")
    assert computer_asks(api, machine["token"], "claude").json()["credentials"][0]["env"] == "ANTHROPIC_API_KEY"
    # Taking the grant back takes the key back.
    listing = api.get("/api/v2/credentials", headers=signed_in()).json()["credentials"]
    row = next(x for x in listing if x["id"] == cid)
    gid = next(g["id"] for g in row["grants"] if g["subject"] == "computers")
    assert api.post(f"/api/v2/credentials/{cid}/grants/{gid}/revoke", json={}, headers=signed_in()).status_code == 200
    assert computer_asks(api, machine["token"], "claude").json() == {"credentials": []}

