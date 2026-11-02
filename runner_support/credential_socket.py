"""A bot's GitHub token, from the supervisor, without the runner's registration.

git's credential helper runs inside a turn, as the turn's user, which must not be able to read
runner.json (runner/isolation.py). So the supervisor listens on a Unix socket and the helper
asks it: it sends the turn's attempt token, and gets a GitHub token for the bot that attempt
belongs to, minted with the runner's registration. The socket has no other question: it cannot
name a bot, so an attempt never gets another bot's token, an attempt that has ended gets nothing,
and the runner's own token never crosses it.

One JSON line each way: {"token": "<attempt token>", "repository": "owner/repo", "purpose": "git"}
(repository and purpose optional) then {"token": "<github token>"} or {"error": "..."}.

The same socket serves an inbox bot's mail access (runner/mail_key.py holds the Google key, which
bots cannot read): {"token": "<attempt token>", "mail": {"service": "gmail", "mailbox": "ana@..."}}
answers {"token": "<Gmail access token>", "expiry": "...", "scopes": [...]} (scopes only for a person's own connected
Google account, connectors/mail/oauth.py, so the turn refuses a write it did not grant). The hub named the mailboxes this attempt's
bot may open when it handed the attempt out; any other mailbox, and any attempt of any other bot, gets an error
("no mailbox" when the hub named none: the bot is nobody's message bot).
"""
import json
import os
import socket
import socketserver
import threading
import tempfile
from pathlib import Path

from .outage import log

SOCKET_ENV = "TICO_CRED_SOCKET"
DEFAULT_PATH = "/run/tico-runner/git-credential.sock"
MAX_LINE = 4096
MAIL_SERVICES = ("gmail", "calendar")


def _server_rejects_token_purpose(exc):
    """Only identify the old-server schema error for the optional purpose field."""
    if getattr(exc, "status", None) != 422:
        return False
    if str(getattr(exc, "code", "")).lower() not in {
        "validation", "validation_error", "unknown_field", "extra_forbidden",
    }:
        return False
    detail = str(getattr(exc, "detail", exc)).lower()
    return "purpose" in detail and any(marker in detail for marker in (
        "extra input", "extra field", "unknown field", "unexpected field", "extra_forbidden",
    ))


class Server:
    """Serves `mint(bot, repository)` to the attempt registered for that bot; repository is optional."""

    def __init__(self, path, mint, mail=None, refresh=None, mint_git=None):
        self.path, self.mint, self.mail = str(path), mint, mail
        self.attempts, self.mailboxes, self.lock = {}, {}, threading.Lock()
        self.server = None
        self.redactors = {}
        self.issued = {}
        self.directory = None
        self.refresh = refresh
        self.mint_git = mint_git

    def register(self, attempt_token, bot, mailboxes=()):
        with self.lock:
            self.attempts[attempt_token] = bot
            self.issued[attempt_token] = set()
            self.mailboxes[attempt_token] = {str(m).strip().lower() for m in mailboxes or ()}

    def unregister(self, attempt_token):
        with self.lock:
            self.attempts.pop(attempt_token, None)
            self.mailboxes.pop(attempt_token, None)
            self.redactors.pop(attempt_token, None)
            self.issued.pop(attempt_token, None)

    def set_redactor(self, attempt_token, redactor):
        with self.lock:
            redactor.add(self.issued.pop(attempt_token, ()))
            self.redactors[attempt_token] = redactor

    def answer(self, line):
        try:
            asked = json.loads(line)
            token = asked.get("token")
        except (ValueError, AttributeError):
            return {"error": "bad request"}
        with self.lock:
            bot = self.attempts.get(token) if isinstance(token, str) and token else None
            allowed = self.mailboxes.get(token, set())
        if not bot:
            return {"error": "unknown attempt"}
        if "mail" in asked:
            return self.answer_mail(bot, allowed, asked["mail"])
        try:
            repository = asked.get("repository")
            if repository is not None and not isinstance(repository, str):
                return {"error": "bad repository"}
            purpose = asked.get("purpose", "turn")
            if purpose not in ("turn", "git"):
                return {"error": "bad purpose"}
            mint = self.mint_git if purpose == "git" and self.mint_git else self.mint
            granted = mint(bot, repository) if repository else mint(bot)
            if asked.get('refresh'):
                if not granted or not repository or not self.refresh:
                    return {'error': 'mirror refresh unavailable'}
                return self.refresh(repository, granted)
        except Exception as exc:
            if getattr(exc, "status", 0):
                # The hub's refusal names the cause (App not installed on it, outside the organization): a failed
                # clone shows it rather than a bare exception name.
                from .git_credentials import token_problem
                return {"error": f"{type(exc).__name__}: {token_problem(exc, repository)}"}
            return {"error": type(exc).__name__}
        if not granted:
            return {"error": "no token"}
        with self.lock:
            if token not in self.attempts:
                return {"error": "unknown attempt"}
            redactor = self.redactors.get(token)
            if redactor:
                redactor.add([granted])
            else:
                self.issued.setdefault(token, set()).add(granted)
        return {"token": granted}

    def answer_mail(self, bot, allowed, asked):
        service = asked.get("service") if isinstance(asked, dict) else None
        mailbox = str(asked.get("mailbox") or "").strip().lower() if isinstance(asked, dict) else ""
        if not self.mail or service not in MAIL_SERVICES:
            return {"error": "no mail access"}
        if not allowed:
            return {"error": "no mailbox", "mailboxes": []}
        if mailbox not in allowed:
            return {"error": "not this bot's mailbox", "mailboxes": sorted(allowed)}
        try:
            granted = self.mail(service, mailbox)
        except Exception as exc:
            return {"error": type(exc).__name__}
        if not granted:
            return {"error": "no token"}
        reply = {"token": granted["token"], "expiry": granted.get("expiry", "")}
        if isinstance(granted.get("scopes"), list):     # a connected account: the turn refuses what it did not grant
            reply["scopes"] = granted["scopes"]
        return reply

    def start(self):
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.request.settimeout(20)
                try:
                    reply = outer.answer(self.rfile.readline(MAX_LINE))
                    self.wfile.write(json.dumps(reply).encode() + b"\n")
                except OSError:
                    pass

        class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        path = Path(self.path)
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        self.server = UnixServer(self.path, Handler)
        # Whoever holds an attempt token may ask; the file mode only keeps strangers' processes out.
        from . import isolation
        os.chmod(self.path, 0o600)
        isolation.chown(self.path)
        threading.Thread(target=self.server.serve_forever, name="credential-socket", daemon=True).start()
        return self

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            Path(self.path).unlink(missing_ok=True)
            if self.directory:
                self.directory.cleanup()


