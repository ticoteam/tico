"""Helpers that other tests share, moved out of test_credential_sharing.py so they import no test file."""

import os
from backend.tests.helpers.api import post
from backend.tests.helpers.credentials import FakeKMS


JIRA = "jira-basic-auth-synthetic-fixture-0123456789"


def local(api):
    """The default: no KMS key is set, so the vault's data key is the file beside the database."""
    settings = api.app.state.store.settings
    assert not settings.credential_kms_key
    settings.credential_admins = ("ana@acme.example", "ben@acme.example")
    return settings.db_path.parent / "credential.key"


def jira(api, **kwargs):
    return post(api, "credentials", {"name": "Jira", "env": "JIRA_BASIC_AUTH", "secret": JIRA, **kwargs})


class KMSWithEncrypt(FakeKMS):
    def encrypt(self, **kwargs):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        assert kwargs["EncryptionContext"] == {"application": "tico-credentials"}
        nonce = os.urandom(12)
        return {"CiphertextBlob": nonce + AESGCM(self.key).encrypt(nonce, kwargs["Plaintext"], b"kms")}
