"""Helpers that other tests share, moved out of test_computer_signin.py so they import no test file."""

from backend.credentials import CredentialCipher
from backend.tests.helpers.credentials import FakeKMS
from backend.tests.helpers.onboarding import OWNER_EMAIL, signed_in


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
    return api.get(
        "/api/v2/runner-model-credentials", params={"runtime": runtime}, headers={"Authorization": "Bearer " + token}
    )


def new_computer(api, label="Tico Team mail"):
    code = api.post("/api/v2/enrollments", json={"operator": "morgan"}, headers=signed_in()).json()["code"]
    return api.post("/api/v2/runners/enroll", json={"code": code, "label": label, "platform": "linux"}).json()
