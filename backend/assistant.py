"""The Assistant: every person's own private chat with the company's assistant bot (docs/assistant.md).

One room per person (`rooms.ASSISTANT_ROOM`, scope personal, owner_actor the person): only they
read it or post in it, the owner and administrators included. A message goes one of two ways:

* every message is a turn of the assistant bot on the company's runner, which reads the question and
  answers it from Tico's own data (no canned answers, no keyword routing);
* that turn acts as the person and never more: `Auth.authenticate` maps its token to the person (`assistant_principal`),
  so every hub tool it calls is checked, and audited "via assistant", exactly as if they had made
  the call. Low-risk writes run directly (`write_allowed`); any other write the turn makes runs as the person when the
  turn cites the person's own message that started it (`instruction`), except what `NEVER` names; a proposal the turn
  makes is a pending action, and only the person's own click (`confirm`) runs it, through the same routes with their own
  credential. The bot itself can never confirm.
"""

import asyncio
import json
import re
import secrets
import unicodedata
import uuid
from datetime import datetime, time, timedelta, timezone
from urllib.parse import unquote

import httpx
from fastapi import Request

from . import strategy_bot
from . import models as M
from . import visuals
from .media import upload_contract
from . import rooms
from . import settings_asks
from .store import H, Problem, digest

RUNNING_TTL_S = 120                # a confirm that has not settled in two minutes failed
CONFIRM_TTL_H = 24                 # a proposal nobody confirmed for a day is not run
MAX_PENDING = 20
MAX_BACKGROUND_PER_DAY = 5      # cards the Assistant's own background run may put in one person's chat a day
MORNING_KEY = "morning"         # the message's refs key: the company day it was sent for (one a day per person)

SCHEMA = """
CREATE TABLE IF NOT EXISTS assistant_actions (
  id TEXT PRIMARY KEY, owner TEXT NOT NULL, conversation_id TEXT, summary TEXT NOT NULL,
  method TEXT NOT NULL, path TEXT NOT NULL, body TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL, proposed_via TEXT, created_at TEXT NOT NULL, decided_at TEXT, result TEXT);
CREATE INDEX IF NOT EXISTS assistant_actions_owner ON assistant_actions(owner, status);
"""


def ensure_schema(c):
    c.executescript(SCHEMA)
    have = {r[1] for r in c.execute("PRAGMA table_info(assistant_actions)")}
    # ask: the ask a card carries to someone who may (backend/settings_asks.py), settled with the card.
    for column in ("confirm_hash", "running_since", "description", "diff", "proposer", "ask"):
        if column not in have:
            c.execute("ALTER TABLE assistant_actions ADD COLUMN %s TEXT" % column)


# ------------------------------------------------------------------ what the assistant may write directly
# What an assistant turn may write on its own is what stays inside the team: creating a task (for its person, a bot or
# another person), updating its person's own tasks but not settling them, comments on tasks no other person is on,
# messages to bots, marking updates read, quiet notes. A message for another person, run-now, spend, access, archive
# and the rest are proposals. The owner's rule "Assistant acts without asking" off (backend/team_rules.py) narrows it
# to what only touches the person.
_SETTLES = ("done", "closed", "declined")


def _mine(actor, value):
    """The full actor id only (`human:ben`): a bare name could be a bot's slug."""
    return str(value or "").strip() == actor


def write_allowed(method, path, settings=None, body=b"", actor="", owns=None, direct=True, to_bot=None):
    """`owns(task_id, shared)` says the task is the person's alone: they own it, no bot is on it (owner, requester,
    origin) and none is delegated, so a comment or update wakes nobody; `shared` also lets bots be on it, but no other
    person. `to_bot(name)` says a recipient is a bot. `direct` is the team's rule: without it, only the person's own."""
    if method in ("GET", "HEAD", "OPTIONS"):
        return True
    if method != "POST":
        return False
    if path == "/api/v2/pages":
        # The page writer authenticates the request and records the person as author.
        return True
    # Saving what the person taught it (backend/learned.py checks the layer: theirs, or the company's for the owner).
    if path in ("/api/v2/updates/read", "/api/v2/changelog/read", "/api/notes", "/api/v2/assistant/actions",
                "/api/v2/assistant/read-back", "/api/v2/assistant/learned", "/api/v2/goals/read-back"):
        return True
    # Asking the settings catalogue for a change's plan changes no setting: it answers with the request, a refusal or a
    # Confirm card in the person's chat (which `create_proposal` checks as any card), and the change itself meets the
    # checks below.
    if re.fullmatch(r"/api/v2/settings/catalog/[^/]+/plan", path):
        return True
    # Taking back a card the Assistant itself proposed (backend/assistant.py action_withdraw): it only ever stops one.
    if re.fullmatch(r"/api/v2/assistant/actions/[^/]+/withdraw", path):
        return True
    # Undoing one of the person's own Assistant changes (backend/assistant_changes.py): the route takes a person only, and
    # `restore` checks each change against that person's own rights, so the undo runs on the person's cited turn.
    if re.fullmatch(r"/api/v2/assistant/changes/[^/]+/restore", path):
        return True
    # Asking someone who can (backend/settings_asks.py) changes nothing either: it puts a card in front of a person who
    # may, and the route itself requires the person's cited words.
    if re.fullmatch(r"/api/v2/settings/catalog/[^/]+/ask", path):
        return True
    # Queuing mail does not deliver it: the shared send gate owns approval, undo, revocation and dispatch.
    if path == "/api/v2/email/sends" or re.fullmatch(r"/api/v2/email/sends/[^/]+/cancel", path):
        return True
    # Reversible Gmail labels; the route checks the person's mailbox level and the company organize switch.
    if path == "/api/v2/mail/messages/organize":
        return True
    # The mail tool's org rule check before a send, and its report of checks Tico never answered, change nothing: a
    # person's own mail is never checked (backend/org_rule_check.py), and the send itself meets its own checks.
    if re.fullmatch(r"/api/v2/bots/[^/]+/mail-check(/misses)?", path):
        return True
    try:
        fields = json.loads(body or b"{}")
    except ValueError:
        return False
    if not isinstance(fields, dict):
        return False
    if path == '/api/v2/inbound/wait':
        return True  # a non-consuming read carried as POST
    if direct and path == '/api/v2/inbound/inboxes' and fields.get('kind','email') == 'email':
        return True  # free address allocation; quota and identity still apply in inbound.py
    if direct and to_bot:
        chatting = re.fullmatch(r"/api/v2/chat/([^/]+)", path)
        if (chatting and (settings is None
                          or unquote(chatting.group(1)).removeprefix("bot:")
                          not in (settings.assistant_bot, strategy_bot.SLUG))) or (
                path == "/api/v2/messages" and to_bot(fields.get("to"))):
            return True
    commenting = re.fullmatch(r"/api/v2/tasks/([^/]+)/comments", path)
    if commenting:
        return bool(owns and owns(commenting.group(1), direct))
    creating = path in ("/api/v2/tasks", "/api/v2/tasks/dry-run")
    updating = re.fullmatch(r"/api/v2/tasks/([^/]+)", path)
    if not (creating or updating) or fields.get("close") or fields.get("status") in _SETTLES:
        return False
    if creating:
        # A new task is reversible and the route still checks the owner and the plain-ask rules, so creating one
        # for another person (named by their full actor id, as in `_mine`) runs directly, like one for a bot.
        owner = str(fields.get("owner") or "").strip()
        return _mine(actor, owner) or bool(direct and (owner.startswith("human:")
                                                       or (to_bot and to_bot(owner))))
    if "owner" in fields and not _mine(actor, fields["owner"]):
        return False
    return bool(owns and owns(updating.group(1), False))


# ------------------------------------------------------------------ what the person asked for in their own chat
# A write the person's own Assistant run makes runs without a card when the turn cites the message that started it: the
# run cites it itself (`X-Tico-On-Behalf-Of` names another message, or `turn`, the one that started the run). The server
# checks the message, not the words: the person's own, typed in their own Assistant room, in the last INSTRUCTION_TTL_S.
# The turn is the person, so their rights stay the ceiling: the route's own checks run as them. Only what `NEVER` names
# stays a card or the person's own tap. A decline is undone by asking again. Approving (send, spend, publish, merge) is a
# click on a card unless the person turned on "My assistant can decide approvals for me" (`decides`, below) and the team
# allows it; then an approval addressed to them is decided on their words too (`approval_words`). When the owner's rule
# "Assistant acts without asking" is off, all of it is a card again.
INSTRUCTION_TTL_S = 30 * 60
# The steps a bulk card (`POST /api/v2/assistant/batch`) may hold: task changes, comments, links, approval decisions and
# answers. A card is still checked against this list, whatever a turn may do on its own.
_INSTRUCTED = [re.compile(p) for p in (r"/api/v2/tasks/[^/]+", r"/api/v2/tasks/[^/]+/(comments|links)",
                                       r"/api/v2/messages/[^/]+/answer")]
# An automation's change and an org rule's add or retire may sit on a bulk card too (one card holds the closes and the
# rule stop). Each runs as the person through its own route, and must pass `admits_on_turn` like any other write.
_CARD_OTHER = [("PATCH", re.compile(r"/api/v2/automations/[^/]+")), ("POST", re.compile(r"/api/v2/rules")),
               ("POST", re.compile(r"/api/v2/rules/[^/]+/retire"))]
_DECIDING = re.compile(r"/api/v2/approvals/([^/]+)")
DECIDES_KEY = "assistant.decides_approvals"


def decides(c, actor):
    """Whether this person's own Assistant may decide approvals addressed to them: their own switch in Settings, on by
    default and turned off by them (an explicit off row stays off), and the team's rule (backend/team_rules.py), on
    unless the owner turned it off."""
    from . import team_rules
    row = c.execute("SELECT value FROM preferences WHERE actor=? AND key=?", (actor, DECIDES_KEY)).fetchone()
    on = json.loads(row[0]) is True if row else True
    allowed = team_rules.load(c)["assistant_approvals"]
    return {"decides_approvals": on, "team_allows": allowed, "active": on and allowed}


def approving(method, path, body=None):
    """Whether this request approves an approval (as opposed to declining one, or anything else)."""
    return (method == "POST" and bool(_DECIDING.fullmatch(normalize_path(path) or ""))
            and _fields(body).get("decision") == "approved")


def approve_hint(settings, state):
    """What the Assistant is told when it may not approve: the one sentence it passes on to the person."""
    where = "Settings > General"
    if not state["team_allows"]:
        return ("Approving stays the person's click on a card: the team's owner turned off assistants deciding "
                "approvals (Settings > Humans). Tell them that in one sentence and propose a card (`hub assistant "
                "propose`)")
    return ("Approving is the person's click on a card until they let you decide approvals. Tell them in one sentence: "
            f"they can turn on “My assistant can decide approvals for me” in {where}. Then propose a card "
            "(`hub assistant propose`)")


def _fields(body):
    """A request body as a dict, whether it arrives as bytes or already parsed; anything else is empty."""
    if isinstance(body, (bytes, str)):
        try:
            body = json.loads(body or b"{}")
        except ValueError:
            return {}
    return body if isinstance(body, dict) else {}


def canonical(path):
    """The canonical spelling of a request path (`route_renames`): an old spelling (`/runners/{id}/...`) is checked as the
    route it answers, so no gate check is skipped by using the old name. None when the path is not one."""
    from .route_renames import new_path
    p = normalize_path(path)
    return (new_path(p) or p) if p else None


def catalogue_setting(method, path, body=None):
    """Whether this write is one setting from the catalogue, exactly as `hub settings set` makes it (backend/settings_
    catalog.py). Its class decides it in `require_direct_setting`. Two settings at once, or anything the catalogue does
    not list, is not one."""
    from . import settings_catalog
    path = canonical(path)
    if isinstance(body, (bytes, str)):
        try:
            body = json.loads(body or b"{}")
        except ValueError:
            return False
    if not isinstance(body, dict):
        return False           # a list or a bare value is no catalogue entry's body: never read as an empty one
    return bool(path and settings_catalog.matched(method, path, body))


def require_direct_setting(c, method, path, body, trial=None, base=""):
    """403 when the setting this cited write changes is one only the person may change themselves (class `person`). A
    direct or a confirm setting runs on the person's cited words: the route's own check decides who may change it. The
    class is read against what is stored (a wake-rule update keeps the stored rule's action; a repository change is tried
    as the person's own click, `trial`), with the same full link (`base`, the public URL)."""
    from . import settings_catalog
    found = settings_catalog.stored_class(c, method, canonical(path) or "", _fields(body), trial)
    if found and found[2] != "person":
        return
    e = found[0] if found else None
    if not e:
        raise Problem("confirm_required", "That setting needs the person's own yes", 403)
    link = (base.rstrip("/") + "/" + e.card) if base else e.card
    raise Problem("person_only", (e.why or "That setting needs the person's own yes") + ": open " + link, 403)