def serve(client, path=None, mail=None, refresh=None):
    """The supervisor's server: `client` is the runner's own, `mail`
    (service, mailbox) -> {"token", "expiry"} mints Gmail access."""
    from . import isolation
    path = path or os.environ.get("TICO_RUNNER_CRED_SOCKET")
    directory = None
    if not isolation.enabled() and not path:
        directory = tempfile.TemporaryDirectory(prefix="tico-credentials-", dir="/tmp")
        path = str(Path(directory.name) / "credential.sock")
    path = path or os.environ.get("TICO_RUNNER_CRED_SOCKET") or DEFAULT_PATH

    def mint(bot, repository=None):
        from .git_credentials import hub_token, select_token
        granted = hub_token(client, {"bot": bot})
        return select_token(granted, repository)
    legacy_purpose_servers = set()
    purpose_lock = threading.Lock()
    server_key = str(getattr(client, "url", None) or id(client))

    def mint_git(bot, repository=None):
        from .git_credentials import hub_token, select_token
        with purpose_lock:
            legacy_server = server_key in legacy_purpose_servers
        if legacy_server:
            granted = hub_token(client, {"bot": bot})
        else:
            try:
                granted = hub_token(client, {"bot": bot, "purpose": "git"})
            except Exception as exc:
                if not _server_rejects_token_purpose(exc):
                    raise
                with purpose_lock:
                    legacy_purpose_servers.add(server_key)
                # Older servers default an omitted purpose to turn. Retry once, then remember
                # that schema for this server so every later git request uses its supported body.
                granted = hub_token(client, {"bot": bot})
        return select_token(granted, repository)
    try:
        server = Server(path, mint, mail, refresh, mint_git=mint_git).start()
        server.directory = directory
        return server
    except OSError as exc:
        if directory:
            directory.cleanup()
        log(f"Tico runner: no credential socket at {path} ({type(exc).__name__}); turns keep their start-of-turn token")
        return None


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
        connection.sendall(json.dumps({'token': attempt_token, 'repository': repository, 'refresh': True}).encode() + b'\n')
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
        connection.sendall(json.dumps({"token": attempt_token, "mail": {"service": service, "mailbox": mailbox}}).encode() + b"\n")
        line = connection.makefile("rb").readline(MAX_LINE)
    reply = json.loads(line)
    if not reply.get("token"):
        raise MailRefused(reply.get("error") or "refused", reply.get("mailboxes"))
    return reply
