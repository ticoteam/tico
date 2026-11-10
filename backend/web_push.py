# pyright: strict
"""Web Push from this server to people's browsers and the installed app: a call ring today (docs/push.md).

Keys: one VAPID P-256 key pair per install (RFC 8292), made on first need. The private key is sealed with the vault
(context `web-push-vapid`) in registry_metadata and is never logged or answered; the public key is the
applicationServerKey a browser subscribes with (`GET /api/v2/push/key`).

Subscriptions: one `push_subscriptions` row per browser, keyed by the endpoint its push service gave it. Only the
person, signed in as themselves, adds or removes their own: never a bot, BotOps or the Assistant acting for them.

Sending: each message is encrypted for the one browser (RFC 8291, aes128gcm) and signed with a VAPID JWT for the
endpoint's origin, then posted off the request path through backend/egress.py, which sends only to a public https
address. A 404 or 410 answer means the browser dropped the subscription: its row is marked gone and never used again.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import struct
import threading
import time
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING, Annotated, Any, cast
from urllib.parse import urlsplit

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Request
from pydantic import Field

from . import call_ring, egress
from . import models as M
from .store import H as _H
from .store import Problem

if TYPE_CHECKING:
    from fastapi import FastAPI

    from .auth import Identity

H: Any = _H  # hubdb's helpers: a module with no annotations, so typed as Any here

PATH = "/api/v2/push"
KEY_ROW = "web_push_vapid"  # registry_metadata: {"public": <b64url point>, "private": <sealed b64url scalar>}
CONTEXT = "web-push-vapid"
RECORD_SIZE = 4096
PAYLOAD_MAX = 3000  # plaintext bytes: under one 4096-byte record with its header, tag and padding delimiter
ENDPOINT_MAX = 2048
PER_PERSON = 20  # browsers kept per person; a new one past this drops the oldest
JWT_SECONDS = 12 * 3600  # RFC 8292 allows at most 24 hours
RING_TTL = 60  # a ring nobody sees within a minute is stale: the call shows on the thread instead
WORKERS = 4
RETIRE_FAILURES = 3  # failed sends in a row, with no success between them ...
RETIRE_AFTER = 86400  # ... over at least this long, retire a subscription
B64URL = re.compile(r"[A-Za-z0-9_-]+={0,2}")

# sealed text -> its private key, so a send unseals once per install
_PRIVATE: dict[str, ec.EllipticCurvePrivateKey] = {}
_POOL: ThreadPoolExecutor | None = None
_POOL_LOCK = threading.Lock()
log = logging.getLogger(__name__)


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: object) -> bytes:
    """The bytes of base64url `text`, with or without padding; ValueError when it is not base64url."""
    text = str(text or "")
    if not B64URL.fullmatch(text):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text.rstrip("=") + "=" * (-len(text.rstrip("=")) % 4))


def _point(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


# ----------------------------------------------------------------------------- RFC 8291 and RFC 8292


def encrypt(
    plaintext: bytes,
    ua_public: bytes,
    auth_secret: bytes,
    *,
    as_private: ec.EllipticCurvePrivateKey | None = None,
    salt: bytes | None = None,
) -> bytes:
    """`plaintext` encrypted for one browser (RFC 8291 section 3, aes128gcm with one record): the request body.
    `ua_public` is the browser's p256dh point and `auth_secret` its 16-byte auth. `as_private` and `salt` are fresh for
    each message; a test passes RFC 8291's own to check the body byte for byte."""
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    salt = salt or os.urandom(16)
    as_public = _point(as_private)
    shared = as_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    # HKDF-SHA-256, each output a single block: extract is HMAC(salt, ikm), expand is HMAC(prk, info || 0x01).
    ikm = _hmac(_hmac(auth_secret, shared), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    sealed = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)  # 0x02: the last (only) record, no padding
    return salt + struct.pack("!IB", RECORD_SIZE, len(as_public)) + as_public + sealed


def vapid_jwt(private_key: ec.EllipticCurvePrivateKey, endpoint: str, sub: str, now: float | None = None) -> str:
    """The VAPID token for one endpoint (RFC 8292 section 2): ES256, `aud` the endpoint's origin, `exp` within a day."""
    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(now or time.time()) + JWT_SECONDS, "sub": sub}
    return jwt.encode(claims, private_key, algorithm="ES256", headers={"typ": "JWT"})


def topic(tag: object) -> str:
    """RFC 8030's Topic header from a notification tag: at most 32 base64url characters, so a longer tag is hashed."""
    tag = str(tag or "")
    if tag and len(tag) <= 32 and re.fullmatch(r"[A-Za-z0-9_-]+", tag):
        return tag
    return b64u(hashlib.sha256(tag.encode()).digest())[:32] if tag else ""


