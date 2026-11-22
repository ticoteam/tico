"""What a Slack loop logs about an exception it caught: the exception's class and a short, scrubbed message.

`describe` is for exceptions raised by Tico's own code or the database (sqlite3, OSError). A catch around a Slack API
call or a Slack event logs the class alone instead: a Slack error's text can echo a user's message.
"""

# pyright: strict

from .run_diagnostics import scrub

RATE_LIMIT_DEFAULT = 30  # seconds, when Slack's Retry-After is unreadable


class SlackError(Exception):
    """Slack answered with an error code, which may leave the effect unknown."""

    def __init__(self, method: str, code: str, retry_after: float | None = None):
        super().__init__(f"Slack {method}: {code}")
        self.method, self.code, self.retry_after = method, code, retry_after


class SlackUnreachable(Exception):
    """No conclusive answer: after a post this is uncertain."""


LIMIT = 200  # characters of the message in a log line


def describe(exc: BaseException) -> str:
    """`ClassName: message`, the message scrubbed of secrets and cut to LIMIT characters; the class alone when empty."""
    message = scrub(" ".join(str(exc).split()))[:LIMIT]
    name = type(exc).__name__
    return f"{name}: {message}" if message else name
