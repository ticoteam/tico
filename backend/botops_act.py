"""BotOps request origins and legacy route classification.

The server dispatcher (app.py) resolves the human or bot that requested the run and applies
that identity's own rights to every v2 route by default. No requester means BotOps' own rights.
The route tables and classifier remain for older callers; they do not limit that dispatcher.
Secrets go through Credentials, never arbitrary API request bodies.
"""
import json
import re


def request_task(c, auth, who, title, body):
    """A server-generated BotOps job carries the rights of the human who requested it.

    The origin is written here, never taken from task text or caller-supplied references.
    """
    from . import rooms
    from .store import H
    task = H.task_create(c, who.actor, title, body, H.bot_actor(H.FLEET_MAINTAINER), allow_planned=True,
                         conversation_id=rooms.task_conversation_id(c, auth, H.bot_actor(H.FLEET_MAINTAINER), who.actor))
    if who.role in ("owner", "human") and who.via != "assistant":
        H.event(c, who.actor, "botops.task_requested", task["id"], {})
    return task


# Message refs that mean words someone else wrote started the run: a comment, the Assistant, Slack, a live meeting.
OTHERS_WORDS = ("comment", "via", "assistant", "slack", "routing", "live_meeting")
# How long a person's request lends their rights after they last spoke on its task (created it, sent the message it
# carries, commented on it), however long the task stays open.
LEND_MAX_DAYS = 30
TERMINAL = ("done", "closed", "declined")


def own_run(c, auth, attempt_id):
    """Whether this BotOps run is its own work, with its own wider rights (`Auth.botops_manages`): only a keeper wake
    from BotOps' own schedule, the daily update or a timed routine only the owner (or the product) wrote. Everything
    else the keeper wakes it with carries words someone else wrote: a service key's or a watcher's task, a stopped job's
    request, a routine BotOps set itself while it worked for someone, a task someone commented on."""
    from .store import H
    turn = c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=? AND a.bot=?",
                     (attempt_id, H.FLEET_MAINTAINER)).fetchone() if attempt_id else None
    msg = H.message(c, turn["message_id"]) if turn and turn["message_id"] else None
    if not msg or msg["from_actor"] != H.KEEPER:
        return False
    refs = msg.get("refs") or {}
    if any(refs.get(key) for key in OTHERS_WORDS):
        return False
    task_id = H.message_task_id(msg)
    if not task_id:
        return msg["kind"] == "notice" and refs.get("wake") == "update"
    return _owners_routine_task(c, auth, H.task(c, task_id))


def _owners_routine_task(c, auth, task):
    """A task the keeper opened for one of BotOps' timed routines, whose every word the owner or the product wrote:
    the routine (created and since changed only by them), the task itself, and anything said on it."""
    from .store import H
    keeper, botops = H.KEEPER, "bot:" + H.FLEET_MAINTAINER
    if not task or task["requester"] != keeper or task["owner"] != botops:
        return False
    made = c.execute("SELECT actor FROM events WHERE action='task.create' AND target=? ORDER BY ts,id LIMIT 1",
                     (task["id"],)).fetchone()
    if not made or made["actor"] != keeper:
        return False
    # An event routine's task carries the event's content (a meeting's notes, say), so only timed and manual runs.
    schedule = c.execute("SELECT o.schedule_id FROM schedule_occurrences o JOIN schedules s ON s.id=o.schedule_id "
                         "WHERE o.task_id=? AND o.outcome<>'event' AND s.bot=? LIMIT 1",
                         (task["id"], H.FLEET_MAINTAINER)).fetchone()
    if not schedule:
        return False
    writers = c.execute("SELECT action,actor,detail_json FROM events WHERE target=? "
                        "AND action IN ('routine.created','routine.updated')", (schedule["schedule_id"],)).fetchall()
    if not any(w["action"] == "routine.created" for w in writers):
        return False                # a routine from a bot.yaml: whoever pushed that file wrote it
    for w in writers:
        if w["actor"] == keeper:
            continue
        if H._json(w["detail_json"], {}).get("via") == "assistant" or not _is_owner(c, auth, w["actor"]):
            return False
    if c.execute("SELECT 1 FROM task_events WHERE task_id=? AND field IN ('title','body') AND actor<>? LIMIT 1",
                 (task["id"], keeper)).fetchone():
        return False
    said = c.execute("SELECT 1 FROM messages m WHERE " + H._task_ref_sql("$.task") + "=? AND m.from_actor NOT IN (?,?) "
                     "LIMIT 1", (task["id"], keeper, botops)).fetchone()
    return not said


