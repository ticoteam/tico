"""The Assistant: every person's own private chat with the company's assistant bot (docs/assistant.md).

One room per person (`rooms.ASSISTANT_ROOM`, scope personal, owner_actor the person): only they
read it or post in it, the owner and administrators included. A message goes one of two ways:

* the fast path answers on the server, from Tico's own data, with no runner turn: what is waiting
  on you, a search, "open X", what a bot did today, how to do something (docs/*.md). The person
  sees the answer at once (a bot turn is 30 seconds or more);
* everything else is a turn of the assistant bot on the company's runner. That turn acts as the
  person and never more: `Auth.authenticate` maps its token to the person (`assistant_principal`),
  so every hub tool it calls is checked, and audited "via assistant", exactly as if they had made
  the call. Low-risk writes run directly (`write_allowed`); anything with a side effect that
  matters is proposed as a pending action, and only the person's own click (`confirm`) runs it,
  through the same routes with their own credential. The bot itself can never confirm.
"""

import asyncio
import json
import re
import secrets
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import Request

from . import models as M
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
# What an assistant turn may write on its own is what stays inside the team: its person's own tasks (create, or update
# but not settle), tasks for bots, comments on tasks no other person is on, messages to bots, marking updates read,
# quiet notes. A task or message for another person, run-now, spend, access, archive and the rest are proposals. The
# owner's rule "Assistant acts without asking" off (backend/team_rules.py) narrows it to what only touches the person.
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
    if path in ("/api/v2/updates/read", "/api/v2/changelog/read", "/api/notes", "/api/v2/assistant/actions"):
        return True
    try:
        fields = json.loads(body or b"{}")
    except ValueError:
        return False
    if not isinstance(fields, dict):
        return False
    if direct and to_bot:
        chatting = re.fullmatch(r"/api/v2/chat/([^/]+)", path)
        if (chatting and (settings is None or chatting.group(1) != settings.assistant_bot)) or (
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
        return _mine(actor, fields.get("owner")) or bool(direct and to_bot and to_bot(fields.get("owner")))
    if "owner" in fields and not _mine(actor, fields["owner"]):
        return False
    return bool(owns and owns(updating.group(1), False))


# What may be proposed at all (method, normalized path). Anything else, and above all tokens, sign-in,
# runner enrollment, this assistant and anything that hands back a secret, can never be proposed.
_PROPOSABLE = [(m, re.compile(p)) for m, p in (
    ("POST", r"/api/v2/approvals/[^/]+"), ("POST", r"/api/v2/messages/[^/]+/answer"),
    ("POST", r"/api/v2/tasks"), ("POST", r"/api/v2/tasks/[^/]+"),
    ("POST", r"/api/v2/tasks/[^/]+/(comments|links|labels|run-now|ask)"),
    ("POST", r"/api/v2/messages"), ("POST", r"/api/v2/chat/[^/]+"), ("POST", r"/api/v2/notes"),
    ("POST", r"/api/v2/updates/[^/]+/reply"),
    ("POST", r"/api/v2/bots"), ("POST", r"/api/v2/bots/[^/]+/(archive|definition|owners|co-owners|updates|goals)"),
    ("POST", r"/api/v2/bots/[^/]+/(assignment|placement)"), ("POST", r"/api/v2/credentials/[^/]+/grants"),
    ("POST", r"/api/v2/(?:people|humans)/[^/]+"), ("POST", r"/api/v2/access/(?:people|humans)(/[^/]+)?"),
    ("PUT", r"/api/v2/providers"), ("PATCH", r"/api/v2/files/[^/]+"),
)]
# What only BotOps may propose, for a person who asked it in chat (backend/botops_act.py lists the routes it
# answers with a card): the same click, on more routes.
_PROPOSABLE_BOTOPS = [(m, re.compile(p)) for m, p in (
    ("PUT", r"/api/v2/access/(limits|allow)"), ("PUT", r"/api/v2/usage/limits(/[^/]+)?"),
    ("POST", r"/api/v2/(?:runners|computers)/[^/]+/(member-bots|revoke|restart)"),
    ("POST", r"/api/v2/credentials/[^/]+/grants/[^/]+/revoke"), ("POST", r"/api/v2/system/update"),
    ("POST", r"/api/v2/(?:goal-proposals|proposals)/[^/]+/decide"), ("POST", r"/api/v2/support/tickets"),
    ("PUT", r"/api/v2/directory"), ("POST", r"/api/v2/directory/(sync|preview)"),
    ("POST", r"/api/v2/(slack|github/app)/disconnect"),
)]
_SHAPE = re.compile(r"/api/v2/[A-Za-z0-9_.:\-/]+")


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


def valid_operation(method, path, settings=None, proposer="assistant"):
    path = normalize_path(path)
    if not path or (settings is not None and path == "/api/v2/chat/" + settings.assistant_bot):
        return False
    routes = _PROPOSABLE + (_PROPOSABLE_BOTOPS if proposer == "botops" else [])
    return any(m == method and p.fullmatch(path) for m, p in routes)


PREVIEW = 160


def preview(text, limit=PREVIEW):
    """The start of a long text, cut at a word and marked with an ellipsis, so a short line never reads as the whole."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    return (text[:limit].rsplit(" ", 1)[0] or text[:limit]).rstrip(" ,;:.") + "…"


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

    def fields(names):
        return ", ".join(str(n) for n in names) or "nothing"

    def computer(request):
        """The computer a placement names and whether it takes members' bots, from the database."""
        row = c.execute("SELECT label,accepts_member_bots FROM runners WHERE id=?", (str(request.get("runner_id") or ""),)).fetchone()
        if not row:
            return "a computer"
        return row["label"] + (", a computer that takes members' bots" if row["accepts_member_bots"]
                               else ", a computer that does not take members' bots")

    def approval_line(aid):
        row = H.approval(c, aid)
        verb = str(body.get("decision") or "decide").title()
        if not row:
            return f"{verb} approval {aid}"
        detail = "; ".join(f"{k}: {v}" for k, v in (row.get("payload") or {}).items() if k in
                           ("subject", "to", "what", "amount", "account", "url", "repo", "pr", "title"))[:240]
        return (f"{verb} the {row['kind']} approval requested by {who(row['requested_by'])}"
                + (f" ({detail})" if detail else ""))
    diff = []
    m = re.fullmatch(r"/api/v2/tasks/([^/]+)", path)
    if m and (t := H.task(c, m.group(1))):
        for k, v in body.items():
            if k not in ("version",) and str(t.get(k) if t.get(k) is not None else "") != str(v if v is not None else ""):
                diff.append({"field": k, "old": t.get(k), "new": v})
        return f"Change task “{t['title']}” ({len(diff)} field{'s' if len(diff) != 1 else ''})", diff
    rules = (
        (r"/api/v2/approvals/([^/]+)", lambda g: approval_line(g[0])),
        (r"/api/v2/messages/[^/]+/answer", lambda g: "Answer a bot's question"),
        (r"/api/v2/tasks", lambda g: f"Create task “{body.get('title', '')}” for {who(body.get('owner'))}"),
        (r"/api/v2/tasks/([^/]+)/(comments|links|labels|run-now|ask)",
         lambda g: f"{g[1].replace('-', ' ').title()} on task “{(H.task(c, g[0]) or {}).get('title', g[0])}”"),
        (r"/api/v2/messages", lambda g: f"Send a message to {who(body.get('to'))}"),
        (r"/api/v2/chat/([^/]+)", lambda g: f"Send a message to {bot(g[0])}"),
        (r"/api/v2/notes", lambda g: f"Leave a note for {who(body.get('to'))}"),
        (r"/api/v2/updates/[^/]+/reply", lambda g: "Reply to a bot's update"),
        (r"/api/v2/bots", lambda g: f"Add bot “{body.get('display_name', '')}”"),
        (r"/api/v2/bots/([^/]+)/archive", lambda g: f"Archive bot {bot(g[0])}"),
        (r"/api/v2/bots/([^/]+)/definition", lambda g: f"Change settings of bot {bot(g[0])}: "
         + fields(k for k in body if k != "expected_revision")),
        (r"/api/v2/bots/([^/]+)/(owners|co-owners|updates|goals)", lambda g: f"Change {g[1]} of bot {bot(g[0])}: "
         + fields(body)),
        (r"/api/v2/bots/([^/]+)/(assignment|placement)", lambda g: f"Place bot {bot(g[0])} on {computer(body)}"),
        (r"/api/v2/credentials/([^/]+)/grants", lambda g: "Give " + ("every computer" if body.get("subject") == "computers" else who(body.get("subject")))
         + " a stored credential"),
        (r"/api/v2/(?:people|humans)/([^/]+)", lambda g: f"Edit person {who(g[0])}: " + fields(body)),
        (r"/api/v2/access/(?:people|humans)", lambda g: f"Add {body.get('name') or body.get('email', '')} ({body.get('email', '')}) "
         "to the roster, and let them sign in" + "".join(f"; {k} {body[k]}" for k in ("team", "reports_to", "title") if body.get(k))),
        (r"/api/v2/access/(?:people|humans)/([^/]+)", lambda g: "Change " + who("human:" + g[0]) + ": "
         + ", ".join(f"{k} to {v}" for k, v in body.items())),
        (r"/api/v2/providers", lambda g: "Change the company's AI providers"),
        (r"/api/v2/access/(limits|allow)", lambda g: "Change who may sign in or what members may do: " + fields(body)),
        (r"/api/v2/usage/limits(?:/([^/]+))?", lambda g: ("Change the spending limit of " + bot(g[0]) if g[0] else "Change the company's spending limit")
         + ": " + ", ".join(f"{k} {'none' if v is None else '$' + format(v, 'g')}" for k, v in body.items() if k.endswith("_usd"))),
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
        (r"/api/v2/files/([^/]+)", lambda g: "Change a file's listing"),
    )
    for pattern, fn in rules:
        if (mm := re.fullmatch(pattern, path)):
            return fn(mm.groups()), diff
    return f"{method} {path}", diff


def create_proposal(c, settings, who, room, proposer, summary, method, path, body):
    """One pending action for `who` to confirm, and its card in `room`, written by the bot that proposed it.

    The Assistant proposes what its turn may not do on its own; BotOps proposes what a person asked it for
    that always needs their own click (adding people, roles, shared credentials, a computer that does not
    take members' bots). Either way only the person's own confirm runs it, as them."""
    path = normalize_path(path)
    if not valid_operation(method, path, settings, proposer):
        raise Problem("operation", "That is not something that can be proposed: it is not on the list of routes "
                      "a person confirms", 422)
    if path == "/api/v2/support/tickets":
        # The person approves the exact text, so it must be one the ticket will take: no card for a message that
        # would fail (too long, control characters) only after the click.
        from .support import clean_message
        clean_message((body or {}).get("message"))
    if c.execute("SELECT count(*) FROM assistant_actions WHERE owner=? AND status='pending'",
                 (who.actor,)).fetchone()[0] >= MAX_PENDING:
        raise Problem("too_many", "Too many proposals are waiting; confirm or cancel some first", 409)
    body = {k: v for k, v in (body or {}).items() if k != "on_behalf_of"}
    same = c.execute("SELECT * FROM assistant_actions WHERE owner=? AND status='pending' AND method=? AND path=? "
                     "AND body_json=? AND proposer=?", (who.actor, method, path, json.dumps(body), proposer)).fetchone()
    if same:
        # The same card again is the same card: a retried command never leaves two to click.
        return {"action": action_view(dict(same)), "message_id": None, "repeated": True}
    what, diff = describe(c, method, path, body)
    if not re.fullmatch(r"/api/v2/tasks/[^/]+", path):
        # The card lists every field the request carries, so nothing that runs is missing from what is confirmed.
        # (A task update lists what changes: its old and new values.)
        diff = [{"field": k, "new": v} for k, v in body.items()]
    row = {"id": H.new_id(), "owner": who.actor, "conversation_id": room["id"], "summary": summary,
           "method": method, "path": path, "body_json": json.dumps(body),
           "status": "pending", "proposed_via": proposer, "created": H.now(),
           "description": what, "diff_json": json.dumps(diff, default=str), "proposer": proposer}
    c.execute("INSERT INTO assistant_actions(id,owner,conversation_id,summary,method,path,body_json,status,"
              "proposed_via,created,description,diff_json,proposer) VALUES(:id,:owner,:conversation_id,:summary,"
              ":method,:path,:body_json,:status,:proposed_via,:created,:description,:diff_json,:proposer)", row)
    bot = settings.assistant_bot if proposer == "assistant" else proposer
    card = H._write_message(c, "bot:" + bot, who.actor, "Needs your OK: " + summary, room, "say",
                            {"assistant": True, "action": row["id"]}, None, None, delivered_at=H.now())
    H.event(c, who.actor, "assistant.action.proposed", row["id"],
            {"summary": summary[:200], "method": method, "path": path, "proposer": proposer})
    return {"action": action_view(row), "message_id": card["id"]}


# ------------------------------------------------------------------ the room
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


def availability(c, settings, who):
    bot = H.bot(c, settings.assistant_bot)
    state = bot["state"] if bot else "missing"
    return {"available": state == "active", "state": state, "bot": settings.assistant_bot,
            "name": settings.assistant_name, "can_turn_on": who.role == "owner" and state != "active"
            and state not in ("quarantined",)}


def action_view(row):
    result = json.loads(row["result_json"]) if row.get("result_json") else None
    return {"id": row["id"], "owner": row["owner"], "conversation_id": row["conversation_id"],
            "summary": row["summary"], "method": row["method"], "path": row["path"],
            "body": json.loads(row["body_json"] or "{}"), "status": row["status"],
            "proposed_via": row["proposed_via"], "proposer": row.get("proposer") or row["proposed_via"],
            "created": row["created"], "decided_at": row.get("decided_at"),
            "description": row.get("description") or "", "diff": json.loads(row.get("diff_json") or "[]"),
            "result": result}


def actions_for(c, actor, ids):
    if not ids:
        return {}
    rows = c.execute("SELECT * FROM assistant_actions WHERE owner=? AND id IN (%s)" % ",".join("?" * len(ids)),
                     (actor, *ids)).fetchall()
    return {row["id"]: action_view(dict(row)) for row in rows}


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
                          r"build|set up|schedule|publish|post|change|update|rename|close|cancel|remind)\b")


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
    if WAITING.search(t):
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

    async def get(self, path, **params):
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
        ids = [m["refs"]["action"] for m in snap["messages"] if isinstance(m.get("refs"), dict) and m["refs"].get("action")]
        pending = [action_view(dict(r)) for r in c.execute(
            "SELECT * FROM assistant_actions WHERE owner=? AND status='pending' ORDER BY created", (who.actor,))]
        return {**availability(c, settings, who), "room_id": room["id"] if room else None,
                "messages": snap["messages"], "has_more": snap["has_more"], "next_before": snap["next_before"],
                "execution": snap["execution"], "actions": actions_for(c, who.actor, ids),
                "pending": pending}

    @app.get("/api/v2/assistant")
    def assistant(request: Request):
        who = own(request)
        with store.transaction() as c:
            expire(c)
        with store.read() as c:
            info = availability(c, settings, who)
            room = find_room(c, who.actor)
        if not room and info["available"]:
            with store.transaction() as c:
                room = ensure_room(c, who.actor, settings.assistant_bot)
        with store.read() as c:
            return view(c, who, room)

    def post_message_sync(request, body, who, intent, reply, links):
        def work(c):
            info = availability(c, settings, who)
            if not info["available"]:
                raise Problem("assistant_off", "The " + settings.assistant_name + " is off", 409)
            auth.require_write(c, who, settings.assistant_bot)
            room = ensure_room(c, who.actor, settings.assistant_bot)
            refs = {"assistant": True}
            if reply is not None:
                refs["quiet"] = True
            message = H.say(c, who.actor, "bot:" + settings.assistant_bot, body.text, conversation_id=room["id"],
                            kind="say", refs=refs)
            out = {"message": message, "reply": None, "fast": reply is not None, "intent": intent}
            if reply is not None:
                out["reply"] = H._write_message(c, "bot:" + settings.assistant_bot, who.actor, reply, room, "say",
                                                {"assistant": True, "fast": True, "intent": intent, "links": links},
                                                message["id"], None, delivered_at=H.now())
                H.event(c, who.actor, "assistant.fast", message["id"], {"intent": intent})
            return out
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
        intent, reply, links = await fast_answer(Internal(request), who, bots, body.text)
        return await asyncio.to_thread(post_message_sync, request, body, who, intent, reply, links)

    @app.post("/api/v2/assistant/turn-on")
    def turn_on(request: Request, body: M.Empty):
        who = own(request)
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner turns the " + settings.assistant_name + " on", 403)
        return mutate(request, body, lambda c: onboarding.turn_on_assistant(c, who))

    @app.post("/api/v2/assistant/actions")
    def propose(request: Request, body: M.AssistantAction):
        who = person(request)

        def work(c):
            if normalize_path(body.path) == "/api/v2/chat/" + settings.assistant_bot or not valid_operation(
                    body.method, body.path, settings):
                raise Problem("operation", "That is not something the " + settings.assistant_name
                              + " can propose: it is not on the list of routes a person confirms (never tokens, "
                              "sign-in, enrollment or this assistant)", 422)
            room = ensure_room(c, who.actor, settings.assistant_bot)
            return create_proposal(c, settings, who, room, "assistant", body.summary, body.method, body.path, body.body)
        return mutate(request, body, work)

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
            return {"action": action_view(load(c, who, aid))}

    def settle(aid, who, status, result, note):
        with store.transaction() as c:
            c.execute("UPDATE assistant_actions SET status=?, decided_at=?, result_json=?, confirm_hash=NULL WHERE id=?",
                      (status, H.now(), json.dumps(result), aid))
            row = load(c, who, aid)
            room = H.conversation(c, row["conversation_id"]) if row["conversation_id"] else None
            if room and note:
                by = row.get("proposer") or "assistant"
                H._write_message(c, "bot:" + (settings.assistant_bot if by == "assistant" else by), who.actor, note,
                                 room, "say", {"assistant": True, "action_result": aid}, None, None,
                                 delivered_at=H.now())
            return {"action": action_view(row)}

    @app.post("/api/v2/assistant/actions/{aid}/cancel")
    def action_cancel(request: Request, aid: str, body: M.Empty):
        who = own(request)

        def work(c):
            row = load(c, who, aid)
            if row["status"] != "pending":
                raise Problem("state", "This proposal is already " + row["status"], 409)
            c.execute("UPDATE assistant_actions SET status='cancelled', decided_at=? WHERE id=?", (H.now(), aid))
            H.event(c, who.actor, "assistant.action.cancelled", aid, {})
            return {"action": action_view(load(c, who, aid))}
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
                return row
        row = await asyncio.to_thread(claim)
        if row is None:
            raise Problem("expired", "This proposal is more than a day old; ask again", 409)
        ok, result = False, {"status_code": 0, "error": "It did not finish; ask again"}
        try:
            path = normalize_path(row["path"])
            if not valid_operation(row["method"], path, settings, row.get("proposer") or "assistant"):
                raise Problem("operation", "This proposal is not on the list of routes a person confirms", 422)
            api = Internal(request)
            api.headers["x-tico-assistant-action"] = aid + "." + secret
            response = await api.call(row["method"], path,
                                      body=json.loads(row["body_json"] or "{}") if row["method"] != "DELETE" else None,
                                      key="assistant-" + aid)
            ok = response.status_code < 400
            detail = None
            if not ok:
                try:
                    payload = response.json()
                    detail = (payload.get("error") or {}).get("detail") if isinstance(payload, dict) else None
                except ValueError:
                    pass
            # Only the status and an error's detail are kept: an answer's body may hold a secret.
            result = {"status_code": response.status_code, "error": None if ok else str(detail or f"HTTP {response.status_code}")[:300]}
        except Problem as exc:
            result = {"status_code": exc.status, "error": exc.detail[:300]}
        except httpx.HTTPError as exc:
            result = {"status_code": 0, "error": "It could not be sent: " + str(exc)[:200]}
        finally:
            note = ("Done: " + row["summary"]) if ok else ("That did not go through: " + str(result["error"])[:300])
            out = settle(aid, who, "done" if ok else "failed", result, note)     # even if the client went away
        return out
