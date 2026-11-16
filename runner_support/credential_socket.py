"""A bot's GitHub token, from the supervisor, without the runner's registration.

git's credential helper runs inside a turn, as the turn's user, which must not be able to read
runner.json (runner_support/isolation.py). So the supervisor (runner-rs/src/gitcred.rs) listens on a Unix socket
and the helper asks it with the client calls below: it sends the turn's attempt token, and gets a GitHub token for the bot that attempt
belongs to, minted with the runner's registration. The socket has no other question: it cannot
name a bot, so an attempt never gets another bot's token, an attempt that has ended gets nothing,
and the runner's own token never crosses it.

One JSON line each way: {"token": "<attempt token>", "repository": "owner/repo", "purpose": "git"}
(repository and purpose optional) then {"token": "<github token>"} or {"error": "..."}.

The same socket serves an inbox bot's mail access (runner_support/mail_key.py holds the Google key, which
bots cannot read): {"token": "<attempt token>", "mail": {"service": "gmail", "mailbox": "ana@..."}}
answers {"token": "<Gmail access token>", "expiry": "...", "scopes": [...]} (scopes only for a person's own connected
Google account, connectors/mail/oauth.py, so the turn refuses a write it did not grant). The hub named the mailboxes this attempt's
bot may open when it handed the attempt out; any other mailbox, and any attempt of any other bot, gets an error
("no mailbox" when the hub named none: the bot is nobody's message bot).
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


def request(path, attempt_token, timeout=20, repository=None, purpose="turn"):
    """The GitHub token for the bot this attempt belongs to. Raises OSError/ValueError on failure."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
        asked = {"token": attempt_token}
        if repository:
            asked["repository"] = repository
        if purpose != "turn":
            if purpose != "git":
                raise ValueError("bad purpose")
            asked["purpose"] = purpose
        connection.sendall(json.dumps(asked).encode() + b"\n")
        line = connection.makefile("rb").readline(MAX_LINE)
    reply = json.loads(line)
    if not reply.get("token"):
        raise ValueError(reply.get("error") or "refused")
    return reply["token"]


def request_refresh(path, attempt_token, repository, timeout=60):
    """Refresh only a mirror this attempt can read; no supervisor token is returned."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
        connection.sendall(
            json.dumps({'token': attempt_token, 'repository': repository, 'refresh': True}).encode() + b'\n'
        )
        reply = json.loads(connection.makefile('rb').readline(MAX_LINE))
    if not reply.get('refreshed') and not reply.get('cached'):
        raise ValueError(reply.get('error') or 'mirror refresh failed')
    return reply


class MailRefused(ValueError):
    """The supervisor said no: `reason` ("no mailbox": the hub named none for this bot, "not this bot's mailbox")
    and, when it is the mailbox that was wrong, the ones this run may ask for."""

    def __init__(self, reason, mailboxes=None):
        super().__init__(reason)
        self.reason, self.mailboxes = reason, [m for m in mailboxes or [] if isinstance(m, str)]


def request_mail(path, attempt_token, service, mailbox, timeout=60):
    """{"token", "expiry"} to act as `mailbox` in `service`, for the attempt's inbox bot. Raises OSError/ValueError."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
        connection.sendall(
            json.dumps({"token": attempt_token, "mail": {"service": service, "mailbox": mailbox}}).encode() + b"\n"
        )
        line = connection.makefile("rb").readline(MAX_LINE)
    reply = json.loads(line)
    if not reply.get("token"):
        raise MailRefused(reply.get("error") or "refused", reply.get("mailboxes"))
    return reply