# What a person's own Assistant turn never makes as them, even on their cited words: each stays a Confirm card, or the
# person's own tap in Tico or Settings. Every other write the turn makes is admitted (`admits_on_turn`), and the route's
# own checks, run as the person, decide the rest. (method, route); a method of None is any method.
NEVER = [(m, re.compile(p)) for m, p in (
    # A personal token, a service key, a stored credential (its value, its grants, its import and its deletion) is issued,
    # shown or revoked by the person in Settings or Credentials, never through a chat. Every route that writes a
    # credential or a grant is here, by its canonical path (`admits_on_turn` renames the old spellings first), and
    # tests/test_assistant_acts_as_you.py walks app.routes so a new one cannot slip past.
    ("POST", r"/api/v2/me/tokens(/[^/]+/revoke)?"),
    ("POST", r"/api/v2/service-keys(/[^/]+/revoke)?"),
    (None, r"/api/v2/credentials(/.*)?"), (None, r"/api/v2/credential-set"),
    # HQ's cloud-machine support switch grants access just like the support_access team rule.
    ("PUT", r"/api/v2/cloud-machines/support"),
    (None, r"/api/v2/credential-imports(/.*)?"), (None, r"/api/v2/runner-credential-(migration|imports)(/.*)?"),
    (None, r"/api/v2/credential-requests/[^/]+/save"),
    (None, r"/api/v2/bots/[^/]+/agent-credential(/revoke)?"),
    (None, r"/api/v2/github/app/replace-lost-key"),
    (None, r"/api/v2/triggers/[^/]+/signing"),
    # A computer's sign-in and its keys: its logins, logouts, shared key and keys (`model_login`, `computer_keys`), and a
    # model subscription's connect. Its enrollment and pairing codes are the person's own tap too.
    (None, r"/api/v2/computers/[^/]+/(shared-key|logins(/.*)?|logouts(/.*)?|keys(/.*)?)"),
    (None, r"/api/v2/runner-logins/[^/]+/(report|subscription)"), (None, r"/api/v2/model-subscriptions(/.*)?"),
    (None, r"/api/v2/computer-subscriptions"), (None, r"/api/v2/runner-model-subscription-refresh"),
    ("POST", r"/api/v2/enrollments"), ("POST", r"/api/v2/runners/enroll"), ("POST", r"/api/v2/agents/pairings(/.*)?"),
    (None, r"/api/v2/agents/pairings(/.*)?"),
    # A message the person wrote is theirs: a turn never edits, deletes or redacts it, so a "No" cannot become a "yes"
    # after the read-back (`instruction` and `approval_words` also refuse a message that was edited).
    ("PATCH", r"/api/v2/messages/[^/]+"), ("DELETE", r"/api/v2/messages/[^/]+"),
    ("POST", r"/api/v2/messages/[^/]+/redact"),
    # A person's password and a reader's invite link: each carries a credential the chat would hold.
    ("POST", r"/api/v2/access/(people|humans)/[^/]+/password"), (None, r"/api/v2/audiences/[^/]+/invites(/.*)?"),
    (None, r"/api/v2/setup/company/people/[^/]+/invite"), (None, r"/api/v2/access/(humans|people)/[^/]+/invite"),
    # A computer's adoption and removal (its enrolment is the person's own tap, and a removal ends its sign-ins).
    (None, r"/api/v2/runners/adopt"), (None, r"/api/v2/computers/[^/]+/removals(/.*)?"),
    # A token for GitHub, a webhook's signing secret and the wake-URL secret: credentials, never a chat's to set.
    (None, r"/api/v2/github/token"), (None, r"/api/v2/webhooks/[^/]+/rotate"), (None, r"/api/v2/hooks/wake-url"),
    # Connections to an outside account: a mail sign-in, the bank and the books, a meeting provider, an importer's login
    # and the postal intake.
    (None, r"/api/v2/mail/google/connect"), (None, r"/api/v2/finance/(connections|books/connect)(/.*)?"),
    # The Google Workspace's setup: its setup codes, key, key keep and check, its removal, and the OAuth client. Its
    # owner's own sign-in (backend/google_workspace.py), never a chat's.
    (None, r"/api/v2/google/workspace(/.*)?"), (None, r"/api/v2/google/oauth-client"),
    # What Tico may do in Google: Send mail lets every bot write outside, a person's own word as a bot's Mail sending is.
    (None, r"/api/v2/google/permissions"),
    # Who can use a mailbox (Settings > Inboxes, backend/mailboxes.py): connecting one, sharing it, each access and
    # stopping it are the person's own tap, never a chat's.
    (None, r"/api/v2/mailboxes(/.*)?"),
    (None, r"/api/v2/letters/(settings|outboxes/.*|bots/.*/settings)"),
    (None, r"/api/v2/postal/addresses/[^/]+/grants/(bot|person)/[^/]+"),
    ("PUT", r"/api/v2/postal/settings"),
    # Dispatch leases and permits belong to the authenticated computer, never a chat.
    (None, r"/api/v2/connectors/email(/.*)?"),
    # A computer's part of a Connect Google sign-in and its Workspace tokens: computers only, never a chat's.
    (None, r"/api/v2/connectors/mail/google/.*"), (None, r"/api/v2/connectors/mail/workspace/token"),
    ("POST", r"/api/v2/live-meetings"), ("POST", r"/api/v2/meeting-importers/[^/]+"), (None, r"/api/v2/postal/intake"),
    # Subscriptions: the owner's own tap.
    (None, r"/api/v2/subscriptions(/.*)?"), (None, r"/api/v2/runner-subscription-refreshes(/.*)?"),
    # Single sign-on, the directory and its SCIM token, and the OAuth sign-in of an agent.
    (None, r"/api/v2/directory(/.*)?"), (None, r"/api/v2/oauth(/.*)?"),
    # Handing the company to another owner.
    (None, r"/api/v2/access/owner(/.*)?"),
    # The Assistant's own switches: whether it decides approvals (the person's own sign-in only) and whether it is on.
    (None, r"/api/v2/assistant/settings"), ("POST", r"/api/v2/assistant/turn-on"),
    # The company's AI provider keys and config (a credential): the owner's own sign-in, never a chat.
    (None, r"/api/v2/providers(/.*)?"),
    # A raw SQL query over the database is not a chat action: the Assistant reads through its own tools, privacy-checked
    # per run, and a query is never made for it from a turn.
    (None, r"/api/v2/sql"),
    # A Tico support report or ticket goes outside the company: the person's own tap.
    (None, r"/api/v2/support/(tickets(/.*)?|feedback)"),
    # Deleting a person, the company's server connection or its storage.
    ("DELETE", r"/api/v2/(people|humans)/[^/]+"), ("DELETE", r"/api/v2/company/server-connection"),
    ("DELETE", r"/api/v2/system/storage"),
)]
# The team's rules that decide the Assistant itself (backend/team_rules.py): only the owner's own sign-in changes them.
ASSISTANT_RULES = ("assistant_direct", "assistant_approvals")


# The automation actions that send something outside the company (backend/automation_actions.py): a webhook, a reply
# sent to mail, a forward. An automation that holds one is never turned on or changed by a turn on the person's words.
OUTSIDE_ACTIONS = ("call_webhook", "send_reply", "forward")


def _sends(flow):
    """Whether a flow (stored text, or the parsed JSON of a request's `flow` or `steps`) holds an outside action."""
    text = flow if isinstance(flow, str) else json.dumps(flow or "")
    return any(action in text for action in OUTSIDE_ACTIONS)


def _body_sends(fields):
    """Whether a write's body holds an outside action in the fields that become its steps. The name, trigger and batch
    send nothing, and checking the whole body would make a rename to "Forward the invoices" a card."""
    return any(_sends(fields.get(key)) for key in ("flow", "steps"))


def automation_sends_outside(store, method, path, body=None):
    """Whether this write makes or changes an automation that sends something outside (its steps hold one of
    `OUTSIDE_ACTIONS`): a create whose body holds one, a change to a stored one (its stored flow, or the body's steps), a
    run of one, and a restore of a version that holds one. Only the person's own tap does those, so a turn's change is a
    card (`confirm_required`)."""
    p = canonical(path) or ""
    fields = _fields(body)
    if p == "/api/v2/automations":
        return method == "POST" and _body_sends(fields)
    if method not in ("PATCH", "PUT", "DELETE", "POST"):
        return False
    restore = re.fullmatch(r"/api/v2/automations/([^/]+)/versions/(\d+)/restore", p)
    if restore:
        with store.read() as c:
            row = c.execute("SELECT flow FROM automation_versions WHERE automation_id=? AND n=?",
                            (restore.group(1), int(restore.group(2)))).fetchone()
        return bool(row) and _sends(row["flow"])
    m = re.fullmatch(r"/api/v2/automations/([^/]+)(?:/run|/delete)?", p)
    if not m:
        return False
    with store.read() as c:
        row = c.execute("SELECT flow FROM automations WHERE id=?", (m.group(1),)).fetchone()
    stored = str(row["flow"] or "") if row else ""
    return _sends(stored) or _body_sends(fields)


def automation_needs_confirm(store, method, path, body=None):
    from . import assistant_routines
    decision = assistant_routines.gate(store, method, canonical(path) or "", _fields(body))
    return decision if decision is not None else automation_sends_outside(store, method, path, body)


def settings_route_unmatched(method, path, body=None):
    """Whether this write goes to a settings catalogue route but is not exactly one catalogue setting (a list or a bare
    value, two settings at once, or a key the setting does not have). Such a write is a card: the gate cannot tell
    which setting it changes, so it cannot tell whether only the person may change it. A route can also carry writes
    that name none of its catalogue keys (a person's reports_to on the people route, whose catalogue entry is `left`):
    those are no setting's write, so they are admitted on the turn like any other, and the route's own checks decide."""
    from . import settings_catalog
    p = canonical(path)
    if not p or not settings_catalog.routed(method, p):
        return False
    fields = _json(body) if isinstance(body, (bytes, str)) else body
    if settings_catalog.matched(method, p, fields) is not None:
        return False
    if not isinstance(fields, dict):
        return True
    named = set()
    for e, _ in settings_catalog.routed(method, p):
        if not e.field:
            return True           # the whole body is the value, so any object may be one: a card
        named.add(e.field.partition(".")[0])
        named.update(e.extra)
    return bool(named & set(fields))


def admits_on_turn(method, path, body=None):
    """Whether a person's own Assistant turn may make this write as them, on their cited words. Every write is admitted
    except what `NEVER` names, a body that carries a secret (a secret never goes through a chat: BotOps' rule, which
    botops_act checks the same way), and the team's rules for the Assistant itself. The approvals are not decided here:
    an approve runs only on its read-back and the person's yes (`approval_words`)."""
    from . import botops_act, team_rules
    path = canonical(path)
    if not path or method not in ("POST", "PUT", "PATCH", "DELETE"):
        return False
    # NEVER is written against the canonical spelling: an old route (`/runners/{id}/shared-key`) answers the same way.
    if any(m in (None, method) and p.fullmatch(path) for m, p in NEVER):
        return False
    parsed = _json(body)
    # The team's rules: a turn may change the ordinary ones (`{"stale_review": false}`), never the ones that grant access to
    # credentials, tokens, the SQL page or support, nor the Assistant's own (backend/team_rules.py).
    if path == "/api/v2/access/rules" and isinstance(parsed, dict) and any(
            parsed.get(k) is not None for k in ASSISTANT_RULES + team_rules.CREDENTIAL_RULES):
        return False
    # `secret_in_request` is the one decision for a body: on a tool route it admits a `${VAR}` placeholder that
    # `secret_in` alone would reject, and on every other route it is `secret_in`.
    if isinstance(parsed, dict):
        return not botops_act.secret_in_request(method, path, parsed)
    return botops_act.secret_in(parsed) is None


def _json(body):
    """A request body as JSON, whether it arrives as bytes or already parsed; a body that is not JSON is `{}`."""
    if isinstance(body, (bytes, str)):
        try:
            return json.loads(body or b"{}")
        except ValueError:
            return {}
    return body if body is not None else {}


def instructed_route(method, path, body=None, decides=False):
    """Whether a bulk card may hold this step: a task change, comment, link or answer, or an approval decision (`decides`:
    an approve, which the card's own click covers). A card is checked against this whatever a turn may do on its own."""
    path = normalize_path(path)
    if not path or path == "/api/v2/tasks/dry-run":
        return False
    if any(m == method and p.fullmatch(path) for m, p in _CARD_OTHER):
        return admits_on_turn(method, path, body)
    if method != "POST":
        return False
    if _DECIDING.fullmatch(path):
        return _fields(body).get("decision") == "declined" or (decides and _fields(body).get("decision") == "approved")
    return any(p.fullmatch(path) for p in _INSTRUCTED)


# Approving is the person's own decision. The Assistant may put an approve on a card only when the person's message in
# their chat names it; "clear my queue" or "close 50 of these" declines, dismisses and closes, and never approves.
APPROVING = re.compile(r"\b(approv\w*|merge[sd]?|merging|publish\w*|send|sending|spend\w*|pay|paying)\b", re.I)


def approvals_in(path, body):
    """The approvals this operation (or bulk card) would approve."""
    steps = body.get("actions") if path == BATCH_PATH and isinstance(body, dict) else [{"path": path, "body": body}]
    return [m.group(1) for s in steps or [] if isinstance(s, dict)
            and (m := _DECIDING.fullmatch(str(s.get("path") or ""))) and _fields(s.get("body")).get("decision") == "approved"]


def require_addressed(c, who, path, body):
    """403 unless every approval and question this operation (or bulk card) decides or answers was put to this person:
    a card never lists, let alone decides, someone else's."""
    steps = body.get("actions") if path == BATCH_PATH and isinstance(body, dict) else [{"path": path}]
    for s in steps or []:
        target = str(s.get("path") or "") if isinstance(s, dict) else ""
        if (m := _DECIDING.fullmatch(target)):
            row = H.approval(c, m.group(1)) or {}
            refuse_own_click(row)
            message_id = row.get("message_id")
        elif (m := re.fullmatch(r"/api/v2/messages/([^/]+)/answer", target)):
            message_id = m.group(1)
        else:
            continue
        if (H.message(c, message_id) or {}).get("recipient_id") != who.actor:
            raise Problem("forbidden", "That approval or question is not addressed to you", 403)


def refuse_own_click(row):
    """A Company page edition or stats board is decided only by the person's own click in Tico, either way, whatever
    "My assistant can decide approvals for me" says: not on a card, not on their words (backend/company_page/editions.py)."""
    from .company_page.editions import own_click_approval
    if own_click_approval(row.get("payload")):
        raise Problem("approve_own_click", "What the Company page sends outside is decided only by the person's own "
                      "click in Tico, so leave it out and tell them it is waiting", 403)


def require_named_approval(c, who, ref):
    """403 unless the person's cited message (default: the one that started this run) asks to approve."""
    cited = instruction(c, who, ref or "turn")["cited"]
    if not APPROVING.search(str((H.message(c, cited) or {}).get("body") or "")):
        raise Problem("approve_not_asked", "Approving is the person's own decision: their message must name it (approve, "
                      "merge, publish, send, spend). Decline, answer or close what they asked for, and leave the "
                      "approvals for them", 403)


# Approving on the person's words is bound to a list they saw: the Assistant first posts its read-back (`read_back`,
# below), which the server writes whole from the approvals themselves and records with their ids. Then the person's plain
# yes, with nothing else from the Assistant in between, approves exactly those, never anything else addressed to them.
AFFIRM = re.compile(r"(yes|yep|yeah|y|ok|okay|sure|go ahead|go|do it|please do|confirm(ed)?|correct|that'?s right)"
                    r"\b[\s,.!]*(please|thanks|thank you|go ahead|do it)?[\s.!]*", re.I)
READ_BACK = "approval.read_back"
# Unicode's Default_Ignorable_Code_Point characters that are not format (Cf) characters: variation selectors, Hangul
# fillers and the like render as nothing.
IGNORABLE = re.compile("[͏ᅟᅠ឴឵᠋-᠏⁥ㅤ︀-️ﾠ￰-￸"
                       "\U000e0000-\U000e0fff]")


def plain_line(text):
    """One read-back line, shown exactly as written: a requesting bot's payload can't add lines, a footer or a question,
    hide part of the line behind markup (an HTML comment opened in one field and closed in another), or reorder it with
    invisible format characters."""
    text = "".join(ch for ch in str(text) if unicodedata.category(ch) != "Cf" and not IGNORABLE.fullmatch(ch))
    return re.sub(r"\s+", " ", text).strip().replace("<", "‹").replace(">", "›")


# Known fields first, in this order; every other payload key follows, sorted, so a field added later is shown, not
# left out. Only the hashes are not: the person can't check them and the fields beside them say what they cover.
APPROVAL_FIELDS = ("amount", "to", "cc", "mailbox", "repo", "pr", "url", "account", "subject", "title", "what",
                   "attachments")
# A read-back cuts nothing: an approval with a field (or a key) longer than this stays the person's click.
FIELD_LIMIT = 400
KEY = re.compile(r"[A-Za-z0-9_]{1,40}")
QUOTES = '"“”„‟″〝〞〟＂«»'


def hidden_key(key):
    return str(key).endswith("sha256") or key == "file_id"


