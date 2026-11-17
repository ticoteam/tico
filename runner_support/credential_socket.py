"""A bot's GitHub token, from the supervisor, without the runner's registration.

git's credential helper runs inside a turn, as the turn's user, which must not be able to read
runner.json (runner_support/isolation.py). So the supervisor (runner-rs/src/gitcred.rs) listens on a Unix socket
and the helper asks it with the client calls below: it sends the turn's attempt token, and gets a GitHub token for the bot that attempt
belongs to, minted with the runner's registration. The socket has no other question: it cannot
name a bot, so an attempt never gets another bot's token, an attempt that has ended gets nothing,
and the runner's own token never crosses it.

One JSON line each way: {"token": "<attempt token>", "repository": "owner/repo", "purpose": "git"}
(repository and purpose optional) then {"token": "<github token>"} or {"error": "..."}.

No mail crosses it: the server reads and sends mail itself, so the supervisor refuses a mail question.
"""

import json
import socket

SOCKET_ENV = "TICO_CRED_SOCKET"
MAX_LINE = 4096


def _server_rejects_token_purpose(exc):
    """Only identify the old-server schema error for the optional purpose field."""
    if getattr(exc, "status", None) != 422:
        return False
    if str(getattr(exc, "code", "")).lower() not in {
        "validation",
        "validation_error",
        "unknown_field",
        "extra_forbidden",
    }:
        return False
    detail = str(getattr(exc, "detail", exc)).lower()
    return "purpose" in detail and any(
        marker in detail
        for marker in (
            "extra input",
            "extra field",
            "unknown field",
            "unexpected field",
            "extra_forbidden",
        )
    )
