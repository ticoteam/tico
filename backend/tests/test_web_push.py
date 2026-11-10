"""Web Push (backend/web_push.py): RFC 8291 encryption and RFC 8292 VAPID, a person's own subscriptions, and the call ring
hook. Nothing reaches a push service: `web_push.transmit` is replaced."""

import json
import os
import time
from concurrent.futures import Future

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend import call_ring, web_push as W
from backend.auth import Identity
from backend.tests.helpers.api import headers  # noqa: F401  (the api fixture)
from backend.tests.helpers.tasks_board import bot_token

# RFC 8291 section 5.
PLAINTEXT = b"When I grow up, I want to be a watermelon"
AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
UA_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
SALT = "DGv6ra1nlYgDCS1FRnbzlw"
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
BODY = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl"
    "7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
)


def _key(scalar):
    return ec.derive_private_key(int.from_bytes(W.unb64u(scalar), "big"), ec.SECP256R1())


def decrypt(body, ua_private, auth):
    """What the browser does with a push body: the receiving half of RFC 8291, written apart from the sender's code."""
    salt, idlen = body[:16], body[20]
    as_public, sealed = body[21 : 21 + idlen], body[21 + idlen :]
    ua_public = W._point(ua_private)
    shared = ua_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = W._hmac(W._hmac(auth, shared), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = W._hmac(salt, ikm)
    cek, nonce = (
        W._hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16],
        W._hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12],
    )
    plain = AESGCM(cek).decrypt(nonce, sealed, None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


def test_encryption_matches_rfc_8291_and_the_browser_side_reads_it_back():
    body = W.encrypt(PLAINTEXT, W.unb64u(UA_PUBLIC), W.unb64u(AUTH), as_private=_key(AS_PRIVATE), salt=W.unb64u(SALT))
    assert W.b64u(body) == BODY
    assert W._point(_key(UA_PRIVATE)) == W.unb64u(UA_PUBLIC)
    assert decrypt(body, _key(UA_PRIVATE), W.unb64u(AUTH)) == PLAINTEXT
    # A fresh message (its own ephemeral key and salt) still reads back.
    assert decrypt(W.encrypt(b"ring", W.unb64u(UA_PUBLIC), W.unb64u(AUTH)), _key(UA_PRIVATE), W.unb64u(AUTH)) == b"ring"


def test_the_vapid_token_names_the_endpoints_origin_expires_within_a_day_and_has_a_subject():
    key = ec.generate_private_key(ec.SECP256R1())
    now = time.time()
    token = W.vapid_jwt(key, "https://push.example:8443/wpush/v2/abc?x=1", "mailto:push@localhost", now)
    claims = jwt.decode(token, key.public_key(), algorithms=["ES256"], audience="https://push.example:8443")
    assert claims["aud"] == "https://push.example:8443"
    assert now < claims["exp"] <= now + 24 * 3600
    assert claims["sub"] == "mailto:push@localhost"
    assert jwt.get_unverified_header(token) == {"alg": "ES256", "typ": "JWT"}


def test_the_vapid_subject_is_the_https_address_else_the_owner_else_a_placeholder():
    from types import SimpleNamespace as Settings

    assert W._subject(Settings(public_url="https://tico.acme.example", owner_email="ana@acme.example")) == (
        "https://tico.acme.example"
    )
    assert W._subject(Settings(public_url="http://127.0.0.1:8765", owner_email="ana@acme.example")) == (
        "mailto:ana@acme.example"
    )
    assert W._subject(Settings(public_url="http://127.0.0.1:8765", owner_email="")) == "mailto:push@localhost"


def browser():
    """A browser's push keys: its private key (to read what it is sent) and the subscription JSON it would post."""
    private, auth = ec.generate_private_key(ec.SECP256R1()), os.urandom(16)
    return private, auth, {"p256dh": W.b64u(W._point(private)), "auth": W.b64u(auth)}


def subscribe(api, token, endpoint, keys=None):
    return api.post(
        "/api/v2/push/subscriptions", json={"endpoint": endpoint, "keys": keys or browser()[2]}, headers=headers(token)
    )


def unsubscribe(api, token, endpoint):
    return api.request("DELETE", "/api/v2/push/subscriptions", json={"endpoint": endpoint}, headers=headers(token))


def rows(api):
    with api.app.state.store.read() as c:
        return {r["endpoint"]: dict(r) for r in c.execute("SELECT * FROM push_subscriptions")}


def test_a_person_manages_only_their_own_browsers_and_bots_are_refused(api):
    mine, keys = "https://fcm.googleapis.com/fcm/send/ben-browser", browser()[2]
    assert subscribe(api, "ben-test", mine, keys).status_code == 200
    assert rows(api)[mine]["actor"] == "human:ben"
    # The page checks on each load: the same browser again writes nothing more.
    assert subscribe(api, "ben-test", mine, keys).status_code == 200
    with api.app.state.store.read() as c:
        assert c.execute("SELECT COUNT(*) FROM events WHERE action='push.subscribe'").fetchone()[0] == 1
    # Someone else removing Ben's browser gets the same quiet answer and removes nothing.
    assert unsubscribe(api, "cara-test", mine).status_code == 200
    assert mine in rows(api)
    assert unsubscribe(api, "ben-test", mine).json() == {"subscribed": False}
    assert mine not in rows(api)
    # Bots, and anyone acting for a person, never hold a browser's subscription or read the key.
    identities = api.app.state.store.settings.test_identities
    identities["for-ben"] = Identity("human:ben", "human", "ben@acme.example", via="assistant")
    # A personal API token is Ben, but not Ben's browser: an agent outside it never manages its push.
    identities["ben-token"] = Identity("human:ben", "human", "ben@acme.example", via_token=True, token_label="muse")
    for token in (bot_token(api, "ops"), "for-ben", "ben-token"):
        assert subscribe(api, token, mine).status_code == 403
        assert unsubscribe(api, token, mine).status_code == 403
        assert api.get("/api/v2/push/key", headers=headers(token)).status_code == 403
    assert rows(api) == {}


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/fcm/send/x",
        "https://127.0.0.1/push",
        "https://localhost/push",
        "https://[::1]/push",
        "https://10.0.0.5/push",
        "https://push.internal/x",
        "https://user:pw@fcm.googleapis.com/x",
    ],
)
def test_an_endpoint_that_is_not_a_public_https_address_is_refused(api, endpoint):
    answer = subscribe(api, "ben-test", endpoint)
    assert answer.status_code == 422 and answer.json()["error"]["code"] == "push_endpoint"
    assert rows(api) == {}


