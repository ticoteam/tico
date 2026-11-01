"""BotOps request origins and legacy route classification.

The server dispatcher (app.py) resolves the human or bot that requested the run and applies
that identity's own rights to every v2 route by default. No requester means BotOps' own rights.
For a person, the dispatcher answers a few routes with a Confirm card in their chat instead (`card_for_person`)
and refuses those that hand a secret back (`person_only`); the identity it lends is never confirmed, so a route's
own "always a click" rule asks too. Only the person's tap on the card runs it.
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
    from BotOps' own schedule, the daily update or a timed automation only the owner (or the product) wrote (a converted
    routine too, when the routine was theirs). Everything else the keeper wakes it with carries words someone else wrote: a service key's or
    a watcher's task, a stopped job's request, a routine BotOps set itself while it worked for someone, a task someone
    commented on."""
    from .store import H
    turn = c.execute("SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=? AND a.bot=?",
                     (attempt_id, H.FLEET_MAINTAINER)).fetchone() if attempt_id else None
    msg = H.message(c, turn["message_id"]) if turn and turn["message_id"] else None
    refs = (msg or {}).get("refs") or {}
    # A converted routine's task is opened by its automation as BotOps itself (backend/automation_actions.py).
    if not msg or msg["from_actor"] not in (H.KEEPER, "bot:" + H.FLEET_MAINTAINER):
        return False
    if msg["from_actor"] != H.KEEPER and not refs.get("automation"):
        return False
    if any(refs.get(key) for key in OTHERS_WORDS):
        return False
    task_id = H.message_task_id(msg)
    if not task_id:
        return msg["from_actor"] == H.KEEPER and msg["kind"] == "notice" and refs.get("wake") == "update"
    task = H.task(c, task_id)
    if refs.get("automation"):
        return _owners_automation_task(c, auth, task, refs)
    return _owners_routine_task(c, auth, task)


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
    if not schedule or not _owners_routine(c, auth, schedule["schedule_id"]):
        return False
    return _untouched(c, task, keeper)


def _owners_automation_task(c, auth, task, refs):
    """A task one of BotOps' timed automations opened, whose every version and change only the owner (or the product)
    wrote, not through the Assistant or a token. A converted routine counts only when the routine itself was the
    owner's (`_owners_routine`): one from a bot.yaml, or one BotOps set while it worked for someone, never does."""
    from .store import H
    botops = "bot:" + H.FLEET_MAINTAINER
    automation_id = str(refs.get("automation") or "")
    if not task or task["owner"] != botops or task["requester"] not in (H.KEEPER, botops):
        return False
    row = c.execute("SELECT owner, flow_json, trigger_type FROM automations WHERE id=? AND deleted_at IS NULL",
                    (automation_id,)).fetchone()
    if not row or row["owner"] != botops or row["trigger_type"] != "schedule" \
            or refs.get("trigger_source") != "automation:" + automation_id:
        return False
    # The run that opened this task is that automation's own, from its schedule or a Run now, not a chained event.
    run = c.execute("SELECT 1 FROM automation_runs WHERE id=? AND automation_id=?",
                    (str(refs.get("automation_run") or ""), automation_id)).fetchone()
    if not run or refs.get("automation_chain"):
        return False
    source = (H._json(row["flow_json"], {}) or {}).get("source")
    if isinstance(source, dict) and source.get("kind") == "routine":
        found = c.execute("SELECT bot FROM schedules WHERE id=?", (source.get("id"),)).fetchone()
        if not found or found["bot"] != H.FLEET_MAINTAINER or not _owners_routine(c, auth, source["id"]):
            return False
    actors = {r[0] for r in c.execute("SELECT actor FROM automation_versions WHERE automation_id=?", (automation_id,))}
    for r in c.execute("SELECT actor, via FROM audit_log WHERE object=?", ("automation:" + automation_id,)):
        if r["via"] == "assistant" or str(r["via"] or "").startswith("token:"):
            return False
        actors.add(r["actor"])
    if not actors or any(a != H.KEEPER and not _is_owner(c, auth, a) for a in actors):
        return False
    return _untouched(c, task, task["requester"])