def field_text(value):
    """One payload value written out whole: a list joined with ", ", a file as its name and size, any other object as
    its key=value pairs; never a hash. A size that is not a whole number is shown as size=..., never "(... bytes)"
    where text could pass for a second file."""
    if isinstance(value, (list, tuple)):
        return ", ".join(field_text(v) for v in value)
    if isinstance(value, dict):
        sized = "name" in value and type(value.get("size")) is int
        rest = " ".join(f"{k}={field_text(v)}" for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
                        if not hidden_key(k) and k != "name" and not (k == "size" and sized))
        head = (field_text(value["name"]) + (f" ({value['size']} bytes)" if sized else "")
                if "name" in value else "")
        return " ".join(x for x in (head, rest) if x)
    return str(value)


def field_value(value, limit=FIELD_LIMIT):
    """One payload value as a card or read-back shows it: one plain line, at most `limit` characters, no quotes of its
    own to close the ones around it."""
    text = plain_line(field_text(value)).translate({ord(q): "'" for q in QUOTES})
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def approval_fields(payload):
    """The payload keys a card or read-back shows, known ones first."""
    shown = [k for k in payload if not hidden_key(k)]
    return [k for k in APPROVAL_FIELDS if k in shown] + sorted(str(k) for k in shown if k not in APPROVAL_FIELDS)


def shows_blank(value):
    """Whether part of a value has something in it yet reads back as nothing (a file with only hidden keys)."""
    if isinstance(value, (list, tuple)):
        return any(shows_blank(v) for v in value)
    if isinstance(value, dict):
        return (bool(value) and not plain_line(field_text(value))) or any(
            shows_blank(v) for k, v in value.items() if not hidden_key(k))
    return value not in (None, "") and not plain_line(field_text(value))


def too_long(payload):
    """The fields a read-back could not show whole (or whose key is not a plain name, or that read back as nothing):
    the approval stays a click."""
    return [str(k) for k in payload if not hidden_key(k) and (
        not KEY.fullmatch(str(k)) or field_value(payload[k], 10 ** 6) != field_value(payload[k])
        or shows_blank(payload[k]))]


def approval_id(path):
    m = _DECIDING.fullmatch(normalize_path(path) or "")
    return m.group(1) if m else None


def edited(c, msg):
    """Whether a message was changed after it was posted: an edit or a delete (`edited_at`, a revision above zero). The
    post itself writes revision 0 into `message_revisions` (backend/threads.py `record`), so only a row above zero counts.
    The person's words are what they posted, so an edited message lends nothing."""
    if msg.get("edited_at") or int(msg.get("revision") or 0) > 0:
        return True
    return bool(c.execute("SELECT 1 FROM message_revisions WHERE message_id=? AND revision>0 LIMIT 1",
                          (msg["id"],)).fetchone())


def approval_words(c, who, cited, assistant_bot, approval):
    """What an approval the Assistant decides for the person is recorded with: their words and the read-back they
    answered. The cited message must be a plain yes, the last thing anyone else said in the room before it (since the
    person's previous message) the read-back the server wrote, listing this approval, and nothing the person wrote
    after it: "wait, no" takes it back. Their own sentence, words in a file, a page, a bot's message or the read-back
    alone never approve, and the Assistant never approves what it asked for itself."""
    bot = "bot:" + assistant_bot
    if (H.approval(c, approval) or {}).get("requested_by_id") == bot:
        raise Problem("own_request", "You asked for this approval yourself, so it stays the person's click: propose it "
                      "on a card (`hub assistant propose`)", 403)
    msg = H.message(c, cited) or {}
    if msg and edited(c, msg):
        raise Problem("approve_not_asked", "That message was edited after it was posted, so it is no yes: post the "
                      "read-back again and ask the person", 403)
    words = str(msg.get("body") or "").strip()
    room, at = msg.get("conversation_id"), str(msg.get("created_at") or "")
    back = None
    later = c.execute("SELECT 1 FROM messages WHERE conversation_id=? AND sender_id=? AND created_at>? AND id<>? "
                      "AND deleted_at IS NULL LIMIT 1", (room, who.actor, at, cited)).fetchone()
    if len(words) <= 60 and AFFIRM.fullmatch(words) and not later:
        before = c.execute("SELECT created_at FROM messages WHERE conversation_id=? AND sender_id=? AND created_at<? "
                           "AND id<>? AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 1",
                           (room, who.actor, at, cited)).fetchone()
        last = c.execute("SELECT m.id, m.sender_id, m.body, e.detail FROM messages m LEFT JOIN events e "
                         "ON e.target=m.id AND e.action=? WHERE m.conversation_id=? AND m.sender_id<>? AND m.created_at>=? AND m.created_at<=? "
                         "AND m.id<>? AND m.deleted_at IS NULL ORDER BY m.created_at DESC, m.rowid DESC LIMIT 1",
                         (READ_BACK, room, who.actor, before["created_at"] if before else "", at, cited)).fetchone()
        if last and last["sender_id"] == bot and last["detail"]:
            back = {"body": last["body"], "approves": (json.loads(last["detail"]) or {}).get("approves") or []}
    if not back or approval not in back["approves"]:
        raise Problem("approve_not_asked", "Approving needs the person's yes to your read-back: post the list with "
                      "`hub approval read-back <id>...` as your reply and end the turn; their plain yes to it approves "
                      "exactly what it lists", 403)
    return {"words": preview(words, 300), "read_back": back["body"], "approves": back["approves"]}


def instruction(c, who, ref):
    """The delegation record (backend/hubdb.py DELEGATION) for the person's own message that asked for this write, or a
    403 saying why it lends nothing. `ref` is the message's id, or `turn`: the message that started this run."""
    def refuse(detail):
        raise Problem("on_behalf_of", detail + "; if they did not clearly ask, propose it (`hub assistant propose`)", 403)
    if who.via != "assistant" or who.confirmed or not who.attempt_id or not H.is_human(who.actor):
        refuse("Only an Assistant run in the person's own chat acts on what they asked")
    message_id = str(ref or "")
    if message_id == "turn":
        row = c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?",
                        (who.attempt_id,)).fetchone()
        message_id = row["message_id"] if row else ""
    msg = H.message(c, message_id) if message_id else None
    if msg and edited(c, msg):
        refuse("That message was edited after it was posted, so it does not say what they asked")
    room = H.conversation(c, msg["conversation_id"]) if msg else None
    refs = (msg or {}).get("refs") or {}
    # Only the person's own Assistant room: the owner's earlier Strategy Bot chat (`rooms.STRATEGY_ROOM`) is history.
    if (not msg or msg["sender_id"] != who.actor or msg["kind"] != "say" or not refs.get("assistant")
            or refs.get("via") or not room or room.get("scope") != rooms.PERSONAL
            or room.get("owner_id") != who.actor or room.get("room_key") != rooms.ASSISTANT_ROOM
            or room.get("closed_at")):
        refuse("Cite a message the person sent you in their own Assistant chat")
    if str(msg["created_at"]) < H.shift(H.now(), seconds=-INSTRUCTION_TTL_S):
        refuse(f"That message is more than {INSTRUCTION_TTL_S // 60} minutes old; ask the person again")
    return {"for": who.actor, "cited": msg["id"], "run": "asked"}


def make_page_task(c, auth, who, task):
    """A live Assistant attempt may publish only for its person's initiating Make page request."""
    attempt = c.execute("SELECT * FROM attempts WHERE id=?", (who.attempt_id,)).fetchone()
    principal = auth.assistant_principal(c, attempt) if attempt else None
    row = c.execute("SELECT m.* FROM attempts a JOIN jobs j ON j.id=a.job_id "
                    "JOIN messages m ON m.id=j.message_id WHERE a.id=?", (who.attempt_id,)).fetchone()
    message = H.message(c, row["id"]) if row else None
    room = H.conversation(c, message["conversation_id"]) if message else None
    target = (message.get("refs") or {}).get("make_page") if message else None
    if (who.via != "assistant" or not principal or principal.actor != who.actor
            or principal.task_actor != who.task_actor or not message or message.get("deleted_at")
            or message["kind"] != "say" or message["sender_id"] != who.actor
            or message["recipient_id"] != "bot:" + attempt["bot_id"] or edited(c, message)
            or (message.get("refs") or {}).get("via") or not room or room.get("closed_at")
            or not isinstance(target, dict) or target.get("task") != task or not task):
        raise Problem("forbidden", "Publish from the person's own Make page request", 403)
    auth.task(c, who, task)
    return message


def require_on_task(c, auth, who, path):
    """A task change, comment or link the turn makes must be on a task the person may read: a task they may not read is
    a 404, as the route answers it, and the refusal never says it is there. Being on the task is not a limit: the
    person's own rights are, and the route checks them as the person. The owner's Assistant acts as the owner."""
    m = re.fullmatch(r"/api/v2/tasks/([^/]+)(?:/(?:comments|links))?", normalize_path(path) or "")
    if not m or who.role == "owner":
        return
    row = H.task(c, m.group(1))
    if not row:
        return          # the route answers for a task that is not there
    auth.task_row(c, who, row)


# What may be proposed at all (method, normalized path). Anything else, and above all tokens, sign-in,
# runner enrollment, this assistant and anything that hands back a secret, can never be proposed.
_PROPOSABLE = [(m, re.compile(p)) for m, p in (
    ("PUT", r"/api/v2/letters/settings"),
    ("PUT", r"/api/v2/letters/outboxes/company/grants/(bot|person)/[^/]+"),
    ("DELETE", r"/api/v2/letters/outboxes/company/grants/(bot|person)/[^/]+"),
    ("PUT", r"/api/v2/letters/bots/[^/]+/settings"),
    ("POST", r"/api/v2/automations"), ("PATCH", r"/api/v2/automations/[^/]+"),
    ("POST", r"/api/v2/approvals/[^/]+"), ("POST", r"/api/v2/messages/[^/]+/answer"),
    ("POST", r"/api/v2/tasks"), ("POST", r"/api/v2/tasks/[^/]+"),
    ("POST", r"/api/v2/tasks/[^/]+/(comments|links|labels|run-now|ask)"),
    ("POST", r"/api/v2/messages"), ("POST", r"/api/v2/chat/[^/]+"), ("POST", r"/api/v2/notes"),
    ("POST", r"/api/v2/updates/[^/]+/reply"), ("POST", r"/api/v2/assistant/batch"),
    ("POST", r"/api/v2/seats/batch"),
    ("POST", r"/api/v2/bots"), ("POST", r"/api/v2/bots/[^/]+/(archive|definition|owners|co-owners|updates|goals)"),
    ("POST", r"/api/v2/bots/[^/]+/(assignment|placement)"), ("POST", r"/api/v2/credentials/[^/]+/grants"),
    ("POST", r"/api/v2/(?:people|humans)/[^/]+"), ("POST", r"/api/v2/access/(?:people|humans)(/[^/]+)?"),
    ("PUT", r"/api/v2/providers"), ("PATCH", r"/api/v2/files/[^/]+"),
)]
# What only BotOps may propose, for a person who asked it in chat (backend/botops_act.py lists the routes it
# answers with a card): the same click, on more routes.
_PROPOSABLE_BOTOPS = [(m, re.compile(p)) for m, p in (
    ("PUT", r"/api/v2/access/(limits|allow)"), ("PUT", r"/api/v2/usage/limits(/[^/]+)?"),
    ("PUT", r"/api/v2/usage/pools/(company|groups/[^/]+)"),
    ("DELETE", r"/api/v2/automations/[^/]+"), ("POST", r"/api/v2/automations/[^/]+/delete"),
    ("POST", r"/api/v2/(?:runners|computers)/[^/]+/(member-bots|revoke|restart)"),
    ("POST", r"/api/v2/credentials/[^/]+/grants/[^/]+/revoke"), ("POST", r"/api/v2/system/update"),
    ("POST", r"/api/v2/(?:goal-proposals|proposals)/[^/]+/decide"), ("POST", r"/api/v2/support/tickets"),
    ("PUT", r"/api/v2/directory"), ("POST", r"/api/v2/directory/(sync|preview)"),
    ("POST", r"/api/v2/(slack|github/app)/disconnect"),
    ("DELETE", r"/api/v2/files/[^/]+"),
    ("POST", r"/api/v2/access/owner"), ("POST", r"/api/v2/enrollments"), ("POST", r"/api/v2/seats/[^/]+/hire"),
    # A webhook moved to another destination while it holds secret headers (app.py `destination_card`).
    ("POST", r"/api/v2/webhooks/[^/]+/update"),
    # Settings a person may change only with a yes (backend/settings_entries.py, confirm): none hands back a secret, and
    # each route takes the person's tap as their own click.
    ("POST", r"/api/v2/assistant/turn-on"), ("POST", r"/api/v2/system/usage-count/reset"),
    ("DELETE", r"/api/v2/company/server-connection"), ("DELETE", r"/api/v2/system/storage"),
    ("POST", r"/api/v2/system/storage/move"), ("DELETE", r"/api/v2/(?:people|humans)/[^/]+"),
    ("POST", r"/api/v2/settings/turn-levels"), ("POST", r"/api/v2/me/tokens/[^/]+/revoke"),
    ("POST", r"/api/v2/bots/[^/]+/budget"), ("POST", r"/api/v2/strategy-bot/(checks|level)"),
    ("POST", r"/api/v2/bots/[^/]+/cloud-machine(?:/replace)?"), ("POST", r"/api/v2/cloud-machines/shared"),
    ("PUT", r"/api/v2/cloud-machines/support"),
    ("POST", r"/api/v2/postal/orders"),
    ("PUT", r"/api/v2/postal/addresses/[^/]+/grants/(bot|person)/[^/]+"),
    ("DELETE", r"/api/v2/postal/addresses/[^/]+/grants/(bot|person)/[^/]+"),
    ("PUT", r"/api/v2/postal/settings"),
    ("POST", r"/api/v2/github/product-repos"), ("POST", r"/api/v2/tasks/[^/]+/requesters"), ("POST", r"/api/v2/tasks/[^/]+/requesters/[^/]+"),
    ("POST", r"/api/v2/tasks/[^/]+/requesters/[^/]+/delete"), ("POST", r"/api/v2/tasks/[^/]+/links"),
    ("DELETE", r"/api/v2/tasks/[^/]+/links/[^/]+"),
    ("PUT", r"/api/v2/listening/destinations/[^/]+"), ("POST", r"/api/v2/listening/destinations/[^/]+/remove"),
    ("POST", r"/api/v2/roadmaps/settings"), ("POST", r"/api/v2/inbound/inboxes/[^/]+/release"),
    ("POST", r"/api/v2/inbound/phone-requests(/[^/]+/accept)?"),
    # Who reads an inbox (backend/inbound.py `set_audience`): owner and admins, on their own click.
    ("PUT", r"/api/v2/inbound/inboxes/[^/]+/audience"),
)]
# An inbox is an email address in its path (`inboxes/ana@acme.example`): the one `@` a route may carry.
_SHAPE = re.compile(r"/api/v2/(?:inboxes/[A-Za-z0-9_.+\-]+@[A-Za-z0-9.\-]+|[A-Za-z0-9_.:+\-/]+)")


