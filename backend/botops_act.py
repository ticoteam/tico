"""BotOps request origins and legacy route classification.

The server dispatcher (app.py) resolves the human or bot that requested the run and applies
that identity's own rights to every v2 route by default. No requester means BotOps' own rights.
For a person, the dispatcher answers a few routes with a Confirm card in their chat instead (`card_for_person`)
and refuses those that hand a secret back (`person_only`); the identity it lends is never confirmed, so a route's
own "always a click" rule asks too. Only the person's tap on the card runs it.
Secrets go through Credentials, never arbitrary API request bodies.
"""

from dataclasses import replace
from fastapi.responses import JSONResponse
from . import team_rules, usage_limits
from .auth import Identity
from .onboarding import BOTOPS
from . import rooms
from . import settings_catalog
from clients import gateway as gateway_client
from . import gateway_connection
from .store import H, Problem

import json
import re


def request_task(c, auth, who, title, body="", instructions=None):
    """A server-generated BotOps job carries the rights of the human who requested it. `body` is the person's request,
    its first chat message; `instructions` are the job's working instructions, which reach BotOps through its run.

    The origin is written here, never taken from task text or caller-supplied references.
    """
    from . import rooms
    from .store import H

    botops = H.bot_actor(H.FLEET_MAINTAINER)
    # A new company files these before BotOps has started (`allow_planned`). Its chat room cannot be opened yet, so the
    # task opens its own thread, and asking for the room would only record a refusal nobody made.
    planned = (H.bot(c, H.FLEET_MAINTAINER) or {}).get("state") == "planned"
    task = H.task_create(
        c,
        who.actor,
        title,
        body,
        botops,
        instructions=instructions,
        allow_planned=True,
        conversation_id=None if planned else rooms.task_conversation_id(c, auth, botops, who.actor),
    )
    if who.role in ("owner", "human") and who.via != "assistant":
        H.event(c, who.actor, "botops.task_requested", task["id"], {})
    return task


# Message refs that mean words someone else wrote started the run: a comment, the Assistant, Slack, a live meeting.
OTHERS_WORDS = ("comment", "via", "assistant", "slack", "routing", "live_meeting")
# How long a person's request lends their rights after they last spoke on its task (created it, sent the message it
# carries, commented on it), however long the task stays open.
LEND_MAX_DAYS = 30


def own_run(c, auth, attempt_id):
    """Whether this BotOps run is its own work, with its own wider rights (`Auth.botops_manages`): only a Tico wake
    from BotOps' own schedule, the daily update or a timed automation only the owner (or the product) wrote (a converted
    routine too, when the routine was theirs). Everything else Tico wakes it with carries words someone else wrote: a service key's or
    a watcher's task, a stopped job's request, a routine BotOps set itself while it worked for someone, a task someone
    commented on."""
    from .store import H

    turn = (
        c.execute(
            "SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=? AND a.bot_id=?",
            (attempt_id, H.FLEET_MAINTAINER),
        ).fetchone()
        if attempt_id
        else None
    )
    msg = H.message(c, turn["message_id"]) if turn and turn["message_id"] else None
    refs = (msg or {}).get("refs") or {}
    # A converted routine's task is opened by its automation as BotOps itself (backend/automation_actions.py).
    if not msg or msg["sender_id"] not in (H.TICO_ACTOR, "bot:" + H.FLEET_MAINTAINER):
        return False
    if msg["sender_id"] != H.TICO_ACTOR and not refs.get("automation"):
        return False
    if any(refs.get(key) for key in OTHERS_WORDS):
        return False
    task_id = H.message_task_id(msg)
    if not task_id:
        return msg["sender_id"] == H.TICO_ACTOR and msg["kind"] == "notice" and refs.get("wake") == "update"
    task = H.task(c, task_id)
    if refs.get("automation"):
        return _owners_automation_task(c, auth, task, refs)
    return _owners_routine_task(c, auth, task)


def _owners_routine_task(c, auth, task):
    """A task Tico opened for one of BotOps' timed routines, whose every word the owner or the product wrote:
    the routine (created and since changed only by them), the task itself, and anything said on it."""
    from .store import H

    tico, botops = H.TICO_ACTOR, "bot:" + H.FLEET_MAINTAINER
    if not task or task["requester_id"] != tico or task["owner_id"] != botops:
        return False
    made = c.execute(
        "SELECT actor FROM events WHERE action='task.create' AND target=? ORDER BY created_at,id LIMIT 1", (task["id"],)
    ).fetchone()
    if not made or made["actor"] != tico:
        return False
    # An event routine's task carries the event's content (a meeting's notes, say), so only timed and manual runs.
    schedule = c.execute(
        "SELECT o.schedule_id FROM schedule_occurrences o JOIN schedules s ON s.id=o.schedule_id "
        "WHERE o.task_id=? AND o.outcome<>'event' AND s.bot=? LIMIT 1",
        (task["id"], H.FLEET_MAINTAINER),
    ).fetchone()
    if not schedule or not _owners_routine(c, auth, schedule["schedule_id"]):
        return False
    return _untouched(c, task, tico)


def _owners_automation_task(c, auth, task, refs):
    """A task one of BotOps' timed automations opened, whose every version and change only the owner (or the product)
    wrote, not through the Assistant or a token. A converted routine counts only when the routine itself was the
    owner's (`_owners_routine`): one from a bot.yaml, or one BotOps set while it worked for someone, never does."""
    from .store import H

    botops = "bot:" + H.FLEET_MAINTAINER
    automation_id = str(refs.get("automation") or "")
    if not task or task["owner_id"] != botops or task["requester_id"] not in (H.TICO_ACTOR, botops):
        return False
    row = c.execute(
        "SELECT owner_id, flow, trigger_type FROM automations WHERE id=? AND deleted_at IS NULL", (automation_id,)
    ).fetchone()
    if (
        not row
        or row["owner_id"] != botops
        or row["trigger_type"] != "schedule"
        or refs.get("trigger_source") != "automation:" + automation_id
    ):
        return False
    # The run that opened this task is that automation's own, from its schedule or a Run now, not a chained event.
    run = c.execute(
        "SELECT 1 FROM automation_runs WHERE id=? AND automation_id=?",
        (str(refs.get("automation_run") or ""), automation_id),
    ).fetchone()
    if not run or refs.get("automation_chain"):
        return False
    source = (H._json(row["flow"], {}) or {}).get("source")
    if isinstance(source, dict) and source.get("kind") == "routine":
        found = c.execute("SELECT bot FROM schedules WHERE id=?", (source.get("id"),)).fetchone()
        if not found or found["bot"] != H.FLEET_MAINTAINER or not _owners_routine(c, auth, source["id"]):
            return False
    actors = {r[0] for r in c.execute("SELECT actor FROM automation_versions WHERE automation_id=?", (automation_id,))}
    for r in c.execute("SELECT actor, via FROM audit_log WHERE object=?", ("automation:" + automation_id,)):
        if r["via"] == "assistant" or str(r["via"] or "").startswith("token:"):
            return False
        actors.add(r["actor"])
    if not actors or any(a != H.TICO_ACTOR and not _is_owner(c, auth, a) for a in actors):
        return False
    return _untouched(c, task, task["requester_id"])


def _owners_routine(c, auth, schedule_id):
    """A routine created through the API and since changed only by the owner (or the product)."""
    from .store import H

    writers = c.execute(
        "SELECT action,actor,detail FROM events WHERE target=? AND action IN ('routine.created','routine.updated')",
        (schedule_id,),
    ).fetchall()
    if not any(w["action"] == "routine.created" for w in writers):
        return False  # a routine from a bot.yaml: whoever pushed that file wrote it
    for w in writers:
        if w["actor"] == H.TICO_ACTOR:
            continue
        if H._json(w["detail"], {}).get("via") == "assistant" or not _is_owner(c, auth, w["actor"]):
            return False
    return True


def _untouched(c, task, writer):
    """Nobody but `writer` (who opened it) and BotOps changed the task's words or said anything on it."""
    from .store import H

    botops = "bot:" + H.FLEET_MAINTAINER
    if c.execute(
        "SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? AND kind IN ('title','body') "
        "AND actor_id NOT IN (?,?) LIMIT 1",
        (task["id"], writer, H.TICO_ACTOR),
    ).fetchone():
        return False
    said = c.execute(
        "SELECT 1 FROM message_refs m WHERE m.ref_task_id=? AND m.sender_id NOT IN (?,?,?) LIMIT 1",
        (task["id"], H.TICO_ACTOR, botops, writer),
    ).fetchone()
    return not said


def _is_owner(c, auth, actor):
    from .store import H, Problem

    if not H.is_human(actor):
        return False
    try:
        return auth.identity_for_actor(c, actor).role == "owner"
    except Problem:
        return False


def reopened_by(c, task_id):
    """Each time the task was reopened after it was done or closed, oldest first: {"actor", "via"} (the engine's move
    rows, backend/task_engine.py)."""
    return [
        dict(row)
        for row in c.execute(
            "SELECT actor_id AS actor, via FROM history_events WHERE subject_type='task' AND subject_id=? "
            "AND kind='move' AND json_extract(data, '$.move')='reopen' ORDER BY rowid",
            (task_id,),
        )
    ]