def _owners_routine(c, auth, schedule_id):
    """A routine created through the API and since changed only by the owner (or the product)."""
    from .store import H
    writers = c.execute("SELECT action,actor,detail_json FROM events WHERE target=? "
                        "AND action IN ('routine.created','routine.updated')", (schedule_id,)).fetchall()
    if not any(w["action"] == "routine.created" for w in writers):
        return False                # a routine from a bot.yaml: whoever pushed that file wrote it
    for w in writers:
        if w["actor"] == H.KEEPER:
            continue
        if H._json(w["detail_json"], {}).get("via") == "assistant" or not _is_owner(c, auth, w["actor"]):
            return False
    return True


def _untouched(c, task, writer):
    """Nobody but `writer` (who opened it) and BotOps changed the task's words or said anything on it."""
    from .store import H
    botops = "bot:" + H.FLEET_MAINTAINER
    if c.execute("SELECT 1 FROM task_events WHERE task_id=? AND field IN ('title','body') AND actor NOT IN (?,?) LIMIT 1",
                 (task["id"], writer, H.KEEPER)).fetchone():
        return False
    said = c.execute("SELECT 1 FROM messages m WHERE " + H._task_ref_sql("$.task") + "=? AND m.from_actor NOT IN (?,?,?) "
                     "LIMIT 1", (task["id"], H.KEEPER, botops, writer)).fetchone()
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
            # Re-asking on the same task can't clear this: the reopening stays in its history. The person's own new
            # words start a run with their rights anyway (a comment on the open task, or their BotOps chat).
            return ("That task was finished and someone other than the person reopened it, so it never lends their rights "
                    "again, even after they reply. Only a run their own comment on it or their message in their BotOps chat "
                    "starts has their rights: in such a run, send no on_behalf_of; otherwise ask them there and end this run")
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


# Thread writes (docs/threads.md): posting, replying, editing, following, reading and managing members, as the person
# who asked. The route checks that person's rights on the thread (backend/threads.py). `messages/` is in OWN for
# BotOps' own messages, so `default_delegable` matches these routes before OWN.
THREADS = _routes(
    ("POST", r"threads"), ("PATCH", rf"threads/{_S}"),
    ("POST", rf"threads/{_S}/(messages|read|read-all|members)"), ("DELETE", rf"threads/{_S}/members/{_S}"),
    ("PUT", rf"threads/{_S}/follow"), ("DELETE", rf"threads/{_S}/follow"),
    ("PATCH", rf"messages/{_S}"), ("POST", rf"threads/messages/{_S}/task"),
)


