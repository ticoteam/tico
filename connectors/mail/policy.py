"""What the mail CLI asks Tico about sending. It decides nothing about a send itself.

Every send - a bot's own email, an automation's action, a calendar booking's invitation - goes through one door:
`queue_send` posts the frozen message to POST /api/v2/email/sends (backend/email_sending.py) and returns Tico's
answer. Tico decides there, and only there: the company, inbox and bot sending policy (On their own / Ask first /
Never), blocked recipients, owner-handles-personally, outside Cc and many-outside-recipient asks, the lint and the
second reviewer on a bot's own writing, the org rule check, bounce suppression and approvals. A send it accepts waits
out an undo window before the connector dispatches it. The CLI keeps the Gmail draft whatever the answer, so a send
Tico holds or blocks still leaves the words somewhere.

The rest of this module is what a run reads from Tico around that door:

  sending_policy(slug, mailbox)    the effective policy for `policy show` and the hq-tickets sender
  sending_preflight(mailbox, ...)  the inbox's Never ceiling, read before a calendar event exists
  company(slug), runner_company()  the company's What Tico may do (organize mail, send mail, change calendar)
  team_domains(slug)               the team's own mail domains: "internal" for the lint and the unsubscribe guard

Tests replace the seams HUB_GET, HUB_POST, SERVER_GET and RUNNER_GET.
"""

import os, re
from urllib.parse import quote

from . import REGISTRY, Failure, Refused, load_yaml

HUB_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

# Mail providers anyone can sign up to: a roster address there says nothing about the team's own domain.
PUBLIC_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com", "ymail.com",
    "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "pm.me", "hey.com", "fastmail.com",
    "gmx.com", "gmx.net", "mail.com", "zoho.com", "yandex.com", "qq.com", "163.com"})

# One try at POST email/sends: the server's longest synchronous check (the org rule check's 15 s deadline at High,
# backend/org_rule_check.py) plus a margin for the write around it.
HUB_POST_TIMEOUT = 20


def _default_hub_get(path):
    """One Tico GET. Tests replace `HUB_GET`. None means: no cloud credential, or the row is gone."""
    url, token = os.environ.get("HUB_API_URL"), os.environ.get("HUB_TOKEN")
    if not url or not token:
        return None
    try:
        from clients.tico import Client
        return Client(url, token).get(path)
    except Exception:
        return None


HUB_GET = _default_hub_get


class HubFailed(Exception):
    """Tico was asked and gave no usable answer. `reason`: forbidden (the token is not this bot's), http_<status>,
    unavailable (timed out or unreachable) or error."""

    def __init__(self, reason, status=0):
        super().__init__(reason)
        self.reason, self.status = reason, status


def _default_hub_post(path, body, key=None):
    """One Tico POST, one try within HUB_POST_TIMEOUT, `key` its Idempotency-Key. Tests replace `HUB_POST`. None
    means no cloud credential (outside a turn); a request Tico refused or did not answer raises HubFailed."""
    url, token = os.environ.get("HUB_API_URL"), os.environ.get("HUB_TOKEN")
    if not url or not token:
        return None
    from clients.tico import APIError, Client
    try:
        return Client(url, token, timeout=HUB_POST_TIMEOUT, retries=0).post(path, body, key=key)
    except APIError as exc:
        reason = "forbidden" if exc.status == 403 else f"http_{exc.status}" if exc.status else \
            "unavailable" if exc.code == "unavailable" else "error"
        raise HubFailed(reason, status=exc.status) from None
    except Exception:
        raise HubFailed("error") from None


HUB_POST = _default_hub_post


# ---------------------------------------------------------------- the one send door

def queue_send(mailbox, raw, thread_id="", reply_to_id="", approval_id="", automation_action_id="", draft_id="",
               calendar_action_id=""):
    """Queue one frozen message with Tico and return its row: {id, state, detail, decision, approval_id,
    send_after, ...}. `state` is pending (the undo window runs), waiting_approval (a person approves in Tico) or
    blocked (`detail` says why). Fails closed: no durable server row, no provider send."""
    body = {"mailbox": mailbox, "raw": raw, "thread_id": thread_id, "reply_to_id": reply_to_id,
            "approval_id": str(approval_id or ""), "automation_action_id": automation_action_id, "draft_id": draft_id,
            "calendar_action_id": calendar_action_id}
    try:
        if automation_action_id or calendar_action_id:
            from . import oauth
            answer = oauth.broker(retries=0).post("email/sends", body)
        else:
            answer = HUB_POST("email/sends", body)
    except Exception as exc:
        raise Failure("Tico did not queue this email; the draft has not been sent.",
                      "Try again when Tico answers. Identical content and recipients never queue twice.") from exc
    if not isinstance(answer, dict) or not isinstance(answer.get("send"), dict):
        raise Failure("Tico did not confirm a durable queued send; the draft has not been sent.")
    return answer["send"]


def sending_preflight(mailbox, calendar_action_id=""):
    """A silent calendar event still checks the inbox's Never ceiling before creating any provider side effect."""
    if calendar_action_id:
        from . import oauth
        answer = oauth.broker(retries=0).post("connectors/email/sends/preflight", {
            "mailbox": mailbox, "calendar_action_id": calendar_action_id})
    else:
        answer = HUB_GET("email/inboxes/" + quote(mailbox, safe="") + "/sending-policy")
    if not isinstance(answer, dict) or not isinstance(answer.get("effective"), dict):
        raise Failure("Tico did not confirm this inbox's sending policy; nothing was scheduled.")
    if answer["effective"].get("mode") == "never":
        raise Refused("Never blocks this inbox's invitations and confirmation emails.")
    return answer["effective"]


