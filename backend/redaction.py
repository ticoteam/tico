"""Text redaction for anything that leaves an install for Tico support: emails, keys and tokens, addresses, URL query
strings, the company's domain and other hostnames become placeholders, and named bots and people become labels.

Standard library only, so the setup wizard (setup/support.py), which runs on a host with no backend installed, applies
the same rules as the diagnostics bundle (diagnostics.py). The installer bundle ships this file beside setup/.
"""
import ipaddress
import re
from urllib.parse import urlsplit

# Hostnames a product runs on; every other hostname in a string becomes [host].
KNOWN_HOSTS = ("tico.team", "github.com", "githubusercontent.com", "ghcr.io", "docker.io", "openrouter.ai", "openai.com",
               "chatgpt.com", "anthropic.com", "claude.ai", "claude.com", "googleapis.com", "x.ai", "cursor.com",
               "google.com", "npmjs.org", "pypi.org")
# A dotted word is a hostname only when it ends in a real top-level domain: `service.py` and `hub.db` are files.
TLDS = ("com", "org", "net", "io", "dev", "ai", "team", "cloud", "xyz", "us", "uk", "de", "fr", "nl", "eu", "ca", "au", "jp",
        "ch", "se", "info", "biz", "tech", "online", "site", "example", "local", "internal", "lan", "test", "localdomain",
        "corp", "home")


# ---------------------------------------------------------------------- the redactor
SECRETS = [
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "[key]"),
    (re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{8,}"), "[token]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{10,}"), "[token]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{6,}"), "[token]"),
    (re.compile(r"\bxapp-[A-Za-z0-9-]{6,}"), "[token]"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[key]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"), "[jwt]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}"), r"\1 [token]"),
]
# Consume quoted values as a unit, including escaped quotes and truncated log lines.
# Unquoted values may contain spaces; discard through the next field separator.
SECRET_FIELD = re.compile(
    r'''(?i)(["']?\b[A-Za-z0-9_.-]*(?:api[_-]?key|token|secret|password|passwd)[A-Za-z0-9_.-]*["']?\s*[=:]\s*)'''
    r'''(?:"(?:\\.|[^"\\])*(?:"|$)|'(?:\\.|[^'\\])*(?:'|$)|[^,;\r\n}\]]+)''')
LONG = re.compile(r"(?<![A-Za-z0-9+/_=-])[A-Za-z0-9+/_=-]{32,}")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u2028\u2029\u202a-\u202e\u2066-\u2069]")   # HQ refuses these in a ticket
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
EMAIL = re.compile(r"[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\.[A-Za-z]{2,24}")
QUERY = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s?#\"'<>]*)\?[^\s\"'<>#]*(#[^\s\"'<>]*)?", re.I)
AUTHORITY = re.compile(r'''(\b[a-z][a-z0-9+.-]*://)([^/\s?#"'<>]+)''', re.I)
IPV4 = re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.]*\d)")
IPV6 = re.compile(r"(?<![A-Za-z0-9:])[0-9A-Fa-f:]*:[0-9A-Fa-f:.]*(?![A-Za-z0-9:])")
HOST = re.compile(r"(?<![A-Za-z0-9@._-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+(?:%s)\b(?![A-Za-z0-9_-])"
                  % "|".join(TLDS), re.I)


def _looks_secret(token):
    """A long unbroken run is a secret when it mixes letters and digits; a UUID, a path or a plain word is not."""
    return bool(re.search(r"\d", token) and re.search(r"[A-Za-z]", token)) and not UUID.fullmatch(token)


def _ipv6(match):
    text = match.group(0)
    if text.count(":") < 2:
        return text
    try:
        ipaddress.IPv6Address(text.strip(":") if not text.startswith("::") and text.endswith(":") and not text.endswith("::") else text)
    except ValueError:
        return text
    return "[ip]"


WORD_MIN = 4        # a name or slug shorter than this is not a word: `coo` or `pm` would relabel ordinary text
ACTOR = re.compile(r"(?<![A-Za-z0-9])(bot|human):([A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?)")