# Runs at once, as the person. Some of these keep their own "always a click" rule inside the route (adding someone
# a role, a Computer that does not take members' bots): the route answers with the card.
# A credential grant to a bot runs at once when the person is a credential administrator (or stored it, for a bot they
# manage) and is refused, with who to ask, when they are not; only a grant to a person or to every computer is a card (backend/credentials.py).
DO = _routes(
    ("POST", r"bots"), ("POST", r"bots/register"),
    ("POST", rf"bots/{_S}/(definition|assignment|placement|place|go-live|model|fallback|transitions|control|owners|co-owners|"
             rf"onboarded|goals|updates|tools|quarantine/clear|limit/retry)"),
    ("POST", rf"bots/{_S}/tools/{_S}/(delete|update)"), ("DELETE", rf"bots/{_S}/tools/{_S}"),
    # Copying a bot or a skill, bringing a copy up to date and suggesting its changes back: each is checked with the requester's own
    # rights on the bots it names (backend/bot_copy.py).
    ("POST", rf"bots/{_S}/(copy|copies|branches|update-from-original|suggest-to-original|skills/copy|repository-read-token)"),
    # A Hermes bot (docs/hermes-agents.md): restoring an archived bot, pairing its profile with the code the connector
    # prints, and rotating or revoking its agent credential. The token itself never comes back to BotOps (the route).
    ("POST", rf"bots/{_S}/(archive|restore)"), ("POST", rf"bots/{_S}/agent-credential"), ("POST", rf"bots/{_S}/agent-credential/revoke"),
    ("POST", r"agents/pairings/(approve|decline)"),
    ("POST", rf"bots/{_S}/access"), ("PUT", rf"bots/{_S}/access"),
    # A person's or a group's access (backend/teammate_access.py): the route checks the person manages them.
    ("POST", rf"(people|humans|groups)/{_S}/access"), ("PUT", rf"(people|humans|groups)/{_S}/access"),
    ("PUT", rf"bots/{_S}/repositories"), ("PUT", rf"repositories/{_S}/{_S}"),
    ("PUT", "repositories/settings"), ("POST", "repositories/refresh"),
    ("PUT", rf"bots/{_S}/github-repos"), ("POST", r"github/repos"), ("DELETE", rf"github/repos/{_S}/{_S}"),
    # Automations (backend/automations.py): the route checks the person's place on the org chart.
    # Deleting one is CONFIRM below.
    ("POST", r"automations"), ("PATCH", rf"automations/{_S}"),
    ("POST", rf"automations/{_S}/run"), ("POST", rf"automations/{_S}/versions/{_S}/restore"),
    ("DELETE", rf"automations/{_S}/versions/{_S}"), ("PATCH", rf"inboxes/{_S}"),
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
    # A page's cadence, data date, replacement, archive or restore: the route checks the person may manage its bot.
    ("PATCH", rf"pages/{_S}"),
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
    # The order of a manager's reports on the chart: whoever may put reports under that manager (the route checks).
    ("PUT", rf"org/order/{_S}"),
    # Which Slack channels bots read and post in: an owner or an admin's call, which the route checks.
    ("POST", r"slack/channels"), ("POST", r"slack/channels/(remove|import)"),
    # Peering with another company's Tico and what it shares: the owner's or an admin's call, and a thread's routes also
    # ask for read and manage on that thread (backend/federation_routes.py).
    ("POST", r"federation/keys(/rotate)?"), ("POST", r"federation/peerings"),
    ("POST", rf"federation/peerings/{_S}/(approve|pause|revoke)"),
    ("POST", rf"federation/threads/{_S}/(readers|installs)"), ("POST", rf"federation/threads/{_S}/installs/{_S}/retry"),
    ("DELETE", rf"federation/threads/{_S}/installs/{_S}"),
    ("POST", rf"credentials/{_S}/grants"), ("POST", rf"credentials/{_S}/grants/{_S}/revoke"),
    ("DELETE", rf"credentials/{_S}"),
    # Computers: a restart, a model sign-in (its code is pasted in the app, never here), who may open its screen (opening
    # one is the person's own, in the app), inbox sharing where one owner runs
    # everything (the route says where). Limits are spending: lowering one is always direct; raising one, and the providers,
    # are direct unless the owner's rule says otherwise (TIGHTENED).
    ("POST", rf"(runners|computers)/{_S}/(restart|inbox-only|logins|screen)"), ("POST", rf"(runners|computers)/{_S}/logins/{_S}/cancel"),
    ("PUT", r"providers"), ("PUT", r"usage/limits"), ("PUT", rf"usage/limits/{_S}"), ("PUT", rf"usage/pools/(company|groups/{_S})"),
    # The Tico AI Gateway address, off or back to automatic is a provider change too (the owner's, which the route
    # checks). Saving its key is a secret, refused in the request; checking it changes nothing.
    ("PUT", r"providers/gateway"),
    ("DELETE", r"providers/gateway"), ("POST", r"providers/gateway/automatic"),
    # Giving a computer a company subscription, or removing one: an owner's or an admin's call, which the route checks;
    # a Confirm card (backend/settings_entries.py), since it restarts that computer's runs. Saving a value is refused there.
    ("PUT", r"computer-subscriptions"), ("DELETE", rf"model-subscriptions/{_S}"),
    # A message or chat to a bot stays in the team; a message to a person is a card (`classify`).
    ("POST", rf"chat/{_S}"),
    ("POST", r"system/update(/check)?"),
    # Sleep when idle: an owner's or an admin's call, which the route checks.
    ("POST", r"sleep"),
    # Incoming triggers and their Tico Gateway addresses: whoever manages the trigger (backend/triggers.py), never
    # BotOps' own right to manage every trigger while it works for someone.
    ("POST", r"triggers"), ("POST", rf"triggers/{_S}"), ("POST", rf"triggers/{_S}/(rotate|signing|recover|delete)"),
    ("POST", rf"triggers/{_S}/deliveries/.+/replay"), ("POST", rf"triggers/{_S}/relay(/(rotate|wake|delete))?"),
    # Task types and tags (movers, a tag's owner), the release schedule and Release now, screening's modes and Test,
    # the team's run budget and Tico's own jobs (an owner or an admin): each route checks the person.
    ("POST", r"task-types"), ("POST", rf"task-types/{_S}(/delete)?"), ("DELETE", rf"task-types/{_S}"),
    ("POST", r"tags"), ("POST", rf"tags/{_S}(/instances)?"),
    ("PATCH", r"release-schedule"), ("POST", r"release/now"),
    ("PUT", r"screening"), ("POST", r"screening/test"),
    ("POST", r"settings/turn-budgets"),
    ("PUT", rf"system-routines/{_S}"), ("POST", rf"system-routines/{_S}/run"), ("POST", r"task-manager/turn-(on|off)"),
)

