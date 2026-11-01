"""The Assistant: every person's own private chat with the company's assistant bot (docs/assistant.md).

One room per person (`rooms.ASSISTANT_ROOM`, scope personal, owner_actor the person): only they
read it or post in it, the owner and administrators included. A message goes one of two ways:

* the fast path answers on the server, from Tico's own data, with no runner turn: what is waiting
  on you, a search, "open X", what a bot did today, how to do something (docs/*.md). The person
  sees the answer at once (a bot turn is 30 seconds or more);
* everything else is a turn of the assistant bot on the company's runner. That turn acts as the
  person and never more: `Auth.authenticate` maps its token to the person (`assistant_principal`),
  so every hub tool it calls is checked, and audited "via assistant", exactly as if they had made
  the call. Low-risk writes run directly (`write_allowed`); a task change, comment or link the
  person asked for in their own words runs directly when the turn cites their message
  (`instruction`); anything else with a side effect that matters is proposed as a pending action,
  and only the person's own click (`confirm`) runs it, through the same routes with their own
  credential. The bot itself can never confirm.
"""

import asyncio
import json
import re
import secrets
import unicodedata
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import Request

from . import strategy_bot
from . import models as M
from . import visuals
from .media import upload_contract
from . import rooms
from .store import H, Problem, digest

RUNNING_TTL_S = 120                # a confirm that has not settled in two minutes failed
CONFIRM_TTL_H = 24                 # a proposal nobody confirmed for a day is not run
MAX_PENDING = 20
GITHUB_DOCS = "https://github.com/ticoteam/tico/blob/main/docs/"
DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"

SCHEMA = """
CREATE TABLE IF NOT EXISTS assistant_actions (
  id TEXT PRIMARY KEY, owner TEXT NOT NULL, conversation_id TEXT, summary TEXT NOT NULL,
  method TEXT NOT NULL, path TEXT NOT NULL, body_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL, proposed_via TEXT, created TEXT NOT NULL, decided_at TEXT, result_json TEXT);
CREATE INDEX IF NOT EXISTS assistant_actions_owner ON assistant_actions(owner, status);
"""