def lend_lapsed(c, task):
    """Why a person's task no longer lends their rights, whatever its status says, or "" while it does: someone other
    than the requester reopened it after it was done or closed (the request it carried was finished), or the
    requester last spoke on it more than LEND_MAX_DAYS ago."""
    from .store import H

    requester = task["requester_id"]
    for row in reopened_by(c, task["id"]):
        if row["actor"] != requester or row.get("via"):
            # Re-asking on the same task can't clear this: the reopening stays in its history. The person's own new
            # words start a run with their rights anyway (a comment on the open task, or their BotOps chat).
            return (
                "That task was finished and someone other than the person reopened it, so it never lends their rights "
                "again, even after they reply. Only a run their own comment on it or their message in their BotOps chat "
                "starts has their rights: in such a run, send no on_behalf_of; otherwise ask them there and end this run"
            )
    spoke = []
    made = c.execute(
        "SELECT actor,detail FROM events WHERE action='task.create' AND target=? ORDER BY created_at,id LIMIT 1",
        (task["id"],),
    ).fetchone()
    if made and made["actor"] == requester and H._json(made["detail"], {}).get("via") != "assistant":
        spoke.append(task["created_at"])
    origin = H.message(c, task["request_id"]) if task.get("request_id") else None
    if origin and origin["sender_id"] == requester:
        spoke.append(origin["created_at"])
    comment = c.execute(
        "SELECT max(m.created_at) FROM message_refs m WHERE m.ref_task_id=? "
        "AND m.sender_id=? AND json_extract(m.refs,'$.comment') IS NOT NULL "
        "AND json_extract(m.refs,'$.via') IS NULL AND json_extract(m.refs,'$.assistant') IS NULL",
        (task["id"], requester),
    ).fetchone()[0]
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
    ("POST", r"threads"),
    ("PATCH", rf"threads/{_S}"),
    ("POST", rf"threads/{_S}/(messages|read|read-all|members)"),
    ("DELETE", rf"threads/{_S}/members/{_S}"),
    ("PUT", rf"threads/{_S}/follow"),
    ("DELETE", rf"threads/{_S}/follow"),
    ("PUT", rf"threads/{_S}/pin"),
    ("DELETE", rf"threads/{_S}/pin"),
    ("PATCH", rf"messages/{_S}"),
    ("DELETE", rf"messages/{_S}"),
    ("POST", rf"threads/messages/{_S}/task"),
    ("PUT", rf"messages/{_S}/reactions/{_S}"),
    ("DELETE", rf"messages/{_S}/reactions/{_S}"),
    ("POST", r"threads/dm"),
    ("POST", rf"threads/{_S}/open-earlier"),
    ("POST", rf"uploads/threads/{_S}/messages"),
)


# Runs at once, as the person. Some of these keep their own "always a click" rule inside the route (adding someone
# a role, a Computer that does not take members' bots): the route answers with the card.
# A credential grant to a bot runs at once when the person is a credential administrator (or stored it, for a bot they
# manage) and is refused, with who to ask, when they are not; only a grant to a person or to every computer is a card (backend/credentials.py).
DO = _routes(
    ("POST", r"bots"),
    ("POST", r"bots/register"),
    (
        "POST",
        rf"bots/{_S}/(definition|assignment|placement|place|go-live|model|fallback|transitions|control|owners|co-owners|"
        rf"onboarded|goals|updates|tools|quarantine/clear|limit/retry)",
    ),
    ("POST", rf"bots/{_S}/tools/{_S}/(delete|update)"),
    ("DELETE", rf"bots/{_S}/tools/{_S}"),
    # Copying a bot or a skill, bringing a copy up to date and suggesting its changes back: each is checked with the requester's own
    # rights on the bots it names (backend/bot_copy.py).
    (
        "POST",
        rf"bots/{_S}/(copy|copies|branches|update-from-original|suggest-to-original|skills/copy|repository-read-token)",
    ),
    # A Hermes bot (docs/hermes-agents.md): restoring an archived bot, pairing its profile with the code the connector
    # prints, and rotating or revoking its agent credential. The token itself never comes back to BotOps (the route).
    ("POST", rf"bots/{_S}/(archive|restore)"),
    ("POST", rf"bots/{_S}/agent-credential"),
    ("POST", rf"bots/{_S}/agent-credential/revoke"),
    ("POST", r"agents/pairings/(approve|decline)"),
    ("POST", rf"bots/{_S}/access"),
    ("PUT", rf"bots/{_S}/access"),
    # A person's or a group's access (backend/teammate_access.py): the route checks the person manages them.
    ("POST", rf"(people|humans|groups)/{_S}/access"),
    ("PUT", rf"(people|humans|groups)/{_S}/access"),
    ("PUT", rf"bots/{_S}/repositories"),
    ("PUT", rf"repositories/{_S}/{_S}"),
    ("PUT", "repositories/settings"),
    ("POST", "repositories/refresh"),
    ("PUT", rf"bots/{_S}/github-repos"),
    ("POST", r"github/repos"),
    ("DELETE", rf"github/repos/{_S}/{_S}"),
    # Automations (backend/automations.py): the route checks the person's place on the org chart.
    # Deleting one is CONFIRM below.
    ("POST", r"automations"),
    ("PATCH", rf"automations/{_S}"),
    ("POST", rf"automations/{_S}/run"),
    ("POST", rf"automations/{_S}/versions/{_S}/restore"),
    ("DELETE", rf"automations/{_S}/versions/{_S}"),
    ("PATCH", rf"inboxes/{_S}"),
    ("POST", rf"settings/history/{_S}/undo"),
    # Goals and KPIs: a goal's or KPI's own rules decide who may change what.
    ("POST", r"goals"),
    ("POST", rf"goals/{_S}"),
    ("POST", rf"goals/{_S}/(status|status/auto|checkins|kpis)"),
    ("POST", rf"goals/{_S}/kpis/{_S}"),
    ("POST", rf"goals/{_S}/kpis/{_S}/unlink"),
    ("POST", r"goals/refresh"),
    ("POST", r"kpis"),
    ("POST", rf"kpis/{_S}"),
    ("POST", rf"kpis/{_S}/readings"),
    ("POST", r"(goal-proposals|proposals)"),
    # Tasks, docs and what the people asked of BotOps.
    ("POST", r"tasks"),
    ("POST", r"tasks/dry-run"),
    ("POST", rf"tasks/{_S}"),
    # A task's moves and owner (task_moves_routes.py): what `POST tasks/{id}` with a status or owner did before.
    ("POST", rf"tasks/{_S}/(comments|links|ask|questions|run-now|move|owner|done|snooze)"),
    # Another bot's task worktrees: a person who moves tasks may add one or ask for one to be cleaned up (the route
    # checks); the computer removes it once the bot is idle.
    ("POST", rf"tasks/{_S}/worktrees(/attach)?"),
    ("DELETE", rf"tasks/{_S}/links/{_S}"),
    ("POST", r"docs"),
    ("PATCH", rf"docs/{_S}"),
    ("POST", rf"docs/{_S}/restore"),
    # A page's cadence, data date, replacement, archive or restore: the route checks the person may manage its bot.
    ("PATCH", rf"pages/{_S}"),
    ("POST", r"linked-docs"),
    ("PATCH", rf"linked-docs/{_S}"),
    ("POST", rf"meetings/{_S}/delete"),
    ("POST", "meetings/granola/sync"),
    ("POST", rf"meetings/{_S}/review"),
    ("POST", "meetings/review"),
    ("POST", "meetings/settings"),
    ("POST", rf"(integrations|tools)/{_S}/learnings"),
    ("POST", rf"(integrations|tools)/{_S}/learnings/{_S}/delete"),
    ("POST", r"health/bot-access/dismiss"),
    # People and access. The route asks for the click on what needs it.
    ("POST", r"access/(people|humans)"),
    ("POST", rf"access/(people|humans)/{_S}"),
    ("PUT", r"access/rules"),
    # Groups: an owner or an admin changes them (the route says so), and a delete moves what is in the group up.
    ("POST", r"groups"),
    ("PATCH", rf"groups/{_S}"),
    ("DELETE", rf"groups/{_S}"),
    # The order of a manager's reports on the chart: whoever may put reports under that manager (the route checks).
    ("PUT", rf"org/order/{_S}"),
    # Which Slack channels bots read and post in: an owner or an admin's call, which the route checks.
    ("POST", r"slack/channels"),
    ("POST", r"slack/channels/remove"),
    # Linking a thread to a Slack channel, taking the link away and checking a suspended one again: an owner's or an
    # admin's call, which the route checks (backend/slack_threads.py).
    ("POST", r"slack/links"),
    ("DELETE", rf"slack/links/{_S}"),
    ("POST", rf"slack/links/{_S}/resume"),
    # Calls: starting one, ending it, adding or removing its bots, asking again for action items and leaving. A join
    # or a token is left out: it hands the person's media token to BotOps, so it stays with the person.
    ("POST", rf"threads/{_S}/calls"),
    ("POST", rf"calls/{_S}/(end|leave|bots|action-items/retry)"),
    ("DELETE", rf"calls/{_S}/bots/{_S}"),
    # Withdrawing a held write to edit and resend it: its author's own call (backend/org_rule_check.py).
    ("POST", rf"rule-holds/{_S}/withdraw"),
    # Who reads an inbox: the route checks the person may change it.
    ("PUT", rf"inbound/inboxes/{_S}/audience"),
    ("POST", rf"credentials/{_S}/grants"),
    ("POST", rf"credentials/{_S}/grants/{_S}/revoke"),
    ("DELETE", rf"credentials/{_S}"),
    # Computers: a restart, a model sign-in (its code is pasted in the app, never here), who may open its screen (opening
    # one is the person's own, in the app), inbox sharing where one owner runs
    # everything (the route says where), its name and how many turns it runs at once (owners and admins, which the routes check). Limits are spending: lowering one is always direct; raising one, and the providers,
    # are direct unless the owner's rule says otherwise (TIGHTENED).
    # The rc.1 spelling runners/{rid}/inbox-only is kept (deprecated, same handler); the rest are computers/ only.
    ("POST", rf"(runners|computers)/{_S}/inbox-only"),
    ("POST", rf"computers/{_S}/(restart|logins|screen|label|turns-at-once)"),
    ("POST", rf"computers/{_S}/logins/{_S}/cancel"),
    ("PUT", r"providers"),
    ("PUT", r"usage/limits"),
    ("PUT", rf"usage/limits/{_S}"),
    ("PUT", rf"usage/pools/(company|groups/{_S})"),
    # The Tico AI Gateway address, off or back to automatic is a provider change too (the owner's, which the route
    # checks). Saving its key is a secret, refused in the request; checking it changes nothing.
    ("PUT", r"providers/gateway"),
    ("DELETE", r"providers/gateway"),
    ("POST", r"providers/gateway/automatic"),
    # Giving a computer a company subscription, or removing one: an owner's or an admin's call, which the route checks;
    # a Confirm card (backend/settings_entries.py), since it restarts that computer's runs. Saving a value is refused there.
    ("PUT", r"computer-subscriptions"),
    ("DELETE", rf"model-subscriptions/{_S}"),
    # A message or chat to a bot stays in the team; a message to a person is a card (`classify`).
    ("POST", rf"chat/{_S}"),
    ("POST", r"system/update(/check)?"),
    # Sleep when idle: an owner's or an admin's call, which the route checks.
    ("POST", r"sleep"),
    # Incoming triggers and their Tico Gateway addresses: whoever manages the trigger (backend/triggers.py), never
    # BotOps' own right to manage every trigger while it works for someone.
    ("POST", r"triggers"),
    ("POST", rf"triggers/{_S}"),
    ("POST", rf"triggers/{_S}/(rotate|signing|recover|delete)"),
    ("POST", rf"triggers/{_S}/deliveries/.+/replay"),
    ("POST", rf"triggers/{_S}/relay(/(rotate|wake|delete))?"),
    # Tags (a tag's owner), the release schedule and Release now, screening's modes and Test, the team's run budget
    # and Tico's own jobs (an owner or an admin): each route checks the person.
    ("POST", r"tags"),
    ("POST", rf"tags/{_S}(/instances)?"),
    ("PATCH", r"release-schedule"),
    ("POST", r"release/now"),
    ("PUT", r"screening"),
    ("POST", r"screening/test"),
    ("POST", r"settings/turn-budgets"),
    ("PUT", rf"system-routines/{_S}"),
    ("POST", rf"system-routines/{_S}/run"),
    ("POST", r"general-manager/turn-(on|off)"),
    # The Company page's drafts, sources, stats and logo: the route checks the person is an owner or an admin. An
    # edition is only drafted here; publishing, approving, overriding, retracting and correcting are not listed, so they
    # are refused for BotOps (editions.require_person, require_own_click). Removing an audience's reader or invite is
    # a way out, so it is direct. Turning an audience on is a card (`enables_audience`), checked before this list.
    ("POST", r"audiences"),
    ("PATCH", rf"audiences/{_S}"),
    ("POST", rf"audiences/{_S}/sources"),
    ("DELETE", rf"audiences/{_S}/sources/{_S}"),
    ("POST", r"company-page/stats"),
    ("PATCH", rf"company-page/stats/{_S}"),
    ("DELETE", rf"company-page/stats/{_S}"),
    ("POST", r"company-page/logo"),
    ("DELETE", r"company-page/logo"),
    ("POST", r"editions"),
    ("PATCH", rf"editions/{_S}"),
    ("POST", rf"editions/{_S}/check"),
    ("DELETE", rf"readers/{_S}"),
    ("DELETE", rf"audiences/{_S}/invites/{_S}"),
)

