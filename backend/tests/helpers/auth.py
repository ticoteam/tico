"""Helpers that other tests share, moved out of test_auth.py so they import no test file."""

import time
from types import SimpleNamespace
from unittest.mock import Mock
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import pytest
from backend.identity_proxy import CloudflareAccess


_KEY = []


@pytest.fixture
def signing(api):
    if not _KEY:  # RSA key generation is slow; one key serves the module
        _KEY.append(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    key = _KEY[0]
    auth = api.app.state.auth
    auth.settings.access_issuer = "https://test.cloudflareaccess.com"
    auth.settings.access_audience = "expected-app-audience"
    jwks = Mock()
    jwks.get_signing_key_from_jwt.return_value = SimpleNamespace(key=key.public_key())
    auth.proxy = CloudflareAccess(auth.settings, jwks=jwks)

    def token(**changes):
        claims = {
            "iss": auth.settings.access_issuer,
            "aud": auth.settings.access_audience,
            "sub": "person-id",
            "email": "ben@acme.example",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        }
        return jwt.encode({**claims, **changes}, key, algorithm="RS256")

    return token
