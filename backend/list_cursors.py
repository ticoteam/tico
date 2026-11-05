"""Opaque continuation positions for bounded scans that returned no readable rows."""
import base64
import binascii
import hashlib
import hmac
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .store import Problem
from .visuals import secret

PREFIX = "opaque:"


def cipher(c):
    # Derive a separate encryption key from the install's existing persistent signing secret.
    return AESGCM(hmac.digest(secret(c.settings), b"tico-list-cursor:v1", hashlib.sha256))


def seal(c, namespace, position):
    nonce = os.urandom(12)
    encrypted = cipher(c).encrypt(nonce, json.dumps(position).encode(), namespace.encode())
    return PREFIX + base64.urlsafe_b64encode(nonce + encrypted).decode()


def unseal(c, namespace, cursor):
    try:
        raw = base64.b64decode(cursor[len(PREFIX):], altchars=b"-_", validate=True)
        position = json.loads(cipher(c).decrypt(raw[:12], raw[12:], namespace.encode()))
        if not isinstance(position, list) or len(position) != 2 or not all(isinstance(v, str) and v for v in position):
            raise ValueError
        return tuple(position)
    except (ValueError, binascii.Error, UnicodeError, InvalidTag):
        raise Problem("validation", "That cursor is not valid", 422) from None
