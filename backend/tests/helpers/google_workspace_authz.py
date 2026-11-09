"""Helpers that other tests share, moved out of test_google_workspace_authz.py so they import no test file."""

import json
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from backend import google_workspace as workspace
from backend.store import H
from backend.tests.helpers.api import post


BOX = "ana@acme.example"


PERSONAL = "cara@acme.example"


@pytest.fixture
def workspace_key(api, monkeypatch):
    """A connected Workspace key for acme.example, sealed by the server's vault. Token mints go to a fake Google."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    creds = {
        "client_email": "tico-sync@tico-test.example",
        "client_id": "100",
        "private_key": key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode(),
    }
    now = H.now()
    with api.app.state.store.transaction() as c:
        sealed = workspace._cipher().seal(c, "google-workspace:wsk-1", json.dumps(creds))
        c.execute(
            "INSERT INTO google_workspaces(id,domain,project_id,client_email,client_id,key_id,key_sealed,revision,"
            "status,checked_at,connected_at,last_error,probe_mailbox,created_by,created_at,updated_at,replaces_id,"
            "generation,mint_failing_since) VALUES('wsk-1','acme.example','tico-test',?,'100','k1',?,1,'connected',"
            "?,?,NULL,NULL,'ana',?,?,NULL,1,NULL)",
            (creds["client_email"], sealed, now, now, now, now),
        )
    calls = []

    def google_call(url, *, data=None, token=None):
        calls.append(dict(data or {}))
        return 200, {"access_token": "ya29.workspace", "expires_in": 3599}

    monkeypatch.setattr(workspace, "google_call", google_call)
    return calls


def place(api, machine, bot="inbox"):
    """The message bot runs on this computer (its assignment)."""
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT OR REPLACE INTO assignments(bot,runner_id,generation,updated_at,updated_by) VALUES(?,?,0,?,'ana')",
            (bot, machine["runner_id"], H.now()),
        )


def revoke(api, machine):
    """The computer is revoked: it is no host and no holder any more."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runners SET revoked_at=? WHERE id=?", (H.now(), machine["runner_id"]))


def publish(api, token, mailbox=BOX, expected=200):
    return post(
        api,
        "connectors/mail/messages",
        {"mailbox": mailbox, "messages": [], "deleted": [], "synced_at": H.now()},
        token,
        expected=expected,
    )


def token(api, token_, mailbox=BOX, service="gmail", expected=200):
    return post(
        api, "connectors/mail/workspace/token", {"mailbox": mailbox, "service": service}, token_, expected=expected
    )


def health(api, token_, failing=(), ok=None, service="mail", expected=200):
    body = {"service": service, "failing": [{"account": a, "reason": r} for a, r in failing]}
    if ok is not None:
        body["ok_accounts"] = list(ok)
    return post(api, "connectors/health", body, token_, expected=expected)
