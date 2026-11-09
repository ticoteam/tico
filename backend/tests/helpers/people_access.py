"""Helpers that other tests share, moved out of test_people_access.py so they import no test file."""

import uuid
from backend.store import H, digest
from backend.tests.helpers.onboarding import signed_in


def person_headers(api, pid):
    """A personal API token: its role is decided by the person's email, as a browser's is."""
    secret = "tok-" + pid + "-" + uuid.uuid4().hex
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO human_tokens(id,human,label,token_hash,created_at,created_by) VALUES(?,?,?,?,?,?)",
            (uuid.uuid4().hex, pid, "test", digest(secret), H.now(), "test"),
        )
    return {"Authorization": "Bearer " + secret, "Idempotency-Key": str(uuid.uuid4())}


def view(api, headers=None, expected=200):
    r = api.get("/api/v2/access", headers=headers or signed_in())
    assert r.status_code == expected, r.text
    return r.json()


def call(api, method, path, body, headers=None, expected=200):
    r = api.request(method, "/api/v2/access" + path, json=body, headers=headers or signed_in())
    assert r.status_code == expected, r.text
    return r.json()


class HeaderProxy:
    """An identity proxy that trusts a test header instead of a signed assertion."""

    name = "cloudflare"

    def email(self, headers):
        return headers.get("x-test-email")