def _is_owner(c, auth, actor):
    from .store import H, Problem
    if not H.is_human(actor):
        return False
    try:
        return auth.identity_for_actor(c, actor).role == "owner"
    except Problem:
        return False


def lend_lapsed(c, task):
    """Why a person's task no longer lends their rights, whatever its status says, or "" while it does: someone other
    than the requester moved it back from done, closed or declined (the request it carried was finished), or the
    requester last spoke on it more than LEND_MAX_DAYS ago."""
    from .store import H
    requester = task["requester"]
    for row in c.execute("SELECT * FROM task_events WHERE task_id=? AND field='status' AND old IN (?,?,?) "
                         "AND new NOT IN (?,?,?)", (task["id"], *TERMINAL, *TERMINAL)):
        if row["actor"] != requester or dict(row).get("via"):
            return "That task was finished and has been reopened; ask the person again"
    spoke = []
    made = c.execute("SELECT actor,detail_json FROM events WHERE action='task.create' AND target=? ORDER BY ts,id LIMIT 1",
                     (task["id"],)).fetchone()
    if made and made["actor"] == requester and H._json(made["detail_json"], {}).get("via") != "assistant":
        spoke.append(task["created"])
    origin = H.message(c, task["request_id"]) if task.get("request_id") else None
    if origin and origin["from_actor"] == requester:
        spoke.append(origin["created"])
    comment = c.execute("SELECT max(m.created) FROM messages m WHERE " + H._task_ref_sql("$.task") + "=? "
                        "AND m.from_actor=? AND json_extract(m.refs_json,'$.comment') IS NOT NULL "
                        "AND json_extract(m.refs_json,'$.via') IS NULL AND json_extract(m.refs_json,'$.assistant') IS NULL",
                        (task["id"], requester)).fetchone()[0]
    if comment:
        spoke.append(comment)
    if not spoke or max(spoke) < H.shift(H.now(), days=-LEND_MAX_DAYS):
        return f"The person last spoke on that task more than {LEND_MAX_DAYS} days ago; ask them again"
    return ""


HEADER = "x-tico-on-behalf-of"
API = "/api/v2/"
_S = r"[^/]+"


def _routes(*rows):
    return [(method, re.compile(API + path)) for method, path in rows]


