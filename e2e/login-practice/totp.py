"""RFC 6238 codes for the practice site, stdlib only. The journey uses it too, as an independent check on the
computer's own authenticator codes (backend/totp.py), so it shares no code with them."""
import base64
import hashlib
import hmac
import secrets
import struct
import time


def new_secret():
    """A base32 key of 160 bits, as an authenticator app expects."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret):
    text = secret.replace(" ", "").upper()
    return base64.b32decode(text + "=" * (-len(text) % 8))


def code(secret, at=None, digits=6, period=30):
    counter = int((time.time() if at is None else at) // period)
    digest = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(value).zfill(digits)


def verify(secret, given, window=1):
    """True when `given` is the code for this period or within `window` periods either side."""
    now = time.time()
    given = (given or "").strip()
    return any(hmac.compare_digest(code(secret, now + step * 30), given) for step in range(-window, window + 1))


def otpauth_uri(secret, account, issuer="Practice Login"):
    return f"otpauth://totp/{issuer}:{account}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"