# Cards again when the owner turned "BotOps changes providers and limits without asking" off. A limit lowered is
# still direct: app.py checks it against what is set.
GATEWAY = _routes(
    ("PUT", r"providers/gateway"), ("DELETE", r"providers/gateway"), ("POST", r"providers/gateway/automatic")
)
TIGHTENED = (
    _routes(
        ("PUT", r"providers"),
        ("PUT", r"usage/limits"),
        ("PUT", rf"usage/limits/{_S}"),
        ("PUT", rf"usage/pools/(company|groups/{_S})"),
    )
    + GATEWAY
)
LIMITS = re.compile(API + rf"usage/limits(/{_S})?")

# Comes back as a Confirm card; it runs only on the person's own click, as them.
CONFIRM = _routes(
    ("PUT", r"letters/settings"),
    ("PUT", rf"letters/outboxes/company/grants/(bot|person)/{_S}"),
    ("DELETE", rf"letters/outboxes/company/grants/(bot|person)/{_S}"),
    ("PUT", rf"letters/bots/{_S}/settings"),
    ("PUT", rf"postal/addresses/{_S}/grants/(bot|person)/{_S}"),
    ("DELETE", rf"postal/addresses/{_S}/grants/(bot|person)/{_S}"),
    ("PUT", r"postal/settings"),
    ("POST", rf"(people|humans)/{_S}"),
    ("PUT", r"access/limits"),
    ("PUT", r"access/allow"),
    ("DELETE", rf"automations/{_S}"),
    ("POST", rf"automations/{_S}/delete"),
    ("POST", rf"computers/{_S}/(member-bots|revoke|browser-host)"),
    ("POST", rf"(goal-proposals|proposals)/{_S}/decide"),
    ("POST", r"support/tickets"),
    ("PATCH", rf"files/{_S}"),
    # Deleting a file for good removes its bytes: always the owner's own tap.
    ("DELETE", rf"files/{_S}"),
    ("PUT", r"directory"),
    ("POST", r"directory/sync"),
    ("POST", r"directory/preview"),
    ("POST", r"slack/disconnect"),
    ("POST", r"github/app/disconnect"),
    # Picking a seat's candidate makes a bot (recruiting._may_hire: only on the person's own confirmation).
    ("POST", rf"seats/{_S}/hire"),
    # The Company page (backend/company_page): what the page or a reader can see is the owner's own tap. Taking a
    # reader's access away is direct (below).
    ("PATCH", r"company-page"),
    ("DELETE", rf"audiences/{_S}"),
    ("PUT", rf"audiences/{_S}/disclosure"),
    ("POST", rf"audiences/{_S}/disclosure/preset"),
    ("POST", r"readers"),
    ("POST", r"readers/import"),
    ("PATCH", rf"readers/{_S}"),
)

# The messages route is a card unless it names a bot (`classify`).
MESSAGES = re.compile(API + r"messages")

# Confirm-card routes only an owner or an admin may ask for: a member is told so at once, not handed a card that fails.
ADMIN_ONLY = _routes(
    ("PUT", r"letters/settings"),
    ("PUT", rf"letters/outboxes/company/grants/(bot|person)/{_S}"),
    ("DELETE", rf"letters/outboxes/company/grants/(bot|person)/{_S}"),
    ("PUT", r"providers"),
    ("PUT", r"access/(limits|allow)"),
    ("PUT", r"usage/limits"),
    ("PUT", rf"usage/pools/(company|groups/{_S})"),
    ("POST", rf"computers/{_S}/(member-bots|browser-host)"),
    ("POST", r"system/update"),
    ("PUT", r"directory"),
    ("POST", r"directory/(sync|preview)"),
    ("POST", r"(slack|github/app)/disconnect"),
    ("POST", rf"credentials/{_S}/grants/{_S}/revoke"),
    ("PATCH", r"company-page"),
    ("DELETE", rf"audiences/{_S}"),
    ("PUT", rf"audiences/{_S}/disclosure"),
    ("POST", r"readers"),
    ("POST", r"readers/import"),
    ("PATCH", rf"readers/{_S}"),
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
    (
        "POST",
        re.compile(API + rf"credentials/{_S}/reveal"),
        "A stored credential's value is shown only to a person, in Credentials",
    ),
    (
        "POST",
        re.compile(API + rf"access/(people|humans)/{_S}/password"),
        "A person makes someone a password in Settings > Humans",
    ),
    ("POST", re.compile(API + r"me/tokens"), "A person makes their own personal API token in Settings"),
    ("POST", re.compile(API + r"directory/scim-token"), "The owner makes a SCIM token in Settings > Humans"),
    # The invite's link is the only copy of its token (readers.create_invite), so it goes to the person, not the chat.
    (
        "POST",
        re.compile(API + rf"audiences/{_S}/invites"),
        "A person makes a reader's invite link in Company page > Readers",
    ),
    (
        "GET",
        re.compile(API + rf"website-tools/{_S}/accounts/{_S}/code"),
        "A website account's 2FA code is shown only to a person, in Tools",
    ),
]
DETAIL_PERSON_ONLY = ": its value never goes to a bot, so BotOps does not do this for you."

# Read as the person, except what hands back a secret or is a computer's own channel.
NO_READ = re.compile(
    API
    + r"(credential-runtime|runner-credential-(migration|grants)|me/tokens.*|mcp|agents/setup-script|jobs.*|attempts.*"
    r"|runner-logins.*|runner-(model|gateway)-credentials|runners/desired|runners/assignments|runners/eligible|runners/me/events|directory/scim-token"
    r"|runner-browser-commands.*|website-tools/[^/]+/accounts/[^/]+/(browser/frame|code))"
)

# Named like a secret: refused whatever route it is on.
SECRET_KEYS = re.compile(
    r"^(secret|password|passwd|passphrase|token|access_token|refresh_token|api_key|apikey|"
    r"private_key|client_secret|authorization|bearer|bot_token|app_token|totp)$",
    re.I,
)
# Names that are a secret on one route only: elsewhere `key` names a setting, a field or an idempotency key.
ROUTE_SECRETS = [(("PUT",), re.compile(API + r"providers/gateway"), ("key",))]

DETAIL_SECRET = (
    "A secret does not go in a request. Ask the person for it with a credential card "
    "(`tico credential request`): the value goes straight to Credentials and never through you."
)
DETAIL_ROUTE = (
    "BotOps does not do this for a person. If the app can do it and this list should, say so with `tico support file`."
)


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
    if method == "PUT" and path == API + "system/suggestions":
        # Outcome reporting follows the owner's words; sharing counts needs the owner's own consent in Settings.
        return "do" if isinstance(body, dict) and set(body) == {"report_suggestion_outcomes"} else None
    if any(m == method and p.fullmatch(path) for m, p in CONFIRM) or enables_audience(method, path, body):
        return "confirm"
    if method == "POST" and MESSAGES.fullmatch(path):
        return "do" if to_bot and isinstance(body, dict) and to_bot(body.get("to")) else "confirm"
    if rules and not rules.get("botops_direct", True) and any(m == method and p.fullmatch(path) for m, p in TIGHTENED):
        return "confirm"
    if any(m == method and p.fullmatch(path) for m, p in DO + THREADS):
        return "do"
    return None


AUDIENCE = re.compile(API + rf"audiences/{_S}")