# ----------------------------------------------------------------------------- the install's key pair


def _vault_cipher(vault: Any) -> Any:
    if vault is None:
        raise Problem("vault_unavailable", "The server's secret store is not ready yet", 503)
    return vault.cipher


def _stored(c: Any) -> dict[str, Any] | None:
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY_ROW,)).fetchone()
    try:
        value = json.loads(row[0]) if row else None
    except ValueError:
        value = None
    found = cast(dict[str, Any], value) if isinstance(value, dict) else None
    return found if found and found.get("public") and found.get("private") else None


def public_key(store: Any, vault: Any) -> str:
    """The install's VAPID public key (base64url, uncompressed point), making the pair the first time it is asked for."""
    with store.read() as c:
        found = _stored(c)
    if found:
        return found["public"]
    with store.transaction() as c:
        found = _stored(c)
        if found:
            return found["public"]
        key = ec.generate_private_key(ec.SECP256R1())
        scalar = key.private_numbers().private_value.to_bytes(32, "big")
        value: dict[str, Any] = {
            "public": b64u(_point(key)),
            "private": _vault_cipher(vault).seal(c, CONTEXT, b64u(scalar)),
        }
        c.execute(
            "INSERT INTO registry_metadata(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (KEY_ROW, json.dumps(value)),
        )
        H.event(c, "system", "push.key.made", "", {"public": value["public"]})
        return value["public"]


def _private_key(store: Any, vault: Any) -> tuple[ec.EllipticCurvePrivateKey, str]:
    public_key(store, vault)
    with store.read() as c:
        found = _stored(c)
        if found is None:
            raise RuntimeError("the push key is missing")
        sealed = found["private"]
        if sealed not in _PRIVATE:
            scalar = unb64u(_vault_cipher(vault).unseal(c, CONTEXT, sealed))
            _PRIVATE[sealed] = ec.derive_private_key(int.from_bytes(scalar, "big"), ec.SECP256R1())
    return _PRIVATE[sealed], found["public"]


# ----------------------------------------------------------------------------- subscriptions


class Keys(M.Contract):
    p256dh: Annotated[str, Field(min_length=80, max_length=100)]
    auth: Annotated[str, Field(min_length=16, max_length=30)]


class SubscriptionBody(M.Contract):
    """What a browser's PushSubscription.toJSON() gives; `expirationTime` is accepted and not kept."""

    endpoint: Annotated[str, Field(min_length=1, max_length=ENDPOINT_MAX)]
    keys: Keys
    expirationTime: float | None = None


class EndpointBody(M.Contract):
    endpoint: Annotated[str, Field(min_length=1, max_length=ENDPOINT_MAX)]


def person(who: "Identity") -> str:
    """The person signed in to this browser as themselves: a browser's push subscription is theirs alone, never a bot's,
    a computer's, BotOps' or the Assistant's acting for them, or a personal token's (an agent outside the browser)."""
    if who.role not in ("owner", "human") or who.via or who.task_actor or who.via_token:
        raise Problem("forbidden", "Only a person, signed in as themselves, turns on push for their browser", 403)
    return "human:" + H.actor_id(who.actor)


def clean_endpoint(endpoint: str) -> str:
    """`endpoint` when Tico may send to it: an https URL that is not, by its spelling, this machine or a private
    network (backend/egress.py). Each send checks the address it resolves to again."""
    try:
        egress.check(endpoint)  # pyright: ignore[reportUnknownMemberType] - egress.py is untyped; check() returns nothing used here
    except egress.Blocked as exc:
        raise Problem("push_endpoint", "That push address is refused: " + exc.reason, 422) from None
    return endpoint


def clean_keys(keys: Keys) -> tuple[str, str]:
    """(p256dh, auth) as the browser sent them, once they decode: a P-256 point and a 16-byte secret."""
    try:
        point, secret = unb64u(keys.p256dh), unb64u(keys.auth)
        if len(point) != 65 or len(secret) != 16:
            raise ValueError("length")
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
    except ValueError:
        raise Problem("push_keys", "The browser's push keys are not valid", 422) from None
    return keys.p256dh, keys.auth


