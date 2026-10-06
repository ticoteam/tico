import base64
import json
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from backend import identity_proxy
from backend.tests.test_api import api

ARN = "arn:aws:elasticloadbalancing:us-west-2:123456789012:loadbalancer/app/tico/50dc6c495c0c9188"
KID = "8c7e1c5a-1111-4222-8333-944455556666"


@pytest.fixture
def alb(api, monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo)
    auth = api.app.state.auth
    settings = auth.settings
    settings.auth_proxy, settings.alb_arn, settings.alb_region = "aws-alb", ARN, "us-west-2"
    auth.proxy = identity_proxy.build(settings)
    fetched = []

    def get(url, **kw):
        fetched.append(url)
        if not url.endswith("/" + KID):
            return SimpleNamespace(content=b"", raise_for_status=lambda: (_ for _ in ()).throw(
                identity_proxy.httpx.HTTPError("404")))
        return SimpleNamespace(content=pem, raise_for_status=lambda: None)
    monkeypatch.setattr(identity_proxy.httpx, "get", get)

    def token(kid=KID, signer=ARN, alg="ES256", **claims):
        body = {"sub": "s", "email": "ben@acme.example", "exp": int(time.time()) + 600, **claims}
        body = {k: v for k, v in body.items() if v is not None}
        return jwt.encode(body, key, algorithm=alg, headers={"kid": kid, "signer": signer})
    return SimpleNamespace(token=token, fetched=fetched, key=key, api=api, auth=auth)


def hdr(token):
    return {"x-amzn-oidc-data": token}


def test_valid_token_maps_to_roster_identity(alb):
    result = alb.api.get("/api/v2/me", headers=hdr(alb.token()))
    assert result.status_code == 200 and result.json()["actor"] == "human:ben"
    assert alb.fetched == ["https://public-keys.auth.elb.us-west-2.amazonaws.com/" + KID]
    alb.api.get("/api/v2/me", headers=hdr(alb.token()))
    assert len(alb.fetched) == 1                    # cached per kid


def test_wrong_signer_is_rejected_without_a_fetch(alb):
    other = ARN.replace("50dc", "ffff")
    assert alb.api.get("/api/v2/me", headers=hdr(alb.token(signer=other))).status_code == 401
    assert alb.fetched == []


def test_alg_confusion_is_rejected_without_a_fetch(alb):
    forged = jwt.encode({"email": "ben@acme.example", "exp": int(time.time()) + 600}, "x" * 32,
                        algorithm="HS256", headers={"kid": KID, "signer": ARN})
    assert alb.api.get("/api/v2/me", headers=hdr(forged)).status_code == 401
    assert alb.fetched == []


def test_expired_or_missing_exp_is_rejected(alb):
    assert alb.api.get("/api/v2/me", headers=hdr(alb.token(exp=int(time.time()) - 5))).status_code == 401
    assert alb.api.get("/api/v2/me", headers=hdr(alb.token(exp=None))).status_code == 401


def test_signature_from_another_key_is_rejected(alb):
    forged = jwt.encode({"email": "ben@acme.example", "exp": int(time.time()) + 600},
                        ec.generate_private_key(ec.SECP256R1()), algorithm="ES256",
                        headers={"kid": KID, "signer": ARN})
    assert alb.api.get("/api/v2/me", headers=hdr(forged)).status_code == 401


def alb_style(key, header, claims):
    """A token the way the ALB builds it: base64url segments keep their "=" padding and the
    signature covers the padded header and payload."""
    seg = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode()
    signing_input = seg(header) + "." + seg(claims)
    signature = jwt.get_algorithm_by_name("ES256").sign(signing_input.encode(), key)
    return signing_input + "." + base64.urlsafe_b64encode(signature).decode()


def test_alb_tokens_signed_over_padded_segments_are_accepted(alb):
    # Pick claims whose encodings need padding, so this fails if the verifier re-encodes first.
    header = {"typ": "JWT", "kid": KID, "alg": "ES256", "signer": ARN, "iss": "https://cognito",
              "client": "c", "exp": int(time.time()) + 600}
    claims = {"sub": "s", "email_verified": "true", "email": "ben@acme.example",
              "username": "google_1", "exp": int(time.time()) + 600, "iss": "https://cognito"}
    for extra in ("", "x", "xx"):
        token = alb_style(alb.key, header, {**claims, "sub": "s" + extra})
        assert "=" in token.split(".")[0] + token.split(".")[1] or extra, token
        assert alb.api.get("/api/v2/me", headers=hdr(token)).status_code == 200


def test_alb_token_with_tampered_padded_payload_is_rejected(alb):
    header = {"kid": KID, "alg": "ES256", "signer": ARN}
    token = alb_style(alb.key, header, {"email": "ben@acme.example", "exp": int(time.time()) + 600})
    head, _, sig = token.split(".")
    other = base64.urlsafe_b64encode(json.dumps({"email": "ana@acme.example",
                                                 "exp": int(time.time()) + 600}).encode()).decode()
    assert alb.api.get("/api/v2/me", headers=hdr(head + "." + other + "." + sig)).status_code == 401


def test_unrostered_email_is_forbidden(alb):
    assert alb.api.get("/api/v2/me", headers=hdr(alb.token(email="nobody@acme.example"))).status_code == 403


def test_email_is_required_and_verified_flag_respected(alb):
    assert alb.api.get("/api/v2/me", headers=hdr(alb.token(email=None))).status_code == 401
    assert alb.api.get("/api/v2/me", headers=hdr(alb.token(email_verified=False))).status_code == 401
    assert alb.api.get("/api/v2/me", headers=hdr(alb.token(email_verified=True))).status_code == 200


def test_cloudflare_credentials_do_nothing_under_aws_alb(alb):
    assert alb.api.get("/api/v2/me", headers={"Cf-Access-Jwt-Assertion": "x"}).status_code == 401
    assert alb.api.get("/api/v2/me", headers={"Cookie": "CF_Authorization=x"}).status_code == 401
    assert alb.api.get("/api/v2/me", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_alb_header_does_nothing_under_cloudflare(alb):
    alb.auth.settings.auth_proxy = "cloudflare"
    alb.auth.settings.access_issuer = "https://t.cloudflareaccess.com"
    alb.auth.settings.access_audience = "aud"
    alb.auth.proxy = identity_proxy.build(alb.auth.settings)
    assert alb.api.get("/api/v2/me", headers=hdr(alb.token())).status_code == 401
    assert alb.fetched == []