def test_keys_that_are_not_a_p256_point_and_a_16_byte_secret_are_refused(api):
    _, _, keys = browser()
    for bad in ({**keys, "auth": W.b64u(os.urandom(12))}, {**keys, "p256dh": W.b64u(b"\x04" + b"\x01" * 64)}):
        answer = subscribe(api, "ben-test", "https://fcm.googleapis.com/fcm/send/x", bad)
        assert answer.status_code == 422, answer.text
    assert rows(api) == {}


def browser_notifications(api, token, person):
    answer = api.post(f"/api/v2/humans/{person}", json={"notify_browser": True}, headers=headers(token))
    assert answer.status_code == 200 and answer.json()["notify_browser"] is True, answer.text


class Now:
    """An executor that runs each send at once, so a test reads the outcome right after the ring."""

    def submit(self, fn, *args):
        done = Future()
        done.set_result(fn(*args))
        return done


def test_a_ring_reaches_only_the_rung_peoples_live_browsers_and_a_410_retires_one(api, monkeypatch):
    monkeypatch.setattr(W, "_pool", lambda: Now())
    ben_key, ben_auth, ben_keys = browser()
    cara_key, cara_auth, cara_keys = browser()
    ben, cara, ana = (
        "https://fcm.googleapis.com/fcm/send/ben",
        "https://web.push.apple.com/cara",
        "https://updates.push.services.mozilla.com/ana",
    )
    for token, endpoint, keys in (("ben-test", ben, ben_keys), ("cara-test", cara, cara_keys), ("ana-test", ana, None)):
        assert subscribe(api, token, endpoint, keys).status_code == 200
    browser_notifications(api, "ben-test", "ben")
    browser_notifications(api, "cara-test", "cara")
    sent, answers = [], {ben: 410, cara: 201}

    def transmit(endpoint, body, headers):
        sent.append((endpoint, body, headers))
        return answers[endpoint]

    monkeypatch.setattr(W, "transmit", transmit)
    payload = {"title": "Ana", "body": "# Launch", "url": "/#/threads/t1?call=c1", "tag": "call-c1"}
    assert W.ring_hook in call_ring.HOOKS
    # Ana is rung too, but her Browser notifications are off; a bot is never sent to.
    call_ring.ring(api.app, "c1", ["human:ben", "human:cara", "human:ana", "bot:ops"], payload)
    assert sorted(e for e, _, _ in sent) == sorted([ben, cara])
    by = {e: (b, h) for e, b, h in sent}
    body, sent_headers = by[cara]
    assert json.loads(decrypt(body, cara_key, cara_auth)) == {**payload, "ring": True}
    assert json.loads(decrypt(by[ben][0], ben_key, ben_auth))["tag"] == "call-c1"
    assert sent_headers["TTL"] == "60" and sent_headers["Urgency"] == "high" and sent_headers["Topic"] == "call-c1"
    assert sent_headers["Content-Encoding"] == "aes128gcm"
    assert sent_headers["Authorization"].startswith("vapid t=")
    token = sent_headers["Authorization"].split("t=", 1)[1].split(",", 1)[0]
    public = api.get("/api/v2/push/key", headers=headers("ben-test")).json()["key"]
    assert sent_headers["Authorization"].endswith(", k=" + public)
    point = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), W.unb64u(public))
    assert jwt.decode(token, point, algorithms=["ES256"], audience="https://web.push.apple.com")
    after = rows(api)
    assert after[ben]["gone_at"] and after[cara]["last_ok_at"] and not after[cara]["gone_at"]
    assert not after[ana]["last_ok_at"] and not after[ana]["failed_at"]
    # A browser the push service dropped is never sent to again.
    sent.clear()
    call_ring.ring(api.app, "c2", ["human:ben", "human:cara"], {**payload, "tag": "call-c2"})
    assert [e for e, _, _ in sent] == [cara]


