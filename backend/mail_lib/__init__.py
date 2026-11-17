# pyright: strict
"""Pure email helpers the server keeps from the retired computer-side mail CLI (connectors/mail).

Nothing here talks to Google: MIME in and out (compose), the deterministic lint, the hub label names, the Gmail message
normalizer and attachment decoding (gmail), and the meeting link of a calendar event (calendar). This module holds the
errors and the time zone they share.
"""

from datetime import tzinfo
from zoneinfo import ZoneInfo

DEFAULT_TZ = "America/Los_Angeles"
MAX_BODY = 32 * 1024  # characters, per docs/mail-service.md
TRUNCATED = "\n\n[... truncated at %d characters ...]" % MAX_BODY
INTERNAL_DOMAIN = "acme.example"


class Failure(Exception):
    """Something went wrong (a bad value, a bad attachment). `msg` says what, `hint` what to do."""

    def __init__(self, msg: str, hint: str = "", status: int | None = None) -> None:
        super().__init__(msg)
        self.msg, self.hint, self.status = msg, hint, status


class Refused(Failure):
    """A hub policy refused the action."""


def zone(name: str | None = DEFAULT_TZ) -> tzinfo:
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except Exception as e:
        raise Failure(f"unknown timezone {name!r}", "Use an IANA name like America/Los_Angeles.") from e