# Runs at once, as the person. Some of these keep their own "always a click" rule inside the route (adding someone
# a role, a Computer that does not take members' bots): the route answers with the card.
# A credential grant to a bot runs at once when the person is a credential administrator (or stored it, for a bot they
# manage) and is refused, with who to ask, when they are not; only a grant to a person or to every computer is a card (backend/credentials.py).
DO = _routes(
    ("POST", r"bots"), ("POST", r"bots/register"),
    ("POST", rf"bots/{_S}/(definition|assignment|placement|place|go-live|model|fallback|transitions|control|owners|co-owners|"
             rf"onboarded|goals|updates|routines|tools|quarantine/clear|limit/retry)"),
    ("POST", rf"bots/{_S}/tools/{_S}/(delete|update)"), ("DELETE", rf"bots/{_S}/tools/{_S}"),
    # Copying a bot or a skill, bringing a copy up to date and suggesting its changes back: each is checked with the requester's own
    # rights on the bots it names (backend/bot_copy.py).
    ("POST", rf"bots/{_S}/(copy|copies|branches|update-from-original|suggest-to-original|skills/copy|repository-read-token)"),
    # A Hermes bot (docs/hermes-agents.md): restoring an archived bot, pairing its profile with the code the connector
    # prints, and rotating or revoking its agent credential. The token itself never comes back to BotOps (the route).
    ("POST", rf"bots/{_S}/(archive|restore)"), ("POST", rf"bots/{_S}/agent-credential"), ("POST", rf"bots/{_S}/agent-credential/revoke"),
    ("POST", r"agents/pairings/(approve|decline)"),
    ("POST", rf"bots/{_S}/access"), ("PUT", rf"bots/{_S}/access"),
    ("PUT", rf"bots/{_S}/repositories"), ("PUT", rf"repositories/{_S}/{_S}"),
    ("PUT", "repositories/settings"), ("POST", "repositories/refresh"),
    ("PUT", "subscriptions"),
    ("PUT", rf"bots/{_S}/github-repos"), ("POST", r"github/repos"), ("DELETE", rf"github/repos/{_S}/{_S}"),
    ("POST", rf"routines/{_S}"), ("POST", rf"routines/{_S}/(delete|run)"),
    ("POST", rf"settings/history/{_S}/undo"),
    ("POST", rf"settings/transitions/{_S}/(apply-without-checkpoint|cancel)"),
    # Goals and KPIs: a goal's or KPI's own rules decide who may change what.
    ("POST", r"goals"), ("POST", rf"goals/{_S}"), ("POST", rf"goals/{_S}/(status|status/auto|checkins|kpis)"),
    ("POST", rf"goals/{_S}/kpis/{_S}"), ("POST", rf"goals/{_S}/kpis/{_S}/unlink"), ("POST", r"goals/refresh"),
    ("POST", r"kpis"), ("POST", rf"kpis/{_S}"), ("POST", rf"kpis/{_S}/readings"), ("POST", r"(goal-proposals|proposals)"),
    # Tasks, docs and what the people asked of BotOps.
    ("POST", r"tasks"), ("POST", r"tasks/dry-run"), ("POST", rf"tasks/{_S}"),
    ("POST", rf"tasks/{_S}/(comments|links|ask|run-now)"),
    # Another bot's task worktrees: a person who moves tasks may add one or ask for one to be cleaned up (the route
    # checks); the computer removes it once the bot is idle.
    ("POST", rf"tasks/{_S}/worktrees(/attach)?"), ("DELETE", rf"tasks/{_S}/links/{_S}"),
    ("POST", r"docs"), ("PATCH", rf"docs/{_S}"), ("POST", rf"docs/{_S}/restore"),
    ("POST", r"linked-docs"), ("PATCH", rf"linked-docs/{_S}"),
    ("POST", rf"meetings/{_S}/delete"), ("POST", "meetings/granola/sync"),
    ("POST", rf"meetings/{_S}/review"), ("POST", "meetings/review"), ("POST", "meetings/settings"),
    ("POST", rf"(integrations|tools)/{_S}/learnings"), ("POST", rf"(integrations|tools)/{_S}/learnings/{_S}/delete"),
    ("POST", r"health/bot-access/dismiss"),
    # People and access. The route asks for the click on what needs it.
    ("POST", r"access/(people|humans)"), ("POST", rf"access/(people|humans)/{_S}"),
    ("PUT", r"access/rules"),
    # Groups: an owner or an admin changes them (the route says so), and a delete moves what is in the group up.
    ("POST", r"groups"), ("PATCH", rf"groups/{_S}"), ("DELETE", rf"groups/{_S}"),
    # Which Slack channels bots read and post in: an owner or an admin's call, which the route checks.
    ("POST", r"slack/channels"), ("POST", r"slack/channels/(remove|import)"),
    ("POST", rf"credentials/{_S}/grants"), ("POST", rf"credentials/{_S}/grants/{_S}/revoke"),
    ("DELETE", rf"credentials/{_S}"),
    # Computers: a restart, a model sign-in (its code is pasted in the app, never here), inbox sharing where one owner runs
    # everything (the route says where). Limits are spending: lowering one is always direct; raising one, and the providers,
    # are direct unless the owner's rule says otherwise (TIGHTENED).
    ("POST", rf"(runners|computers)/{_S}/(restart|inbox-sharing|logins)"), ("POST", rf"(runners|computers)/{_S}/logins/{_S}/cancel"),
    ("PUT", r"providers"), ("PUT", r"usage/limits"), ("PUT", rf"usage/limits/{_S}"),
    # A message or chat to a bot stays in the team; a message to a person is a card (`classify`).
    ("POST", rf"chat/{_S}"),
    ("POST", r"system/update(/check)?"),
)

