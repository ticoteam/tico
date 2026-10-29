"""Authenticator codes for website logins (RFC 6238 TOTP over RFC 4226 HOTP), standard library only.

A login's 2FA setup key arrives as the base32 key a site shows under "can't scan the code?" or as the `otpauth://` URI
inside its QR code. `parse_totp` turns either into the stored form, `code` makes the current code from it. The secret
never appears in an error message: a refusal says what is wrong with the shape, not what was typed.
"""
import base64
import binascii
import hashlib
import hmac
import struct
import time
from urllib.parse import parse_qs, unquote, urlsplit

from .store import Problem

ALGORITHMS = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}
MAX_KEY = 512


def _refuse(why):
    raise Problem("totp", "That 2FA setup key " + why + ". Paste the key the site shows, or its otpauth:// link", 422)


def _base32(text):
    """The key in canonical form: upper case, no spaces, dashes or padding; refused unless it decodes to bytes."""
    key = "".join(str(text or "").split()).replace("-", "").rstrip("=").upper()
    if not key or len(key) > MAX_KEY:
        _refuse("is empty or too long")
    try:
        raw = base64.b32decode(key + "=" * (-len(key) % 8))
    except (binascii.Error, ValueError):
        _refuse("is not a base32 key")
    if len(raw) < 10:
        # Under 80 bits is a typo or a recovery code, not a site's key (RFC 4226 asks for at least 128).
        _refuse("is too short")
    return key


def parse_totp(text):
    """{"secret", "digits", "period", "algorithm"} from a base32 key or an otpauth://totp/ URI; None for empty input."""
    text = str(text or "").strip()
    if not text:
        return None
    if not text.lower().startswith("otpauth:"):
        return {"secret": _base32(text), "digits": 6, "period": 30, "algorithm": "SHA1"}
    parts = urlsplit(text)
    if parts.scheme.lower() != "otpauth" or parts.netloc.lower() != "totp":
        _refuse("is not a time-based (totp) link")
    query = {k.lower(): v[-1] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
    secret = _base32(unquote(query.get("secret", "")))
    algorithm = (query.get("algorithm") or "SHA1").upper().replace("-", "")
    if algorithm not in ALGORITHMS:
        _refuse("uses an algorithm other than SHA1, SHA256 or SHA512")
    try:
        digits = int(query.get("digits") or 6)
        period = int(query.get("period") or 30)
    except ValueError:
        _refuse("has digits or period that are not numbers")
    if digits not in (6, 7, 8):
        _refuse("asks for codes of other than 6, 7 or 8 digits")
    if not 1 <= period <= 300:
        _refuse("has a period outside 1 to 300 seconds")
    return {"secret": secret, "digits": digits, "period": period, "algorithm": algorithm}


def _key(totp):
    secret = totp["secret"]
    return base64.b32decode(secret + "=" * (-len(secret) % 8))


def code(totp, at=None):
    """The code for time `at` (now when None): RFC 6238 with the stored digits, period and algorithm."""
    period = int(totp.get("period") or 30)
    counter = int((time.time() if at is None else at) // period)
    digest = hmac.new(_key(totp), struct.pack(">Q", counter), ALGORITHMS[(totp.get("algorithm") or "SHA1").upper()]).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    digits = int(totp.get("digits") or 6)
    return str(value % 10 ** digits).zfill(digits)


def expires_in(totp, at=None):
    """Seconds the current code has left."""
    period = int(totp.get("period") or 30)
    now = time.time() if at is None else at
    return max(1, int(period - (now % period)))
