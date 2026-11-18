"""Keep the secrets a turn was granted out of the git text the Python helpers write down.

A bot's credentials reach it as environment variables for one turn. The Rust runner scrubs what a turn writes down:
its events, its log, the repository it pushes and the files it publishes (runner-rs/src/redact.rs). The helpers here
scrub the git output and errors the Python side shows: `scrub_credentials`, `env_secrets` and `git_failure`.

`Redactor(values)` holds the values known for one turn and replaces each, and the common ways it turns up (URL-encoded,
base64, inside `Basic <base64>`), with `••••`. It is one precompiled pattern and a single pass, and a turn with no
secrets gets `None` from `for_turn`, so nothing runs. It fails closed: an internal error yields `[redacted: error]`
for that text, never the text.
"""

import base64
import json
import re
import threading
from urllib.parse import quote, quote_plus

MASK = "••••"
ERROR = "[redacted: error]"
MIN_LENGTH = 8
TEXT_LIMIT = 2_000_000  # redact.rs TEXT_LIMIT: a changed file past it is left out
# Words that are a setting, not a secret: replacing them would only damage the text.
COMMON = {
    "localhost",
    "127.0.0.1",
    "production",
    "development",
    "password",
    "username",
    "enabled",
    "disabled",
    "undefined",
    "identity",
    "anonymous",
    "internal",
    "external",
    "default",
    "standard",
    "true",
    "false",
}


def _variants(value):
    """The forms one secret is written in besides itself."""
    raw = value.encode()
    forms = {value, quote(value, safe=""), quote_plus(value)}
    for encode in (base64.b64encode, base64.urlsafe_b64encode):
        text = encode(raw).decode()
        forms.update({text, text.rstrip("=")})
    forms |= json_forms(value)
    forms |= utf16_forms(value)
    return forms


_JSON_SHORT = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def json_forms(value):
    """The value as a JSON string's inside: `/` as is or as `\\/`, non-ASCII as is or as `\\uXXXX` (lower or upper hex,
    as encoders write it), and every character as `\\uXXXX`. A value only partly `\\u`-escaped is not one of them
    (spec Out: a value split inside itself). Kept step for step with redact.rs json_forms."""
    forms = set()
    for slash in (False, True):
        for ascii_only in (False, True):
            for upper in (False, True):
                out = []
                for ch in value:
                    if ch in _JSON_SHORT:
                        out.append(_JSON_SHORT[ch])
                    elif ch == "/" and slash:
                        out.append("\\/")
                    elif ord(ch) < 0x20 or (ascii_only and ord(ch) > 0x7F):
                        out.append(_unicode_escape(ch, upper))
                    else:
                        out.append(ch)
                forms.add("".join(out))
    for upper in (False, True):
        forms.add("".join(_unicode_escape(ch, upper) for ch in value))
    return forms


def _unicode_escape(ch, upper):
    """`\\uXXXX` for one character: two for one past the Basic Multilingual Plane, as JSON writes it (UTF-16 units)."""
    data = ch.encode("utf-16-be", "surrogatepass")
    units = [int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2)]
    return "".join(("\\u%04X" if upper else "\\u%04x") % u for u in units)


def utf16_forms(value):
    """The value's UTF-16 LE and BE bytes, each byte read as one latin-1 character, the way `holds` reads a file: a
    UTF-16 file, or UTF-16 text in a log line or an event, holding the value matches it. Kept step for step with
    redact.rs utf16_forms."""
    return {
        value.encode("utf-16-le", "surrogatepass").decode("latin-1"),
        value.encode("utf-16-be", "surrogatepass").decode("latin-1"),
    }


def _worth(value):
    value = str(value or "")
    if len(value) < MIN_LENGTH or value.lower() in COMMON or len(set(value)) < 3:
        return False
    return True


def _values(secrets):
    """The secret values to hunt for: each one, plus the password half of `user:password`."""
    found = set()
    for value in secrets:
        value = str(value or "").strip()
        if not _worth(value):
            continue
        found.add(value)
        head, sep, tail = value.partition(":")
        if sep and "\n" not in value and _worth(tail):
            found.add(tail)
    return found