# Cards again when the owner turned "BotOps changes providers and limits without asking" off. A limit lowered is
# still direct: app.py checks it against what is set.
TIGHTENED = _routes(("PUT", r"providers"), ("PUT", r"usage/limits"), ("PUT", rf"usage/limits/{_S}"))
LIMITS = re.compile(API + rf"usage/limits(/{_S})?")

# Comes back as a Confirm card; it runs only on the person's own click, as them.
CONFIRM = _routes(
    ("POST", rf"(people|humans)/{_S}"),
    ("PUT", r"access/limits"), ("PUT", r"access/allow"),
    ("POST", rf"(runners|computers)/{_S}/(member-bots|revoke)"),
    ("POST", rf"(goal-proposals|proposals)/{_S}/decide"),
    ("POST", r"support/tickets"),
    ("PATCH", rf"files/{_S}"),
    ("PUT", r"directory"), ("POST", r"directory/sync"), ("POST", r"directory/preview"),
    ("POST", r"slack/disconnect"), ("POST", r"github/app/disconnect"),
)

# The messages route is a card unless it names a bot (`classify`).
MESSAGES = re.compile(API + r"messages")

# Confirm-card routes only an owner or an admin may ask for: a member is told so at once, not handed a card that fails.
ADMIN_ONLY = _routes(
    ("PUT", r"providers"), ("PUT", r"access/(limits|allow)"), ("PUT", r"usage/limits"), ("POST", rf"(runners|computers)/{_S}/member-bots"),
    ("POST", r"system/update"), ("PUT", r"directory"), ("POST", r"directory/(sync|preview)"),
    ("POST", r"(slack|github/app)/disconnect"), ("POST", rf"credentials/{_S}/grants/{_S}/revoke"),
)

# Read as the person, except what hands back a secret or is a computer's own channel.
NO_READ = re.compile(API + r"(credential-runtime|runner-credential-(migration|grants)|me/tokens.*|mcp|agents/setup-script|jobs.*|attempts.*"
                     r"|runner-logins.*|runner-model-credentials|runners/desired|runners/assignments|runners/eligible|runners/me/events|directory/scim-token)")

# Named like a secret: refused whatever route it is on.
SECRET_KEYS = re.compile(r"^(secret|password|passwd|passphrase|token|access_token|refresh_token|api_key|apikey|"
                         r"private_key|client_secret|authorization|bearer)$", re.I)

DETAIL_SECRET = ("A secret does not go in a request. Ask the person for it with a credential card "
                 "(`hub credential request`): the value goes straight to Credentials and never through you.")
DETAIL_ROUTE = ("BotOps does not do this for a person. If the app can do it and this list should, say so with "
                "`hub support file`.")


def normalize(path):
    """The route as `/api/v2/...` with plain characters only, or None."""
    from .assistant import normalize_path
    path = str(path or "")
    if not path.startswith(API):
        path = API + path.lstrip("/")
    return normalize_path(path)


def classify(method, path, body=None, rules=None, to_bot=None):
    """`do`, `confirm` or None (not delegable) for this request. `rules` are the team's (backend/team_rules.py);
    `to_bot(name)` says a message's recipient is a bot."""
    path = normalize(path)
    method = str(method or "").upper()
    if not path:
        return None
    if method == "GET":
        return None if NO_READ.fullmatch(path) else "do"
    if any(m == method and p.fullmatch(path) for m, p in CONFIRM):
        return "confirm"
    if method == "POST" and MESSAGES.fullmatch(path):
        return "do" if to_bot and isinstance(body, dict) and to_bot(body.get("to")) else "confirm"
    if rules and not rules.get("botops_direct", True) and any(m == method and p.fullmatch(path) for m, p in TIGHTENED):
        return "confirm"
    if any(m == method and p.fullmatch(path) for m, p in DO):
        return "do"
    return None


def admin_only(method, path):
    path = normalize(path)
    return bool(path) and any(m == str(method).upper() and p.fullmatch(path) for m, p in ADMIN_ONLY)