# Cards again when the owner turned "BotOps changes providers and limits without asking" off. A limit lowered is
# still direct: app.py checks it against what is set.
GATEWAY = _routes(("PUT", r"providers/gateway"), ("DELETE", r"providers/gateway"),
                  ("POST", r"providers/gateway/automatic"))
TIGHTENED = _routes(("PUT", r"providers"), ("PUT", r"usage/limits"), ("PUT", rf"usage/limits/{_S}"),
                    ("PUT", rf"usage/pools/(company|groups/{_S})")) + GATEWAY
LIMITS = re.compile(API + rf"usage/limits(/{_S})?")

# Comes back as a Confirm card; it runs only on the person's own click, as them.
CONFIRM = _routes(
    ("POST", rf"(people|humans)/{_S}"),
    ("PUT", r"access/limits"), ("PUT", r"access/allow"),
    ("DELETE", rf"automations/{_S}"), ("POST", rf"automations/{_S}/delete"),
    ("POST", rf"(runners|computers)/{_S}/(member-bots|revoke)"),
    ("POST", rf"(goal-proposals|proposals)/{_S}/decide"),
    ("POST", r"support/tickets"),
    ("PATCH", rf"files/{_S}"),
    # Deleting a file for good removes its bytes: always the owner's own tap.
    ("DELETE", rf"files/{_S}"),
    ("PUT", r"directory"), ("POST", r"directory/sync"), ("POST", r"directory/preview"),
    ("POST", r"slack/disconnect"), ("POST", r"github/app/disconnect"),
    # Picking a seat's candidate makes a bot (recruiting._may_hire: only on the person's own confirmation).
    ("POST", rf"seats/{_S}/hire"),
)

# The messages route is a card unless it names a bot (`classify`).
MESSAGES = re.compile(API + r"messages")

# Confirm-card routes only an owner or an admin may ask for: a member is told so at once, not handed a card that fails.
ADMIN_ONLY = _routes(
    ("PUT", r"providers"), ("PUT", r"access/(limits|allow)"), ("PUT", r"usage/limits"), ("PUT", rf"usage/pools/(company|groups/{_S})"),
    ("POST", rf"(runners|computers)/{_S}/member-bots"),
    ("POST", r"system/update"), ("PUT", r"directory"), ("POST", r"directory/(sync|preview)"),
    ("POST", r"(slack|github/app)/disconnect"), ("POST", rf"credentials/{_S}/grants/{_S}/revoke"),
)

# Always the person's own tap when BotOps acts for them, whatever they said in chat: handing over the company, updating
# Tico, and a computer's one-time code (shown on the card once they tap, never in BotOps' answer).
ALWAYS = _routes(("POST", r"access/owner"), ("POST", r"system/update"), ("POST", r"enrollments"))
# Owner-only: a member or an admin is told so at once, not handed a card that fails (the Gateway connection included).
OWNER_ONLY = _routes(("POST", r"access/owner"), ("DELETE", rf"files/{_S}")) + GATEWAY
# Team rules that set BotOps' own powers: a change to one of them is always the person's own tap.
OWN_POWER_RULES = ("botops_direct", "botops_manages_bots", "managers_manage_reports")
RULES = re.compile(API + r"access/rules")

# Hand a secret's value back to whoever calls: BotOps acting for a person is refused, with where the person does it.
PERSON_ONLY = [
    ("POST", re.compile(API + rf"credentials/{_S}/reveal"), "A stored credential's value is shown only to a person, in Credentials"),
    ("POST", re.compile(API + rf"access/(people|humans)/{_S}/password"), "A person makes someone a password in Settings > Humans"),
    ("POST", re.compile(API + r"me/tokens"), "A person makes their own personal API token in Settings"),
    ("POST", re.compile(API + r"directory/scim-token"), "The owner makes a SCIM token in Settings > Humans"),
]
DETAIL_PERSON_ONLY = ": its value never goes to a bot, so BotOps does not do this for you."