def test_failed_sends_retire_a_browser_only_after_a_streak_that_lasts_a_day(api, monkeypatch, caplog):
    monkeypatch.setattr(W, "_pool", lambda: Now())
    endpoint = "https://fcm.googleapis.com/fcm/send/ben"
    assert subscribe(api, "ben-test", endpoint).status_code == 200
    browser_notifications(api, "ben-test", "ben")
    answers = []

    def transmit(endpoint, body, headers):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(W, "transmit", transmit)

    def ring():
        call_ring.ring(api.app, "c1", ["human:ben"], {"title": "Ana", "tag": "call-c1"})
        return rows(api)[endpoint]

    # A network failure and a push service's 500s, all within the hour: counted, not retired.
    answers += [OSError("network down"), 500, 503]
    for _ in range(3):
        row = ring()
    assert row["failures"] == 3 and row["failed_at"] and not row["gone_at"]
    # A success ends the streak.
    answers.append(201)
    row = ring()
    assert row["failures"] == 0 and row["failed_at"] is None and row["last_ok_at"]
    # A streak of three that began over a day ago retires it.
    answers += [500, 500]
    ring()
    assert ring()["failures"] == 2
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE push_subscriptions SET failed_at=? WHERE endpoint=?", ("2026-01-01T00:00:00.000000Z", endpoint)
        )
    answers.append(500)
    assert ring()["gone_at"]
    # An outcome that cannot be written is logged, not lost in a future nobody reads.
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE push_subscriptions SET gone_at=NULL, failures=0, failed_at=NULL WHERE endpoint=?", (endpoint,)
        )

    def broken(store, row_id, status):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(W, "_record", broken)
    answers.append(201)
    with caplog.at_level("ERROR", logger=W.log.name):
        ring()
    assert "push outcome not recorded" in caplog.text


def test_the_private_key_is_sealed_and_never_in_an_answer(api, monkeypatch):
    monkeypatch.setattr(W, "_pool", lambda: Now())
    monkeypatch.setattr(W, "transmit", lambda endpoint, body, headers: 201)
    answers = [api.get("/api/v2/push/key", headers=headers("ben-test"))]
    endpoint = "https://fcm.googleapis.com/fcm/send/ben"
    answers.append(subscribe(api, "ben-test", endpoint))
    browser_notifications(api, "ben-test", "ben")
    call_ring.ring(api.app, "c1", ["human:ben"], {"title": "Ana", "tag": "call-c1"})
    answers.append(unsubscribe(api, "ben-test", endpoint))
    store = api.app.state.store
    with store.read() as c:
        stored = W._stored(c)
        scalar = W.unb64u(api.app.state.vault.cipher.unseal(c, W.CONTEXT, stored["private"]))
        audit = " ".join(str(dict(r)) for r in c.execute("SELECT * FROM events WHERE action LIKE 'push.%'"))
    assert stored["private"] != W.b64u(scalar) and W.b64u(scalar) not in stored["private"]
    assert W.b64u(W._point(_key(W.b64u(scalar)))) == stored["public"]
    for answer in answers:
        assert answer.status_code == 200
        assert W.b64u(scalar) not in answer.text and stored["private"] not in answer.text
    assert W.b64u(scalar) not in audit and endpoint not in audit
    # The pair is made once: the key does not change on a later read.
    assert api.get("/api/v2/push/key", headers=headers("cara-test")).json()["key"] == stored["public"]