def enables_audience(method, path, body=None):
    """Turning an audience on (Public, or a reader's audience) serves what its approved content says, so it is the
    owner's own tap. Turning one off is a way out and stays direct."""
    path, method = normalize(path) or "", str(method or "").upper()
    return (
        method == "PATCH"
        and bool(AUDIENCE.fullmatch(path))
        and isinstance(body, dict)
        and "enabled" in body
        and bool(body["enabled"])
    )


def person_only(method, path):
    """Why BotOps acting for a person may not make this call (the route hands a secret's value back), or ""."""
    path, method = normalize(path), str(method or "").upper()
    return next(
        (why + DETAIL_PERSON_ONLY for m, p, why in PERSON_ONLY if path and m == method and p.fullmatch(path)), ""
    )


def owner_only(method, path, body=None):
    """A card only the owner may ask for: handing over the company, and the team rules that set BotOps' own powers."""
    path, method = normalize(path), str(method or "").upper()
    if not path:
        return False
    return any(m == method and p.fullmatch(path) for m, p in OWNER_ONLY) or (
        method == "PUT" and bool(RULES.fullmatch(path)) and own_powers(body)
    )


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
    if match(ALWAYS) or match(CONFIRM) or enables_audience(method, path, body):
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
    (
        "POST",
        re.compile(API + r"enrollments"),
        "Make a one-time code to add a computer: it shows here once you confirm",
    ),
    ("PUT", RULES, "Change the team's rules"),
    ("PUT", re.compile(API + r"providers"), "Change the team's AI providers"),
    ("PUT", re.compile(API + r"providers/gateway"), "Change the Tico AI Gateway address"),
    ("DELETE", re.compile(API + r"providers/gateway"), "Turn off the Tico AI Gateway (its saved key is forgotten)"),
    ("POST", re.compile(API + r"providers/gateway/automatic"), "Connect the Tico AI Gateway automatically again"),
    ("PUT", re.compile(API + rf"usage/(limits(/{_S})?|pools/.+)"), "Change a spending limit"),
    ("PATCH", AUDIENCE, "Turn on a Company page audience"),
    ("DELETE", re.compile(API + rf"automations/{_S}"), "Delete an automation"),
    ("POST", re.compile(API + rf"automations/{_S}/delete"), "Delete an automation"),
    ("POST", re.compile(API + r"support/tickets"), "Report a bug to Tico support"),
]