def normalize_path(path):
    """The one path a proposal may name, or None: plain route characters only, no `//`, `..`, `%`, `\\`
    or trailing slash. What is stored and run is exactly this string."""
    path = str(path or "")
    if (not _SHAPE.fullmatch(path) or "//" in path or ".." in path or "%" in path or "\\" in path
            or path.endswith("/")):
        return None
    if any(seg in ("", ".", "..") for seg in path[len("/api/v2/"):].split("/")):
        return None            # a `.` segment is collapsed by the HTTP client: what runs must be what was checked
    return path


def catalogue_card(method, path):
    """Whether the Assistant may put this route on a card as a setting: a catalogue entry that is not person-only writes
    it, and BotOps may card it (the Assistant changes settings with the same rules as BotOps)."""
    from . import settings_catalog
    return (any(e.kind != "person" for e, _ in settings_catalog.routed(method, path))
            and valid_operation(method, path, None, "botops"))


# Routes whose handler accepts only the person's own tap (`owner(request, tap=True)`, backend/tico_storage.py): an
# Assistant or BotOps run of them is refused, so on a turn they are a Confirm card, never a direct run.
TAP_ONLY_ROUTES = [(m, re.compile(p)) for m, p in (
    ("POST", r"/api/v2/system/storage/move"), ("POST", r"/api/v2/strategy-bot/(checks|level)"),
)]


def needs_persons_tap(method, path):
    path = canonical(path)
    return any(m == method and p.fullmatch(path or "") for m, p in TAP_ONLY_ROUTES)


def valid_operation(method, path, settings=None, proposer="assistant"):
    path = normalize_path(path)
    if not path or (settings is not None and path == "/api/v2/chat/" + settings.assistant_bot):
        return False
    routes = _PROPOSABLE + (_PROPOSABLE_BOTOPS if proposer == "botops" else [])
    if any(m == method and p.fullmatch(path) for m, p in routes):
        return True
    if proposer == "assistant" and catalogue_card(method, path):
        return True
    # BotOps also puts to a person, as a card, any change it makes for a person directly (a bot's hand-off that
    # lends nothing: app.py `confirm_for_carried`). It runs only on their click, as them.
    from . import botops_act
    return proposer == "botops" and method != "GET" and any(m == method and p.fullmatch(path) for m, p in botops_act.DO)


PREVIEW = 160