def secret_in(value, _depth=0):
    """The first key named like a secret anywhere in a JSON body, or None."""
    if _depth > 8:
        return None
    if isinstance(value, dict):
        for key, item in value.items():
            if SECRET_KEYS.match(str(key)):
                return str(key)
            found = secret_in(item, _depth + 1)
            if found:
                return found
    elif isinstance(value, list):
        for item in value[:200]:
            found = secret_in(item, _depth + 1)
            if found:
                return found
    return None


def parse_body(raw):
    """The request body as JSON, or None when there is none."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


# BotOps' own: the run's plumbing and what BotOps says itself. Never switched to the requester by default.
OWN = re.compile(API + r"(credential-runtime|runner-credential-.*|runner-watcher-credentials|attempts.*|jobs.*|runners/.*|"
                 r"messages(/.*)?|chat/.*|conversations/.*|bots/" + "botops" + r"(/.*)?|files/(uploads|imports)|uploads/.*|me(/.*)?)")


def default_delegable(method, path, body=None):
    """Whether a BotOps call with no explicit `on_behalf_of` acts as the person who asked: only the routes BotOps
    may do or propose for a person, and never its own plumbing or its own messages."""
    path = normalize(path)
    if path and str(method).upper() in ("GET", "POST") and re.fullmatch(API + rf"(?:runners|computers)/{_S}/logins(?:/{_S}(?:/cancel)?)?", path):
        return classify(method, path, body) == "do"
    if not path or OWN.fullmatch(path):
        return False
    return classify(method, path, body) in ("do", "confirm")


# What a person clicks when BotOps is refused: one plain sentence and a link to the exact place in the app. Whose
# rights were used decides the sentence: BotOps' own (no person asked in this run), a requesting bot's, or the person's.
def _place(path):
    rest = (normalize(path) or "")[len(API):]
    parts = rest.split("/")
    if parts[0] == "bots" and len(parts) > 1 and parts[1] not in ("register", ""):
        slug = parts[1]
        what = "its repository access" if parts[2:3] == ["repositories"] else "its settings"
        return "#/bot/" + slug + "/more", slug, what
    if parts[0] == "repositories":
        return "#/repositories", "", "Settings > Repositories"
    if parts[0] == "tasks" and len(parts) > 1 and parts[1]:
        return "#/task/" + parts[1], "", "the task"
    if parts[0] == "credentials":
        return "#/credentials", "", "Credentials"
    if parts[0] in ("runners", "computers"):
        return "#/settings", "", "Settings > Computers"
    return "#/settings", "", "Settings"


def fix(path, code, status, acting, names, base=""):
    """`{"fix", "link"}` for a refusal of a BotOps call, or None. `acting` is the identity the route checked;
    `names` maps actor ids to display names."""
    link, slug, what = _place(path)
    bot = names.get("bot:" + slug) or slug.replace("-", " ").title()
    where = (bot + "'s " + what[4:]) if slug and what.startswith("its ") else what
    if code == "runner_not_ready":
        text = ("Sign in the bot's AI tool on its computer in Settings > Computers, or choose a model that computer "
                "already runs")
        link = "#/settings"
    elif status != 403:
        return None
    elif code == "on_behalf_of":
        # The cited request lends nothing (closed, too old, another person's, words others wrote): the detail says why.
        text = ("BotOps can't act on that earlier request: ask it again in your BotOps chat so it acts with your "
                "rights now, or change " + where + " yourself")
    elif acting.actor == "bot:botops":
        text = ("No one's request was attached to this run, so BotOps used its own rights: say go ahead in your "
                "BotOps chat, or change " + where + " yourself")
    elif acting.role == "bot":
        asker = names.get(acting.actor) or acting.actor.split(":", 1)[-1]
        text = (asker + " asked for this, so BotOps had only " + asker + "'s rights: say go ahead in your BotOps "
                "chat, or change " + where + " yourself")
    else:
        text = "Your role can't change this: ask an owner or admin to change " + where
    return {"fix": text, "link": (base.rstrip("/") + "/" + link) if base else link}
