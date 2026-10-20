"""Key-shaped strings: API keys, tokens, private keys and `password: ...` values, removed from text before it is kept.

The setup chat never takes credentials (tools are connected on the install), so anything that looks like one is cut
out of every text field, whatever its source, and the caller is told it happened.
"""
import math
import re

MARK = "[removed]"

# Each pattern matches the whole secret; `LABELLED` keeps its label and cuts only the value.
PATTERNS = [re.compile(p) for p in (
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
    r"\bsk-[A-Za-z0-9_-]{16,}",                              # OpenAI, Anthropic (sk-ant-...)
    r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}",           # GitHub tokens
    r"\bgithub_pat_[A-Za-z0-9_]{20,}",
    r"\bglpat-[A-Za-z0-9_-]{20,}",                           # GitLab
    r"\bxox[abposr]-[A-Za-z0-9-]{10,}",                      # Slack
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",                        # AWS access key ids
    r"\bAIza[0-9A-Za-z_-]{30,}",                             # Google API keys
    r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}",        # Stripe
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",   # JWTs
    r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*",
)]
# The label may be quoted, as a JSON or YAML key is: {"password": "x"}, or {\"password\": \"x\"} inside a JSON string.
# A quoted value runs to its closing quote, past escaped (or, in YAML, doubled) ones, or to the end of the line when it
# has none; separator and value stay on the label's line.
# Inside a JSON string the value's own escapes are escaped again: its \" is \\\", so a backslash pair there takes the
# token after it, and only a bare \" closes the value.
# A label may end a longer identifier, as in an env file (DB_PASSWORD=x, STRIPE_API_KEY: x); such a label needs an
# explicit `=` or `:`.
LABELLED = re.compile(r"(?i)((?:(\b[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*_)|\b)(?:password|passwd|passcode|pwd|secret|token|"
                      r"api[ _-]?key|access[ _-]?key|client[ _-]?secret|private[ _-]?key)\b(?:\\?[\"'])?[ \t]*"
                      r"([:=]|\bis\b)[ \t]*)"
                      r"(\\\"(?:\\\\(?:\\.|[^\\\n])|\\[^\"\\\n]|[^\\\n])*(?:\\\"|(?=\n)|\Z)|"
                      r"\"(?:[^\"\\\n]|\\.)*(?:\"|(?=\n)|\Z)|'(?:[^'\\\n]|\\.|'')*(?:'|(?=\n)|\Z)|"
                      r"(?!\[removed\])[^\s,;}\]]+)")
PASSWORD = re.compile(r"(?i)\s*[\"']?(?:password|passwd|passcode|pwd)\b")
# "token: 5 per user" is a count, not a token: one to three digits after a bare `token` label are kept (API_TOKEN=5 is
# not a count).
COUNT = re.compile(r"(?i)token\W*$")
QUOTES = ('\\"', '"', "'")
# A long run of token characters with letters and digits mixed, and close to random (or letters and digits only).
TOKEN = re.compile(r"[A-Za-z0-9_+/=-]{32,}")


def _random(word):
    if not (re.search(r"[A-Za-z]", word) and re.search(r"[0-9]", word)):
        return False
    if word.isalnum():
        return True                     # 32 or more letters and digits, however regular: no word or number is that
    counts = {}
    for char in word:
        counts[char] = counts.get(char, 0) + 1
    entropy = -sum(n / len(word) * math.log2(n / len(word)) for n in counts.values())
    return entropy >= 3.5


def _labelled(match):
    label, prefix, separator, value = match.groups()
    # "the token is optional": with "is", only a password-like label counts, and never one inside an identifier.
    if separator.lower() == "is" and (prefix or not PASSWORD.match(label)):
        return match.group(0)
    quote = next((q for q in QUOTES if len(value) >= 2 * len(q) and value.startswith(q) and value.endswith(q)), "")
    inner = value[len(quote):len(value) - len(quote)]
    if not inner or inner == MARK or (not quote and not prefix and inner.isdigit() and len(inner) <= 3
                                           and COUNT.search(label)):
        return match.group(0)
    return label + quote + MARK + quote


def scrub(text):
    """(text without key-shaped strings, whether any was removed)."""
    if not isinstance(text, str) or not text:
        return text, False
    out = text
    for pattern in PATTERNS:
        out = pattern.sub(MARK, out)
    out = LABELLED.sub(_labelled, out)
    out = TOKEN.sub(lambda m: MARK if _random(m.group(0)) else m.group(0), out)
    return out, out != text


def scrub_all(value):
    """Every string in a JSON value scrubbed: (new value, whether any key was removed)."""
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, list):
        pairs = [scrub_all(item) for item in value]
        return [item for item, _ in pairs], any(hit for _, hit in pairs)
    if isinstance(value, dict):
        # Keys too: a refusal can name a field it doesn't know.
        pairs = [(scrub(key) if isinstance(key, str) else (key, False), scrub_all(item)) for key, item in value.items()]
        return ({key: item for (key, _), (item, _) in pairs},
                any(key_hit or hit for (_, key_hit), (_, hit) in pairs))
    return value, False