def subscribe(c: Any, who: "Identity", body: SubscriptionBody) -> dict[str, bool]:
    """Keep this browser's subscription for the person. An endpoint another person had moves to this one: the browser
    is theirs now. Past PER_PERSON browsers the person's oldest is dropped."""
    actor = person(who)
    endpoint = clean_endpoint(body.endpoint)
    p256dh, auth = clean_keys(body.keys)
    kept = c.execute(
        "SELECT actor, p256dh, auth, gone_at FROM push_subscriptions WHERE endpoint=?", (endpoint,)
    ).fetchone()
    if kept and tuple(kept) == (actor, p256dh, auth, None):
        return {"subscribed": True}  # the page checks on each load: an unchanged browser writes nothing
    now = H.now()
    c.execute(
        "INSERT INTO push_subscriptions(id, actor, endpoint, p256dh, auth, created_at) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(endpoint) DO UPDATE SET actor=excluded.actor, p256dh=excluded.p256dh, auth=excluded.auth, "
        "created_at=CASE WHEN push_subscriptions.actor=excluded.actor THEN push_subscriptions.created_at "
        "ELSE excluded.created_at END, failed_at=NULL, failures=0, gone_at=NULL",
        (H.new_id(), actor, endpoint, p256dh, auth, now),
    )
    extra = c.execute(
        "SELECT id FROM push_subscriptions WHERE actor=? ORDER BY created_at DESC, id DESC LIMIT -1 OFFSET ?",
        (actor, PER_PERSON),
    ).fetchall()
    c.executemany("DELETE FROM push_subscriptions WHERE id=?", [(row[0],) for row in extra])
    H.event(c, who.actor, "push.subscribe", urlsplit(endpoint).hostname or "", {})
    return {"subscribed": True}


def unsubscribe(c: Any, who: "Identity", endpoint: str) -> dict[str, bool]:
    """Drop this browser's subscription, when it is the person's own. Another's, or none, is the same quiet answer."""
    actor = person(who)
    gone = c.execute("DELETE FROM push_subscriptions WHERE actor=? AND endpoint=?", (actor, endpoint)).rowcount
    if gone:
        H.event(c, who.actor, "push.unsubscribe", urlsplit(endpoint).hostname or "", {})
    return {"subscribed": False}


# ----------------------------------------------------------------------------- sending


def transmit(endpoint: str, body: bytes, headers: dict[str, str]) -> int:
    """POST one encrypted message to a push service: its HTTP status. The one function that reaches the network, so
    tests replace it. egress.post refuses an address that resolves to this machine or a private network."""
    return egress.post(endpoint, body, headers).status  # pyright: ignore[reportUnknownMemberType] - egress.py is untyped; Answer.status is an int


def _pool() -> ThreadPoolExecutor:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="tico-push")  # pyright: ignore[reportConstantRedefinition] - the module's pool global keeps its name; renaming it is not an annotation
        return _POOL


def _subject(settings: object) -> str:
    """VAPID's `sub`, the contact a push service may use about this sender: the server's https address; on an install
    without one, the owner's address (TICO_OWNER_EMAIL or the roster's owner); else a placeholder mailto."""
    url = str(getattr(settings, "public_url", "") or "")
    if url.startswith("https://"):
        return url
    owner = str(getattr(settings, "owner_email", "") or "").strip()
    return "mailto:" + owner if re.fullmatch(r"[^@\s<>]+@[^@\s<>]+", owner) else "mailto:push@localhost"


def _message(payload: dict[str, Any]) -> bytes:
    """The JSON the service worker shows (title, body, url, tag, ring), cut to fit one record."""
    fields = {k: str(payload.get(k) or "") for k in ("title", "body", "url", "tag")}
    ring = bool(payload.get("ring"))
    data = json.dumps({**fields, "ring": ring}, ensure_ascii=False).encode()
    while len(data) > PAYLOAD_MAX and (fields["body"] or fields["title"]):
        key = "body" if fields["body"] else "title"
        fields[key] = fields[key][: len(fields[key]) // 2]
        data = json.dumps({**fields, "ring": ring}, ensure_ascii=False).encode()
    return data


def send(
    store: Any,
    vault: Any,
    actors: Iterable[str] | None,
    payload: dict[str, Any],
    *,
    ttl: int = RING_TTL,
    urgency: str = "high",
) -> list[Future[int]]:
    """Queue `payload` to every live subscription of `actors`; the futures of the sends. Only people who have Browser
    notifications on and have not left are sent to, and nothing is sent in a rehearsal (backend/devcopy.py)."""
    from . import people as P
    from . import views
    from .auth import standing  # pyright: ignore[reportUnknownVariableType] - auth.py is untyped

    people = sorted({str(a) for a in actors or () if H.is_human(str(a))})
    if not people or getattr(store.settings, "rehearsal", False):
        return []
    with store.read() as c:
        roster = views.roster(c)  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType] - views.py is untyped
        people = [
            a
            for a in people
            if (P.person(H.actor_id(a), roster) or {}).get("notify_browser") is True and not standing(c, H.actor_id(a))  # pyright: ignore[reportUnknownMemberType] - people.py and auth.py are untyped
        ]
        if not people:
            return []
        marks = ",".join("?" * len(people))
        rows: list[dict[str, Any]] = [
            dict(r)
            for r in c.execute(
                f"SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE gone_at IS NULL AND actor IN ({marks})",
                people,
            )
        ]
    if not rows:
        return []
    message, label = _message(payload), topic(payload.get("tag"))
    return [_pool().submit(_send_one, store, vault, row, message, ttl, urgency, label) for row in rows]


