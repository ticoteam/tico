"""Independent RFC 6238 verifier for the local practice pages (stdlib only)."""

import base64
import hashlib
import hmac
import struct
import time


def code(secret, at=None):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    counter = int((time.time() if at is None else at) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 1_000_000).zfill(6)


def verify(secret, given):
    return any(hmac.compare_digest(code(secret, time.time() + n * 30), given) for n in (-1, 0, 1))