class Redactor:
    """Every string in a bundle goes through `text()`. `people` and `bots` are the names to turn into labels; a label is
    made once per bundle, in sorted order, so the same person is the same `person-N` wherever they appear.

    A name or slug is relabeled as a word only when it has WORD_MIN letters or more. A short one (`coo`) is still
    relabeled where it cannot be an ordinary word: as an exact `bot:<slug>` or `human:<id>` actor reference, in `label()`,
    and inside an email address, which is always relabeled (or, for an address nobody here owns, `[email]`)."""

    def __init__(self, company_domains=(), bots=(), people=()):
        self.domains = sorted({d.lower().strip(".") for d in company_domains if d and "." in d}, key=len, reverse=True)
        self.labels = {}                      # lower-case spelling -> label
        self.exact = {}                       # case-sensitive short aliases (given names) -> label
        self.actors = {"bot": {}, "human": {}}    # the slug or person id, lower case -> label
        self._names = []                      # (compiled pattern, label), longest spelling first
        for number, (slug, names) in enumerate(sorted(bots), 1):
            self._alias("bot-%d" % number, [slug, *names])
            self.actors["bot"].setdefault(str(slug or "").strip().lower(), "bot-%d" % number)
        for number, (ident, names) in enumerate(sorted(people), 1):
            self._alias("person-%d" % number, [ident, *names], parts=True)
            self.actors["human"].setdefault(str(ident or "").strip().lower(), "person-%d" % number)
        self._names.sort(key=lambda item: len(item[0]), reverse=True)
        self._pattern = re.compile("|".join("(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(spelling)
                                          for spelling, _ in self._names), re.I) if self._names else None
        self._exact = re.compile("|".join("(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(s) for s in
                                          sorted(self.exact, key=len, reverse=True))) if self.exact else None

    def _alias(self, label, spellings, parts=False):
        for spelling in spellings:
            spelling = str(spelling or "").strip()
            if len(spelling) < 2:
                continue
            key = spelling.lower()
            if key not in self.labels:
                self.labels[key] = label
                if len(spelling) >= WORD_MIN or "@" in spelling:
                    self._names.append((spelling, label))
            if parts and " " in spelling:
                for word in spelling.split():
                    if len(word) >= WORD_MIN and word not in self.exact and word.lower() not in self.labels:
                        self.exact[word] = label

    def _actor(self, match):
        label = self.actors[match.group(1)].get(match.group(2).lower())
        return label or match.group(0)

    def label(self, value):
        """The label for a bot slug, name or person known to this bundle, else None."""
        return self.labels.get(str(value or "").strip().lower())

    def text(self, value):
        s = CONTROL.sub("", str(value if value is not None else ""))
        s = SECRET_FIELD.sub(r"\1[redacted]", s)
        s = AUTHORITY.sub(self._authority, s)
        s = ACTOR.sub(self._actor, s)
        if self._pattern:
            s = self._pattern.sub(lambda m: self.labels[m.group(0).lower()], s)
        if self._exact:
            s = self._exact.sub(lambda m: self.exact[m.group(0)], s)
        s = EMAIL.sub("[email]", s)
        s = QUERY.sub(lambda m: m.group(1) + "?[query]" + (m.group(2) or ""), s)
        for pattern, replacement in SECRETS:
            s = pattern.sub(replacement, s)
        s = LONG.sub(lambda m: "[secret]" if _looks_secret(m.group(0)) else m.group(0), s)
        s = IPV4.sub("[ip]", s)
        s = IPV6.sub(_ipv6, s)
        for domain in self.domains:
            s = re.sub(r"(?<![A-Za-z0-9-])(?:[A-Za-z0-9-]+\.)*%s\b" % re.escape(domain), "[company-domain]", s, flags=re.I)
        return HOST.sub(lambda m: m.group(0) if known_host(m.group(0)) else "[host]", s)

    def _authority(self, match):
        # A URL identifies its authority without guessing a TLD or confusing it with a filename.
        try:
            url = urlsplit(match.group(0))
            host = (url.hostname or "").lower().rstrip(".")
            if known_host(host):
                authority = host + (":" + str(url.port) if url.port is not None else "")
            elif any(host == d or host.endswith("." + d) for d in self.domains):
                authority = "[company-domain]"
            else:
                authority = "[host]"
        except ValueError:
            authority = "[host]"
        return match.group(1) + authority

    def clean(self, value):
        """A whole structure: strings redacted, numbers, booleans and nothing kept as they are, everything else dropped."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
            return value
        if isinstance(value, dict):
            return {str(k): self.clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.clean(v) for v in value]
        return None


def known_host(host):
    host = host.lower().strip(".")
    return any(host == known or host.endswith("." + known) for known in KNOWN_HOSTS)