def _send_one(store: Any, vault: Any, row: dict[str, Any], message: bytes, ttl: int, urgency: str, label: str) -> int:
    """One encrypted, signed send; the row records the outcome. Returns the status, or 0 when it was not sent."""
    try:
        private, public = _private_key(store, vault)
        headers: dict[str, str] = {
            "Authorization": f"vapid t={vapid_jwt(private, row['endpoint'], _subject(store.settings))}, k={public}",
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(int(ttl)),
            "Urgency": urgency,
        }
        if label:
            headers["Topic"] = label
        body = encrypt(message, unb64u(row["p256dh"]), unb64u(row["auth"]))
        status = transmit(row["endpoint"], body, headers)
    # Whatever stops a send (a refused address, the network, the vault) is the row's failure; the log names its kind only,
    # never the endpoint.
    except Exception as exc:  # noqa: BLE001
        log.warning("push send failed: %s", type(exc).__name__)
        status = 0
    try:
        _record(store, row["id"], status)
    except Exception:  # a send pool's future is never read: the failure is logged here or nowhere
        log.exception("push outcome not recorded")
    return status


def _record(store: Any, row_id: str, status: int) -> None:
    """A send's outcome on its row. 2xx clears the failure streak; 404 or 410 retires the row; anything else adds to
    the streak, and a streak of RETIRE_FAILURES that has lasted RETIRE_AFTER retires it too, so a browser that stopped
    answering is not tried for ever while a short outage on this server's side retires none."""
    now = H.now()
    with store.transaction() as c:
        if 200 <= status < 300:
            c.execute(
                "UPDATE push_subscriptions SET last_ok_at=?, failed_at=NULL, failures=0 WHERE id=?", (now, row_id)
            )
        elif status in (404, 410):
            c.execute("UPDATE push_subscriptions SET gone_at=? WHERE id=?", (now, row_id))
        else:
            c.execute(
                "UPDATE push_subscriptions SET failed_at=COALESCE(failed_at, ?), failures=failures+1 WHERE id=?",
                (now, row_id),
            )
            c.execute(
                "UPDATE push_subscriptions SET gone_at=? WHERE id=? AND gone_at IS NULL AND failures>=? AND failed_at<=?",
                (now, row_id, RETIRE_FAILURES, H.shift(now, seconds=-RETIRE_AFTER)),
            )


def ring_hook(app: Any, call_id: str, actors: list[str], payload: dict[str, str]) -> None:
    """call_ring's hook: the ring to each rung person's browsers, shown until answered where the browser allows."""
    send(app.state.store, app.state.vault, actors, {**payload, "ring": True}, ttl=RING_TTL, urgency="high")


# ----------------------------------------------------------------------------- the routes


def install(app: "FastAPI", store: Any) -> None:
    """The push key and a person's own subscriptions. Installed after install_credentials, so the vault is there."""
    if ring_hook not in call_ring.HOOKS:
        call_ring.HOOKS.append(ring_hook)

    @app.get(PATH + "/key")
    def push_key(request: Request) -> dict[str, str]:
        """The install's VAPID public key, the applicationServerKey a browser subscribes with."""
        person(request.state.identity)
        return {"key": public_key(store, app.state.vault)}

    @app.post(PATH + "/subscriptions")
    def push_subscribe(request: Request, body: SubscriptionBody) -> dict[str, bool]:
        """This browser's push subscription, for the person signed in."""
        with store.transaction() as c:
            return subscribe(c, request.state.identity, body)

    @app.delete(PATH + "/subscriptions")
    def push_unsubscribe(request: Request, body: EndpointBody) -> dict[str, bool]:
        """Stop pushing to this browser."""
        with store.transaction() as c:
            return unsubscribe(c, request.state.identity, body.endpoint)
