"""Volatile values out of a response, so two runs of the same scenario compare equal.

Ids the scenario made are replaced by their names (`<task>`); other ids become `<id1>`, `<id2>`... in the order they
first appear in one response, so a reference between two rows in the same response stays visible. Timestamps, hashes
and cursors become fixed words, and floats are rounded to six places. Nothing else changes: a field that moves, appears or disappears still shows.
"""
import re

TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
DIGEST = re.compile(r"\b[0-9a-f]{64}\b")

# Keys whose values change on every run: opaque tokens and cursors, and measured durations (a timer's reading, an
# age, how long something took). Their presence still matters; only the value is replaced.
VOLATILE = frozenset({"cursor", "next_cursor", "next_before", "etag", "token"})
# A string `version` is an opaque digest of the answer (Needs you holds a version to ask "changed?"); an integer
# version is a row's counter and stays.
OPAQUE_WHEN_TEXT = frozenset({"version"})
VOLATILE_DURATION = re.compile(r"(^|_)(seconds|secs|ms|s|duration|elapsed|latency|took|age|ago)$")


class Normalizer:
    def __init__(self, names):
        # Longest first, so an id that contains another never gets half-replaced.
        self.names = sorted(((value, name) for name, value in names.items() if isinstance(value, str) and value),
                            key=lambda pair: -len(pair[0]))
        # A short id is the first eight characters of a uuid (a task's `short_id`): replaced only when it is the whole value.
        self.short = {value[:8]: name for value, name in self.names if UUID.fullmatch(value)}
        self.labels = {}

    def label(self, match):
        value = match.group(0)
        if value not in self.labels:
            self.labels[value] = "<id%d>" % (len(self.labels) + 1)
        return self.labels[value]

    def text(self, value):
        if value in self.short:
            return "<%s-short>" % self.short[value]
        for raw, name in self.names:
            value = value.replace(raw, "<%s>" % name)
        value = TIME.sub("<time>", value)
        value = UUID.sub(self.label, value)
        return DIGEST.sub("<digest>", value)

    def __call__(self, value, key=None):
        if key in OPAQUE_WHEN_TEXT and isinstance(value, str):
            return "<opaque>"
        if key in VOLATILE or (isinstance(key, str) and VOLATILE_DURATION.search(key)
                               and isinstance(value, (int, float)) and not isinstance(value, bool)):
            return "<volatile>"
        if isinstance(value, dict):
            return {(self.text(k) if isinstance(k, str) else k): self(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [self(v) for v in value]
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, float):
            # Floats the server derives from the clock (a target's expected value at this moment) differ past the
            # sixth decimal from one run to the next; a contract change is never that small.
            return round(value, 6)
        return value


def normalize(value, names):
    """A fresh normalizer per response: labels restart at <id1> for each golden file."""
    return Normalizer(names)(value)