def card_why(c, auth, person, attempt_id, method, path, body=None):
    """The line after a BotOps card's title, from server facts only (never BotOps' words): the work its run was on, by
    the task and who asked for it, and why the change waits for this person. A task the person may not read is named
    only by who asked for it; a run on no task was asked in the person's chat."""
    row = (
        c.execute("SELECT task_id FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        if attempt_id and H._has_table(c, "attempts")
        else None
    )
    task = H.task(c, row["task_id"]) if row and row["task_id"] else None
    if not task:
        work = "BotOps asked in your chat with it."
    else:
        asker = task.get("requester_id") or ""
        by = "you" if asker == person.actor else H.actor_name(c, asker) if asker else ""
        try:
            auth.task(c, person, task["id"])
            work = f"BotOps asked while working on “{task['title']}”" + (f", which {by} asked for." if by else ".")
        except Problem:
            work = f"BotOps asked while working for {by}." if by else "BotOps asked while working on a task."
    if automation_deleted(method, path):
        need = "Deleting an automation always needs your own OK."
    else:
        need = "This change needs your own OK, so nothing has changed yet."
    return work + " " + need


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
    ("POST", r"tasks"),
    ("POST", r"tasks/dry-run"),
    ("POST", rf"tasks/{_S}"),
    ("POST", rf"tasks/{_S}/(comments|links|ask)"),
    ("POST", r"docs"),
    ("PATCH", rf"docs/{_S}"),
    ("POST", r"linked-docs"),
    ("PATCH", rf"linked-docs/{_S}"),
    ("POST", r"goals"),
    ("POST", rf"goals/{_S}"),
    ("POST", rf"goals/{_S}/(status|checkins|kpis)"),
    ("POST", rf"goals/{_S}/kpis/{_S}"),
    ("POST", r"kpis"),
    ("POST", rf"kpis/{_S}"),
    ("POST", rf"kpis/{_S}/readings"),
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
    return bool(names) and (not bare or bare.lower() in SCHEMES) and (env is None or all(name == env for name in names))


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
    if (
        not path
        or not isinstance(mcp, dict)
        or not isinstance(mcp.get("headers"), dict)
        or not any(m == str(method).upper() and p.fullmatch(path) for m, p in TOOL_ROUTES)
    ):
        return secret_in(body)
    found = secret_in({**body, "mcp": {k: v for k, v in mcp.items() if k != "headers"}})
    if found:
        return found
    env = body.get("env") if "env" in body else None
    for name, value in mcp["headers"].items():
        if SECRET_KEYS.match(str(name).strip()):  # clean_mcp strips the name it stores
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
# Taking back its own Confirm card, and listing them (backend/assistant.py): BotOps' own cards, never run for a person.
OWN = re.compile(
    API + r"(credential-runtime|runner-credential-.*|runner-watcher-credentials|attempts.*|jobs.*|runners/.*|"
    r"messages(/.*)?|chat/.*|conversations/.*|bots/" + "botops" + r"(/.*)?|files/(uploads|imports)|uploads/.*|me(/.*)?|"
    r"assistant/proposals|assistant/actions/" + _S + r"/withdraw)"
)


def default_delegable(method, path, body=None, query=None):
    """Whether a BotOps call with no explicit `on_behalf_of` acts as the person who asked: only the routes BotOps
    may do or propose for a person, and never its own plumbing or its own messages."""
    path = normalize(path)
    if (
        path
        and str(method).upper() in ("GET", "POST")
        and re.fullmatch(API + rf"computers/{_S}/logins(?:/{_S}(?:/cancel)?)?", path)
    ):
        return classify(method, path, body) == "do"
    if path and any(m == str(method).upper() and p.fullmatch(path) for m, p in THREADS):
        return classify(method, path, body) == "do"
    if not path or OWN.fullmatch(path):
        return False
    mine = str((query or {}).get("mine", "")).lower() in ("1", "true", "yes", "on")
    if path == API + "rules" and str(method).upper() == "GET" and mine:
        return False  # BotOps's own rules and settings (`tico rules list --mine`): its own rights, not its requester's
    # The person's own card or plain refusal (`card_for_person`, `person_only`) reaches a client that sends no
    # header too, not BotOps' own 403.
    if any(m == str(method).upper() and p.fullmatch(path) for m, p in ALWAYS) or person_only(method, path):
        return True
    return classify(method, path, body) in ("do", "confirm")


# What a person clicks when BotOps is refused: one plain sentence and a link to the exact place in the app. Whose
# rights were used decides the sentence: BotOps' own (no person asked in this run), a requesting bot's, or the person's.
def _place(path):
    rest = (normalize(path) or "")[len(API) :]
    parts = rest.split("/")
    if parts[0] == "bots" and len(parts) > 1 and parts[1] not in ("register", ""):
        slug = parts[1]
        what = "its repository access" if parts[2:3] == ["repositories"] else "its settings"
        return "#/bot/" + slug + "/more", slug, what
    if parts[0] == "repositories":
        return "#/settings/general/set-repos", "", "Settings > General › Repositories"
    if parts[0] == "tasks" and len(parts) > 1 and parts[1]:
        return "#/task/" + parts[1], "", "the task"
    if parts[0] == "credentials":
        return "#/tools/keys?show=credentials", "", "Tools > Keys"
    if parts[0] == "computers":
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
    return (
        "A person who manages "
        + bot
        + " turns its mail sending on in Settings > Bots > "
        + bot
        + " > Mail sending, or with `tico bot mail "
        + (slug or bot)
        + " --send`"
    )


ROLE_FIX = "Your role can't change this: "  # `fix` for a person's role refused (app.py words a setting's who may)


def fix(path, code, status, acting, names, base=""):
    """`{"fix", "link"}` for a refusal of a BotOps call, or None. `acting` is the identity the route checked;
    `names` maps actor ids to display names."""
    link, slug, what = _place(path)
    bot = names.get("bot:" + slug) or slug.replace("-", " ").title()
    where = (bot + "'s " + what[4:]) if slug and what.startswith("its ") else what
    if code == "runner_not_ready":
        text = (
            "Sign in the bot's AI tool on its computer in Settings > Computers, or choose a model that computer "
            "already runs"
        )
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
        text = (
            "The task's bot needs write access to that repository: an owner or admin gives it in Settings > "
            "General › Repositories; then BotOps can add the worktree itself"
        )
        link = "#/settings/general/set-repos"
    elif code == "on_behalf_of":
        # The cited request lends nothing (closed, too old, another person's, words others wrote): the detail says why.
        text = (
            "BotOps can't act on that earlier request: ask it again in your BotOps chat so it acts with your "
            "rights now, or change " + where + " yourself"
        )
    elif acting.actor == "bot:botops":
        text = (
            "No one's request was attached to this run, so BotOps used its own rights: say go ahead in your "
            "BotOps chat, or change " + where + " yourself"
        )
    elif acting.role == "bot":
        asker = names.get(acting.actor) or acting.actor.split(":", 1)[-1]
        text = (
            asker + " asked for this without citing who wanted it, so BotOps had only " + asker + "'s rights: say "
            "go ahead in your BotOps chat, or change " + where + " yourself"
        )
    else:
        text = ROLE_FIX + "ask an owner or admin to change " + where
    return {"fix": text, "link": (base.rstrip("/") + "/" + link) if base else link}


def configure(app, assistant_valid, auth, create_proposal, settings, store):
    # tools_delegation builds its route table from this module's _routes, so it is imported once both exist.
    from . import tools_delegation

    # A person's request lends their rights for as long as the task it created (or that cites it) is open, up to
    # botops_act.LEND_MAX_DAYS after they last spoke on it, and never again once someone else reopens it. A chat
    # message with no open task lends them for a week; a new task filed from a message also needs it within the week,
    # or one open task could renew another for ever.
    DELEGATION_DAYS = 7
    # Wakes a follow-through run may start from: the daily update, and a task's due, stall or routine notice.
    FOLLOW_THROUGH_WAKES = ("update", "due", "stalled", "occurrence")

    def open_task(task):
        return bool(task) and task["status"] not in ("done", "closed")

    def lending_task(c, task):
        """An open task that still lends its requester's rights (`botops_act.lend_lapsed`)."""
        return open_task(task) and not lend_lapsed(c, task)

    def week_old(created):
        return created < H.shift(H.now(), days=-DELEGATION_DAYS)

    def handed_over(c, task, botops):
        """Who gave BotOps a task a person asked for, when it was not that person: the actor of the last owner change, or
        "" when there is none to name. None when BotOps has owned it since it was filed, or the requester moved it to
        BotOps themselves (not through BotOps or the Assistant). Otherwise any bot that owns a person's task could
        reassign it to BotOps and borrow their rights for its next run."""
        moved = c.execute(
            "SELECT new, actor_id AS actor, via FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='reassign' "
            "ORDER BY created_at DESC, rowid DESC "
            "LIMIT 1",
            (task["id"],),
        ).fetchone()
        if not moved:
            made = c.execute(
                "SELECT detail FROM events WHERE action='task.create' AND target=? ORDER BY created_at,id LIMIT 1",
                (task["id"],),
            ).fetchone()
            return None if made and H._json(made["detail"], {}).get("owner") == botops else ""
        moved = dict(moved)
        if moved["new"] == botops and moved["actor"] == task["requester_id"] and not moved.get("via"):
            return None
        return moved["actor"] or ""

    def delegated_identity(c, who, ref, new_task=False, carry=False):
        """Resolve the run's requester, never a human named in its text. Human messages and tasks
        carry that human's rights; bot messages and tasks carry only that bot's rights. Fleet work
        with no requester keeps BotOps' own. Explicit message references must belong to this requester.
        `new_task`: the reference is the request a task is being filed from. `carry`: the call is one a bot's hand-off
        (carried_request) makes with the person's rights (botops_act.carried_lends); otherwise it keeps the bot's.
        """
        if who.via in tools_delegation.DELEGATE_VIAS and who.role == "bot" and not who.attempt_id:
            if ref in ("turn", "default"):
                return who
            raise Problem("on_behalf_of", "A bot request cannot borrow a human's rights", 403)
        if who.via in tools_delegation.DELEGATE_VIAS and who.attempt_id:
            who = Identity(
                who.task_actor or "bot:" + BOTOPS,
                "bot",
                runner_id=who.runner_id,
                attempt_id=who.attempt_id,
                agent=who.agent,
            )
        # BotOps, or the Tools page's owner on a Tools change (act_for_requester admits it only there).
        agent = who.actor
        if agent != "bot:" + BOTOPS and not tools_delegation.is_tools_owner(c, agent):
            raise Problem("forbidden", "Only BotOps and the Tools Owner apply changes on a requester's behalf", 403)
        via = tools_delegation.via_of(agent)
        named = tools_delegation.name_of(c, agent)

        def acting_as(principal, cited="", run="live"):
            # Keep the actual bot's credential so writes revalidate its lease under the lock,
            # and private reads intersect the requester with the bot doing the work.
            # Never confirmed: only the person's own tap on a Confirm card is (auth.py, x-tico-assistant-action), so
            # every route's "always a click" rule (`risky`) asks them.
            if H.is_human(principal.actor):
                lent = {"for": principal.actor, "cited": cited, "run": run}
                if via != "botops":
                    lent["by"] = agent  # the Tools Owner names itself on what it does for the person
                H.DELEGATION.set(lent)  # pyright: ignore[reportArgumentType] - typed by its None default
            return replace(
                principal,
                via=via,
                confirmed=False,
                task_actor=who.actor,
                runner_id=who.runner_id,
                attempt_id=who.attempt_id,
                agent=who.agent,
            )

        message_id = ref
        explicit = bool(ref) and ref not in ("turn", "default")
        turn = (
            c.execute(
                "SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (who.attempt_id,)
            ).fetchone()
            if who.attempt_id
            else None
        )
        if explicit and not turn:
            raise Problem("on_behalf_of", "Cite the request that started this run", 403)
        if explicit:
            cited = followed_task(c, ref)
            if cited:
                return acting_as(follow_through(c, who, cited), cited["id"], "follow_through")
        if not explicit:
            if not turn:
                return who
            message_id = turn["message_id"]
            initial = H.message(c, message_id)
            task_id = H.message_task_id(initial) if initial else None
            task = H.task(c, task_id) if task_id else None
            requester = task["requester_id"] if task else (initial or {}).get("sender_id", "")
            if task and H.is_human(requester):
                creator = c.execute(
                    "SELECT actor FROM events WHERE action='task.create' AND target=? ORDER BY created_at,id LIMIT 1",
                    (task_id,),
                ).fetchone()
                if creator and H.is_bot(creator["actor"]):
                    requester = creator["actor"]
                requester = filed_for_bot(c, task_id, who) or requester
            handed = (
                handed_over(c, task, who.actor)
                if task and H.is_human(requester) and task["owner_id"] == who.actor
                else None
            )
            if handed is not None:
                # Someone other than the person gave BotOps their task: it runs with the rights of the bot that moved
                # it (its refusals become the person's Confirm card), never the person's.
                if not H.is_bot(handed) or handed == who.actor:
                    raise Problem(
                        "on_behalf_of",
                        f"Someone other than the person who asked for this task gave it to {named}, "
                        f"so it lends no one's rights; they can move it to {named} themselves or ask in their "
                        f"{named} chat",
                        403,
                    )
                requester = handed
            others_comment = False
            if task and task_id and initial:
                refs = initial.get("refs") or {}
                relayed = any(refs.get(key) for key in ("via", "assistant", "slack", "routing"))
                if (
                    refs.get("comment")
                    and not relayed
                    and H.is_human(requester)
                    and initial["sender_id"] == requester
                    and task["owner_id"] == who.actor
                    and open_task(task)
                ):
                    # The task's requester commenting on it, in their own words: that is them asking.
                    auth.conversation(c, who, initial["conversation_id"])
                    H.VIA.set(via)
                    return acting_as(auth.identity_for_actor(c, requester), task_id, "comment")
                if (
                    refs.get("comment")
                    and not relayed
                    and handed is not None
                    and initial["sender_id"] == task["requester_id"]
                ):
                    raise Problem(
                        "on_behalf_of",
                        f"A bot gave this task to {named}, so your comment doesn't lend your rights; "
                        f"move it to {named} yourself or ask in your {named} chat",
                        403,
                    )
                if (
                    refs.get("comment")
                    and not relayed
                    and H.is_bot(requester)
                    and H.is_human(initial["sender_id"])
                    and task["owner_id"] == who.actor
                    and open_task(task)
                ):
                    # A person's comment on a task a bot asked BotOps for lends nothing: BotOps keeps that bot's rights,
                    # and what they can't do becomes a Confirm card for the person who commented (confirm_for_carried).
                    others_comment = True
                elif refs.get("comment") or relayed:
                    raise Problem(
                        "on_behalf_of",
                        "Only the task's requester's own comment lends their rights; "
                        "other comments and routed messages lend nothing",
                        403,
                    )
            if initial and (task or str(requester).startswith("bot:")) and requester != H.TICO_ACTOR:
                auth.conversation(c, who, initial["conversation_id"])
                lapsed = lend_lapsed(c, task) if task and H.is_human(requester) else ""
                if lapsed:
                    raise Problem("on_behalf_of", lapsed, 403)
                if not open_task(task) and week_old(initial["created_at"]):
                    raise Problem("on_behalf_of", "That request is more than a week old; ask the requester again", 403)
            if str(requester).startswith("bot:") and requester != who.actor:
                if (H.bot(c, H.actor_id(requester)) or {}).get("state") != "active":
                    raise Problem("forbidden", "The requesting bot is no longer active", 403)
                person = lending_carried(c, task, requester) if carry else None
                if person and others_comment and initial and person["person"] != initial["sender_id"]:
                    person = None  # someone else's comment never lends the carried person's rights
                if person:
                    # The bot handed on a person's own request (carried_request): BotOps acts as that person.
                    H.VIA.set(via)
                    return acting_as(auth.identity_for_actor(c, person["person"]), person["message_id"], "carried")
                return acting_as(Identity(requester, "bot"))
            if not initial or requester == H.TICO_ACTOR or requester == who.actor:
                return who
            if task and task_id and task["owner_id"] == who.actor and not task.get("request_id"):
                if str(task["requester_id"]).startswith("human:"):
                    made = c.execute(
                        "SELECT detail FROM events WHERE action='task.create' AND target=? AND actor=? LIMIT 1",
                        (task_id, task["requester_id"]),
                    ).fetchone()
                    if made and H._json(made["detail"], {}).get("via") != "assistant":
                        person = auth.identity_for_actor(c, task["requester_id"])
                        return acting_as(person, task_id)
                origin = c.execute(
                    "SELECT actor FROM events WHERE action='botops.task_requested' AND target=? AND actor=? LIMIT 1",
                    (task_id, task["requester_id"]),
                ).fetchone()
                if origin and str(origin["actor"]).startswith("human:"):
                    auth.conversation(c, who, task["conversation_id"])
                    person = auth.identity_for_actor(c, origin["actor"])
                    H.VIA.set(via)
                    return acting_as(person, task_id)
            asked = (
                c.execute(
                    "SELECT detail FROM events WHERE action='botops.asked_via_assistant' AND target=? "
                    "AND actor=? LIMIT 1",
                    (task_id, task["requester_id"]),
                ).fetchone()
                if task and via == "botops"
                else c.execute(
                    "SELECT detail FROM events WHERE action='assistant.asked_helper' AND target=? AND actor=? "
                    "AND json_extract(detail,'$.helper')=? LIMIT 1",
                    (task_id, task["requester_id"], agent),
                ).fetchone()
                if task
                else None
            )
            if (
                asked
                and task
                and task["owner_id"] == who.actor
                and H.is_human(task["requester_id"])
                and open_task(task)
            ):
                # The person's own words to their Assistant, checked when it filed this (task_create): BotOps acts
                # as them, as if they had asked it in their BotOps chat.
                origin = H.message(c, (H._json(asked["detail"], {}) or {}).get("cited") or "")
                if (
                    not origin
                    or origin.get("deleted_at")
                    or origin["sender_id"] != task["requester_id"]
                    or week_old(origin["created_at"])
                ):
                    raise Problem(
                        "on_behalf_of",
                        "The request the Assistant handed on no longer stands; ask the person again",
                        403,
                    )
                H.VIA.set(via)
                return acting_as(auth.identity_for_actor(c, task["requester_id"]), origin["id"], "assistant")
            if task and task.get("request_id") in ("turn", "default"):
                # Tasks filed before the request was stored by id: the person's message the filing cited.
                made = c.execute(
                    "SELECT detail FROM events WHERE action='task.create' AND target=? ORDER BY created_at,id LIMIT 1",
                    (task_id,),
                ).fetchone()
                task = {
                    **task,
                    "request_id": ((H._json(made["detail"], {}) if made else {}).get("delegation") or {}).get("cited"),
                }
            if task and task.get("request_id") and task["owner_id"] == who.actor:
                origin = H.message(c, task["request_id"])
                made = c.execute(
                    "SELECT 1 FROM events WHERE action='task.create' AND target=? AND actor=? LIMIT 1",
                    (task_id, task["requester_id"]),
                ).fetchone()
                if made and origin and origin["sender_id"] == task["requester_id"]:
                    message_id = task["request_id"]
                    c.execute(
                        "INSERT OR IGNORE INTO attempt_conversations VALUES(?,?)",
                        (who.attempt_id, origin["conversation_id"]),
                    )
        msg = H.message(c, message_id)
        started = (H.message(c, turn["message_id"]) or {}).get("sender_id", "") if turn else ""
        if explicit and started and msg and msg["sender_id"] != started:
            # A turn a person started acts for that person: another person's message id borrows nothing.
            raise Problem("on_behalf_of", "Cite the message of the person who asked you, not someone else's", 403)
        if (
            not msg
            or not str(msg["sender_id"]).startswith("human:")
            or msg["recipient_id"] != agent
            or msg["kind"] not in ("say", "answer")
        ):
            raise Problem("on_behalf_of", f"Cite a message a person sent {named} asking for this change", 403)
        refs = msg.get("refs") or {}
        if refs.get("via") or refs.get("assistant"):
            # The Assistant wrote this for the person: its writes never carry the person's authority on.
            raise Problem("on_behalf_of", "That message was written by the Assistant; ask the person to send it", 403)
        conversation = H.conversation(c, msg["conversation_id"]) or {}
        if conversation.get("task_id") or conversation.get("scope") == "task" or refs.get("task"):
            raise Problem(
                "on_behalf_of", f"A person's words inside a task are not a request to {named}; ask in chat", 403
            )
        slack = slack_raw if isinstance(slack_raw := refs.get("slack"), dict) else {}
        own_dm = (
            slack.get("kind") == "im"
            and not slack.get("recorded_only")
            and set(conversation.get("participants") or []) == {msg["sender_id"], agent}
        )
        if (refs.get("slack") or refs.get("routing")) and not own_dm:
            # Anyone in a Slack channel or thread can put words in a routed message, so those never lend a person's
            # rights. A 1:1 Slack DM is different: the gateway verified the sender (workspace member, not a guest,
            # admitted, a Tico person) and nobody else writes in it, so it counts like their Tico chat (owner
            # decision, 2026-10-01). The message decides, not the room: what they type in Tico is always their own request.
            raise Problem(
                "on_behalf_of",
                f"This request arrived through a Slack channel, so {named} can't act on it for "
                f"the person. They can DM it to the Tico app in Slack or send it in their Tico chat with {named}",
                403,
            )
        if explicit and set(conversation.get("participants") or []) != {msg["sender_id"], agent}:
            # A message cited by id: the person's own, in their own room with BotOps (not a room another person spoke in).
            raise Problem("on_behalf_of", f"Cite a message from the person's own chat with {named}", 403)
        if week_old(msg["created_at"]) and (
            new_task
            or not any(
                lending_task(c, H.task(c, row["id"]))
                for row in c.execute(
                    "SELECT id FROM tasks WHERE request_id=? AND requester_id=?", (message_id, msg["sender_id"])
                ).fetchall()
            )
        ):
            raise Problem(
                "on_behalf_of",
                "That request is more than a week old and no open task carries it; ask the person again",
                403,
            )
        # Only a request in a conversation this run may read: otherwise any message a bot sends
        # BotOps could borrow the rights of whoever last asked it for something.
        try:
            auth.conversation(c, who, msg["conversation_id"])
        except Problem:
            raise Problem("on_behalf_of", "Cite a request from the conversation you are working on", 403) from None
        person = auth.identity_for_actor(c, msg["sender_id"])
        H.VIA.set(via)  # every event and history row from here says "via BotOps"
        return acting_as(person, message_id)

    def carried_request(c, who, ref):
        """The person whose request a bot is handing BotOps, recorded on the task it files (`botops.request_carried`):
        `{"person", "message_id", "lends", "why"}`, or None when no person is behind it.

        The bot cites the person's message (`--request-id`: its id, or `turn` for the message that started this run).
        It lends that person's rights to BotOps when they wrote it themselves to this bot, in their 1:1 chat with it
        (the app or a Slack DM) or as a comment on a task they asked this bot for, in the conversation this run is
        working on, within DELEGATION_DAYS. Anything else (words in a channel, written by the Assistant, from another
        conversation, too old) still names the person, and BotOps puts what it would do to them as a Confirm card
        (`confirm_for_carried`). With no citation, a run a person's own message started names them the same way."""
        turn = (
            c.execute(
                "SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=? AND a.bot_id=?",
                (who.attempt_id, H.actor_id(who.actor)),
            ).fetchone()
            if who.attempt_id
            else None
        )
        start = H.message(c, turn["message_id"]) if turn else None
        if not ref:
            refs = (start or {}).get("refs") or {}
            slack = slack_raw if isinstance(slack_raw := refs.get("slack"), dict) else {}
            if (
                start
                and H.is_human(start["sender_id"])
                and start["recipient_id"] == who.actor
                and not any(refs.get(k) for k in ("via", "assistant", "routing"))
                and (not refs.get("slack") or slack.get("kind") == "im")
            ):
                return {
                    "person": start["sender_id"],
                    "message_id": start["id"],
                    "lends": False,
                    "why": "No message was cited",
                }
            return None
        msg = start if ref == "turn" else H.message(c, ref)
        if not msg or not H.is_human(msg["sender_id"]) or msg.get("deleted_at"):
            raise Problem(
                "request_id",
                "Cite a message a person wrote to you (its id, or `turn` for the one that started this run)",
                422,
            )
        try:
            auth.conversation(c, who, msg["conversation_id"])
        except Problem:
            raise Problem("request_id", "Cite a message from a conversation you are working on", 403) from None
        person = msg["sender_id"]
        refs = msg.get("refs") or {}
        conversation = H.conversation(c, msg["conversation_id"]) or {}
        task = H.task(c, H.message_task_id(msg, conversation) or "")
        slack = slack_raw if isinstance(slack_raw := refs.get("slack"), dict) else {}
        own_dm = slack.get("kind") == "im" and not slack.get("recorded_only")
        if msg["recipient_id"] != who.actor or msg["kind"] not in ("say", "answer"):
            why = "That message was not sent to you"
        elif refs.get("via") or refs.get("assistant"):
            why = "The Assistant wrote that message"
        elif refs.get("routing") or (refs.get("slack") and not own_dm):
            why = "That message came from a channel others write in"
        elif not start or start["conversation_id"] != msg["conversation_id"]:
            why = "That message is not in the conversation this run is working on"
        elif task and not (
            refs.get("comment") and task["requester_id"] == person and task["owner_id"] == who.actor and open_task(task)
        ):
            why = "Only the person's own comment on a task they asked you for carries their request"
        elif not task and set(conversation.get("participants") or []) != {person, who.actor}:
            why = "That message is not from the person's own chat with you"
        elif week_old(msg["created_at"]):
            why = "That message is more than a week old"
        else:
            why = ""
        return {"person": person, "message_id": msg["id"], "lends": not why, "why": why}

    def carried(c, task, requester):
        """What `carried_request` recorded on this task, filed by `requester`, or None."""
        row = (
            c.execute(
                "SELECT detail FROM events WHERE action='botops.request_carried' AND target=? AND actor=? "
                "ORDER BY created_at DESC LIMIT 1",
                (task["id"], requester),
            ).fetchone()
            if task
            else None
        )
        return H._json(row["detail"], {}) if row else None

    def filed_for_bot(c, task_id, who):
        """The bot a task was filed for, when BotOps filed it with rights that bot's hand-off lent (carried_request):
        it lends what that bot's rights do, never the person's, or a carried call could file itself a task that lends
        everything. None for any other task."""
        made = c.execute(
            "SELECT detail FROM events WHERE action='task.create' AND target=? ORDER BY created_at,id LIMIT 1",
            (task_id,),
        ).fetchone()
        lent = (H._json(made["detail"], {}) if made else {}).get("delegation") or {}
        if not isinstance(lent, dict) or lent.get("run") != "carried":
            return None
        cited = H.message(c, lent.get("cited") or "")
        return cited["recipient_id"] if cited and H.is_bot(cited["recipient_id"]) else who.actor

    def lending_carried(c, task, requester):
        """The carried request when it still lends the person's rights: the task is open and the message still stands,
        from that person, within DELEGATION_DAYS."""
        found = carried(c, task, requester)
        if not found or not found.get("lends") or not open_task(task):
            return None
        if reopened_by(c, task["id"]):
            return None  # finished once: the request it carried was done, whoever reopened it
        msg = H.message(c, found.get("message_id") or "")
        if not msg or msg.get("deleted_at") or msg["sender_id"] != found.get("person") or week_old(msg["created_at"]):
            return None
        return found

    def confirm_for_carried(request, exc):
        """A Confirm card for the person behind a bot's hand-off when BotOps, with only that bot's rights, was refused:
        what BotOps tried, run as the person on their own click. None when there is no such person or no such card."""
        who, acting = getattr(request.state, "privacy_source", None), getattr(request.state, "identity", None)
        if (
            exc.status != 403
            or exc.code != "forbidden"
            or not who
            or who.actor != "bot:" + BOTOPS
            or not acting
            or acting.role != "bot"
            or acting.actor == who.actor
            or request.method == "GET"
            or not who.attempt_id
        ):
            return None
        path = normalize(request.url.path)
        body = parse_body(getattr(request, "_body", b"")) if request.method != "DELETE" else None
        body = body if isinstance(body, dict) else {}
        if (
            not path
            or secret_in_request(request.method, path, body)
            or not assistant_valid(request.method, path, settings, BOTOPS)
        ):
            return None
        with store.transaction() as c:
            turn = c.execute(
                "SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (who.attempt_id,)
            ).fetchone()
            start = H.message(c, turn["message_id"]) if turn else None
            task = H.task(c, H.message_task_id(start) or "") if start else None
            said = (start or {}).get("refs") or {}
            commented = (
                task
                and task["requester_id"] == acting.actor
                and task["owner_id"] == who.actor
                and said.get("comment")
                and start
                and H.is_human(start["sender_id"])
                and not any(said.get(k) for k in ("via", "assistant", "slack", "routing"))
            )
            # The person whose comment started this run asked, ahead of whoever the bot's hand-off named.
            found = (
                {"person": start["sender_id"]}
                if commented and start
                else carried(c, task, acting.actor)
                if task and task["requester_id"] == acting.actor
                else None
            )
            if (
                not found
                and task
                and H.is_human(task["requester_id"])
                and task["owner_id"] == who.actor
                and handed_over(c, task, who.actor) == acting.actor
            ):
                found = {"person": task["requester_id"]}  # the bot gave BotOps a task this person asked for
            if not found or not task or not open_task(task):
                return None
            try:
                person = auth.identity_for_actor(c, found["person"])
            except Problem:
                return None
            if admin_only(request.method, path) and person.role != "owner" and not auth.bot_admin(person):
                return None
            asker = (H.bot(c, H.actor_id(acting.actor)) or {}).get("name") or H.actor_id(acting.actor)
            summary = (
                ("BotOps needs your OK to do this on " + asker + "'s task: ")
                if commented
                else (asker + " asked BotOps for this for you: ")
            ) + task["title"]
            return propose_card(
                c, person, request.method, path, {k: v for k, v in body.items() if k != "on_behalf_of"}, summary[:300]
            )

    def followed_task(c, ref):
        """The task `ref` names (its id, or an unambiguous prefix of eight or more), or None when it names none."""
        ref = str(ref or "")
        prefix = ref.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = c.execute(
            "SELECT id FROM tasks WHERE id=? OR (length(?)>=8 AND id LIKE ? || '%' ESCAPE '\\') LIMIT 2",
            (ref, ref, prefix),
        ).fetchall()
        exact = [r for r in rows if r["id"] == ref]
        return H.task(c, (exact or rows)[0]["id"]) if len(exact or rows) == 1 else None

    def follow_through(c, who, task):
        """The person whose open request BotOps is following through on, in a later run: a daily-update wake, a retry
        once a busy bot finished, a notice. The run that started it is gone, so BotOps cites the task the person asked
        for. Only a task BotOps owns, that the person filed themselves (not through the Assistant), for as long as it is
        open; and never from a run a bot or another person started, which keeps its own requester's
        rights."""
        requester = task["requester_id"]
        if task["owner_id"] != who.actor or not H.is_human(requester):
            raise Problem("on_behalf_of", "Cite a task a person asked BotOps for", 403)
        if task["status"] in ("done", "closed"):
            raise Problem("on_behalf_of", "That task is closed; ask the person again", 403)
        lapsed = lend_lapsed(c, task)
        if lapsed:
            raise Problem("on_behalf_of", lapsed, 403)
        made = c.execute(
            "SELECT actor,detail FROM events WHERE action='task.create' AND target=? ORDER BY created_at,id LIMIT 1",
            (task["id"],),
        ).fetchone()
        via = H._json(made["detail"], {}).get("via") if made else None
        if not made or made["actor"] != requester or via == "assistant" or filed_for_bot(c, task["id"], who):
            raise Problem("on_behalf_of", "That task was not filed by the person who asked for it", 403)
        if handed_over(c, task, who.actor) is not None:
            raise Problem(
                "on_behalf_of", "Someone other than the person who asked for that task gave it to BotOps", 403
            )
        if via and not task.get("request_id"):
            # Filed by BotOps as the person: only a continuation of their message (--request-id) counts, or a
            # follow-through run could file a fresh task as them and so lend their rights for ever.
            raise Problem(
                "on_behalf_of", "BotOps filed that task without the person's message; ask the person again", 403
            )
        origin = H.message(c, task["request_id"]) if task.get("request_id") else None
        if task.get("request_id") and (not origin or origin["sender_id"] != requester):
            raise Problem("on_behalf_of", "That task's request is not the person's own", 403)
        current = delegated_identity(c, who, "turn")
        if current.actor not in (who.actor, requester):
            raise Problem(
                "on_behalf_of", "This run is for " + current.actor + "; it keeps that requester's rights", 403
            )
        if current.actor == who.actor:
            # A run with no requester lends a person's rights only when the system started it on its own schedule. A
            # Slack digest, a live meeting, a watcher or a task comment also start such runs, with words anyone there
            # wrote; those never carry a person's rights (Slack and comments are refused the same way above).
            turn = c.execute(
                "SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (who.attempt_id,)
            ).fetchone()
            start = H.message(c, turn["message_id"]) if turn else None
            refs = (start or {}).get("refs") or {}
            if (
                not start
                or start["sender_id"] != H.TICO_ACTOR
                or start["kind"] != "notice"
                or refs.get("wake") not in FOLLOW_THROUGH_WAKES
                or any(refs.get(k) for k in ("slack", "routing", "via", "assistant", "comment", "live_meeting"))
            ):
                raise Problem(
                    "on_behalf_of",
                    "This run started from words others wrote; act on the person's task in "
                    "their chat or the next daily update",
                    403,
                )
        H.VIA.set("botops")
        return auth.identity_for_actor(c, requester)

    def botops_owns_task(path, body=None):
        """A note, comment or status on a task BotOps owns is BotOps' own work: it needs no one's rights and is BotOps'
        words, so it is never recorded as the person who asked (their rights still apply to everyone else's tasks).
        Closing is the exception: only the requester or a human closes, so a close (a move) keeps the person's rights.
        Its own task's worktrees are its own work too: the route checks the owning bot's repository access, which no
        one's rights change. So is an ask or a sub-task for a person under its own task: the ask grants nothing by
        itself, whoever asked for the task."""
        body = body if isinstance(body, dict) else {}
        person = None
        if path in ("/api/v2/tasks", "/api/v2/tasks/dry-run"):
            person = body.get("owner")
            ref = body.get("parent")
            # Only a plain ask: no roles, goals, links or relations reached with BotOps' rights for whoever asked.
            if (
                not ref
                or not person
                or set(body) - {"title", "message", "body", "owner", "parent", "due", "private", "on_behalf_of"}
            ):
                return False
        else:
            found = re.fullmatch(r"/api/v2/tasks/([^/]+)(?:/(?:comments|asks|links|worktrees(?:/attach)?))?", path)
            if not found:
                return False
            ref = found.group(1)
        with store.read() as c:
            row = followed_task(c, ref)
            if person is not None and not H.is_human(H.resolve_actor(c, person) or ""):
                return False
        return bool(row and row["owner_id"] == "bot:" + BOTOPS)

    def manager_asker(c, who):
        """The manager bot whose chat message to BotOps started this run, as the identity BotOps acts as for a report's
        create, archive or restore (`Auth.report_lifecycle`): its actor is the manager and `via` is "botops", so its own
        token, which has no `via`, is never mistaken for it. None when a bot did not ask."""
        turn = (
            c.execute(
                "SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (who.attempt_id,)
            ).fetchone()
            if who.attempt_id
            else None
        )
        msg = H.message(c, turn["message_id"]) if turn else None
        if (
            not msg
            or msg.get("deleted_at")
            or msg["recipient_id"] != "bot:" + BOTOPS
            or msg["kind"] not in ("say", "answer")
            or not H.is_bot(msg["sender_id"])
        ):
            return None
        return replace(who, actor=msg["sender_id"], role="bot", via="botops", task_actor="")

    def act_for_requester(request, ref):
        """Apply the requester's identity before the route checks its normal permissions."""
        who = request.state.identity
        if who.actor != "bot:" + BOTOPS:
            # The Tools Owner on a Tools change (backend/tools_delegation.py), else nobody but BotOps.
            tools = tools_delegation.route(request.method, request.url.path)
            if tools:
                # Its writes then carry the person's rights, and the one rule (backend/tool_access.py) keeps the
                # Tools Owner itself from ever being given a key that way.
                with store.read() as c:
                    tools = tools_delegation.is_tools_owner(c, who.actor)
            if not tools:
                if ref == "default":
                    return None, None  # a bot's own Tools change, with its own rights
                raise Problem(
                    "forbidden",
                    "Only BotOps, and the Tools Owner for a Tools change, act on a person's behalf: ask either in your "
                    "chat with it",
                    403,
                )
        if lifecycle_call(request.method, request.url.path) and ref in ("turn", "default", ""):
            # A manager bot's report: BotOps acts as that manager, which the lifecycle routes check (Auth.report_lifecycle).
            with store.read() as c:
                asker = manager_asker(c, who)
            if asker is not None:
                return None, asker
        # Runs on an executor thread: the delegation record is read back here and set again for the route.
        token = H.DELEGATION.set(None)
        try:
            with store.read() as c:
                acting = delegated_identity(c, who, ref, carry=carried_lends(request.method, request.url.path))
            request.state.delegation = H.DELEGATION.get()
        finally:
            H.DELEGATION.reset(token)
        with store.read() as c:
            if acting.actor == who.actor:
                return None, None
            body = parse_body(getattr(request, "_body", b"")) if request.method != "GET" else None
            if secret_in_request(request.method, request.url.path, body):
                raise Problem("secret_in_request", DETAIL_SECRET, 422)
            if H.is_human(acting.actor):
                card = person_card(c, request, acting, body)
                if card is not None:
                    return card, None
            # The ordinary route checks the requester's rights, including its own outbound send switch.
            return None, acting

    def person_card(c, request, acting, body):
        """BotOps acting for a person: a refusal for a route that hands a secret back, or the Confirm card in their BotOps
        chat for one that always needs their own tap (`botops_act.card_for_person`). None when the route runs as them."""
        method, path = request.method, normalize(request.url.path)
        why = person_only(method, path)
        if why:
            raise Problem(
                "person_only",
                why,
                403,
                extra=refusal_for_person(path, "Do this yourself in " + settings.app_name, settings.public_url),
            )
        body = {k: v for k, v in body.items() if k != "on_behalf_of"} if isinstance(body, dict) else {}
        rules = team_rules.load(c)

        def lowers():
            assert path is not None  # a route's path normalizes; lowers only runs for one
            rest = path[len(API) :]
            if rest.startswith("usage/pools/"):
                scope = rest[len("usage/pools/") :]
                return usage_limits.pool_only_lowers(
                    c, "company" if scope == "company" else "group:" + scope.split("/", 1)[-1], body
                )
            return usage_limits.only_lowers(
                c, rest[len("usage/limits/") :] if rest.startswith("usage/limits/") else None, body
            )

        # A Gateway URL change sends the saved key to the proposed address as a connection check.
        # Require the owner's tap whenever a key is saved, even when botops_direct is on. A key the server's
        # environment sets can't be changed here at all, so that save is refused at once rather than carded.
        gateway_put = method == "PUT" and path == API + "providers/gateway"
        moved, shown, what, refusal = destination_card(c, acting, method, path, body)
        gateway_key_address_change = (
            gateway_put
            and (bool(body.get("url")) or bool(moved))
            and (gateway_connection.connected(c, settings) and not gateway_connection.env_key(settings))
        )
        if not path or not card_for_person(
            method, path, body, rules, lowers, force_confirm=gateway_key_address_change or bool(moved)
        ):
            return None
        # Told at once, not handed a card that fails on the tap: first in the catalogue's words, which know whose a
        # trigger, webhook or computer is (backend/settings_catalog.py).
        said = settings_catalog.raw_refusal(c, auth, acting, method, path, body, settings.public_url)
        if said:
            raise Problem("forbidden", said["fix"], 403, extra=said)
        who_may = (
            "the owner"
            if owner_only(method, path, body) and acting.role != "owner"
            else "an owner or an admin"
            if admin_only(method, path) and acting.role != "owner" and not auth.bot_admin(acting)
            else ""
        )
        if who_may:
            raise Problem(
                "forbidden",
                "Only " + who_may + " can do this",
                403,
                extra=refusal_for_person(path, "Your role can't change this: ask " + who_may, settings.public_url),
            )
        if refusal:
            raise refusal
        if aid := automation_deleted(method, path):
            from . import automations

            automations.deletable(c, acting, aid)  # refused at once (404, 403, a built-in 409)
        with store.transaction() as w:
            card = propose_card(
                w,
                acting,
                method,
                path,
                body,
                card_summary(method, path, body),
                what=what,
                shown=shown,
                run=getattr(request.state.identity, "attempt_id", ""),
            )
        return JSONResponse(card)

    def destination_card(c, acting, method, path, body):
        """For BotOps acting for a person: (change, card lines, card title, refusal) when `body` moves saved credentials
        to another service (backend/destinations.py). It always waits for the person's own tap; until BotOps
        says which (`credentials` "keep" or "clear") and names the revision it read, it is refused at once (`refusal`,
        raised once the person's role is checked) with what the person must decide, so no card keeps or clears
        anything without their word. An address edit that moves nothing saved gets no change and no refusal, but still
        its card lines and title, so no card for either integration shows an address as typed. Else Nones."""
        from . import destinations, webhooks
        from .assistant import spoken

        if method == "PUT" and path == API + "providers/gateway":
            moved, labels = gateway_connection.destination_change(c, body, settings), [gateway_connection.LABEL_KEY]
            current, integration, name = gateway_connection.revision(c), "gateway", gateway_client.LABEL
            old_url, new_url = gateway_connection.url(c, settings), gateway_client.base_url(str(body.get("url") or ""))
        elif method == "POST" and (hook := webhooks.UPDATE_PATH.fullmatch(path)):
            row, moved, labels = webhooks.pending_change(c, acting, hook.group(1), body, app.state.vault)
            current, integration, name = row["revision"], "webhook", row["name"]
            old_url, new_url = row["url"], body.get("url") or row["url"]
        else:
            return None, None, None, None
        # Whatever else the same request changes runs at the tap too, so the card names it (the address is shown
        # only as its safe destination, never as typed).
        also = [k for k in body if k not in ("url", "credentials", "expected_revision")]
        and_also = f", and change {', '.join(spoken(k) for k in also)}" if also else ""
        others = [{"field": k, "new": shown_value(body[k])} for k in also]
        if not moved:
            if "url" not in body:
                return None, None, None, None
            seen = destinations.shown(old_url, new_url)
            old, new = seen["old"]["address"], seen["new"]["address"] or "an address that is not a URL"
            what = f"Change {name}'s address to {new} ({seen['path']}; nothing saved moves to another service)"
            return (
                None,
                [{"field": "destination", "old": old, "new": new}, {"field": "path", "new": seen["path"]}, *others],
                (what + and_also)[:300],
                None,
            )
        option = destinations.choice(body.get("credentials"))
        if not option:
            return moved, None, None, destinations.held(integration, moved, labels, current, name)
        if body.get("expected_revision") is None:
            return (
                moved,
                None,
                None,
                Problem(
                    "expected_revision",
                    "Changing where saved credentials go needs expected_revision: the revision you read",
                    422,
                ),
            )
        if not isinstance(body["expected_revision"], int) or body["expected_revision"] != int(current):
            return (
                moved,
                None,
                None,
                Problem("version_conflict", name + " changed since you read it; read it again", 409),
            )
        old, new, listed = moved["old"]["address"], moved["new"]["address"], ", ".join(labels)
        what = (
            f"Send {name}'s saved {listed} to {new} instead of {old}"
            if option == destinations.KEEP
            else f"Move {name} to {new} and clear its saved {listed}"
        )
        shown = [
            {"field": "destination", "old": old, "new": new},
            *([{"field": "path", "new": moved["path"]}] if "path" in moved else []),
            {"field": "credentials", "new": ("keep: " if option == destinations.KEEP else "clear: ") + listed},
            *others,
        ]
        return moved, shown, (what + and_also)[:300], None

    def shown_value(value):
        """A short card value for a field that changes alongside a destination: a scalar as itself, a header list as
        its names (a secret's value is refused before any card), anything else by size."""
        if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            return value[:120]
        if isinstance(value, list) and all(isinstance(h, dict) and "name" in h for h in value):
            return ", ".join(str(h["name"])[:60] for h in value) or "none"
        return f"{len(value)} item{'s' if len(value) != 1 else ''}" if isinstance(value, (list, dict)) else "changed"

    def botops_run_identity(c, attempt_id):
        """Whose rights this BotOps run carries (the GitHub token's scope, backend/github_app.py): the person's or bot's
        it acts for, BotOps' own, or None when its start lends nobody's (someone else's comment)."""
        import contextvars

        row = c.execute("SELECT runner_id FROM attempts WHERE id=? AND bot_id=?", (attempt_id, BOTOPS)).fetchone()
        if not row:
            return None
        own = Identity("bot:" + BOTOPS, "bot", runner_id=row["runner_id"], attempt_id=attempt_id)
        try:
            # Its own context: resolving the run here records nothing as delegated.
            # A bot's hand-off (carried_request) lends no repository access: the token keeps that bot's scope.
            return contextvars.copy_context().run(delegated_identity, c, own, "turn")
        except Problem:
            return None

    app.state.botops_run_identity = botops_run_identity

    def propose_card(c, acting, method, path, body, summary, what=None, shown=None, run=None):
        """What always needs the requesting person's own click: a Confirm card in their chat with BotOps,
        and the answer BotOps reports back instead of asking them to go to Settings. `what`: the card's title, when the
        route that asks already said in plain words what confirming does (never a bot's words). `run`: BotOps' attempt,
        where the request's own run (H.RUN) is not set, as on act_for_requester's executor thread."""
        room = rooms.chat_room(c, auth, acting, BOTOPS)
        why = card_why(c, auth, acting, run or H.RUN.get(), method, path, body)
        card = create_proposal(
            c,
            settings,
            acting,
            room,
            BOTOPS,
            summary,
            method,
            path,
            body,
            what=what,
            routes=app.routes,
            shown=shown,
            why=why,
        )
        return {
            "proposed": True,
            "needs_confirm": True,
            **card,
            "detail": "This needs "
            + ((H.human(c, H.actor_id(acting.actor)) or {}).get("name") or "the person")
            + "'s own OK. A Confirm card is in their chat with BotOps; nothing has changed yet.",
        }

    app.state.botops_propose = propose_card  # a route outside this file asking for the card (backend/repositories.py)

    def risky(who):
        """A delegated identity that has not been confirmed: BotOps acting for a person, before their click."""
        return who.via == "botops" and not who.confirmed

    return (
        act_for_requester,
        botops_owns_task,
        carried_request,
        confirm_for_carried,
        delegated_identity,
        propose_card,
        risky,
    )