def preview(text, limit=PREVIEW):
    """The start of a long text, cut at a word and marked with an ellipsis, so a short line never reads as the whole."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    return (text[:limit].rsplit(" ", 1)[0] or text[:limit]).rstrip(" ,;:.") + "…"


def days_old(ts):
    at, now = H.parse_ts(ts), H.parse_ts(H.now())
    return max(0.0, (now - at).total_seconds() / 86400) if at and now else 0.0


def age(ts):
    """How long ago an item was asked, as the card says it."""
    days = days_old(ts)
    return "today" if days < 1 else "1 day old" if days < 2 else f"{int(days)} days old"


def _kind(step):
    """What one step of a card is, for counting and grouping: approvals, answers or task actions."""
    path = str(step.get("path") or "")
    return "approval" if _DECIDING.fullmatch(path) else "answer" if path.endswith("/answer") else "task"


def batch_line(steps):
    counts = {}
    for s in steps:
        counts[_kind(s)] = counts.get(_kind(s), 0) + 1
    if set(counts) <= {"task"}:
        return f"{len(steps)} task actions, on one Confirm"
    names = {"approval": ("approval decision", "approval decisions"), "answer": ("answer", "answers"),
             "task": ("task action", "task actions")}
    return (f"{len(steps)} actions, on one Confirm: "
            + ", ".join(f"{n} {names[k][n != 1]}" for k, n in counts.items()))


AGE_BANDS = ((7, "under a week old"), (30, "1 to 4 weeks old"), (None, "over a month old"))


def groups(c, steps):
    """Card lines that sum a bulk card up by what it does and how old the items are ("Decline 11 publish approvals,
    over a month old"), with the reason when the whole group shares one, above the line for every item."""
    out = {}
    for s in steps:
        body, path, created = s.get("body") or {}, str(s.get("path") or ""), None
        if (m := _DECIDING.fullmatch(path)):
            row = H.approval(c, m.group(1)) or {}
            verb = "Approve" if body.get("decision") == "approved" else "Decline"
            noun, created = f"{row.get('kind') or ''} approval".strip(), row.get("created_at")
        elif (m := re.fullmatch(r"/api/v2/messages/([^/]+)/answer", path)):
            verb, noun, created = "Answer", "bot question", (H.message(c, m.group(1)) or {}).get("created_at")
        else:
            m = re.fullmatch(r"/api/v2/tasks/([^/]+)(?:/(comments|links))?", path)
            sub = m.group(2) if m else None
            verb = ({"comments": "Comment on", "links": "Link"}.get(sub) or ("Close" if body.get("close") else None)
                    or {"done": "Mark done", "declined": "Decline"}.get(body.get("status"), "Change"))
            noun, created = "task", (H.task(c, m.group(1)) or {}).get("created_at") if m else None
        days = days_old(created)
        band = next(label for limit, label in AGE_BANDS if limit is None or days < limit)
        out.setdefault((verb, noun, band), []).append(str(body.get("note") or "").strip())
    lines = []
    for (verb, noun, band), notes in out.items():
        shared = notes[0] if notes and all(n == notes[0] for n in notes) else ""
        lines.append({"field": f"{verb} {len(notes)} {noun}{'s' if len(notes) != 1 else ''}, {band}",
                      "new": f"reason: {preview(shared, 160)}" if shared else "each listed below", "group": True})
    return lines


def describe(c, method, path, body):
    """A one-line, server-derived description of what confirming would do (never the bot's words), and
    for a task update the exact field changes."""
    def bot(slug):
        row = H.bot(c, slug) or {}
        return row.get("display_name") or row.get("slug") or slug

    def who(actor):
        a = str(actor or "")
        if a.startswith("human:"):
            return (H.human(c, a[6:]) or {}).get("name") or a[6:]
        if a.startswith("bot:"):
            return bot(a[4:])
        resolved = H.resolve_actor(c, a)
        return who(resolved) if resolved and resolved != a else a
    body = body if isinstance(body, dict) else {}
    from .repositories import plain_request

    def fields(names):
        return ", ".join(spoken(n) for n in names) or "nothing"

    def values(sent):
        """Field names with their short plain values (`paused: yes`), so a title says which way a change goes. A text
        value is a word only when its field holds a name (`model: …`): any other (`all`, `own`, `chosen`) is a setting
        no title can read as English, so only its field is named and the value stays under the card's Details."""
        def plain(k, v):
            if isinstance(v, bool):
                return "yes" if v else "no"
            if v is None:
                return "cleared"
            if isinstance(v, (int, float)):
                return format(v, "g")
            if isinstance(v, str) and k in NAMED and len(v) <= 40 and "\n" not in v and v.strip():
                return v.strip()
            return ""
        return ", ".join(spoken(k) + (f": {plain(k, v)}" if plain(k, v) else "") for k, v in sent.items()
                         if k not in ("expected_revision", "version")) or "nothing"

    def computer(request):
        """The computer a placement names and whether it takes members' bots, from the database."""
        row = c.execute("SELECT label,accepts_member_bots FROM runners WHERE id=?", (str(request.get("runner_id") or ""),)).fetchone()
        if not row:
            return "a computer"
        return row["label"] + (", a computer that takes members' bots" if row["accepts_member_bots"]
                               else ", a computer that does not take members' bots")

    def approval_line(aid):
        row = H.approval(c, aid)
        verb = {"approved": "Approve", "declined": "Decline"}.get(body.get("decision"), "Decide")
        if not row:
            return f"{verb} approval {aid}"
        # What the person says yes to: the amount and target first, each field cleaned, capped and quoted on its own,
        # so a long or crafted field (padding, "card; amount: 9") can't push a later one out or pose as it.
        payload = row.get("payload") or {}
        detail = "; ".join(f"{field_value(k, 40)}: “{field_value(payload[k])}”" for k in approval_fields(payload))
        return (f"{verb} the {row['kind']} approval requested by {who(row['requested_by_id'])}"
                + (f" ({detail})" if detail else "") + f", {age(row['created_at'])}"
                + (f"; reason: {preview(body['note'], 120)}" if body.get("note") else ""))

    def answer_line(mid):
        asked = H.message(c, mid) or {}
        return (f"Answer {who(asked.get('sender_id'))}'s question “{preview(asked.get('body'), 80)}”"
                + (f", {age(asked['created_at'])}" if asked.get("created_at") else "") + f": {preview(body.get('text'), 120)}")
    diff = []
    m = re.fullmatch(r"/api/v2/tasks/([^/]+)", path)
    if m and (t := H.task(c, m.group(1))):
        for k, v in body.items():
            if k not in ("version",) and str(t.get(k) if t.get(k) is not None else "") != str(v if v is not None else ""):
                diff.append({"field": k, "old": t.get(k), "new": v})
        return f"Change task “{t['title']}” ({len(diff)} field{'s' if len(diff) != 1 else ''})", diff
    rules = (
        (r"/api/v2/approvals/([^/]+)", lambda g: approval_line(g[0])),
        (r"/api/v2/messages/([^/]+)/answer", lambda g: answer_line(g[0])),
        (r"/api/v2/tasks", lambda g: f"Create task “{body.get('title', '')}” for {who(body.get('owner'))}"),
        (r"/api/v2/tasks/([^/]+)/(comments|links|labels|run-now|ask)",
         lambda g: f"{g[1].replace('-', ' ').title()} on task “{(H.task(c, g[0]) or {}).get('title', g[0])}”"),
        (r"/api/v2/messages", lambda g: f"Send a message to {who(body.get('to'))}"),
        (r"/api/v2/chat/([^/]+)", lambda g: f"Send a message to {bot(g[0])}"),
        (r"/api/v2/notes", lambda g: f"Leave a note for {who(body.get('to'))}"),
        (r"/api/v2/updates/[^/]+/reply", lambda g: "Reply to a bot's update"),
        (r"/api/v2/assistant/batch", lambda g: batch_line(body.get("actions") or [])),
        (r"/api/v2/bots", lambda g: f"Add bot “{body.get('display_name', '')}”"),
        (r"/api/v2/bots/([^/]+)/archive", lambda g: f"Archive bot {bot(g[0])}"),
        (r"/api/v2/bots/([^/]+)/definition", lambda g: f"Change settings of bot {bot(g[0])}: "
         + fields(k for k in body if k != "expected_revision")),
        (r"/api/v2/bots/([^/]+)/(owners|co-owners|updates|goals)", lambda g: f"Change {g[1]} of bot {bot(g[0])}: "
         + fields(body)),
        (r"/api/v2/bots/([^/]+)/(assignment|placement)", lambda g: f"Place bot {bot(g[0])} on {computer(body)}"),
        (r"/api/v2/credentials/([^/]+)/grants", lambda g: "Give " + ("every computer" if body.get("subject") == "computers" else who(body.get("subject")))
         + " a stored credential"),
        (r"/api/v2/bots/([^/]+)/repositories", lambda g: plain_request(bot(g[0]), body)),
        (r"/api/v2/bots/([^/]+)/(restore|go-live|onboarded|quarantine/clear|limit/retry)", lambda g: {
            "restore": "Bring back bot {}", "go-live": "Put bot {} live", "onboarded": "Mark bot {} as set up",
            "quarantine/clear": "Let bot {} run again (clear its quarantine)",
            "limit/retry": "Let bot {} try again after its spending limit"}[g[1]].format(bot(g[0]))),
        (r"/api/v2/bots/([^/]+)/(model|fallback|tools|routines|access|github-repos|transitions|control)",
         lambda g: f"Change bot {bot(g[0])}'s {spoken(g[1])}" + (": " + values(body) if body else "")),
        (r"/api/v2/bots/([^/]+)/agent-credential(/revoke)?",
         lambda g: ("Revoke" if g[1] else "Make a new") + f" agent credential for bot {bot(g[0])}"),
        (r"/api/v2/repositories/settings", lambda g: {
            "own": "New bots get only their own repository",
            "all": "New bots get every repository ticked in Settings > Repositories"}.get(
                body.get("new_bot_default"), "Change which repositories new bots get")),
        (r"/api/v2/repositories/refresh", lambda g: "Refresh the repository list from GitHub"),
        (r"/api/v2/repositories/([^/]+)/([^/]+)", lambda g: f"Change repository {g[1]}: " + fields(body)),
        (r"/api/v2/credentials/([^/]+)", lambda g: "Delete the stored credential “" + ((c.execute(
            "SELECT name FROM credentials WHERE id=?", (g[0],)).fetchone() or {"name": g[0]})["name"]) + "”"),
        (r"/api/v2/(?:people|humans)/([^/]+)", lambda g: f"Delete {who('human:' + g[0])} from the team for good"
         if method == "DELETE" else f"Edit person {who(g[0])}: " + fields(body)),
        (r"/api/v2/access/(?:people|humans)", lambda g: f"Add {body.get('name') or body.get('email', '')} ({body.get('email', '')}) "
         "to the roster, and let them sign in" + "".join(f"; {k} {body[k]}" for k in ("team", "reports_to", "title") if body.get(k))),
        (r"/api/v2/access/(?:people|humans)/([^/]+)", lambda g: "Change " + who("human:" + g[0]) + ": "
         + ", ".join(f"{spoken(k)} to {v}" for k, v in body.items())),
        (r"/api/v2/providers", lambda g: "Change the company's AI providers"),
        (r"/api/v2/access/(limits|allow)", lambda g: "Change who may sign in or what members may do: " + fields(body)),
        (r"/api/v2/usage/limits(?:/([^/]+))?", lambda g: ("Change the spending limit of " + bot(g[0]) if g[0] else "Change the company's spending limit")
         + ": " + ", ".join(f"{spoken(k[:-4])} {'none' if v is None else '$' + format(v, 'g')}" for k, v in body.items() if k.endswith("_usd"))),
        (r"/api/v2/usage/pools/(?:company|groups/([^/]+))", lambda g: ("Change the combined spending limit of the " + (g[0] + " group" if g[0] else "whole team"))
         + ": " + ", ".join(f"{spoken(k[:-4])} {'none' if v is None else '$' + format(v, 'g')}" for k, v in body.items() if k.endswith("_usd"))),
        (r"/api/v2/(?:runners|computers)/([^/]+)/(member-bots|revoke|restart)", lambda g: {
            "member-bots": "Let a computer take members' bots" if body.get("accepts") else "Stop a computer taking members' bots",
            "revoke": "Remove a computer", "restart": "Restart a computer's runner"}[g[1]]
         + " (" + ((c.execute("SELECT label FROM runners WHERE id=?", (g[0],)).fetchone() or {"label": g[0]})["label"]) + ")"),
        (r"/api/v2/credentials/[^/]+/grants/[^/]+/revoke", lambda g: "Take a stored credential away from a bot"),
        (r"/api/v2/system/update", lambda g: "Update this Tico to the newest version"),
        (r"/api/v2/(?:goal-proposals|proposals)/[^/]+/decide", lambda g: str(body.get("decision") or "Decide").title() + " a goal proposal"),
        # Only a preview: the card lists the whole message among its fields, and what is sent is exactly that.
        (r"/api/v2/support/tickets", lambda g: "Send this to the Tico team: " + preview(body.get("message"))),
        (r"/api/v2/directory(/sync|/preview)?", lambda g: "Change the people directory sync"),
        (r"/api/v2/(slack|github/app)/disconnect", lambda g: "Disconnect " + g[0].split("/")[0].title()),
        (r"/api/v2/tasks/([^/]+)/requesters/([^/]+)/delete", lambda g: "Remove a requester from this task"),
        (r"/api/v2/tasks/([^/]+)/requesters/([^/]+)", lambda g: "Change a requester on this task"),
        (r"/api/v2/tasks/([^/]+)/links", lambda g: ("Remove a link from this task" if body.get("remove") else "Add a link to this task")),
        (r"/api/v2/tasks/([^/]+)/links/([^/]+)", lambda g: "Remove a link from this task"),
        (r"/api/v2/tasks/([^/]+)/requesters", lambda g: "Add " + (who(body.get("actor")) if body.get("actor") else (body.get("name") or body.get("email") or "a contact")) + " as a requester on this task"),
        (r"/api/v2/files/([^/]+)", lambda g: "Delete a file for good" if method == "DELETE" else "Change a file's listing"),
        (r"/api/v2/assistant/turn-on", lambda g: "Turn the Assistant on for the team"),
        (r"/api/v2/system/usage-count/reset", lambda g: "Reset this Tico's install ID for the usage count"),
        (r"/api/v2/company/server-connection", lambda g: "Disconnect this Tico from the Tico company server"),
        (r"/api/v2/system/storage", lambda g: "Stop storing new files in company storage (files already there are "
         "still read from it)"),
        (r"/api/v2/system/storage/move", lambda g: "Copy every file into company storage"
         + (f", about {format(body['expect_bytes'] / 1e9, '.2g')} GB" if isinstance(body.get("expect_bytes"), int)
            else "") + "; nothing is deleted where it was"),
        (r"/api/v2/settings/turn-levels", lambda g: "Change the company's turn levels: " + values(body)),
        (r"/api/v2/me/tokens/([^/]+)/revoke", lambda g: "Revoke the API token “" + ((c.execute(
            "SELECT label FROM human_tokens WHERE id=?", (g[0],)).fetchone() or {"label": g[0]})["label"]) + "”"),
        (r"/api/v2/bots/([^/]+)/cloud-machine", lambda g: f"Change bot {bot(g[0])}'s cloud machine: " + values(body)),
        (r"/api/v2/bots/([^/]+)/cloud-machine/replace", lambda g: f"Replace bot {bot(g[0])}'s cloud machine and delete its disk. The next task starts a fresh one."),
        (r"/api/v2/cloud-machines/shared", lambda g: "Change the shared cloud machine: " + values(body)),
        (r"/api/v2/postal/orders", lambda g: "Order a postal address for the mailbox: " + values(body)),
        (r"/api/v2/bots/([^/]+)/budget", lambda g: f"Change bot {bot(g[0])}'s run budget: " + values(body)),
        (r"/api/v2/strategy-bot/checks", lambda g: ("Turn the Strategy Bot's regular checks on" if body.get("on")
                                               else "Turn the Strategy Bot's regular checks off")),
        (r"/api/v2/strategy-bot/level", lambda g: "Set how much the Strategy Bot does: " + {
            1: "passive", 2: "suggests actions", 3: "acts on its own"}.get(body.get("level"), "a level")),
        (r"/api/v2/github/product-repos", lambda g: f"Create the private, empty GitHub repository "
         f"{body.get('org', '')}/{body.get('name', '')}"),
        (r"/api/v2/listening/destinations/([^/]+)", lambda g: f"Change the listening destination “{g[0]}”: "
         + values(body)),
        (r"/api/v2/listening/destinations/([^/]+)/remove", lambda g: f"Remove the listening destination “{g[0]}”"),
        (r"/api/v2/roadmaps/settings", lambda g: "Change the public roadmap: " + values(body)),
        (r"/api/v2/inbound/inboxes/([^/]+)/release", lambda g: "Release the inbox " + ((c.execute(
            "SELECT address FROM inbound_inboxes WHERE inbox_id=?", (g[0],)).fetchone()
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='inbound_inboxes'").fetchone() else None)
            or {"address": g[0]})["address"]),
        (r"/api/v2/inbound/phone-requests", lambda g: f"Ask for a phone number named “{body.get('name') or 'Mobile number'}”"),
        (r"/api/v2/inbound/phone-requests/([^/]+)/accept", lambda g: "Accept the phone number's price: "
         + (f"${body['price_micros'] / 1e6:,.2f}" if isinstance(body.get("price_micros"), int) else "the price shown")),
    )
    for pattern, fn in rules:
        if (mm := re.fullmatch(pattern, path)):
            return fn(mm.groups()), diff
    return unnamed(c, method, path, bot) + (": " + values(body) if body else ""), diff


# Fields whose text is a name a person reads as is (a model, a title): any other text value is a setting word.
NAMED = frozenset(("model", "fallback", "provider", "name", "display_name", "title", "label", "email", "team"))


def spoken(name):
    """A field or route name as words: `thread_mode` and `go-live` read `thread mode` and `go live`."""
    return re.sub(r"[_\-]+", " ", str(name)).strip()


def unnamed(c, method, path, bot):
    """A plain line for a card whose route has no sentence of its own: what it touches, never the method and path
    (those are under the card's Details)."""
    parts = path[len("/api/v2/"):].split("/") if path.startswith("/api/v2/") else []
    verb = "Remove something from" if method == "DELETE" else "Make a change to"
    if len(parts) > 1 and parts[0] == "bots":
        return f"{verb} bot {bot(parts[1])}" + (f" ({spoken(' '.join(parts[2:]))})" if parts[2:] else "")
    if len(parts) > 1 and parts[0] == "tasks":
        return f"{verb} task “{(H.task(c, parts[1]) or {}).get('title') or 'a task'}”"
    return ("Remove something" if method == "DELETE" else "Make a change") + " in Tico" + (f" ({spoken(parts[0])})" if parts else "")


# Body fields a proposal may never carry: each has its own explicit confirm that a card's click does not replace.
PERSON_ONLY_FIELDS = ("replace_lost_key",)

# One card for a group of Needs-you actions ("close these 20 tasks", "decline the old publish batches"):
# `{"actions": [{method, path, body}, ...]}`, each a task change, comment or link, an approval decision or an answer to
# a bot's question, never anything else. One click runs them in order, each as the person through its own route, and a
# step that fails does not stop the rest.
BATCH_PATH = "/api/v2/assistant/batch"
MAX_BATCH = 100


def batch_steps(body):
    """The steps of a bulk card, checked, or a 422."""
    steps = body.get("actions") if isinstance(body, dict) and set(body) == {"actions"} else None
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_BATCH:
        raise Problem("operation", f"A bulk card carries only `actions`: 1 to {MAX_BATCH} task changes, comments or links", 422)
    out = []
    for step in steps:
        if not isinstance(step, dict) or set(step) - {"method", "path", "body"}:
            raise Problem("operation", "Each action is {method, path, body}", 422)
        method, path, sent = str(step.get("method") or "POST"), normalize_path(step.get("path")), step.get("body") or {}
        deciding = (isinstance(sent, dict) and bool(path and _DECIDING.fullmatch(path))
                    and sent.get("decision") in ("approved", "declined"))
        if (not isinstance(sent, dict) or not (instructed_route(method, path, sent) or (method == "POST" and deciding))
                or any(f in sent for f in PERSON_ONLY_FIELDS)):
            raise Problem("operation", "A bulk card holds only task changes, comments and links, approval decisions, "
                          "answers to bots' questions, automation changes (PATCH /api/v2/automations/<id>) and org rules "
                          "(POST /api/v2/rules, /api/v2/rules/<id>/retire); propose anything else on its own", 422)
        out.append({"method": method, "path": path, "body": {k: v for k, v in sent.items() if k != "on_behalf_of"}})
    return out


def teammate(who):
    """Whether the caller may take a waiting Confirm card back: any signed-in person or bot of the company. Confirming
    stays the card owner's own click (`/confirm`); withdrawing only stops it."""
    return who.role in ("owner", "human", "bot")


def covers(row, method, path):
    """Whether a confirmed proposal covers this request: its own operation, or one step of a bulk card."""
    if row["path"] == BATCH_PATH:
        steps = json.loads(row["body"] or "{}").get("actions") or []
        return any(s.get("method") == method and s.get("path") == path for s in steps)
    return row["method"] == method and row["path"] == path


def runnable(c, method, path, body):
    """A task change as the route takes it. "closed" is not a status a task update sets: closing is `close`, so a card
    that says `{"status": "closed"}` closes (every Confirm of one failed with "body.status: Input should be ..."). One
    without the task's version gets the one it has now (and failed with "body.version: Field required"); a change to
    the task after that is still a conflict, not a silent overwrite."""
    m = re.fullmatch(r"/api/v2/tasks/([^/]+)", path or "")
    if method != "POST" or not m or m.group(1) == "dry-run" or not isinstance(body, dict):
        return body
    if body.get("status") == "closed":
        body = {**{k: v for k, v in body.items() if k != "status"}, "close": True}
    if "version" not in body and (t := H.task(c, m.group(1))):
        body = {**body, "version": t["version"]}
    return body


def refuse_unrunnable(routes, method, path, body):
    """Refuse a card's body the route itself would refuse, as a direct call is (422, field names and reasons, never the
    value): a card keeps its body and shows it back, so a field the route has no place for (a key sent with a
    providers change) would otherwise be stored and echoed. An unknown field is refused even where the model ignores
    extras. A route that reads its own body has no model to check here; secret names are still checked by the caller."""
    from fastapi.routing import APIRoute
    from pydantic import BaseModel
    route = next((r for r in routes or () if isinstance(r, APIRoute) and method in r.methods
                  and r.path_regex.fullmatch(path or "")), None)
    field = getattr(route, "body_field", None)
    if field is None:
        return
    _, errors = field.validate(body, {}, loc=("body",))
    model = field.field_info.annotation
    if not errors and isinstance(model, type) and issubclass(model, BaseModel) and isinstance(body, dict):
        known = {n for name, f in model.model_fields.items() for n in (name, f.alias) if n}
        errors = [{"loc": ("body", k), "msg": "Extra inputs are not permitted"} for k in body if k not in known]
    if errors:
        raise Problem("validation", "; ".join(".".join(str(part) for part in e["loc"]) + ": " + e["msg"]
                                              for e in errors), 422)


def create_proposal(c, settings, who, room, proposer, summary, method, path, body, asked=None, what=None, routes=None,
                    shown=None):
    """One pending action for `who` to confirm, and its card in `room`, written by the bot that proposed it.

    The Assistant proposes what its turn may not do on its own; BotOps proposes what a person asked it for
    that always needs their own click (adding people, roles, shared credentials, a computer that does not
    take members' bots). Either way only the person's own confirm runs it, as them. `shown`: the card's lines, when the
    route that asks says what changes in safer words than the body (a destination by scheme, host and port only)."""
    path = normalize_path(path)
    if not valid_operation(method, path, settings, proposer):
        raise Problem("operation", "That is not something that can be proposed: it is not on the list of routes "
                      "a person confirms", 422)
    if any(field in (body or {}) for field in PERSON_ONLY_FIELDS):
        raise Problem("operation", "Replacing a lost key is confirmed by a person directly, never through a proposal", 422)
    from . import assistant_routines
    routine_route = ((method == "POST" and path == assistant_routines.CREATE)
                     or (method == "PATCH" and assistant_routines.EDIT.fullmatch(path)))
    if proposer == "assistant" and (not any(m == method and p.fullmatch(path) for m, p in _PROPOSABLE)
                                    or (routine_route and not assistant_routines.request_is_message(c, method, path, body))):
        # A setting the Assistant puts on a card is one catalogue entry's body, and never a person-only one.
        from . import settings_catalog
        found = settings_catalog.stored_class(c, method, path, body if isinstance(body, dict) else None)
        if not found or found[2] == "person":
            raise Problem("operation", "A setting on a card is one setting from the catalogue (`hub settings find`) "
                          "that is not the person's own to change in Settings", 422)
    if path == "/api/v2/support/tickets":
        # The person approves the exact text, so it must be one the ticket will take: no card for a message that
        # would fail (too long, control characters) only after the click.
        from .support import clean_message
        clean_message((body or {}).get("message"))
    if c.execute("SELECT count(*) FROM assistant_actions WHERE owner=? AND status='pending'",
                 (who.actor,)).fetchone()[0] >= MAX_PENDING:
        raise Problem("too_many", "Too many proposals are waiting; confirm or cancel some first", 409)
    body = {k: v for k, v in (body or {}).items() if k != "on_behalf_of"}
    if path == BATCH_PATH:
        body = {"actions": [{**s, "body": runnable(c, s["method"], s["path"], s["body"])} for s in batch_steps(body)]}
    else:
        body = runnable(c, method, path, body)
    if routes is not None:
        refuse_unrunnable(routes, method, path, body)
    body, routine = assistant_routines.prepare(c, settings, who, method, path, body) if proposer == "assistant" else (body, None)
    routine_steps = {}
    if path == BATCH_PATH and proposer == "assistant":
        for i, step in enumerate(body["actions"]):
            if routes is not None:
                refuse_unrunnable(routes, step["method"], step["path"], step["body"])
            step["body"], planned = assistant_routines.prepare(c, settings, who, step["method"], step["path"], step["body"])
            if planned:
                routine_steps[i] = planned
    if routes is not None:
        refuse_unrunnable(routes, method, path, body)
        # A bulk card's own model takes any step body: each step is checked against its own route, and one refused
        # step refuses the whole card.
        for s in body["actions"] if path == BATCH_PATH else ():
            refuse_unrunnable(routes, s["method"], s["path"], s["body"])
    require_addressed(c, who, path, body)
    if who.via == "assistant" and not who.confirmed and approvals_in(path, body):
        require_named_approval(c, who, asked)
    same = c.execute("SELECT * FROM assistant_actions WHERE owner=? AND status='pending' AND method=? AND path=? "
                     "AND body=? AND proposer=?", (who.actor, method, path, json.dumps(body), proposer)).fetchone()
    if same:
        # The same card again is the same card: a retried command never leaves two to click.
        return {"action": action_view(dict(same), c), "message_id": None, "repeated": True}
    said, diff = describe(c, method, path, body)
    if routine:
        said, diff = routine
    if not what and (m := re.fullmatch(r"/api/v2/bots/([^/]+)/repositories", path)) and method == "PUT":
        # What confirming does, from the change tried as this person and rolled back, not read off the request.
        from .repositories import preview_change
        what = preview_change(c, settings, who.actor, m.group(1), body)
    what = what or said         # a route that tried the change first says what it does (backend/repositories.py)
    if path == BATCH_PATH:
        # One line per step: what it does to which task, and the fields it changes.
        diff = groups(c, body["actions"])
        for i, s in enumerate(body["actions"]):
            line, changes = routine_steps.get(i) or describe(c, s["method"], s["path"], s["body"])
            fields = changes or [{"field": k, "new": v} for k, v in s["body"].items()]
            diff.append({"field": line, "new": ", ".join(f"{d['field']}: {d['new']}" for d in fields
                                                          if d["field"] != "version"),
                         **({"routine": True, "routine_path": s["path"],
                             "routine_revision": fields[0].get("routine_revision")} if i in routine_steps else {})})
    elif not routine and not re.fullmatch(r"/api/v2/tasks/[^/]+", path):
        # The card lists every field the request carries, so nothing that runs is missing from what is confirmed.
        # (A task update lists what changes: its old and new values.)
        diff = [{"field": k, "new": v} for k, v in body.items()]
    if shown is not None:
        diff = shown
    row = {"id": H.new_id(), "owner": who.actor, "conversation_id": room["id"], "summary": summary,
           "method": method, "path": path, "body": json.dumps(body),
           "status": "pending", "proposed_via": proposer, "created_at": H.now(),
           "description": what, "diff": json.dumps(diff, default=str), "proposer": proposer}
    c.execute("INSERT INTO assistant_actions(id,owner,conversation_id,summary,method,path,body,status,"
              "proposed_via,created_at,description,diff,proposer) VALUES(:id,:owner,:conversation_id,:summary,"
              ":method,:path,:body,:status,:proposed_via,:created_at,:description,:diff,:proposer)", row)
    bot = room_bot(settings, room) if proposer == "assistant" else proposer
    card = H._write_message(c, "bot:" + bot, who.actor, "Needs your OK: " + summary, room, "say",
                            {"assistant": True, "action": row["id"]}, None, None, delivered_at=H.now())
    H.event(c, who.actor, "assistant.action.proposed", row["id"],
            {"summary": summary[:200], "method": method, "path": path, "proposer": proposer})
    return {"action": action_view(row, c), "message_id": card["id"]}


# ------------------------------------------------------------------ the room
def room_bot(settings, room):
    """The bot that talks in this person's own room: the Assistant, and the Strategy Bot in the owner's earlier
    Strategy Bot chat (its old cards and their results)."""
    return strategy_bot.SLUG if (room or {}).get("room_key") == rooms.STRATEGY_ROOM else settings.assistant_bot


def find_room(c, actor):
    row = c.execute("SELECT id FROM conversations WHERE scope='personal' AND owner_id=? AND room_key=? "
                    "AND closed_at IS NULL ORDER BY created_at LIMIT 1", (actor, rooms.ASSISTANT_ROOM)).fetchone()
    return H.conversation(c, row["id"]) if row else None


def own_room(c, who, conversation_id):
    """Whether this conversation is the caller's own Assistant room (and the caller is a person)."""
    if not conversation_id or who.role not in ("owner", "human") or who.via:
        return False
    room = H.conversation(c, conversation_id)
    return bool(room and room.get("scope") == rooms.PERSONAL and room.get("owner_id") == who.actor
                and room.get("room_key") == rooms.ASSISTANT_ROOM and not room.get("closed_at"))


def ensure_room(c, actor, bot):
    room = find_room(c, actor)
    if room:
        return room
    return H.open_conversation(c, actor, [actor, "bot:" + bot], kind="chat", subject="Assistant",
                               scope=rooms.PERSONAL, owner_actor=actor, room_key=rooms.ASSISTANT_ROOM)


# ------------------------------------------------------------------ one Assistant for everyone (docs/assistant.md)
# Every person, the owner included, has one chat: their Assistant. The Strategy Bot, the Librarian and BotOps are
# helpers it hands work to (`HELPERS`, backend/app.py task_create). The owner's earlier chat with the Strategy Bot
# (`rooms.STRATEGY_ROOM`, one per company, belonging to the owner seat) is history: readable by the owner under
# "Earlier chats", never written to again. A transfer of ownership still moves it to the new owner
# (`move_strategy_room`), and `migrate` moved the cards that were waiting in it to the owner's Assistant room.
HELPERS = ("botops", strategy_bot.SLUG, "librarian", H.COUNSEL)


def strategy_on(c):
    """Whether this install has its Strategy Bot (every install does once it is set up)."""
    row = H.bot(c, strategy_bot.SLUG)
    return bool(row) and row["state"] not in ("archived", "planned")


def chat_bot(c, settings, who):
    """The bot this person talks to in their own chat: the Assistant, for everyone."""
    return settings.assistant_bot


def chat_name(c, settings, bot):
    return strategy_bot.name(c) if bot == strategy_bot.SLUG else settings.assistant_name


def find_strategy_room(c, actor=None):
    """The owner's earlier Strategy Bot chat: the owner seat's one room. With `actor`, only while it is theirs."""
    row = c.execute("SELECT id FROM conversations WHERE scope='personal' AND room_key=? AND closed_at IS NULL "
                    "ORDER BY created_at LIMIT 1", (rooms.STRATEGY_ROOM,)).fetchone()
    room = H.conversation(c, row["id"]) if row else None
    return room if room and (actor is None or room.get("owner_id") == actor) else None


def move_strategy_room(c, actor, to):
    """The earlier Strategy Bot chat goes with the owner seat to `to`, the new owner, as history. A card still in it
    is withdrawn: it ran as the old owner, who is no longer the owner."""
    room = find_strategy_room(c)
    if not room or room.get("owner_id") == to:
        return room
    before = room.get("owner_id")
    c.execute("UPDATE conversations SET owner_id=?, participants=? WHERE id=?",
              (to, json.dumps([to, "bot:" + strategy_bot.SLUG]), room["id"]))
    c.execute("UPDATE assistant_actions SET status='canceled', decided_at=? WHERE conversation_id=? AND owner=? "
              "AND status='pending'", (H.now(), room["id"], before))
    H.event(c, actor, "strategy_bot.chat_moved", room["id"], {"from": before, "to": to})
    return H.conversation(c, room["id"])


def migrate(c, settings):
    """Every start (backend/store.py), idempotent: a card still waiting in the owner's earlier Strategy Bot chat moves
    to its owner's Assistant room, with its card posted there, so it stays where they look and its result lands there.
    Moving is safe: a card runs only on its owner's own click, as them, whichever room it sits in, and a card of an
    earlier owner was already withdrawn when the seat moved on (`move_strategy_room`). Each card moves once: after the
    move none is left in that room."""
    moved = 0
    for row in c.execute("SELECT a.id, a.owner, a.summary FROM assistant_actions a JOIN conversations v "
                         "ON v.id=a.conversation_id WHERE a.status='pending' AND v.scope='personal' AND v.room_key=? "
                         "AND a.owner=v.owner_id ORDER BY a.created_at", (rooms.STRATEGY_ROOM,)).fetchall():
        room = ensure_room(c, row["owner"], settings.assistant_bot)
        c.execute("UPDATE assistant_actions SET conversation_id=? WHERE id=?", (room["id"], row["id"]))
        H._write_message(c, "bot:" + settings.assistant_bot, row["owner"], "Needs your OK: " + row["summary"], room,
                         "say", {"assistant": True, "action": row["id"]}, None, None, delivered_at=H.now())
        moved += 1
    if moved:
        H.event(c, H.KEEPER, "assistant.strategy_cards_moved", "", {"cards": moved})
    return moved


def earlier_chat(c, settings, who):
    """"Earlier chats" on the owner's Assistant page: their earlier Strategy Bot chat, read-only (GET
    /api/v2/conversations/<id>/messages), once it has a message. None for anyone else."""
    if who.role != "owner":
        return None
    old = find_strategy_room(c, who.actor)
    if not old or not c.execute("SELECT 1 FROM messages WHERE conversation_id=? AND deleted_at IS NULL LIMIT 1",
                                (old["id"],)).fetchone():
        return None
    return {"room_id": old["id"], "bot": strategy_bot.SLUG, "name": strategy_bot.name(c)}


def chat_room(c, settings, who, create=True):
    """This person's own Assistant room, opened with `create`."""
    return ensure_room(c, who.actor, settings.assistant_bot) if create else find_room(c, who.actor)


def morning_day():
    """The company day a morning message is for: today's date on the company's clock (updates.ZONE, the zone the update
    ladder and the automations read, not UTC), and the UTC instant that day ends, when the next one may go."""
    from .updates import ZONE
    local = H.parse_ts(H.now()).astimezone(ZONE)
    ends = datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=ZONE)
    return local.date().isoformat(), ends.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def availability(c, settings, who):
    slug = chat_bot(c, settings, who)
    bot = H.bot(c, slug)
    state = bot["state"] if bot else "missing"
    return {"available": state == "active", "state": state, "bot": slug, "name": chat_name(c, settings, slug),
            "can_turn_on": who.role == "owner" and state != "active" and state not in ("quarantined",)}


# A repository card stored before its route had a sentence: titled "PUT /api/v2/bots/x/repositories", its summary
# "Set X's repository access to chosen. This stops: …" (backend/repositories.py before the plain wording).
OLD_REPO_SUMMARY = re.compile(r"Set .+'s repository access to (own|all|chosen)\b")


def action_view(row, c=None):
    result = json.loads(row["result"]) if row.get("result") else None
    view = {"id": row["id"], "owner": row["owner"], "conversation_id": row["conversation_id"],
            "summary": row["summary"], "method": row["method"], "path": row["path"],
            "body": json.loads(row["body"] or "{}"), "status": row["status"],
            "proposed_via": row["proposed_via"], "proposer": row.get("proposer") or row["proposed_via"],
            "created_at": row["created_at"], "decided_at": row.get("decided_at"),
            "description": row.get("description") or "", "diff": json.loads(row.get("diff") or "[]"),
            "result": result}
    if c is not None and view["proposer"] == "assistant" and view["conversation_id"]:
        # A card the Strategy Bot put in the owner's earlier chat with it says it is the Strategy Bot's, by its name.
        if (H.conversation(c, view["conversation_id"]) or {}).get("room_key") == rooms.STRATEGY_ROOM:
            view["by_name"] = strategy_bot.name(c)
    m = re.fullmatch(r"/api/v2/bots/([^/]+)/repositories", view["path"] or "")
    if m and c is not None:
        # Read again from what the stored request sends (never a guess at what it would do today), and its old line,
        # which reads the mode word as English, only under the card's Details.
        if not view["description"] or re.match(r"(GET|POST|PUT|PATCH|DELETE) /", view["description"]):
            from .repositories import plain_request
            view["description"] = plain_request((H.resolve_bot(c, m.group(1)) or {}).get("display_name") or m.group(1), view["body"])
        if OLD_REPO_SUMMARY.match(view["summary"] or ""):
            view["original_summary"], view["summary"] = view["summary"], ""
    return view


def actions_for(c, actor, ids):
    if not ids:
        return {}
    rows = c.execute("SELECT * FROM assistant_actions WHERE owner=? AND id IN (%s)" % ",".join("?" * len(ids)),
                     (actor, *ids)).fetchall()
    return {row["id"]: action_view(dict(row), c) for row in rows}


# ------------------------------------------------------------------ the confirm path's helpers
SHOWN_ONCE = {"/api/v2/enrollments": ("code", "expires_at", "operator")}


def shown_once(response, fields):
    try:
        payload = response.json()
    except ValueError:
        return None
    return {k: payload.get(k) for k in fields} if isinstance(payload, dict) and payload.get(fields[0]) else None


def outcome(response):
    """Only the status and an error's detail are kept: an answer's body may hold a secret."""
    if response.status_code < 400:
        return {"status_code": response.status_code, "error": None}
    detail = None
    try:
        payload = response.json()
        detail = (payload.get("error") or {}).get("detail") if isinstance(payload, dict) else None
    except ValueError:
        pass
    return {"status_code": response.status_code, "error": str(detail or f"HTTP {response.status_code}")[:300]}


class Internal:
    """Reads Tico's own API in-process as the person, with the credential they came in with, so a confirmed
    proposal runs exactly as the page would run it: never more."""

    def __init__(self, request):
        self.app = request.app
        self.base = request.app.state.store.settings.public_url or "http://127.0.0.1"
        self.headers = {k: v for k, v in request.headers.items()
                        if k.lower() not in ("host", "content-length", "content-type", "idempotency-key",
                                             "x-tico-assistant-action", "accept-encoding", "if-none-match")}

    async def call(self, method, path, body=None, params=None, key=None):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url=self.base, timeout=60) as client:
            headers = dict(self.headers)
            if body is not None:
                headers["content-type"] = "application/json"
            if method != "GET":
                headers["idempotency-key"] = key or str(uuid.uuid4())
            target = path if path.startswith("/api/v2/") else "/api/v2/" + path.lstrip("/")
            return await client.request(method, target, json=body,
                                        params={k: v for k, v in (params or {}).items() if v is not None},
                                        headers=headers)

    async def get(self, path, /, **params):
        try:
            response = await self.call("GET", path, params=params)
            return response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None