# Read as the person, except what hands back a secret or is a computer's own channel.
NO_READ = re.compile(API + r"(credential-runtime|runner-credential-(migration|grants)|me/tokens.*|mcp|agents/setup-script|jobs.*|attempts.*"
                     r"|runner-logins.*|runner-(model|gateway)-credentials|runners/desired|runners/assignments|runners/eligible|runners/me/events|directory/scim-token)")

# Named like a secret: refused whatever route it is on.
SECRET_KEYS = re.compile(r"^(secret|password|passwd|passphrase|token|access_token|refresh_token|api_key|apikey|"
                         r"private_key|client_secret|authorization|bearer|bot_token|app_token)$", re.I)
# Names that are a secret on one route only: elsewhere `key` names a setting, a field or an idempotency key.
ROUTE_SECRETS = [(("PUT",), re.compile(API + r"providers/gateway"), ("key",))]

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
    if any(m == method and p.fullmatch(path) for m, p in DO + THREADS):
        return "do"
    return None


def person_only(method, path):
    """Why BotOps acting for a person may not make this call (the route hands a secret's value back), or ""."""
    path, method = normalize(path), str(method or "").upper()
    return next((why + DETAIL_PERSON_ONLY for m, p, why in PERSON_ONLY if path and m == method and p.fullmatch(path)), "")


def owner_only(method, path, body=None):
    """A card only the owner may ask for: handing over the company, and the team rules that set BotOps' own powers."""
    path, method = normalize(path), str(method or "").upper()
    if not path:
        return False
    return (any(m == method and p.fullmatch(path) for m, p in OWNER_ONLY)
            or (method == "PUT" and bool(RULES.fullmatch(path)) and own_powers(body)))


AUTOMATION_DELETE = (re.compile(API + rf"automations/({_S})"), re.compile(API + rf"automations/({_S})/delete"))


def automation_deleted(method, path):
    """The automation id a delete names (DELETE automations/{id}, POST automations/{id}/delete), else None."""
    path, method = normalize(path) or "", str(method or "").upper()
    found = AUTOMATION_DELETE[0 if method == "DELETE" else 1].fullmatch(path) if method in ("DELETE", "POST") else None
    return found.group(1) if found else None


def own_powers(body):
    return isinstance(body, dict) and any(body.get(name) is not None for name in OWN_POWER_RULES)


def card_for_person(method, path, body=None, rules=None, lowers=None, *, force_confirm=False):
    """Whether BotOps acting for a person answers with a Confirm card in their chat instead of making the change: the
    routes that always need their own tap (ALWAYS, a team rule on BotOps' own powers), the CONFIRM routes and, when
    the team turned `botops_direct` off, providers and limits (TIGHTENED) unless `lowers()` says the change only lowers
    a limit. Taking a credential grant back stays direct, as giving one to a bot is. A run never confirms for anyone."""
    path, method = normalize(path), str(method or "").upper()
    if not path or method in ("GET", "HEAD", "OPTIONS"):
        return False
    if force_confirm:
        return True
    match = lambda routes: any(m == method and p.fullmatch(path) for m, p in routes)
    if match(ALWAYS) or match(CONFIRM):
        return True
    if method == "PUT" and RULES.fullmatch(path) and own_powers(body):
        return True
    if match(TIGHTENED):
        if rules is None or rules.get("botops_direct", True):
            return False
        return not (LIMITS.fullmatch(path) or path.startswith(API + "usage/pools/")) or not (lowers and lowers())
    return False


