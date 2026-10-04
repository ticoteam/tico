"""Service account + domain-wide delegation.

One Google service account acts as every mailbox. The key is never in a bot's environment;
this module reads it by path (`GOOGLE_SA_KEY`, default <projects>/secrets/google-sa.json) and
mints a credential impersonating one mailbox at a time (`subject=`).

Nothing here ever prints or returns the private key. `key_info()` returns only the public
identifiers the owner needs for the Admin console: client_email, client_id, project_id.

The Google client libraries are imported lazily so the rest of the package - normalization,
rules, the database, the tests - works with nothing installed.
"""

import json, os, stat
from pathlib import Path

from . import DEFAULT_KEY, Failure

KEY_ENV = "GOOGLE_SA_KEY"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
SCOPES = [GMAIL_SCOPE, CALENDAR_SCOPE]
SERVICE_SCOPES = {"gmail": [GMAIL_SCOPE], "calendar": [CALENDAR_SCOPE]}
SOCKET_ENV = "TICO_CRED_SOCKET"     # set in an isolated turn: the supervisor holds the key (runner/mail_key.py)
REQUIRED_FIELDS = ("type", "client_email", "client_id", "private_key", "token_uri")

SETUP_HINT = ("The owner creates it once: Google Cloud console > project acme-tico-hub > enable "
              "the Gmail API and the Google Calendar API > IAM & Admin > Service Accounts > "
              "company-hub-mail > Keys > Add key > JSON. Save it as the path above and "
              "`chmod 600` it. Full steps: connectors/mail/README.md.")


def key_path():
    return Path(os.environ.get(KEY_ENV) or DEFAULT_KEY).expanduser()


def read_key(path=None):
    """The parsed key file. Raises Failure with the exact fix when it is missing or wrong."""
    p = Path(path) if path else key_path()
    if not p.exists():
        raise Failure(f"the Google service-account key is not at {p}",
                      f"{SETUP_HINT} Or point {KEY_ENV} at an existing key file.")
    mode = stat.S_IMODE(p.stat().st_mode)
    if mode != 0o600:
        raise Failure(f"{p} is mode {mode:o}; the key must be private",
                      f"chmod 600 {p}")
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        raise Failure(f"{p} is not valid JSON: {e}",
                      "Download the key again from the service account's Keys tab; keep the "
                      "file exactly as Google produced it.")
    missing = [f for f in REQUIRED_FIELDS if not data.get(f)]
    if missing:
        raise Failure(f"{p} is missing {', '.join(missing)}",
                      "That is not a service-account key. Create a JSON key on the service "
                      "account itself, not an OAuth client id.")
    if data.get("type") != "service_account":
        raise Failure(f"{p} is a {data.get('type')!r} key, not a service account",
                      "Domain-wide delegation only works with a service account key.")
    return data


def key_info(path=None):
    """Public identifiers only - never the private key."""
    d = read_key(path)
    return {"path": str(Path(path) if path else key_path()),
            "client_email": d["client_email"],
            "client_id": str(d["client_id"]),
            "project_id": d.get("project_id", ""),
            "private_key_id": str(d.get("private_key_id", ""))[:8] + "..."}


def delegation_hint(scope, client_id):
    return ("domain-wide delegation is not granted for scope %s: Google Workspace Admin console > "
            "Security > Access and data control > API controls > Domain-wide delegation > "
            "Add new, client id %s, scope %s." % (scope, client_id, scope))


def mint_token(mailbox, service):
    """(access token, ISO expiry) acting as `mailbox`, from the key file. Only the supervisor can read it."""
    creds = credentials(mailbox, SERVICE_SCOPES[service], key_only=True)
    from google.auth.transport.requests import Request  # noqa: PLC0415
    creds.refresh(Request())
    return creds.token, creds.expiry.isoformat() if creds.expiry else ""


def supervisor_token(mailbox, scopes):
    """A token for `mailbox` from the runner's supervisor, or None when this process has no socket
    (a Mac, or a Docker runner without the two-user layout: the key file is read directly)."""
    path = os.environ.get(SOCKET_ENV)
    service = next((k for k, v in SERVICE_SCOPES.items() if list(scopes or SCOPES) == v), None)
    if not path:
        return None
    if not os.environ.get("HUB_TOKEN"):
        raise Failure("the runner credential socket is configured but HUB_TOKEN is missing",
                      "Start mail through the runner so it can provide a scoped credential token.")
    if not service:
        raise Failure("mail access through the runner covers one service at a time", "Ask for gmail or calendar.")
    from runner import credential_socket                # noqa: PLC0415
    try:
        return credential_socket.request_mail(path, os.environ["HUB_TOKEN"], service, mailbox)
    except OSError as e:
        raise Failure(f"could not reach the runner to ask for access to {mailbox}: {e}",
                      "The runner holds the Google key on this computer and gives a run a token; it did not answer.")
    except ValueError as e:
        if getattr(e, "reason", "") == "no mailbox":
            raise Failure("this bot has no mailbox: the key is on this computer's runner, which gives a token only to a "
                          "message bot, and the server has not named this bot as anyone's",
                          "An owner or admin sets it: POST /api/v2/access/people/<person> "
                          '{"inbox_bot": "<this bot>", "mailbox": "<address>"}. docs/mail.md, Who can read the key.')
        if getattr(e, "mailboxes", None):
            raise Failure(f"the runner would not let this turn act as {mailbox}; this bot may use {', '.join(e.mailboxes)}",
                          "The server names those for this bot, not its bot.yaml. If the address is wrong, an owner or admin "
                          'sets it: POST /api/v2/access/people/<person> {"mailbox": "<address>"}. docs/mail.md, Who can read the key.')
        raise Failure(f"the runner would not give this turn access to {mailbox}: {e}",
                      "The run's attempt is not known to the runner, or it has ended. docs/mail.md, Works on Linux runners.")


def credentials(mailbox, scopes=None, path=None, key_only=False):
    """A credential that acts as `mailbox`. Requires the google-auth library."""
    if not key_only and path is None:
        granted = supervisor_token(mailbox, scopes)
        if granted:
            from google.oauth2.credentials import Credentials     # noqa: PLC0415
            return Credentials(token=granted["token"])
    try:
        from google.oauth2 import service_account       # noqa: PLC0415
    except ImportError:
        raise Failure("the google-auth library is not installed",
                      "Run mail through scripts/mail.sh, which builds the venv "
                      "(connectors/mail/requirements.txt).")
    read_key(path)                                      # mode + shape checks, with good errors
    p = str(Path(path) if path else key_path())
    creds = service_account.Credentials.from_service_account_file(p, scopes=scopes or SCOPES)
    return creds.with_subject(mailbox)


def build_service(api, version, mailbox, scopes=None, path=None):
    try:
        from googleapiclient.discovery import build     # noqa: PLC0415
    except ImportError:
        raise Failure("the google-api-python-client library is not installed",
                      "Run mail through scripts/mail.sh, which builds the venv "
                      "(connectors/mail/requirements.txt).")
    # A test seam for the container smoke: a fake Google on this address. Unset, Google's own.
    endpoint = os.environ.get("TICO_GOOGLE_API_ENDPOINT")
    options = {"client_options": {"api_endpoint": endpoint}} if endpoint else {}
    return build(api, version, credentials=credentials(mailbox, scopes, path),
                 cache_discovery=False, **options)


def gmail_service(mailbox, path=None):
    return build_service("gmail", "v1", mailbox, [GMAIL_SCOPE], path)


def calendar_service(mailbox, path=None):
    return build_service("calendar", "v3", mailbox, [CALENDAR_SCOPE], path)