def sending_policy(slug, mailbox=""):
    """`slug`'s sending policy as Tico answers it (GET /api/v2/bots/<slug>/email-sending-policy): {company, inbox,
    bot, effective}, `effective` holding the mode (on_their_own, ask_first, never) and the caps. None when Tico could
    not be asked; a reader then knows nothing, and the server still decides every send."""
    path = "bots/" + quote(str(slug or ""), safe="") + "/email-sending-policy"
    if mailbox:
        path += "?mailbox=" + quote(str(mailbox), safe="")
    try:
        answer = HUB_GET(path)
    except Exception:
        answer = None
    return answer if isinstance(answer, dict) and isinstance(answer.get("effective"), dict) else None


# ---------------------------------------------------------------- the team's own domains

def _walk_emails(value, found, depth=0):
    if depth > 8:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) == "email" and isinstance(item, str):
                found.append(item)
            else:
                _walk_emails(item, found, depth + 1)
    elif isinstance(value, list):
        for item in value[:2000]:
            _walk_emails(item, found, depth + 1)


def team_domains(slug="", env=None):
    """The team's own mail domains: `TICO_INTERNAL_DOMAINS` when the owner names them, else the domain of the bot's
    own mailbox (the company's Workspace domain) and the domains on the team roster (the registry's people.yaml, else
    the hub's org chart), leaving out public providers like gmail.com."""
    env = os.environ if env is None else env
    found = []

    def add(domain, public_ok=False):
        domain = str(domain or "").strip().lower().lstrip("@").rsplit("@", 1)[-1]
        if domain and "." in domain and domain not in found and (public_ok or domain not in PUBLIC_MAIL_DOMAINS):
            found.append(domain)

    for domain in re.split(r"[,\s]+", env.get("TICO_INTERNAL_DOMAINS") or ""):
        add(domain, public_ok=True)
    if found:
        return found
    try:
        from . import access                                # noqa: PLC0415 - avoids a cycle
        for entry in access.entries(access.load(slug or access.employee(None, env))):
            if str(entry.get("service", "")).lower() == "gmail":
                add(entry.get("identity"))
    except Exception:                                       # no bot, no manifest: the roster still counts
        pass
    emails = []
    try:
        people = load_yaml(REGISTRY / "people.yaml", "registry/people.yaml").get("people") or []
        _walk_emails(people, emails)
    except Exception:
        pass
    if not emails:
        try:
            _walk_emails(HUB_GET("org"), emails)
        except Exception:
            pass
    for email in emails:
        add(email)
    return found


def signature_names(mailbox=""):
    """The names a signature may carry: the mailbox's own name (ana@acme.example signs as Ana)."""
    local = str(mailbox or "").split("@")[0].strip().lower()
    return [local] if local else []


# ---------------------------------------------------------------- What Tico may do

# The bot's mail setting (GET /api/v2/bots/<slug>/mail-settings, backend/mail_settings.py) carries the company's What
# Tico may do as company_* fields, set only by a person in Tico. Tests replace `SERVER_GET`.
SERVER_GET = None
COMPANY_FIELDS = {"organize_mail": "company_organize", "send_mail": "company_send",
                  "change_calendar": "company_calendar"}
UNREAD = "unread"                                       # company(): Tico could not be asked (oauth.company_allows)


def _server_row(slug):
    get = SERVER_GET or (lambda s: HUB_GET(f"bots/{s}/mail-settings"))
    try:
        row = get(slug)
    except Exception:
        row = None
    return row if isinstance(row, dict) and ("bot" in row or any(f in row for f in COMPANY_FIELDS.values())) \
        else None


def company(slug):
    """The company's What Tico may do for `slug`'s run, as {organize_mail, send_mail, change_calendar}, from Tico and
    nothing the run says itself: a bot's from its mail setting, the owner's (the runner's own commands, a person's
    run) from the runner's read (runner_company). It holds everyone who acts through Tico.

    None for a server that answers without the fields (an older one). UNREAD when the setting could not be read: the
    run's writes are refused then, and its reads go on."""
    from . import access                                # noqa: PLC0415 - avoids a cycle
    slug = str(slug or "").strip().lower()
    if slug == access.CONNECTOR:
        return runner_company()
    row = _server_row(slug)
    if row is None:
        if access.entries(access._manifest(slug)):
            return UNREAD                               # a bot here whose own mail setting Tico did not answer
        # Not a bot with a mail setting (a person's own run): What Tico may do as Tico answered about this run's access.
        try:
            got = access.answer(slug).get("company")
        except Refused:
            got = None
        if not isinstance(got, dict):
            return UNREAD
        return {name: got.get(name) is not False for name in COMPANY_FIELDS}
    return _allowed(row)


def _allowed(row):
    if not any(f in row for f in COMPANY_FIELDS.values()):
        return None
    return {name: row.get(field) is not False for name, field in COMPANY_FIELDS.items()}


# The runner's read of What Tico may do (GET /api/v2/connectors/mail/targets over its own registration). Tests
# replace RUNNER_GET.
RUNNER_GET = None


def runner_company():
    """What Tico may do for the connector (an automation's mail action, a calendar booking): asked of Tico over the
    runner's registration, as a bot's run asks for its own, so nothing on stdin decides it. UNREAD when Tico could not
    be asked, a run without the registration included."""
    try:
        if RUNNER_GET is not None:
            answer = RUNNER_GET("connectors/mail/targets")
        else:
            from . import oauth                         # noqa: PLC0415
            answer = oauth.broker().get("connectors/mail/targets")
    except Exception:
        answer = None
    if not isinstance(answer, dict):
        return UNREAD
    may = answer.get("company")
    if not isinstance(may, dict):
        return None                                     # an older server: it says nothing of What Tico may do
    return {name: may.get(name) is not False for name in COMPANY_FIELDS}
