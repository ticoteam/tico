"""What a Slack loop logs about an exception it caught: the exception's class and a short, scrubbed message.

`describe` is for exceptions raised by Tico's own code or the database (sqlite3, OSError). A catch around a Slack API
call or a Slack event logs the class alone instead: a Slack error's text can echo a user's message.
"""

from .run_diagnostics import scrub

LIMIT = 200  # characters of the message in a log line


def describe(exc: BaseException) -> str:
    """`ClassName: message`, the message scrubbed of secrets and cut to LIMIT characters; the class alone when empty."""
    message = scrub(" ".join(str(exc).split()))[:LIMIT]
    name = type(exc).__name__
    return f"{name}: {message}" if message else name
