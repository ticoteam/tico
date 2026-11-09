"""Helpers that other tests share, moved out of test_invites.py so they import no test file."""

import uuid


def owner(signin):  # noqa: F811
    """Ana, the owner, signed in through the issuer; the headers a browser write carries."""
    done = signin.login(claims={"email": "ana@acme.example"})
    assert done.status_code == 302, done.text
    return {"origin": signin.settings.public_url, "idempotency-key": "invite-" + uuid.uuid4().hex}
