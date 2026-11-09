"""Helpers that other tests share, moved out of test_credentials.py so they import no test file."""

import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from backend.credentials import CredentialCipher
from backend.tests.helpers.api import post


class FakeKMS:
    def __init__(self):
        self.key = os.urandom(32)

    def generate_data_key(self, **kwargs):
        assert kwargs['EncryptionContext'] == {'application': 'tico-credentials'}
        key = os.urandom(32)
        nonce = os.urandom(12)
        return {'Plaintext': key, 'CiphertextBlob': nonce + AESGCM(self.key).encrypt(nonce, key, b'kms')}

    def decrypt(self, **kwargs):
        blob = kwargs['CiphertextBlob']
        return {'Plaintext': AESGCM(self.key).decrypt(blob[:12], blob[12:], b'kms')}


def setup(api):
    api.app.state.store.settings.credential_kms_key = 'test-key'
    api.app.state.store.settings.credential_admins = ('ana@acme.example', 'ben@acme.example')
    api.app.state.vault.cipher = CredentialCipher('test-key', FakeKMS())


def create(api, **kwargs):
    return post(
        api,
        'credentials',
        {'name': 'PostHog', 'secret': 'phx-synthetic-private-987654', 'env': 'POSTHOG_API_KEY', **kwargs},
    )