def ensure_schema(c):
    c.executescript(SCHEMA)
    have = {r[1] for r in c.execute("PRAGMA table_info(assistant_actions)")}
    for column in ("confirm_hash", "running_since", "description", "diff_json", "proposer"):
        if column not in have:
            c.execute("ALTER TABLE assistant_actions ADD COLUMN %s TEXT" % column)
    if not any(r[1] == "via" for r in c.execute("PRAGMA table_info(task_events)")):
        c.execute("ALTER TABLE task_events ADD COLUMN via TEXT")


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
    # Saving what the person taught it (backend/learned.py checks the layer: theirs, or the company's for the owner).
    if path in ("/api/v2/updates/read", "/api/v2/changelog/read", "/api/notes", "/api/v2/assistant/actions",
                "/api/v2/assistant/read-back", "/api/v2/assistant/learned", "/api/v2/goals/read-back"):
        return True
    # Asking the settings catalogue for a change's plan changes no setting: it answers with the request, a refusal or a
    # Confirm card in the person's chat (which `create_proposal` checks as any card), and the change itself meets the
    # checks below.
    if re.fullmatch(r"/api/v2/settings/catalog/[^/]+/plan", path):
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
                          or chatting.group(1) not in (settings.assistant_bot, strategy_bot.SLUG))) or (
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
# A task change, comment or link, declining an approval or answering a bot's question, that the person asked their
# Assistant for in their own Assistant chat runs without a card when the turn cites that message (`X-Tico-On-Behalf-Of`:
# its id, or `turn` for the message that started the run). The server checks the message, not the words: the person's
# own, typed in their own Assistant room, in the last INSTRUCTION_TTL_S. The turn is the person, so their rights stay the
# ceiling: only an approval addressed to them, only a question asked of them. A decline is undone by asking again, so it
# is a task action. Approving (send, spend, publish, merge) is a click on a card unless the person turned on "My
# assistant can decide approvals for me" (`decides`, below) and the team allows it; then an approval addressed to them is
# decided on their words too (`approval_words`). Running a task now and the rest stay a click, and so does all of it
# when the owner's rule "Assistant acts without asking" is off.
INSTRUCTION_TTL_S = 30 * 60
_INSTRUCTED = [re.compile(p) for p in (r"/api/v2/tasks/[^/]+", r"/api/v2/tasks/[^/]+/(comments|links)",
                                       r"/api/v2/messages/[^/]+/answer")]
_DECIDING = re.compile(r"/api/v2/approvals/([^/]+)")
DECIDES_KEY = "assistant.decides_approvals"


def decides(c, actor):
    """Whether this person's own Assistant may decide approvals addressed to them: their own switch in Settings, which
    only they turn on, and the team's rule (backend/team_rules.py), on unless the owner turned it off."""
    from . import team_rules
    row = c.execute("SELECT value_json FROM preferences WHERE actor=? AND key=?", (actor, DECIDES_KEY)).fetchone()
    on = bool(row) and json.loads(row[0]) is True
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


def instructed_setting(method, path, body=None):
    """Whether this write is one direct setting from the catalogue, exactly as `hub settings set` makes it, so it may run
    on the person's cited words like a task action (backend/settings_catalog.py). Two settings at once, a confirm or
    person-only one, or anything the catalogue does not list stays a card."""
    from . import settings_catalog
    path = normalize_path(path)
    if isinstance(body, (bytes, str)):
        try:
            body = json.loads(body or b"{}")
        except ValueError:
            return False
    if not isinstance(body, dict):
        return False           # a list or a bare value is no catalogue entry's body: never read as an empty one
    found = path and settings_catalog.matched(method, path, body)
    return bool(found) and found[2] in ("direct", "stored")      # "stored": `require_direct_setting` reads the rule


def require_direct_setting(c, method, path, body, trial=None, base=""):
    """403 unless the setting this cited write changes is direct once read against what is stored (a wake-rule update
    keeps the stored rule's action; a repository change is tried as the person's own click, `trial`): the same class
    `plan` gave it, with the same full link (`base`, the public URL)."""
    from . import settings_catalog
    found = settings_catalog.stored_class(c, method, normalize_path(path) or "", _fields(body), trial)
    if not found or found[2] != "direct":
        e = found[0] if found else None
        link = e and ((base.rstrip("/") + "/" + e.card) if base else e.card)
        raise Problem("person_only" if found and found[2] == "person" else "confirm_required",
                      (e.why if e and e.why else "That setting needs the person's own yes") + ": open " + link
                      if e else "That setting needs the person's own yes", 403)


# An org change the person asked for in their chat (the redesign's Strategy Bot suggests them in words; the person says
# which to make): a group made, renamed, nested, filled, led or deleted, and who a teammate reports to. It runs on their
# cited words with exactly their rights, which the route checks as for their own click: the owner changes anything, a
# lead moves teammates inside what it leads, anyone else is refused there. A bot asking, or the Strategy Bot on its own,
# never borrows a person's rights: its own are refused (backend/groups.py `_may_change`).
_ORG = [(m, re.compile(p)) for m, p in (
    ("POST", r"/api/v2/groups"), ("PATCH", r"/api/v2/groups/[^/]+"), ("DELETE", r"/api/v2/groups/[^/]+"))]
_REPORTS = (re.compile(r"/api/v2/(?:people|humans)/[^/]+"), re.compile(r"/api/v2/bots/[^/]+/definition"))


def instructed_org(method, path, body=None):
    """Whether this write is an org change that may run on the person's cited words: a group write, a change of who a
    person or a bot reports to, or an org rule the person asked for (backend/org_rules.py), and nothing else."""
    path = normalize_path(path)
    if not path:
        return False
    if any(m == method and p.fullmatch(path) for m, p in _ORG):
        return True
    if method == "POST" and path in ("/api/v2/rules", "/api/v2/rules/read", "/api/v2/rules/ask"):
        return True
    fields = _fields(body)
    return (method == "POST" and any(p.fullmatch(path) for p in _REPORTS) and "reports_to" in fields
            and not set(fields) - {"reports_to", "expected_revision"})


def instructed_route(method, path, body=None, decides=False):
    """Whether this write may run on the person's cited words. `decides`: their Assistant may decide approvals."""
    path = normalize_path(path)
    if method != "POST" or not path or path == "/api/v2/tasks/dry-run":
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
            message_id = (H.approval(c, m.group(1)) or {}).get("message_id")
        elif (m := re.fullmatch(r"/api/v2/messages/([^/]+)/answer", target)):
            message_id = m.group(1)
        else:
            continue
        if (H.message(c, message_id) or {}).get("to_actor") != who.actor:
            raise Problem("forbidden", "That approval or question is not addressed to you", 403)


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


def approval_words(c, who, cited, assistant_bot, approval):
    """What an approval the Assistant decides for the person is recorded with: their words and the read-back they
    answered. The cited message must be a plain yes, the last thing anyone else said in the room before it (since the
    person's previous message) the read-back the server wrote, listing this approval, and nothing the person wrote
    after it: "wait, no" takes it back. Their own sentence, words in a file, a page, a bot's message or the read-back
    alone never approve, and the Assistant never approves what it asked for itself."""
    bot = "bot:" + assistant_bot
    if (H.approval(c, approval) or {}).get("requested_by") == bot:
        raise Problem("own_request", "You asked for this approval yourself, so it stays the person's click: propose it "
                      "on a card (`hub assistant propose`)", 403)
    msg = H.message(c, cited) or {}
    words = str(msg.get("body") or "").strip()
    room, at = msg.get("conversation_id"), str(msg.get("created") or "")
    back = None
    later = c.execute("SELECT 1 FROM messages WHERE conversation_id=? AND from_actor=? AND created>? AND id<>? "
                      "AND deleted_at IS NULL LIMIT 1", (room, who.actor, at, cited)).fetchone()
    if len(words) <= 60 and AFFIRM.fullmatch(words) and not later:
        before = c.execute("SELECT created FROM messages WHERE conversation_id=? AND from_actor=? AND created<? "
                           "AND id<>? AND deleted_at IS NULL ORDER BY created DESC LIMIT 1",
                           (room, who.actor, at, cited)).fetchone()
        last = c.execute("SELECT m.id, m.from_actor, m.body, e.detail_json FROM messages m LEFT JOIN events e "
                         "ON e.target=m.id AND e.action=? WHERE m.conversation_id=? AND m.from_actor<>? AND m.created>=? AND m.created<=? "
                         "AND m.id<>? AND m.deleted_at IS NULL ORDER BY m.created DESC, m.rowid DESC LIMIT 1",
                         (READ_BACK, room, who.actor, before["created"] if before else "", at, cited)).fetchone()
        if last and last["from_actor"] == bot and last["detail_json"]:
            back = {"body": last["body"], "approves": (json.loads(last["detail_json"]) or {}).get("approves") or []}
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
    room = H.conversation(c, msg["conversation_id"]) if msg else None
    refs = (msg or {}).get("refs") or {}
    # Only the person's own Assistant room: the owner's earlier Strategy Bot chat (`rooms.STRATEGY_ROOM`) is history.
    if (not msg or msg["from_actor"] != who.actor or msg["kind"] != "say" or not refs.get("assistant")
            or refs.get("via") or not room or room.get("scope") != rooms.PERSONAL
            or room.get("owner_actor") != who.actor or room.get("room_key") != rooms.ASSISTANT_ROOM
            or room.get("closed_at")):
        refuse("Cite a message the person sent you in their own Assistant chat")
    if str(msg["created"]) < H.shift(H.now(), seconds=-INSTRUCTION_TTL_S):
        refuse(f"That message is more than {INSTRUCTION_TTL_S // 60} minutes old; ask the person again")
    return {"for": who.actor, "cited": msg["id"], "run": "asked"}


def require_on_task(c, auth, who, path):
    """403 unless a task change, comment or link the person asked for is on a task they are on (its owner, requester or
    in one of its roles): an Assistant acts only as its person, never on another person's task, even when asked to. The
    owner's Assistant acts as the owner, who may act on any task. A task they may not read is a 404,
    as the route answers it: the refusal never says it is there."""
    m = re.fullmatch(r"/api/v2/tasks/([^/]+)(?:/(?:comments|links))?", normalize_path(path) or "")
    if not m or who.role == "owner":
        return
    row = H.task(c, m.group(1))
    if not row:
        return          # the route answers for a task that is not there
    auth.task_row(c, who, row)
    from . import task_roles
    people = {row["owner"], row["requester"]} | {a for actors in task_roles.roles_of(c, row["id"]).values()
                                                 for a in actors}
    if who.actor not in people:
        raise Problem("on_behalf_of", "That task is another person's: an Assistant acts only on the person's own "
                      "tasks; ask them to message its owner or comment on it themselves", 403)


# What may be proposed at all (method, normalized path). Anything else, and above all tokens, sign-in,
# runner enrollment, this assistant and anything that hands back a secret, can never be proposed.
_PROPOSABLE = [(m, re.compile(p)) for m, p in (
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
)]
# An inbox is an email address in its path (`inboxes/ana@acme.example`): the one `@` a route may carry.
_SHAPE = re.compile(r"/api/v2/(?:inboxes/[A-Za-z0-9_.+\-]+@[A-Za-z0-9.\-]+|[A-Za-z0-9_.:\-/]+)")


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
            noun, created = f"{row.get('kind') or ''} approval".strip(), row.get("created")
        elif (m := re.fullmatch(r"/api/v2/messages/([^/]+)/answer", path)):
            verb, noun, created = "Answer", "bot question", (H.message(c, m.group(1)) or {}).get("created")
        else:
            m = re.fullmatch(r"/api/v2/tasks/([^/]+)(?:/(comments|links))?", path)
            sub = m.group(2) if m else None
            verb = ({"comments": "Comment on", "links": "Link"}.get(sub) or ("Close" if body.get("close") else None)
                    or {"done": "Mark done", "declined": "Decline"}.get(body.get("status"), "Change"))
            noun, created = "task", (H.task(c, m.group(1)) or {}).get("created") if m else None
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
        return (H.bot(c, slug) or {}).get("display_name") or slug

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
        return (f"{verb} the {row['kind']} approval requested by {who(row['requested_by'])}"
                + (f" ({detail})" if detail else "") + f", {age(row['created'])}"
                + (f"; reason: {preview(body['note'], 120)}" if body.get("note") else ""))

    def answer_line(mid):
        asked = H.message(c, mid) or {}
        return (f"Answer {who(asked.get('from_actor'))}'s question “{preview(asked.get('body'), 80)}”"
                + (f", {age(asked['created'])}" if asked.get("created") else "") + f": {preview(body.get('text'), 120)}")
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
        (r"/api/v2/(?:people|humans)/([^/]+)", lambda g: f"Edit person {who(g[0])}: " + fields(body)),
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
        (r"/api/v2/files/([^/]+)", lambda g: "Delete a file for good" if method == "DELETE" else "Change a file's listing"),
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
            raise Problem("operation", "A bulk card holds only task changes, comments and links, approval decisions and "
                          "answers to bots' questions (POST /api/v2/tasks/<id>, /comments, /links, /api/v2/approvals/<id>, "
                          "/api/v2/messages/<id>/answer); propose anything else on its own", 422)
        out.append({"method": method, "path": path, "body": {k: v for k, v in sent.items() if k != "on_behalf_of"}})
    return out


def covers(row, method, path):
    """Whether a confirmed proposal covers this request: its own operation, or one step of a bulk card."""
    if row["path"] == BATCH_PATH:
        steps = json.loads(row["body_json"] or "{}").get("actions") or []
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
    if proposer == "assistant" and not any(m == method and p.fullmatch(path) for m, p in _PROPOSABLE):
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
        # A bulk card's own model takes any step body: each step is checked against its own route, and one refused
        # step refuses the whole card.
        for s in body["actions"] if path == BATCH_PATH else ():
            refuse_unrunnable(routes, s["method"], s["path"], s["body"])
    require_addressed(c, who, path, body)
    if who.via == "assistant" and not who.confirmed and approvals_in(path, body):
        require_named_approval(c, who, asked)
    same = c.execute("SELECT * FROM assistant_actions WHERE owner=? AND status='pending' AND method=? AND path=? "
                     "AND body_json=? AND proposer=?", (who.actor, method, path, json.dumps(body), proposer)).fetchone()
    if same:
        # The same card again is the same card: a retried command never leaves two to click.
        return {"action": action_view(dict(same), c), "message_id": None, "repeated": True}
    said, diff = describe(c, method, path, body)
    if not what and (m := re.fullmatch(r"/api/v2/bots/([^/]+)/repositories", path)) and method == "PUT":
        # What confirming does, from the change tried as this person and rolled back, not read off the request.
        from .repositories import preview_change
        what = preview_change(c, settings, who.actor, m.group(1), body)
    what = what or said         # a route that tried the change first says what it does (backend/repositories.py)
    if path == BATCH_PATH:
        # One line per step: what it does to which task, and the fields it changes.
        diff = groups(c, body["actions"])
        for s in body["actions"]:
            line, changes = describe(c, s["method"], s["path"], s["body"])
            fields = changes or [{"field": k, "new": v} for k, v in s["body"].items()]
            diff.append({"field": line, "new": ", ".join(f"{d['field']}: {d['new']}" for d in fields
                                                          if d["field"] != "version")})
    elif not re.fullmatch(r"/api/v2/tasks/[^/]+", path):
        # The card lists every field the request carries, so nothing that runs is missing from what is confirmed.
        # (A task update lists what changes: its old and new values.)
        diff = [{"field": k, "new": v} for k, v in body.items()]
    if shown is not None:
        diff = shown
    row = {"id": H.new_id(), "owner": who.actor, "conversation_id": room["id"], "summary": summary,
           "method": method, "path": path, "body_json": json.dumps(body),
           "status": "pending", "proposed_via": proposer, "created": H.now(),
           "description": what, "diff_json": json.dumps(diff, default=str), "proposer": proposer}
    c.execute("INSERT INTO assistant_actions(id,owner,conversation_id,summary,method,path,body_json,status,"
              "proposed_via,created,description,diff_json,proposer) VALUES(:id,:owner,:conversation_id,:summary,"
              ":method,:path,:body_json,:status,:proposed_via,:created,:description,:diff_json,:proposer)", row)
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
    row = c.execute("SELECT id FROM conversations WHERE scope='personal' AND owner_actor=? AND room_key=? "
                    "AND closed_at IS NULL ORDER BY created LIMIT 1", (actor, rooms.ASSISTANT_ROOM)).fetchone()
    return H.conversation(c, row["id"]) if row else None


def own_room(c, who, conversation_id):
    """Whether this conversation is the caller's own Assistant room (and the caller is a person)."""
    if not conversation_id or who.role not in ("owner", "human") or who.via:
        return False
    room = H.conversation(c, conversation_id)
    return bool(room and room.get("scope") == rooms.PERSONAL and room.get("owner_actor") == who.actor
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
                    "ORDER BY created LIMIT 1", (rooms.STRATEGY_ROOM,)).fetchone()
    room = H.conversation(c, row["id"]) if row else None
    return room if room and (actor is None or room.get("owner_actor") == actor) else None


def move_strategy_room(c, actor, to):
    """The earlier Strategy Bot chat goes with the owner seat to `to`, the new owner, as history. A card still in it
    is withdrawn: it ran as the old owner, who is no longer the owner."""
    room = find_strategy_room(c)
    if not room or room.get("owner_actor") == to:
        return room
    before = room.get("owner_actor")
    c.execute("UPDATE conversations SET owner_actor=?, participants_json=? WHERE id=?",
              (to, json.dumps([to, "bot:" + strategy_bot.SLUG]), room["id"]))
    c.execute("UPDATE assistant_actions SET status='cancelled', decided_at=? WHERE conversation_id=? AND owner=? "
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
                         "AND a.owner=v.owner_actor ORDER BY a.created", (rooms.STRATEGY_ROOM,)).fetchall():
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
    result = json.loads(row["result_json"]) if row.get("result_json") else None
    view = {"id": row["id"], "owner": row["owner"], "conversation_id": row["conversation_id"],
            "summary": row["summary"], "method": row["method"], "path": row["path"],
            "body": json.loads(row["body_json"] or "{}"), "status": row["status"],
            "proposed_via": row["proposed_via"], "proposer": row.get("proposer") or row["proposed_via"],
            "created": row["created"], "decided_at": row.get("decided_at"),
            "description": row.get("description") or "", "diff": json.loads(row.get("diff_json") or "[]"),
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
            view["description"] = plain_request((H.bot(c, m.group(1)) or {}).get("display_name") or m.group(1), view["body"])
        if OLD_REPO_SUMMARY.match(view["summary"] or ""):
            view["original_summary"], view["summary"] = view["summary"], ""
    return view


def actions_for(c, actor, ids):
    if not ids:
        return {}
    rows = c.execute("SELECT * FROM assistant_actions WHERE owner=? AND id IN (%s)" % ",".join("?" * len(ids)),
                     (actor, *ids)).fetchall()
    return {row["id"]: action_view(dict(row), c) for row in rows}


# ------------------------------------------------------------------ the fast path
STOP = set("a an the of to in on for and or is are was were be do does did i me my we our you your it its this that these "
           "those with about from at by as how what which who where when can could should would please tell show give "
           "get have has had there here into out up any some".split())
KINDS = {"task": "tasks", "tasks": "tasks", "issue": "tasks", "doc": "docs", "docs": "docs", "document": "docs",
         "documents": "docs", "meeting": "meetings", "meetings": "meetings", "call": "meetings",
         "file": "files", "files": "files", "person": "people", "people": "people", "bot": "bots", "bots": "bots"}
PAGES = {"tasks": "#/tasks", "board": "#/board", "meetings": "#/meetings", "updates": "#/updates",
         "docs": "#/docs", "documentation": "#/docs", "settings": "#/settings", "help": "#/help", "goals": "#/goals",
         "integrations": "#/integrations", "health": "#/settings", "overview": "#/overview", "changelog": "#/changelog"}

WAITING = re.compile(r"\b(waiting (on|for) me|needs? (my|me)\b|need(s)? you|my (inbox|queue|to-?do)|"
                     r"what should i (do|work on)|what do i need to|anything (for me|waiting)|pending (for|on) me)")
GREETING = re.compile(r"^(?:hi|hello|hey|good morning|good afternoon|good evening)$")
TASK_TYPE_INVENTORY = re.compile(
    r"^(?:for\s+[a-z][a-z0-9&'-]{0,40},\s*)?(?:what|which)\s+(?:kind\s+of\s+)?"
    r"(?:task\s+types?|types?\s+of\s+tasks?)\s+"
    r"(?:do\s+we\s+use(?:\s+here)?|does\s+(?:this\s+)?(?:team|workspace)\s+use|are\s+available|exist)"
)
TASK_TYPE_INVENTORY_ARE = re.compile(
    r"^(?:what|which)\s+are\s+(?:the\s+)?(?:task\s+types?|types?\s+of\s+tasks?)"
    r"(?:\s+(?:here|in\s+this\s+workspace))?$"
)
NAVIGATE = re.compile(r"^(open|go to|goto|take me to|show me|where is|where's|wheres|navigate to|pull up|bring up)\b\s*(.*)$")
SEARCH = re.compile(r"^(search( for)?|find|look ?up|look for)\b\s*(.*)$")
HELP = re.compile(r"^(help\b[:,\s]*|how (do|can|should|would|does|to)\b|how'?s? (it|tico)\b|explain\b|what is a\b|what are\b|"
                  r"what does .* mean|where do i\b|can i\b.*\?|what'?s the difference)")
BOT_TODAY = re.compile(r"\b(today|this morning|last 24|yesterday|lately|so far)\b|\bwhat (did|has|have|is|was)\b.*\b(do|done|doing|up to|work)")
ACTION_WORDS = re.compile(r"\b(create|make|add|assign|ask|tell|send|approve|decline|reject|archive|delete|remove|"
                          r"build|set up|schedule|publish|post|change|update|rename|close|cancel|remind|clear|dismiss|answer)\b")


def words(text):
    return [w for w in re.findall(r"[\w'-]+", text.casefold()) if w not in STOP]


def route(text, bots=()):
    """(intent, arg) for the fast path, or (None, None) when the model should take it.

    Keyword and structure first; a verb that asks for something to be done always goes to the model."""
    t = re.sub(r"\s+", " ", text.strip().casefold()).strip(" ?!.")
    if not t or len(t) > 400:
        return None, None
    if GREETING.fullmatch(t):
        return "greeting", None
    # "for <team>," can be any word, a verb too ("for create, ..."), so the action check still applies.
    if (TASK_TYPE_INVENTORY.fullmatch(t) or TASK_TYPE_INVENTORY_ARE.fullmatch(t)) and not ACTION_WORDS.search(t):
        return "task_types", None
    if WAITING.search(t) and not ACTION_WORDS.search(t):     # "clear my queue" is work, not a question
        return "waiting", None
    named = next((b for b in bots if b["slug"] in t.split() or b["slug"].replace("-", " ") in t
                  or b["name"].casefold() in t), None)
    if named and BOT_TODAY.search(t) and not ACTION_WORDS.search(t):
        return "bot_today", named["slug"]
    m = NAVIGATE.match(t)
    if m and m.group(2).strip():
        return "navigate", m.group(2).strip()
    m = SEARCH.match(t)
    if m and m.group(3).strip() and not ACTION_WORDS.search(m.group(3)):
        return "search", m.group(3).strip()
    if HELP.search(t) and not re.search(r"\b(for me|on my behalf)\b", t):
        return "help", t
    return None, None


def link(title, href):
    return "[" + re.sub(r"[\[\]]", "", str(title or "Untitled")).strip()[:120] + "](" + href + ")"


def _terms(arg):
    hint, terms = None, []
    for w in words(arg):
        if w in KINDS:
            hint = hint or KINDS[w]
        else:
            terms.append(w)
    return hint, terms


# Cards whose answer carries a one-time value for the person who taps: the route and the fields shown to them.
SHOWN_ONCE = {"/api/v2/enrollments": ("code", "expires", "operator")}


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
    """Reads Tico's own API in-process as the person, with the credential they came in with, so a
    fast answer can show exactly what the page would: never more."""

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


def _task_href(item):
    return "#/task/" + quote(str(item.get("task_id") or item.get("id")), safe="")


async def fast_waiting(api, who, bots):
    data = await api.get("needs-you") or {}
    items = data.get("items") or []
    if not items:
        return "Nothing is waiting on you.", []
    label = {"task": "Task", "question": "Question", "declined": "Declined", "approval": "Approval"}
    links, lines = [], []
    for item in items[:8]:
        href = _task_href(item) if item.get("kind") != "approval" or item.get("task_id") else "#/tasks"
        links.append({"title": item.get("title") or "", "href": href, "kind": "task"})
        lines.append(f"- {label.get(item.get('kind'), 'Item')}: {link(item.get('title'), href)}")
    more = len(items) - 8
    head = f"{len(items)} thing{'s' if len(items) != 1 else ''} waiting on you" + (":" if items else ".")
    return head + "\n" + "\n".join(lines) + (f"\n…and {more} more in {link('Tasks', '#/tasks')}." if more > 0 else ""), links


async def search_all(api, arg, bots, limit=4):
    hint, terms = _terms(arg)
    if not terms:
        return hint, terms, {}
    q = " ".join(terms)

    def matches(*parts):
        blob = " ".join(str(p or "") for p in parts).casefold()
        return all(t in blob for t in terms)

    async def tasks():
        data = await api.get("tasks", limit=500) or {}
        rows = [t for t in data.get("tasks") or [] if matches(t.get("title"), t.get("id"))]
        rows.sort(key=lambda t: (t.get("status") in ("done", "closed", "declined"), t.get("updated") or ""), reverse=False)
        return [{"title": t["title"], "href": _task_href(t), "note": t.get("status")} for t in rows[:limit]]

    async def docs():
        data = await api.get("context/search", q=q, source="docs", limit=limit) or {}
        return [{"title": d.get("title"), "href": d.get("url") or "#/docs", "note": d.get("collection")}
                for d in (data.get("results") or [])[:limit]]

    async def meetings():
        data = await api.get("meetings/search", q=q, limit=limit) or {}
        return [{"title": m.get("title"), "href": m.get("url") or "#/meetings", "note": (m.get("started") or m.get("created") or "")[:10]}
                for m in (data.get("results") or [])[:limit]]

    async def org():
        data = await api.get("org") or {}
        out = {"people": [], "bots": []}
        for p in data.get("people") or []:
            if not p.get("hidden") and matches(p.get("name"), p.get("title"), p.get("id")):
                out["people"].append({"title": p.get("name") or p["id"], "href": "#/person/" + quote(p["id"], safe=""),
                                      "note": p.get("title")})
        for b in data.get("bots") or []:
            if b.get("status") != "archived" and matches(b.get("display_name"), b.get("id"), b.get("description")):
                out["bots"].append({"title": b.get("display_name") or b["id"], "href": "#/bot/" + quote(b["id"], safe=""),
                                    "note": b.get("status")})
        return {k: v[:limit] for k, v in out.items()}

    async def files():
        found = []

        async def one(bot):
            data = await api.get(f"bots/{bot['slug']}/files", limit=50) or {}
            for f in data.get("files") or []:
                if matches(f.get("title"), f.get("name")):
                    op = f.get("open") or {}
                    href = op.get("url") if op.get("type") == "external" and str(op.get("url", "")).startswith("https://") \
                        else "#/bot/" + quote(bot["slug"], safe="")
                    found.append({"title": f.get("title") or f.get("name"), "href": href, "note": bot["name"]})
        await asyncio.gather(*(one(b) for b in bots[:25]))
        return found[:limit]

    wanted = [hint] if hint else ["tasks", "docs", "meetings", "people", "bots", "files"]
    jobs = {"tasks": tasks, "docs": docs, "meetings": meetings, "files": files}
    results = {}
    coros = {k: jobs[k]() for k in wanted if k in jobs}
    if "people" in wanted or "bots" in wanted:
        coros["org"] = org()
    done = await asyncio.gather(*coros.values(), return_exceptions=True)
    for key, value in zip(coros, done):
        if isinstance(value, Exception):
            continue
        if key == "org":
            for k in ("people", "bots"):
                if k in wanted and value[k]:
                    results[k] = value[k]
        elif value:
            results[key] = value
    return hint, terms, results


GROUP = {"tasks": "Tasks", "docs": "Docs", "meetings": "Meetings", "files": "Files", "people": "Humans", "bots": "Bots"}


def _rows(results):
    text, links = [], []
    for key in ("tasks", "docs", "meetings", "files", "people", "bots"):
        if key not in results:
            continue
        text.append(f"**{GROUP[key]}**")
        for r in results[key]:
            text.append(f"- {link(r['title'], r['href'])}" + (f" ({r['note']})" if r.get("note") else ""))
            links.append({"title": r["title"], "href": r["href"], "kind": key})
    return "\n".join(text), links


async def fast_search(api, who, bots, arg):
    hint, terms, results = await search_all(api, arg, bots)
    if not terms:
        return None, []
    if not results:
        return f"I found nothing for “{' '.join(terms)}”. Try fewer words, or ask me to look into it.", []
    body, links = _rows(results)
    return f"Found for “{' '.join(terms)}”:\n{body}", links


async def fast_navigate(api, who, bots, arg):
    page = PAGES.get(re.sub(r"^(the|my)\s+|\s+(page|tab)$", "", arg.strip()))
    if page:
        return f"Here it is: {link(arg.strip().title(), page)}", [{"title": arg, "href": page, "kind": "page"}]
    hint, terms, results = await search_all(api, arg, bots, limit=3)
    if not terms:
        return None, []
    flat = [(k, r) for k, rows in results.items() for r in rows]
    if not flat:
        return f"I could not find “{' '.join(terms)}”. Try a search: “search {' '.join(terms)}”.", []
    kind, best = flat[0]
    others = flat[1:5]
    text = f"Opening the best match: {link(best['title'], best['href'])}"
    if others:
        text += "\nOthers:\n" + "\n".join(f"- {link(r['title'], r['href'])} ({k})" for k, r in others)
    return text, [{"title": best["title"], "href": best["href"], "kind": kind, "open": True}] + \
        [{"title": r["title"], "href": r["href"], "kind": k} for k, r in others]


async def fast_bot_today(api, who, bots, slug):
    bot = next((b for b in bots if b["slug"] == slug), None)
    if not bot:
        return None, []
    since = H.shift(H.now(), hours=-24)
    updates = await api.get("updates", bot=slug, limit=10) or {}
    recent = [u for u in updates.get("updates") or [] if str(u.get("created") or "") >= since]
    tasks = await api.get("tasks", owner=slug, limit=100) or {}
    touched = [t for t in tasks.get("tasks") or [] if str(t.get("updated") or "") >= since]
    lines, links = [], []
    for u in recent[:3]:
        lines.append(f"- {u.get('kind', 'update').title()} update: {link(u.get('headline') or 'Update', '#/updates')}")
    for t in touched[:6]:
        lines.append(f"- {link(t['title'], _task_href(t))} ({t.get('status')})")
        links.append({"title": t["title"], "href": _task_href(t), "kind": "task"})
    href = "#/bot/" + quote(slug, safe="")
    links.append({"title": bot["name"], "href": href, "kind": "bots"})
    if not lines:
        return f"{link(bot['name'], href)} has posted no update and touched no task in the last 24 hours.", links
    return f"{link(bot['name'], href)} in the last 24 hours:\n" + "\n".join(lines), links


async def fast_task_types(api):
    """List only the task types the authenticated Tico API returned for this workspace."""
    data = await api.get("task-types")
    rows = data.get("types") if isinstance(data, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get("name"), str)
                                         or not row["name"].strip() for row in rows):
        return "I couldn't read the task types for this Tico workspace. Please try again later.", []
    if not rows:
        return "No task types are configured in this Tico workspace.", []
    return "Task types in this Tico workspace:\n" + "\n".join(f"- {row['name']}" for row in rows), []


_SECTIONS = None


def doc_sections():
    """docs/*.md split at their second-level headings: (file, heading, anchor, text)."""
    global _SECTIONS
    if _SECTIONS is None:
        out = []
        try:
            for path in sorted(DOCS_DIR.glob("*.md")):
                text = path.read_text(errors="replace")
                title = (re.match(r"#\s+(.+)", text) or [None, path.stem])[1]
                parts = re.split(r"(?m)^##\s+", text)
                out.append((path.name, title, "", parts[0]))
                for part in parts[1:]:
                    heading, _, body = part.partition("\n")
                    anchor = re.sub(r"[^\w\- ]", "", heading.casefold()).strip().replace(" ", "-")
                    out.append((path.name, heading.strip(), anchor, body))
        except OSError:
            out = []
        _SECTIONS = out
    return _SECTIONS


def help_answer(question):
    terms = [w for w in words(question) if w not in ("help", "explain", "tico", "use", "using")]
    if not terms:
        return None, []
    scored = []
    for file, heading, anchor, body in doc_sections():
        h, b = heading.casefold(), body.casefold()
        score = sum(3 * (t in h) + min(b.count(t), 3) + (2 * (t in file) if t in file else 0) for t in terms)
        hits = sum(1 for t in terms if t in h or t in b or t in file)
        if hits >= max(1, (len(terms) + 1) // 2) and score >= 3:
            scored.append((score, file, heading, anchor, body))
    if not scored:
        return None, []
    scored.sort(key=lambda s: -s[0])
    _, file, heading, anchor, body = scored[0]
    clean = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", body)
    clean = re.sub(r"`", "", clean)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", clean) if p.strip() and not p.strip().startswith("|")]
    excerpt = ""
    for p in paragraphs:
        excerpt += ("\n\n" if excerpt else "") + re.sub(r"\s*\n\s*", " ", p) if not p.startswith(("-", "*", "1.")) else \
            ("\n" if excerpt else "") + p
        if len(excerpt) > 450:
            break
    excerpt = excerpt[:700].rstrip()
    href = GITHUB_DOCS + file + ("#" + anchor if anchor else "")
    links = [{"title": f"docs/{file}", "href": href, "kind": "docs"}]
    text = f"From {link(heading, href)}:\n\n{excerpt}"
    for _, f2, h2, a2, _b in scored[1:3]:
        if (f2, h2) != (file, heading):
            u = GITHUB_DOCS + f2 + ("#" + a2 if a2 else "")
            text += f"\n\nSee also: {link(h2, u)}"
            links.append({"title": h2, "href": u, "kind": "docs"})
    return text, links


INTENT_CHOICES = {"waiting": "They ask what is waiting on them, what needs their attention or decision.",
                  "search": "They want to find or list tasks, docs, meetings, files, people or bots by a word.",
                  "navigate": "They want to open a specific page or item, or ask where something is.",
                  "help": "They ask how to do something in Tico or how Tico works.",
                  "other": "Anything else: a request to do something, write something, or think something through."}


async def decide(api, text):
    """A `choice` decision question when the company has a decisions provider; else (None, 0)."""
    try:
        info = await api.get("decisions")
        if not info or not info.get("configured") or ACTION_WORDS.search(text.casefold()) or len(text) > 200:
            return None
        response = await api.call("POST", "decisions", body={
            "state": {"message": text}, "label": "assistant-intent@1",
            "questions": {"intent": {"type": "choice", "criteria": INTENT_CHOICES,
                                     "instructions": "What does this message to a company assistant ask for?"}}})
        if response.status_code != 200:
            return None
        answer = (response.json().get("answers") or {}).get("intent") or {}
        if answer.get("choice") in ("waiting", "search", "navigate", "help") and float(answer.get("confidence") or 0) >= 0.75:
            return answer["choice"]
    except (httpx.HTTPError, ValueError, TypeError):
        return None
    return None


async def fast_answer(api, who, bots, text):
    """(intent, reply text, links), or (None, None, []) when the model should answer."""
    intent, arg = route(text, bots)
    if intent is None:
        intent = await decide(api, text)
        arg = text if intent else None
    if intent is None:
        return None, None, []
    if intent == "greeting":
        reply, links = "Hi! How can I help?", []
    elif intent == "task_types":
        reply, links = await fast_task_types(api)
    elif intent == "waiting":
        reply, links = await fast_waiting(api, who, bots)
    elif intent == "bot_today":
        reply, links = await fast_bot_today(api, who, bots, arg)
    elif intent == "navigate":
        reply, links = await fast_navigate(api, who, bots, arg)
    elif intent == "search":
        reply, links = await fast_search(api, who, bots, arg)
    else:
        reply, links = help_answer(arg)
    if not reply:
        return None, None, []
    return intent, reply, links


# ------------------------------------------------------------------ the routes
def install(app, store, auth, mutate, onboarding):
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

    def bots_for(c, who):
        access = auth.bot_accesses(c, who)
        return [{"slug": r["slug"], "name": r["display_name"] or r["slug"]} for r in H.bots(c)
                if r["state"] not in ("archived",) and access.get(r["slug"], auth.FULL)["see"]]

    def view(c, who, room):
        from .views import conversation_snapshot
        snap = conversation_snapshot(c, room["id"], settings=settings) if room else {"messages": [], "execution": None, "has_more": False,
                                                                     "next_before": None}
        visuals.for_reader(app, settings, c, who, snap["messages"])
        ids = [m["refs"]["action"] for m in snap["messages"] if isinstance(m.get("refs"), dict) and m["refs"].get("action")]
        pending = [action_view(dict(r), c) for r in c.execute(
            "SELECT * FROM assistant_actions WHERE owner=? AND status='pending' ORDER BY created", (who.actor,))]
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

    def post_message_sync(request, body, who, intent, reply, links, uploads=None):
        def work(c):
            bot = chat_bot_for(c, who)
            room = chat_room(c, settings, who)
            said, kind, fast_links = reply, intent, links
            # The canned hello only opens a chat: a "hi" after anything the person said goes to the model, which knows
            # what came before, rather than repeating the same line.
            if kind == "greeting" and c.execute("SELECT 1 FROM messages WHERE conversation_id=? AND from_actor=? LIMIT 1",
                                                (room["id"], who.actor)).fetchone():
                said, kind, fast_links = None, None, []
            refs = {"assistant": True}
            if getattr(body, "place", None) == "updates":
                refs["updates"] = True        # its turn is handed the updates they may read (backend/updates_chat.py)
            page = getattr(body, "page", None)
            if page and page.path.startswith("#/"):
                refs["page"] = {"path": page.path, "title": page.title}   # where they asked from (runner-rs prompt.rs)
            if said is not None:
                refs["quiet"] = True
            message = H.say(c, who.actor, "bot:" + bot, body.text, conversation_id=room["id"],
                            kind="say", refs=refs)
            if uploads is not None:
                message = app.state.uploads.attach(c, who, bot, message, uploads, body.titles)
            out = {"message": message, "reply": None, "fast": said is not None, "intent": kind}
            if said is not None:
                out["reply"] = H._write_message(c, "bot:" + bot, who.actor, said, room, "say",
                                                {"assistant": True, "fast": True, "intent": kind, "links": fast_links},
                                                message["id"], None, delivered_at=H.now())
                H.event(c, who.actor, "assistant.fast", message["id"], {"intent": kind})
            return out
        if uploads is not None:
            return app.state.uploads.write(request, body, uploads, work)
        return mutate(request, body, work)

    @app.post("/api/v2/assistant/messages")
    async def assistant_message(request: Request, body: M.AssistantMessage):
        who = own(request)

        def read():
            with store.read() as c:
                return availability(c, settings, who), bots_for(c, who)
        info, bots = await asyncio.to_thread(read)
        if not info["available"]:
            raise Problem("assistant_off", "The " + info["name"] + " is off", 409)
        # A question from the Updates chat is about the updates its turn is handed, which the quick answers never read.
        intent, reply, links = (None, None, []) if body.place == "updates" else \
            await fast_answer(Internal(request), who, bots, body.text)
        return await asyncio.to_thread(post_message_sync, request, body, who, intent, reply, links)

    # Files and pictures in a person's own Assistant chat (the Ask panel, the Assistant page), stored on the message like
    # any bot chat's, in their room's conversation. They skip the quick answers, which read words only.
    @app.post("/api/v2/uploads/assistant/messages", openapi_extra=upload_contract(M.AssistantUpload))
    async def assistant_upload(request: Request):
        who = own(request)

        def admit(body):
            # Before any byte is stored, so a refused send keeps nothing; the write checks again in its transaction.
            with store.read() as c:
                chat_bot_for(c, who)
        body, uploads = await app.state.uploads.form(request, M.AssistantUpload, admit)
        return await asyncio.to_thread(post_message_sync, request, body, who, None, None, [], uploads)

    @app.post("/api/v2/assistant/turn-on")
    def turn_on(request: Request, body: M.Empty):
        who = own(request)
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
            c.execute("INSERT INTO preferences(actor,key,value_json,updated) VALUES(?,?,?,?) ON CONFLICT(actor,key) "
                      "DO UPDATE SET value_json=excluded.value_json, updated=excluded.updated",
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
            rows = c.execute("SELECT a.id FROM approvals a JOIN messages m ON m.id=a.message_id WHERE a.decided_by=? "
                             "AND a.decided_via='assistant' AND m.to_actor=? AND a.decided_at>=? "
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
        the ids: only those may then be approved, on their yes (`approval_words`)."""
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
                if not row or (H.message(c, row.get("message_id")) or {}).get("to_actor") != who.actor:
                    raise Problem("forbidden", "That approval is not addressed to you", 403)
                if row["decision"]:
                    raise Problem("conflict", f"Approval {aid} is already decided", 409)
                if row["requested_by"] == "bot:" + bot:
                    raise Problem("own_request", "You asked for this approval yourself, so it stays the person's "
                                  "click: propose it on a card (`hub assistant propose`)", 403)
                if row["kind"] == "support":
                    raise Problem("approve_support", "A report to the Tico team is sent only on the person's own "
                                  "click, so leave it out of the read-back", 403)
                if cut := too_long(row.get("payload") or {}):
                    raise Problem("approve_too_long", f"Approval {aid} has more in {', '.join(field_value(k, 40) for k in cut)} "
                                  "than the read-back can show in full, so it stays the person's click: leave it out", 409)
                what, _ = describe(c, "POST", "/api/v2/approvals/" + aid, {"decision": "approved"})
                lines.append("- " + plain_line(what))
            n = len(ids)
            text = (f"I'll approve {'this' if n == 1 else f'these {n}'} for you:\n" + "\n".join(lines)
                    + "\n\nReply yes to approve exactly " + ("this" if n == 1 else "these")
                    + "; any other reply approves nothing.")
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
        who = person(request)

        def work(c):
            if normalize_path(body.path) == "/api/v2/seats/batch" and who.role != "owner":
                raise Problem("forbidden", "Only the owner may confirm a chart-model re-org", 403)
            if normalize_path(body.path) in ("/api/v2/chat/" + settings.assistant_bot,
                                             "/api/v2/chat/" + strategy_bot.SLUG) \
                    or not valid_operation(body.method, body.path, settings):
                raise Problem("operation", "That is not something the " + settings.assistant_name
                              + " can propose: it is not on the list of routes a person confirms (never tokens, "
                              "sign-in, enrollment or this assistant)", 422)
            room = chat_room(c, settings, who)
            return create_proposal(c, settings, who, room, "assistant", body.summary, body.method, body.path, body.body,
                                   request.headers.get("x-tico-on-behalf-of"), routes=request.app.routes)
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
        """A confirm that never settled (a restart, a dropped connection) fails after two minutes."""
        c.execute("UPDATE assistant_actions SET status='failed', decided_at=?, confirm_hash=NULL, result_json=? "
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
            c.execute("UPDATE assistant_actions SET status=?, decided_at=?, result_json=?, confirm_hash=NULL WHERE id=?",
                      (status, H.now(), json.dumps(result), aid))
            row = load(c, who, aid)
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
            c.execute("UPDATE assistant_actions SET status='cancelled', decided_at=? WHERE id=?", (H.now(), aid))
            H.event(c, who.actor, "assistant.action.cancelled", aid, {})
            return {"action": action_view(load(c, who, aid), c)}
        return mutate(request, body, work)

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
                if row["created"] < H.shift(H.now(), hours=-CONFIRM_TTL_H):
                    # Recorded here, raised after the transaction commits (raising inside it rolls it back).
                    c.execute("UPDATE assistant_actions SET status='expired', decided_at=? WHERE id=?", (H.now(), aid))
                    return None
                # Only the run that holds this secret (the in-process call below) is "confirmed".
                c.execute("UPDATE assistant_actions SET status='running', decided_at=?, running_since=?, "
                          "confirm_hash=? WHERE id=?", (H.now(), H.now(), digest(secret), aid))
                H.event(c, who.actor, "assistant.action.confirmed", aid,
                        {"summary": row["summary"][:200], "method": row["method"], "path": row["path"]})
                # A card stored before its task change was made runnable (`runnable`) still runs, read as it is now.
                sent = json.loads(row["body_json"] or "{}")
                if row["path"] == BATCH_PATH and isinstance(sent, dict) and isinstance(sent.get("actions"), list):
                    sent = {"actions": [{**s, "body": runnable(c, s.get("method") or "POST", s.get("path"), s.get("body") or {})}
                                        if isinstance(s, dict) else s for s in sent["actions"]]}
                else:
                    sent = runnable(c, row["method"], row["path"], sent)
                return {**row, "body_json": json.dumps(sent)}
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
            sent = json.loads(row["body_json"] or "{}") if row["method"] != "DELETE" else None
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