class Redactor:
    def __init__(self, secrets):
        self.update_lock = threading.Lock()
        self.values = sorted(_values(secrets), key=len, reverse=True)
        forms = set()
        for value in self.values:
            forms |= _variants(value)
        forms.discard("")
        self.pattern = (
            re.compile("|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))) if forms else None
        )
        # The UTF-16 forms alone (redact.rs Built.wide): a file holding one is left out wherever it is.
        wide = {f for value in self.values for f in utf16_forms(value)} - {""}
        self.wide = re.compile("|".join(re.escape(f) for f in sorted(wide, key=len, reverse=True))) if wide else None

    def add(self, secrets):
        with self.update_lock:
            added = _values(secrets) - set(self.values)
            if not added:
                return
            updated = Redactor([*self.values, *added])
            self.values, self.pattern, self.wide = updated.values, updated.pattern, updated.wide

    def scrub_text(self, text):
        if not isinstance(text, str) or self.pattern is None:
            return text
        try:
            return self.pattern.sub(MASK, text)
        except Exception:
            return ERROR

    def holds(self, data):
        """Whether these bytes carry a secret in any of its forms, read as latin-1 and as UTF-8 (redact.rs holds)."""
        if self.pattern is None:
            return False
        return bool(self.pattern.search(data.decode("latin-1")) or self.pattern.search(data.decode("utf-8", "replace")))

    def scrub_bytes(self, data):
        """Bytes scrubbed as they are: bytes that are not UTF-8 are kept (surrogateescape), as redact.rs scrub_bytes."""
        if self.pattern is None:
            return data
        return self.pattern.sub(MASK, data.decode("utf-8", "surrogateescape")).encode("utf-8", "surrogateescape")

    def file_outcome(self, data):
        """What one changed file's bytes become (redact.rs file_outcome): ("clean", None) when no form of a secret is in
        them; ("left_out", None) when a UTF-16 form of one is in them (wherever it is: a UTF-16 file whose first NUL
        comes after 8000 bytes is not rewritten), git would treat them as binary (a NUL in the first 8000 bytes, which
        every UTF-16 text with ASCII in it has), they start with a UTF-16 byte order mark, are past TEXT_LIMIT, or still
        hold a form once masked; else ("masked", the masked bytes)."""
        if not self.holds(data):
            return "clean", None
        if (
            (self.wide is not None and self.wide.search(data.decode("latin-1")))
            or b"\0" in data[:8000]
            or data.startswith((b"\xff\xfe", b"\xfe\xff"))
            or len(data) > TEXT_LIMIT
        ):
            return "left_out", None
        masked = self.scrub_bytes(data)
        if self.holds(masked):
            return "left_out", None
        return "masked", masked


def for_turn(env, vault_values=(), names=("TOKEN", "SECRET", "PASSWORD", "API_KEY")):
    """The turn's redactor, or None when it holds nothing worth hiding: the granted values, and any environment
    variable whose name says it is one."""
    secrets = list(vault_values)
    secrets.extend(v for k, v in (env or {}).items() if any(s in k.upper() for s in names))
    try:
        groups = json.loads((env or {}).get('TICO_GITHUB_TOKENS', '[]'))
        secrets.extend(
            group['token'] for group in groups if isinstance(group, dict) and isinstance(group.get('token'), str)
        )
    except (ValueError, TypeError):
        pass
    redactor = Redactor(secrets)
    return redactor if redactor.pattern is not None else None


# Credentials that can turn up in git's own output whoever's they are: a token in a URL's user part, GitHub's
# token formats, an Authorization header (GIT_TRACE_CURL, http.extraHeader) and a credential helper's answer.
_CREDENTIAL_SHAPES = [
    (re.compile(r"([a-z][a-z0-9+.-]*://)[^/\s@'\"]+@", re.I), r"\1" + MASK + "@"),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9._-]{16,}|github_pat_[A-Za-z0-9_]{16,})"), MASK),
    (re.compile(r"\b((?:proxy-)?authorization\s*:\s*)(?:(?:basic|bearer|token)\s+)?\S+", re.I), r"\1" + MASK),
    (re.compile(r"\b(basic|bearer)\s+[A-Za-z0-9+/._~-]{8,}=*", re.I), r"\1 " + MASK),
    (re.compile(r"\b(password|oauth_refresh_token|access_token|token)\s*=\s*\S+", re.I), r"\1=" + MASK),
]
# Git's progress, advice and recovery hints: never the reason something failed.
_GIT_NOISE = re.compile(
    r"^(?:hint:|cloning into|remote: (?:enumerating|counting|compressing|total)|receiving objects|"
    r"resolving deltas|updating files|filtering content|checking out files|"
    r"you can inspect what was checked out|and retry with|warning: clone succeeded|"
    r"fatal: credential helper .* told us to quit)",
    re.I,
)
_GIT_REASON = re.compile(
    r"^(?:fatal:|error:|remote:|tico\b|git-lfs|batch response|smudge|.*\b(?:error|denied|"
    r"not found|timed out|too long|failed)\b)",
    re.I,
)


def scrub_credentials(text, secrets=()):
    """`text` with the given secret values and anything shaped like a credential masked. Fails closed."""
    try:
        text = str(text or "")
        known = Redactor(secrets)
        text = known.scrub_text(text)
        for pattern, replacement in _CREDENTIAL_SHAPES:
            text = pattern.sub(replacement, text)
        return text
    except Exception:
        return ERROR


def env_secrets(env, names=("TOKEN", "SECRET", "PASSWORD", "API_KEY")):
    """The values in a git environment that are secrets: its tokens, by name and in TICO_GITHUB_TOKENS."""
    redactor = for_turn(env, names=names)
    return list(redactor.values) if redactor else []


def git_failure(stderr, secrets=(), limit=300):
    """Why a git command failed, in git's own words and safe to show: the last few meaningful lines of its
    stderr ("remote: Repository not found.", "fatal: unable to checkout working tree", an LFS smudge error),
    without progress or advice, credentials masked, at most `limit` characters. "" when git said nothing."""
    lines = []
    # Scrubbed whole, before anything is cut: a token cut in half no longer looks like one.
    for raw in scrub_credentials(str(stderr or "").replace("\r", "\n"), secrets).splitlines():
        line = " ".join(raw.split())
        if line and not _GIT_NOISE.match(line) and line not in lines:
            lines.append(line)
    reasons = [line for line in lines if _GIT_REASON.match(line)] or lines
    text = " / ".join(line[:200] for line in reasons[-3:])
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