# ------------------------------------------------------------------ the routes
def install(app, store, auth, mutate, onboarding):
    from . import org_rule_check
    settings = store.settings

    def person(request):
        who = request.state.identity
        if who.role not in ("owner", "human"):
            raise Problem("identity", "The " + settings.assistant_name + " is for people", 403)
        return who

    def own(request):
        who = person(request)
        if who.via:
            raise Problem("forbidden", "Only the person can do this, not the " + settings.assistant_name, 403)
        return who

    def view(c, who, room):
        from .views import conversation_snapshot
        snap = conversation_snapshot(c, room["id"], settings=settings) if room else {"messages": [], "execution": None, "has_more": False,
                                                                     "next_before": None}
        visuals.for_reader(app, settings, c, who, snap["messages"])
        ids = [m["refs"]["action"] for m in snap["messages"] if isinstance(m.get("refs"), dict) and m["refs"].get("action")]
        pending = [action_view(dict(r), c) for r in c.execute(
            "SELECT * FROM assistant_actions WHERE owner=? AND status='pending' ORDER BY created_at", (who.actor,))]
        info = availability(c, settings, who)
        earlier = earlier_chat(c, settings, who)
        return {**info, "room_id": room["id"] if room else None,
                "messages": snap["messages"], "has_more": snap["has_more"], "next_before": snap["next_before"],
                "execution": snap["execution"], "actions": actions_for(c, who.actor, ids),
                "pending": pending, "earlier": earlier}

    @app.get("/api/v2/assistant")
    def assistant(request: Request):
        """The caller's own chat: their Assistant room, the owner's included."""
        who = own(request)
        # Every chat load: read first, and take the write lock only when a confirm is overdue or the room needs
        # opening.
        with store.read() as c:
            create = availability(c, settings, who)["available"]
            room = find_room(c, who.actor)
            ready = bool(room) or not create
            if ready and not expiring(c):
                return view(c, who, room)
        with store.transaction() as c:
            expire(c)
            room = chat_room(c, settings, who, create=availability(c, settings, who)["available"])
        with store.read() as c:
            return view(c, who, room)

    def chat_bot_for(c, who):
        """The bot this person's chat posts to, refusing a chat that is off or is not theirs."""
        info = availability(c, settings, who)
        if not info["available"]:
            raise Problem("assistant_off", "The " + info["name"] + " is off", 409)
        auth.require_write(c, who, info["bot"])
        return info["bot"]

    def assistant_checks(who, bot, text):
        """The org rule check of a person's message to their Assistant, outside the write, as `chat` checks one to a
        bot: the chat is its place (`where:` rules), the bot its destination. Held, nothing is written (`assistant_say`).
        None when no rule is in force, so nothing is read or called."""
        if not text:
            return None
        with store.read() as c:
            if not org_rule_check.in_force(c):
                return None
            room = find_room(c, who.actor)
        room_id = (room or {}).get("id") or ""
        return org_rule_check.preflight(store, app.state.judge, who, "assistant_message", text, ["bot:" + bot],
                                        place=lambda c, author, recipients: org_rule_check.place_for(
                                            c, author, recipients, room_id))

    def assistant_say(c, who, bot, room, text, refs, rc, uploads=None, titles=()):
        """One person's message to their Assistant, as the route and Send anyway write it: the org rule check's verdict
        first (held: nothing is written and the hold is the answer), then the message with its files. `uploads` are the
        files' digests, sizes and names, kept already; a replay carries them, so Send anyway attaches the same files.
        Its bot turn answers it."""
        titles = list(titles or [])
        gate = org_rule_check.apply(c, rc, text, {"text": text, "to": "bot:" + bot, "refs": refs,
                                                  "uploads": uploads, "titles": titles})
        if gate and gate.get("held"):
            return org_rule_check.held_answer(c, who, gate, auth=auth)
        message = H.say(c, who.actor, "bot:" + bot, org_rule_check.warned_text(gate, text) if gate else text,
                        conversation_id=room["id"], kind="say", refs=refs, make_page="make_page" in refs)
        if uploads is not None:
            message = app.state.uploads.attach(c, who, bot, message, uploads, titles)
        return {"message": message, **org_rule_check.answer(gate)}

    def post_message_sync(request, body, who, uploads=None, rc=None):
        def work(c):
            bot = chat_bot_for(c, who)
            room = chat_room(c, settings, who)
            refs = {"assistant": True}
            if getattr(body, "place", None) == "updates":
                refs["updates"] = True        # its turn is handed the updates they may read (backend/updates_chat.py)
            page = getattr(body, "page", None)
            if page and page.path.startswith("#/"):
                refs["page"] = {"path": page.path, "title": page.title}   # where they asked from (runner-rs prompt.rs)
            return assistant_say(c, who, bot, room, body.text, refs, rc, uploads,
                                 getattr(body, "titles", None) or [])
        if uploads is not None:
            return app.state.uploads.write(request, body, uploads, work)
        return mutate(request, body, work)

    @org_rule_check.replayer("assistant_message")
    def assistant_replay(c, who, hold, replay, ingest, gate=None):
        """Send anyway's write: the person's message to their Assistant, as the person, with its files (kept already).
        It is posted without a quick answer; its turn answers it."""
        bot = chat_bot_for(c, who)
        if "bot:" + bot != replay.get("to"):
            raise Problem("changed", "Your " + settings.assistant_name + " changed since the message was held; send it "
                          "again", 409)
        target = (replay.get("refs") or {}).get("make_page")
        if target:
            from .pages import Pages
            task = auth.task(c, who, target["task"])
            Pages.require_pin_space(c, task["id"])
        return assistant_say(c, who, bot, chat_room(c, settings, who), replay["text"], replay.get("refs") or {},
                             org_rule_check.decided(gate), replay.get("uploads"), replay.get("titles") or [])

    @app.post("/api/v2/assistant/messages")
    async def assistant_message(request: Request, body: M.AssistantMessage):
        who = own(request)

        def read():
            with store.read() as c:
                return availability(c, settings, who)
        info = await asyncio.to_thread(read)
        if not info["available"]:
            raise Problem("assistant_off", "The " + info["name"] + " is off", 409)
        rc = await asyncio.to_thread(assistant_checks, who, info["bot"], body.text)
        return await asyncio.to_thread(post_message_sync, request, body, who, None, rc)

    @app.post("/api/v2/tasks/{tid}/make-page")
    async def make_page(request: Request, tid: str, body: M.MakePage):
        from .pages import Pages
        who = own(request)
        text = body.text.strip()
        if not text:
            raise Problem("validation", "Say what page to make", 422)
        def admit():
            with store.read() as c:
                task = auth.task(c, who, auth.resolve_task(c, who, tid))
                Pages.require_pin_space(c, task["id"])
                return task["id"], chat_bot_for(c, who)
        task, bot = await asyncio.to_thread(admit)
        rc = await asyncio.to_thread(assistant_checks, who, bot, text)
        def work(c):
            auth.task(c, who, task)
            Pages.require_pin_space(c, task)
            bot = chat_bot_for(c, who)
            return assistant_say(c, who, bot, chat_room(c, settings, who), text,
                                 {"assistant": True, "make_page": {"task": task}, "delivery": "queue"}, rc)
        return await asyncio.to_thread(mutate, request, body, work)

    @app.get("/api/v2/tasks/{tid}/make-page")
    def make_page_status(request: Request, tid: str):
        """Only the requester's latest Make page request, including after a reconnect or another chat turn."""
        who = own(request)
        with store.read_transaction() as c:
            task = auth.task(c, who, auth.resolve_task(c, who, tid))
            room = find_room(c, who.actor)
            row = c.execute("SELECT m.id,m.conversation_id,j.bot_id,j.state,a.state AS attempt_state,a.lease_until "
                            "FROM messages m LEFT JOIN jobs j ON j.message_id=m.id "
                            "LEFT JOIN attempts a ON a.id=j.attempt_id WHERE m.conversation_id=? "
                            "AND m.sender_id=? AND m.deleted_at IS NULL AND json_extract(m.refs,'$.assistant')=1 "
                            "AND json_extract(m.refs,'$.make_page.task')=? ORDER BY m.rowid DESC LIMIT 1",
                            ((room or {}).get("id"), who.actor, task["id"])).fetchone()
            if not row:
                return {"make_page": None}
            # Publish and pin commit together. Later unlinking or archiving keeps a successful request successful.
            made = c.execute("SELECT 1 FROM bot_files f JOIN pages p ON p.file_id=f.id "
                             "WHERE f.scope=? "
                             "AND EXISTS (SELECT 1 FROM bot_file_activity v JOIN attempts a ON a.id=v.attempt_id "
                             "JOIN jobs j ON j.id=a.job_id WHERE v.file_id=f.id AND j.message_id=? "
                             "AND v.action IN ('created','modified','published')) LIMIT 1",
                             ("task:" + task["id"], row["id"])).fetchone()
            active = (row["state"] == "queued" or row["state"] in ("leased", "running")
                      and row["attempt_state"] in ("leased", "running") and str(row["lease_until"] or "") > H.now())
            state = "made" if made else "making" if active else "failed"
            if not made and row["state"] == "queued":
                from .execution import MAKE_PAGE, capability_set
                computer = c.execute("SELECT r.capabilities FROM assignments a JOIN runners_with_state r ON r.id=a.runner_id "
                                     "WHERE a.bot=?", (row["bot_id"],)).fetchone()
                if computer and MAKE_PAGE not in capability_set(computer["capabilities"]):
                    state = "update_required"
            return {"make_page": {"message_id": row["id"], "conversation_id": row["conversation_id"],
                                  "state": state}}

    # Files and pictures in a person's own Assistant chat (the Ask panel, the Assistant page), stored on the message like
    # any bot chat's, in their room's conversation.
    @app.post("/api/v2/uploads/assistant/messages", openapi_extra=upload_contract(M.AssistantUpload))
    async def assistant_upload(request: Request):
        who = own(request)

        def admit(body):
            # Before any byte is stored, so a refused send keeps nothing; the write checks again in its transaction.
            with store.read() as c:
                chat_bot_for(c, who)
        body, uploads = await app.state.uploads.form(request, M.AssistantUpload, admit)
        def bot_of():
            with store.read() as c:
                return chat_bot_for(c, who)
        bot = await asyncio.to_thread(bot_of)
        rc = await asyncio.to_thread(assistant_checks, who, bot, body.text)
        return await asyncio.to_thread(post_message_sync, request, body, who, uploads, rc)

    @app.post("/api/v2/assistant/turn-on")
    def turn_on(request: Request, body: M.Empty):
        who = person(request)
        if who.via and not who.confirmed:      # the owner's own click, or their tap on a Confirm card
            raise Problem("forbidden", "Only the person can do this, not the " + settings.assistant_name, 403)
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner turns the " + settings.assistant_name + " on", 403)
        return mutate(request, body, lambda c: onboarding.turn_on_assistant(c, who))

    @app.get("/api/v2/assistant/settings")
    def my_settings(request: Request):
        who = person(request)
        with store.read() as c:
            return {**decides(c, who.actor), "where": f"Settings > {settings.assistant_name}"}

    @app.post("/api/v2/assistant/settings")
    def set_my_settings(request: Request, body: M.AssistantSettings):
        """Only the person, signed in to the app: never their Assistant, a bot, or a token turning it on for them."""
        who = own(request)
        if body.decides_approvals and who.via_token:
            raise Problem("forbidden", "Turn this on in " + settings.app_name + " itself, in Settings > "
                          + settings.assistant_name, 403)

        def work(c):
            before = decides(c, who.actor)["decides_approvals"]
            c.execute("INSERT INTO preferences(actor,key,value,updated_at) VALUES(?,?,?,?) ON CONFLICT(actor,key) "
                      "DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                      (who.actor, DECIDES_KEY, json.dumps(body.decides_approvals), H.now()))
            if before != body.decides_approvals:
                H.event(c, who.actor, "assistant.decides_approvals", who.actor, {"on": body.decides_approvals})
            return {**decides(c, who.actor), "where": f"Settings > {settings.assistant_name}"}
        return mutate(request, body, work)

    @app.get("/api/v2/assistant/decisions")
    def my_decisions(request: Request, days: int = 30):
        """The approvals this person's Assistant decided for them, newest first, each with their words and whether it
        can still be undone (nothing has acted on it yet)."""
        who = own(request)
        since = H.shift(H.now(), days=-max(1, min(days, 90)))
        with store.read() as c:
            rows = c.execute("SELECT a.id FROM approvals a JOIN messages m ON m.id=a.message_id WHERE a.decided_by_id=? "
                             "AND a.decided_via='assistant' AND m.recipient_id=? AND a.decided_at>=? "
                             "ORDER BY a.decided_at DESC LIMIT 200", (who.actor, who.actor, since)).fetchall()
            out = []
            for r in rows:
                row = H.approval(c, r["id"])
                what, _ = describe(c, "POST", "/api/v2/approvals/" + row["id"], {"decision": row["decision"]})
                out.append({"id": row["id"], "kind": row["kind"], "decision": row["decision"],
                            "what": what.split(" ", 1)[-1],          # "the publish approval requested by ..."
                            "decided_at": row["decided_at"], "decided_for": row.get("decided_for"),
                            "consumed_at": row["consumed_at"], "can_undo": not row["consumed_at"]})
            return {"decisions": out, "assistant": settings.assistant_name}

    @app.post("/api/v2/assistant/read-back")
    def read_back(request: Request, body: M.AssistantReadBack):
        """The Assistant's list of what it is about to approve for the person, posted in their chat as its reply. The
        server writes all of it from the approvals themselves, so what the person says yes to is what runs, and records
        the ids: only those may then be approved, on their yes (`approval_words`). `note` is the Assistant's own words
        shown above the list (the real decision it found, with its link); the yes still approves only the listed ids."""
        who = person(request)
        if who.via != "assistant" or who.confirmed or not who.attempt_id:
            raise Problem("forbidden", "Only the " + settings.assistant_name + ", in the person's own chat, reads back "
                          "approvals", 403)
        ids = list(dict.fromkeys(body.approvals))

        def work(c):
            state = decides(c, who.actor)
            if not state["active"]:
                raise Problem("approve_setting_off", approve_hint(settings, state), 403)
            room = chat_room(c, settings, who, create=False)
            if not room or "bot:" + room_bot(settings, room) != who.task_actor:
                raise Problem("not_found", "No Assistant chat", 404)
            bot = room_bot(settings, room)
            lines = []
            for aid in ids:
                row = H.approval(c, aid)
                if not row or (H.message(c, row.get("message_id")) or {}).get("recipient_id") != who.actor:
                    raise Problem("forbidden", "That approval is not addressed to you", 403)
                if row["decision"]:
                    raise Problem("conflict", f"Approval {aid} is already decided", 409)
                if row["requested_by_id"] == "bot:" + bot:
                    raise Problem("own_request", "You asked for this approval yourself, so it stays the person's "
                                  "click: propose it on a card (`hub assistant propose`)", 403)
                refuse_own_click(row)
                if cut := too_long(row.get("payload") or {}):
                    raise Problem("approve_too_long", f"Approval {aid} has more in {', '.join(field_value(k, 40) for k in cut)} "
                                  "than the read-back can show in full, so it stays the person's click: leave it out", 409)
                what, _ = describe(c, "POST", "/api/v2/approvals/" + aid, {"decision": "approved"})
                lines.append("- " + plain_line(what))
            n = len(ids)
            text = (f"I'll approve {'this' if n == 1 else f'these {n}'} for you:\n" + "\n".join(lines)
                    + "\n\nReply yes to approve exactly " + ("this" if n == 1 else "these")
                    + "; any other reply approves nothing.")
            # The note is the Assistant's own words, one plain line above the list; the list and the yes line the server
            # writes stay below it and last, so the note can never be read as part of what the yes approves.
            if note := " ".join(body.note.split()):
                text = note + "\n\n" + text
            # The reply to the person's message: the turn's closing text is not posted after it (backend/execution.py),
            # so their next message answers this list and nothing else.
            turn = c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?",
                             (who.attempt_id,)).fetchone()
            msg = H._write_message(c, "bot:" + bot, who.actor, text, room, "say",
                                   {"assistant": True, "read_back": True}, turn["message_id"] if turn else None, None,
                                   delivered_at=H.now())
            H.event(c, who.actor, READ_BACK, msg["id"], {"approves": ids})
            return {"message_id": msg["id"], "approves": ids, "text": text}
        return mutate(request, body, work)

    @app.post("/api/v2/assistant/actions")
    def propose(request: Request, body: M.AssistantAction):
        caller = request.state.identity
        # The Assistant's own background run (its daily sweep: a routine of the Assistant bot, so `via` is never
        # "assistant") may put a card in one person's own Assistant chat, with `for`: as that person's card, proposed by
        # the Assistant, never approving anything. The person is resolved the way their own chat turn is (`chat_person`):
        # an unknown, gone or suspended person is refused before any room is made, and the role is their real one.
        background = body.for_person is not None and caller.role not in ("owner", "human")
        if background:
            if H.actor_id(caller.actor) != settings.assistant_bot or not H.is_human(body.for_person):
                raise Problem("forbidden", "Only the " + settings.assistant_name + " puts a card in a person's chat, "
                              "and only for that person", 403)
        else:
            caller_person = person(request)
            if body.for_person not in (None, caller_person.actor):
                raise Problem("forbidden", "Only the " + settings.assistant_name + " puts a card in someone else's chat, "
                              "and only from its own background run", 403)

        def work(c):
            if background:
                from dataclasses import replace
                found = auth.chat_person(c, body.for_person, settings.assistant_bot)
                if not found:
                    raise Problem("not_found", "No active person " + body.for_person + " to put a card in the chat of", 404)
                # The card is the person's own; it is not a turn of theirs, so it carries no citation of its own.
                who = replace(found, via="", task_actor="", attempt_id="", runner_id="")
            else:
                who = caller_person
            if normalize_path(body.path) == "/api/v2/seats/batch" and who.role != "owner":
                raise Problem("forbidden", "Only the owner may confirm a chart-model re-org", 403)
            if normalize_path(body.path) in ("/api/v2/chat/" + settings.assistant_bot,
                                             "/api/v2/chat/" + strategy_bot.SLUG) \
                    or not valid_operation(body.method, body.path, settings):
                raise Problem("operation", "That is not something the " + settings.assistant_name
                              + " can propose: it is not on the list of routes a person confirms (never tokens, "
                              "sign-in, enrollment or this assistant)", 422)
            if background:
                # Never an approve, on a background card: the person decides approvals in their own chat or in Tico.
                if _DECIDING.fullmatch(normalize_path(body.path)) or approvals_in(body.path, body.body):
                    raise Problem("forbidden", "A background card never approves: the person decides it themselves", 403)
                sent = c.execute("SELECT count(*) FROM assistant_actions WHERE owner=? AND proposer='assistant' AND "
                                 "created_at>=?", (who.actor, H.shift(H.now(), seconds=-86400))).fetchone()[0]
                if sent >= MAX_BACKGROUND_PER_DAY:
                    raise Problem("too_many", "The " + settings.assistant_name + " has put enough cards in this person's "
                                  "chat today", 429)
            room = chat_room(c, settings, who)
            return create_proposal(c, settings, who, room, "assistant", body.summary, body.method, body.path, body.body,
                                   request.headers.get("x-tico-on-behalf-of"), routes=request.app.routes)
        return mutate(request, body, work)

    @app.post("/api/v2/assistant/morning")
    def morning(request: Request, body: M.AssistantMorning):
        """The Assistant's one morning message to a person: what they can do today to move their goals, in their own
        Assistant chat, as the Assistant. No card and nothing to confirm. Only the Assistant bot's background run may
        send it, for an active person, and once a company day per person: a second one that day is refused
        `409 already_sent_today`, whatever the caller sends."""
        caller = request.state.identity
        assistant_run = H.is_bot(caller.actor) and H.actor_id(caller.actor) == settings.assistant_bot
        if not assistant_run or not H.is_human(body.for_person):
            raise Problem("forbidden", "Only the " + settings.assistant_name + " sends a person their morning message, "
                          "from its own background run, and only to that person", 403)

        def work(c):
            found = auth.chat_person(c, body.for_person, settings.assistant_bot)
            if not found:
                raise Problem("not_found", "No active person " + body.for_person + " to send a morning message to", 404)
            day, next_at = morning_day()
            sent = c.execute("SELECT 1 FROM messages WHERE recipient_id=? AND sender_id=? AND kind='say' AND "
                             "json_extract(refs,'$." + MORNING_KEY + "')=? LIMIT 1",
                             (found.actor, "bot:" + settings.assistant_bot, day)).fetchone()
            if sent:
                raise Problem("already_sent_today", "The " + settings.assistant_name + " has already sent this person "
                              "their morning message today; the next can go at " + next_at + " (midnight, "
                              "company time)", 409, extra={"next_at": next_at})
            room = chat_room(c, settings, found)
            message = H._write_message(c, "bot:" + settings.assistant_bot, found.actor, body.body, room, "say",
                                       {"assistant": True, MORNING_KEY: day}, None, None, delivered_at=H.now())
            H.event(c, found.actor, "assistant.morning", message["id"],
                    {"day": day, "by": caller.actor, "chars": len(body.body)})
            return {"message_id": message["id"], "conversation_id": room["id"], "day": day, "next_at": next_at}
        return mutate(request, body, work)

    def propose_setting(c, who, method, path, data, summary, asked=None, what=None):
        """A setting the person asked their Assistant for that needs their own click: a Confirm card in their chat
        (backend/settings_catalog.py plan), and what the Assistant tells them. `what`: the card's title, as BotOps'."""
        card = create_proposal(c, settings, who, chat_room(c, settings, who), "assistant", summary[:300], method, path,
                               data, asked, what=what, routes=app.routes)
        return {"proposed": True, "needs_confirm": True, **card,
                "detail": "This needs the person's own OK: a Confirm card is in their chat; nothing has changed yet."}
    app.state.assistant_propose = propose_setting

    def expiring(c):
        return c.execute("SELECT 1 FROM assistant_actions WHERE status='running' AND running_since<? LIMIT 1",
                         (H.shift(H.now(), seconds=-RUNNING_TTL_S),)).fetchone()

    def expire(c):
        """A confirm that never settled (a restart, a dropped connection) fails after two minutes; an asked one also
        settles its ask (backend/settings_asks.py), its asker told it may not have finished."""
        settings_asks.recover(c)
        c.execute("UPDATE assistant_actions SET status='failed', decided_at=?, confirm_hash=NULL, result=? "
                  "WHERE status='running' AND running_since<?",
                  (H.now(), json.dumps({"status_code": 0, "error": "It did not finish; ask again"}),
                   H.shift(H.now(), seconds=-RUNNING_TTL_S)))

    def load(c, who, aid):
        row = c.execute("SELECT * FROM assistant_actions WHERE id=?", (aid,)).fetchone()
        if not row or row["owner"] != who.actor:
            raise Problem("not_found", "No such proposal", 404)     # nobody else's is visible
        return dict(row)

    @app.get("/api/v2/assistant/actions/{aid}")
    def action_show(request: Request, aid: str):
        who = person(request)
        with store.read() as c:
            return {"action": action_view(load(c, who, aid), c)}

    def settle(aid, who, status, result, note):
        with store.transaction() as c:
            c.execute("UPDATE assistant_actions SET status=?, decided_at=?, result=?, confirm_hash=NULL WHERE id=?",
                      (status, H.now(), json.dumps(result), aid))
            row = load(c, who, aid)
            settings_asks.settled(c, row, status, result)
            room = H.conversation(c, row["conversation_id"]) if row["conversation_id"] else None
            if room and note:
                by = row.get("proposer") or "assistant"
                H._write_message(c, "bot:" + (room_bot(settings, room) if by == "assistant" else by), who.actor, note,
                                 room, "say", {"assistant": True, "action_result": aid}, None, None,
                                 delivered_at=H.now())
            return {"action": action_view(row, c)}

    @app.post("/api/v2/assistant/actions/{aid}/cancel")
    def action_cancel(request: Request, aid: str, body: M.Empty):
        who = own(request)

        def work(c):
            row = load(c, who, aid)
            if row["status"] != "pending":
                raise Problem("state", "This proposal is already " + row["status"], 409)
            c.execute("UPDATE assistant_actions SET status='canceled', decided_at=? WHERE id=?", (H.now(), aid))
            H.event(c, who.actor, "assistant.action.canceled", aid, {})
            settings_asks.settled(c, row, "canceled")
            return {"action": action_view(load(c, who, aid), c)}
        return mutate(request, body, work)

    @app.post("/api/v2/assistant/actions/{aid}/withdraw")
    def action_withdraw(request: Request, aid: str, body: M.AssistantWithdraw):
        """Any signed-in teammate (a person or a bot) takes back a card that is still waiting: it never runs, and the
        card owner's chat says who withdrew it, with the reason if given. Confirming stays the owner's own click."""
        who = request.state.identity
        if not teammate(who):
            raise Problem("forbidden", "Only people and bots of the company can withdraw a proposal", 403)

        def work(c):
            row = c.execute("SELECT * FROM assistant_actions WHERE id=?", (aid,)).fetchone()
            if not row:
                raise Problem("not_found", "No such proposal", 404)
            if row["status"] != "pending":
                raise Problem("state", "This proposal is already " + row["status"], 409)
            reason = " ".join(str(body.reason or "").split())[:300]
            who_name = (settings_asks._name(c, who.actor) if who.actor.startswith("human:")
                        else (H.resolve_bot(c, who.actor.removeprefix("bot:")) or {}).get("display_name")
                        or who.actor.removeprefix("bot:"))
            c.execute("UPDATE assistant_actions SET status='withdrawn', decided_at=? WHERE id=?", (H.now(), aid))
            H.event(c, who.actor, "assistant.action.withdrawn", aid,
                    {"summary": row["summary"][:200], "reason": reason, "owner": row["owner"]})
            settings_asks.settled(c, row, "withdrawn")
            room = H.conversation(c, row["conversation_id"]) if row["conversation_id"] else None
            if room and not room.get("closed_at"):
                # Said by the proposing bot (the Assistant in its own room), to the card's owner.
                by = row["proposer"] or "assistant"
                said = who_name + " withdrew that card, so nothing will run." + (" " + reason if reason else "")
                H._write_message(c, "bot:" + (room_bot(settings, room) if by == "assistant" else by), row["owner"],
                                 said, room, "say", {"assistant": True, "action_result": aid}, None, None,
                                 delivered_at=H.now())
            return {"action": action_view(dict(c.execute("SELECT * FROM assistant_actions WHERE id=?", (aid,))
                                                .fetchone()), c)}
        return mutate(request, body, work)

    @app.get("/api/v2/assistant/proposals")
    def proposals(request: Request, proposer: str = "", owner: str = ""):
        """The Confirm cards still waiting, newest first, so a teammate can find one to withdraw: summary and what it
        does, with its id, owner and proposer. Filter by `proposer` (botops, assistant) and/or `owner` (a person's id)."""
        who = request.state.identity
        if not teammate(who):
            raise Problem("forbidden", "Only people and bots of the company can list proposals", 403)
        # A card the Assistant made in someone's private Assistant chat is listed to that person only: its words came
        # from a private conversation. Cards bots propose (BotOps) are team work, listed to every teammate.
        sql, args = "SELECT * FROM assistant_actions WHERE status='pending' AND (owner=? OR coalesce(proposer,'assistant')!='assistant')", [who.actor]
        if proposer:
            sql += " AND proposer=?"
            args.append(proposer)
        if owner:
            sql += " AND owner=?"
            args.append(owner)
        with store.read() as c:
            rows = c.execute(sql + " ORDER BY created_at DESC LIMIT 100", args).fetchall()
            return {"actions": [{"id": r["id"], "owner": r["owner"], "proposer": r["proposer"], "summary": r["summary"],
                                 "description": r["description"], "created_at": r["created_at"]} for r in rows]}

    @app.post("/api/v2/assistant/actions/{aid}/confirm")
    async def action_confirm(request: Request, aid: str):
        who = own(request)
        if who.via_token:
            raise Problem("forbidden", "Confirm in " + settings.app_name + " itself, with your own click", 403)
        secret = secrets.token_urlsafe(24)

        def claim():
            with store.transaction() as c:
                expire(c)
                row = load(c, who, aid)
                if row["status"] != "pending":
                    raise Problem("state", "This proposal is already " + row["status"], 409)
                if row["created_at"] < H.shift(H.now(), hours=-CONFIRM_TTL_H):
                    # Recorded here, raised after the transaction commits (raising inside it rolls it back).
                    c.execute("UPDATE assistant_actions SET status='expired', decided_at=? WHERE id=?", (H.now(), aid))
                    settings_asks.settled(c, row, "expired")
                    return None
                # Only the run that holds this secret (the in-process call below) is "confirmed".
                c.execute("UPDATE assistant_actions SET status='running', decided_at=?, running_since=?, "
                          "confirm_hash=? WHERE id=?", (H.now(), H.now(), digest(secret), aid))
                H.event(c, who.actor, "assistant.action.confirmed", aid,
                        {"summary": row["summary"][:200], "method": row["method"], "path": row["path"]})
                # A card stored before its task change was made runnable (`runnable`) still runs, read as it is now.
                sent = json.loads(row["body"] or "{}")
                if row["path"] == BATCH_PATH and isinstance(sent, dict) and isinstance(sent.get("actions"), list):
                    sent = {"actions": [{**s, "body": runnable(c, s.get("method") or "POST", s.get("path"), s.get("body") or {})}
                                        if isinstance(s, dict) else s for s in sent["actions"]]}
                else:
                    sent = runnable(c, row["method"], row["path"], sent)
                return {**row, "body": json.dumps(sent)}
        row = await asyncio.to_thread(claim)
        if row is None:
            raise Problem("expired", "This proposal is more than a day old; ask again", 409)
        ok, result = False, {"status_code": 0, "error": "It did not finish; ask again"}
        shown = None
        try:
            path = normalize_path(row["path"])
            if not valid_operation(row["method"], path, settings, row.get("proposer") or "assistant"):
                raise Problem("operation", "This proposal is not on the list of routes a person confirms", 422)
            api = Internal(request)
            api.headers["x-tico-assistant-action"] = aid + "." + secret
            sent = json.loads(row["body"] or "{}") if row["method"] != "DELETE" else None
            if isinstance(sent, dict):
                sent = {k: v for k, v in sent.items() if k not in PERSON_ONLY_FIELDS}   # a card stored before the check
            if path == BATCH_PATH:
                # Each step through its own route, as the person; one that fails is reported and the rest still run.
                items = []
                for i, step in enumerate(batch_steps(sent)):
                    try:
                        response = await api.call(step["method"], step["path"], body=step["body"],
                                                  key=f"assistant-{aid}-{i}")
                        items.append({"path": step["path"], **outcome(response)})
                    except httpx.HTTPError as exc:
                        items.append({"path": step["path"], "status_code": 0, "error": "It could not be sent: " + str(exc)[:200]})
                failed = [item for item in items if item["error"]]
                ok = len(failed) < len(items)
                result = {"status_code": failed[0]["status_code"] if failed and not ok else 200,
                          "error": (f"{len(failed)} of {len(items)} did not go through: "
                                    + "; ".join(item["error"] for item in failed))[:300] if failed else None,
                          "items": items}
            else:
                response = await api.call(row["method"], path, body=sent,
                                          key="assistant-" + aid)
                result = outcome(response)
                ok = result["error"] is None
                if ok and path in SHOWN_ONCE:
                    shown = shown_once(response, SHOWN_ONCE[path])
        except Problem as exc:
            result = {"status_code": exc.status, "error": exc.detail[:300]}
        except httpx.HTTPError as exc:
            result = {"status_code": 0, "error": "It could not be sent: " + str(exc)[:200]}
        finally:
            note = (("Done: " + row["summary"] + (". " + result["error"] if result.get("error") else "")) if ok
                    else ("That did not go through: " + str(result["error"])[:300]))
            # Off the event loop; the thread finishes the write even if the client went away.
            out = await asyncio.to_thread(settle, aid, who, "done" if ok else "failed", result, note)
        # A one-time value (a computer's code) goes to the person who tapped, in this answer only: never stored on the
        # card, in its result or in the chat, where the bot that proposed it could read it.
        return {**out, "shown_once": shown} if shown else out