# The card's line for what BotOps asked to do, in plain words; the card lists every field it sends.
SUMMARIES = [
    ("POST", re.compile(API + r"access/owner"), "Hand ownership of the team to another person"),
    ("POST", re.compile(API + r"system/update"), "Update Tico now (the server restarts)"),
    ("POST", re.compile(API + r"enrollments"), "Make a one-time code to add a computer: it shows here once you confirm"),
    ("PUT", RULES, "Change the team's rules"),
    ("PUT", re.compile(API + r"providers"), "Change the team's AI providers"),
    ("PUT", re.compile(API + r"providers/gateway"), "Change the Tico AI Gateway address"),
    ("DELETE", re.compile(API + r"providers/gateway"), "Turn off the Tico AI Gateway (its saved key is forgotten)"),
    ("POST", re.compile(API + r"providers/gateway/automatic"), "Connect the Tico AI Gateway automatically again"),
    ("PUT", re.compile(API + rf"usage/(limits(/{_S})?|pools/.+)"), "Change a spending limit"),
    ("DELETE", re.compile(API + rf"automations/{_S}"), "Delete an automation"),
    ("POST", re.compile(API + rf"automations/{_S}/delete"), "Delete an automation"),
]


def card_summary(method, path, body=None):
    path, method = normalize(path) or "", str(method or "").upper()
    found = next((text for m, p, text in SUMMARIES if m == method and p.fullmatch(path)), None)
    if found:
        return found
    _, _, where = _place(path)
    return "Make this change in " + where.replace("its ", "the bot's ") + " for you"


# What a request a bot carried for a person (app.py carried_request) does with the person's rights: their own work
# items, never who may do what or what it costs. Everything else runs with the asking bot's rights, and a refusal
# becomes the person's Confirm card. An allow-list, so a new route lends nothing until it is added here.
CARRIED = _routes(
    ("POST", r"tasks"), ("POST", r"tasks/dry-run"), ("POST", rf"tasks/{_S}"), ("POST", rf"tasks/{_S}/(comments|links|ask)"),
    ("POST", r"docs"), ("PATCH", rf"docs/{_S}"), ("POST", r"linked-docs"), ("PATCH", rf"linked-docs/{_S}"),
    ("POST", r"goals"), ("POST", rf"goals/{_S}"), ("POST", rf"goals/{_S}/(status|checkins|kpis)"),
    ("POST", rf"goals/{_S}/kpis/{_S}"), ("POST", r"kpis"), ("POST", rf"kpis/{_S}"), ("POST", rf"kpis/{_S}/readings"),
)


def carried_lends(method, path):
    """Whether a carried request makes this call with the person's rights (CARRIED)."""
    path, method = normalize(path), str(method).upper()
    return bool(path) and any(m == method and p.fullmatch(path) for m, p in CARRIED)


# A manager bot's report, made, archived or restored by BotOps on the manager's ask (backend/report_managers.py).
LIFECYCLE = _routes(("POST", r"bots"), ("POST", rf"bots/{_S}/archive"), ("POST", rf"bots/{_S}/restore"))


def lifecycle_call(method, path):
    """Whether this call creates, archives or restores a bot: the routes a manager bot's ask may reach through BotOps."""
    path, method = normalize(path), str(method).upper()
    return bool(path) and any(m == method and p.fullmatch(path) for m, p in LIFECYCLE)


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


# Registering or changing a bot's tool (backend/bot_tools.py): an MCP server's header names its credential as a
# `${VAR}` placeholder, so `Authorization` there holds a variable's name, not a value.
TOOL_ROUTES = _routes(("POST", rf"bots/{_S}/tools"), ("POST", rf"bots/{_S}/tools/{_S}/update"))
SCHEMES = {"bearer", "basic", "token", "bot"}


def placeholder_only(value, env=None):
    """Whether a header value is one optional auth scheme (SCHEMES) and `${VAR}` placeholders, each for `env` when it is
    given: the shape clients/access_entry.clean_mcp writes, with no literal that could be a credential."""
    from clients.access_entry import PLACEHOLDER
    if not isinstance(value, str) or len(value) > 500 or "\n" in value or "\r" in value:
        return False
    names, bare = PLACEHOLDER.findall(value), PLACEHOLDER.sub("", value).strip()
    return (bool(names) and (not bare or bare.lower() in SCHEMES)
            and (env is None or all(name == env for name in names)))


