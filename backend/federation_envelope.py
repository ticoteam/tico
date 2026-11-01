"""Signed envelopes between Tico installs: the signed text, the keys, the namespace rule and the payload encoding.

The signed text is the one HQ checks (`federation/envelope.py` in ticoteam/tico-services); change both together. An
envelope is the bytes a sender posts to HQ, with five headers: the recipient, the envelope id, the sent-at time (Unix
seconds), the sender's key id and an Ed25519 signature over the signed text. HQ checks the peering and the signature
for the routing; the recipient checks everything else itself, with the key it pinned at approval.

Inside a shared thread an item also carries the author's own signature (backend/federation_sync.py), so a replica can
check a message the home relayed from a third install. Its signed text names the home as recipient.

Namespace: an actor is `human:<id>@<install>` or `bot:<slug>@<install>`. Only the install named after the `@` may sign
for that actor (`human:x@peerA` only from peerA). A local actor, `human:x`, is qualified with this install when it leaves.
"""
import base64
import hashlib
import json
import re
import secrets

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

VERSION = "tico-federation-v1"
PUBLIC_KEY_BYTES = 32                   # a raw Ed25519 public key
SKEW = 300                              # seconds an envelope's sent-at may differ from this install's clock
MAX_BODY = 180 * 1024                   # a payload; an item travels as base64 in a wrapper that must fit HQ's 256 KB envelope
INSTALL = re.compile(r"gi_[0-9a-f]{24}")
KEY_ID = re.compile(r"fk_[0-9a-f]{24}")
ENVELOPE_ID = re.compile(r"[A-Za-z0-9_-]{16,64}")
HEADERS = ("x-tico-to", "x-tico-envelope", "x-tico-sent-at", "x-tico-key-id", "x-tico-signature")


def key_id_of(raw_public_key):
    """The id a public key is known by: `fk_` and the first 24 hex digits of its SHA-256 (as HQ names it)."""
    return "fk_" + hashlib.sha256(raw_public_key).hexdigest()[:24]


def generate():
    return Ed25519PrivateKey.generate()


def raw_public(private):
    return private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def private_bytes(private):
    return private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())


def new_envelope_id():
    return "env-" + secrets.token_urlsafe(12)


def signed_text(sender, recipient, envelope_id, sent_at, body):
    digest = hashlib.sha256(body).hexdigest()
    return "\n".join([VERSION, sender, recipient, envelope_id, str(int(sent_at)), digest]).encode("utf-8")


def sign(private, sender, recipient, envelope_id, sent_at, body):
    """The base64 Ed25519 signature a sender puts in x-tico-signature."""
    return base64.b64encode(private.sign(signed_text(sender, recipient, envelope_id, sent_at, body))).decode("ascii")


def verify(raw_public_key, signature_b64, text):
    """True when `signature_b64` is an Ed25519 signature of `text` by the 32-byte `raw_public_key`."""
    try:
        signature = base64.b64decode(signature_b64, validate=True)
        Ed25519PublicKey.from_public_bytes(raw_public_key).verify(signature, text)
        return True
    except (ValueError, TypeError, InvalidSignature):
        return False


def private_from(raw):
    return Ed25519PrivateKey.from_private_bytes(raw)


def install_of(actor):
    """The install an actor belongs to (the part after its last `@`), or '' for a local actor."""
    if "@" not in str(actor or ""):
        return ""
    tail = str(actor).rsplit("@", 1)[1]
    return tail if INSTALL.fullmatch(tail) else ""


def qualify(actor, install):
    """A local actor as the wire names it: `human:x` becomes `human:x@<install>`. Already-qualified actors are kept."""
    return actor if install_of(actor) else f"{actor}@{install}"


def in_namespace(actor, install):
    return bool(install) and install_of(actor) == install


def payload_bytes(payload):
    """The bytes a payload is signed and sent as: sorted keys, no spaces, UTF-8."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def parse_payload(raw):
    """The payload dict in `raw`, or None when it is not a JSON object within the cap."""
    if len(raw) > MAX_BODY:
        return None
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None