def secret_in_request(method, path, body):
    """`secret_in` for a request: on a tool route only, a secret-named `mcp.headers` key passes when its value is
    `placeholder_only` for the body's `env`. Every other key, and every other route, is checked as `secret_in`."""
    path = normalize(path)
    if path and isinstance(body, dict):
        for methods, route, names in ROUTE_SECRETS:
            if str(method).upper() in methods and route.fullmatch(path):
                found = next((name for name in names if body.get(name) not in (None, "")), None)
                if found:
                    return found
    mcp = body.get("mcp") if isinstance(body, dict) else None
    if (not path or not isinstance(mcp, dict) or not isinstance(mcp.get("headers"), dict)
            or not any(m == str(method).upper() and p.fullmatch(path) for m, p in TOOL_ROUTES)):
        return secret_in(body)
    found = secret_in({**body, "mcp": {k: v for k, v in mcp.items() if k != "headers"}})
    if found:
        return found
    env = body.get("env") if "env" in body else None
    for name, value in mcp["headers"].items():
        if SECRET_KEYS.match(str(name).strip()):        # clean_mcp strips the name it stores
            if not placeholder_only(value, None if env is None else str(env)):
                return str(name)
        else:
            found = secret_in(value, 2)
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


def default_delegable(method, path, body=None, query=None):
    """Whether a BotOps call with no explicit `on_behalf_of` acts as the person who asked: only the routes BotOps
    may do or propose for a person, and never its own plumbing or its own messages."""
    path = normalize(path)
    if path and str(method).upper() in ("GET", "POST") and re.fullmatch(API + rf"(?:runners|computers)/{_S}/logins(?:/{_S}(?:/cancel)?)?", path):
        return classify(method, path, body) == "do"
    if path and any(m == str(method).upper() and p.fullmatch(path) for m, p in THREADS):
        return classify(method, path, body) == "do"
    if not path or OWN.fullmatch(path):
        return False
    mine = str((query or {}).get("mine", "")).lower() in ("1", "true", "yes", "on")
    if path == API + "rules" and str(method).upper() == "GET" and mine:
        return False      # BotOps's own rules and settings (`hub rules list --mine`): its own rights, not its requester's
    # The person's own card or plain refusal (`card_for_person`, `person_only`) reaches a client that sends no
    # header too, not BotOps' own 403.
    if any(m == str(method).upper() and p.fullmatch(path) for m, p in ALWAYS) or person_only(method, path):
        return True
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


def refusal_for_person(path, text, base=""):
    """`{"fix", "link"}` for a refusal decided for the person BotOps acts for (`person_only`, a role): what they do
    themselves, and where."""
    link = _place(path)[0]
    return {"fix": text, "link": (base.rstrip("/") + "/" + link) if base else link}


def mail_settings_hint(bot, slug=""):
    """Where a person turns a bot's mail sending on: the only fix for a refused write to its mail setting. `bot` is
    the name the app shows, `slug` the one `hub` takes."""
    return ("A person who manages " + bot + " turns its mail sending on in Settings > Bots > " + bot
            + " > Mail sending, or with `hub bot mail " + (slug or bot) + " --send`")


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
    elif slug and re.fullmatch(API + rf"bots/{_S}/mail-settings", normalize(path) or ""):
        # Mail sending is a person's switch (backend/mail_settings.py): no request lends it to BotOps, so "go ahead"
        # in chat would not help. Say who sets it and where.
        text = mail_settings_hint(bot, slug)
        link = "#/settings"
    elif re.fullmatch(API + rf"tasks/{_S}/worktrees(?:/attach)?", normalize(path) or ""):
        # A worktree is made with the task's owner bot's own repository access (backend/worktrees.py), so no one's
        # request lends it: say who gives that access.
        text = ("The task's bot needs write access to that repository: an owner or admin gives it in Settings > "
                "Repositories; then BotOps can add the worktree itself")
        link = "#/repositories"
    elif code == "on_behalf_of":
        # The cited request lends nothing (closed, too old, another person's, words others wrote): the detail says why.
        text = ("BotOps can't act on that earlier request: ask it again in your BotOps chat so it acts with your "
                "rights now, or change " + where + " yourself")
    elif acting.actor == "bot:botops":
        text = ("No one's request was attached to this run, so BotOps used its own rights: say go ahead in your "
                "BotOps chat, or change " + where + " yourself")
    elif acting.role == "bot":
        asker = names.get(acting.actor) or acting.actor.split(":", 1)[-1]
        text = (asker + " asked for this without citing who wanted it, so BotOps had only " + asker + "'s rights: say "
                "go ahead in your BotOps chat, or change " + where + " yourself")
    else:
        text = "Your role can't change this: ask an owner or admin to change " + where
    return {"fix": text, "link": (base.rstrip("/") + "/" + link) if base else link}
