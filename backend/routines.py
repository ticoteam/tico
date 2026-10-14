"""Routines: the server's table of recurring work, and what happens when one is due.

A routine is one row in `schedules`: a bot, a time (a cron) or a hub event, a title, and the
text the bot receives. People and bots write rows through the API (`hub routine ...`); the
scheduler reads them. Nothing else defines a routine: not a file in the bot's repository, not
a Git branch. When one is due the hub opens a task for the bot with that text, and the bot
takes it from there like any other task.
"""
import json
import re
from datetime import datetime, timezone

from clients.manifest import tools_of
from clients.routines import DEFAULT_ZONE, validate_schedules
from . import people as P, task_privacy as privacy
from .harnesses import SYNCED_SQL
from .statuses import is_parked
from .scheduler import next_due, stamp
from .store import H, Problem, encode

OCCURRENCE_LIMIT = 50
DEFAULT_TEXT = "Run this scheduled routine."
# Every active bot looks at its own open tasks once a day, staggered across
# 08:00-09:59 America/Los_Angeles by slug so the fleet does not wake at once.
OPEN_TASKS_KEY = "open-tasks-daily"      # retired; the key finds the old rows
SWEEP_KEY = "task-sweep"
SWEEP_TEXT = ("Sweep stuck tasks across the fleet (follow playbooks/task-sweep.md if you have it). "
              "`hub task list --stuck` lists every bot's open work that has not moved in a day and waits on "
              "nobody. For each: start it with `hub task run <id>` when the bot can simply do it; "
              "fix the cause when something in the bot or Tico keeps it stuck; or tell the requester "
              "in one line why it cannot move. Then review the open tasks you filed for humans "
              "(`hub task list --requester me --status open`): close each whose condition is already true or superseded "
              "with a one-line note, and ask once about one you cannot tell. "
              "Nothing to do: finish this task with one line.")
OPEN_TASKS_TEXT = ("Daily open-tasks check. List the tasks you own that are still open (`hub task list`). "
                   "Move the top one forward: finish it, take its next step, or say in one line with "
                   "`hub task update <id> --note` what it is waiting on. If a person asked for something that "
                   "is not a task yet, file it now. Nothing open: finish this task with one line.")
FIELDS = ("title", "cron", "on", "timezone", "text")


def _clock(at):
    return at or datetime.now(timezone.utc)


def validate(fields):
    """One routine's fields, checked the way a manifest entry is (clients/routines.py)."""
    entry = {"title": fields.get("title"), "cron": fields.get("cron") or "", "on": fields.get("on") or "",
             "timezone": fields.get("timezone") or DEFAULT_ZONE, "instructions": fields.get("text") or ""}
    return validate_schedules([entry])[0]


def row(c, sid):
    found = c.execute("SELECT s.*,coalesce(sc.timezone,?) AS timezone,coalesce(sc.enabled,1) AS enabled "
                      "FROM schedules s LEFT JOIN schedule_config sc ON sc.schedule_id=s.id WHERE s.id=?",
                      (DEFAULT_ZONE, sid)).fetchone()
    return dict(found) if found else None


def create(c, actor, bot, fields, *, key=None, at=None):
    """A new routine for `bot`. With `key`, the same key names the same routine: creating it
    again updates it in place (a bot that sets up its own routines every turn makes one)."""
    at = _clock(at)
    entry = validate(fields)
    H._takes_tasks(c, actor, "bot:" + bot)        # every run of a routine is a task
    if key is not None:
        existing = c.execute("SELECT id FROM schedules WHERE bot=? AND routine_key=?", (bot, key)).fetchone()
        if existing:
            return update(c, actor, existing["id"], {**fields, "enabled": fields.get("enabled", True)},
                          at=at, restore=True)
    sid = bot + ":" + (key or H.new_id()[:12])
    now = stamp(at)
    event = entry["on"] or None
    due = None if event else stamp(next_due(entry["cron"], at, entry["timezone"]))
    c.execute("INSERT INTO schedules(id,bot,routine_key,cron,event_name,title,playbook,next_due,source,updated_at) "
              "VALUES(?,?,?,?,?,?,?,?,'hub',?)",
              (sid, bot, key, entry["cron"], event, entry["title"], entry["instructions"] or DEFAULT_TEXT, due, now))
    c.execute("INSERT INTO schedule_config(schedule_id,timezone,enabled) VALUES(?,?,?)",
              (sid, entry["timezone"], 1 if fields.get("enabled", True) else 0))
    H.event(c, actor, "routine.created", sid, {"bot": bot, "cron": entry["cron"], "on": entry["on"]})
    return row(c, sid)


def update(c, actor, sid, changes, *, at=None, restore=False):
    """Change what a routine says or when it runs. A cadence change moves the next fire from
    now; a text change reaches the next occurrence and never a task already opened."""
    at = _clock(at)
    current = row(c, sid)
    if not current or (current["deleted_at"] and not restore):
        raise Problem("not_found", "Routine not found", 404)
    merged = {"title": current["title"], "cron": current["cron"] or "", "on": current["event_name"] or "",
              "timezone": current["timezone"], "text": current["playbook"] or ""}
    for name in FIELDS:
        if changes.get(name) is not None:
            merged[name] = changes[name]
    if changes.get("cron"):
        merged["on"] = ""
    elif changes.get("on"):
        merged["cron"] = ""
    entry = validate(merged)
    now = stamp(at)
    event = entry["on"] or None
    cadence_changed = (entry["cron"] != (current["cron"] or "") or event != current["event_name"]
                       or entry["timezone"] != current["timezone"] or bool(current["deleted_at"]))
    if event:
        due = None
    else:
        due = stamp(next_due(entry["cron"], at, entry["timezone"])) if cadence_changed else current["next_due"]
    c.execute("UPDATE schedules SET cron=?,event_name=?,title=?,playbook=?,next_due=?,deleted_at=NULL,updated_at=? WHERE id=?",
              (entry["cron"], event, entry["title"], entry["instructions"] or DEFAULT_TEXT, due, now, sid))
    enabled = current["enabled"] if changes.get("enabled") is None else (1 if changes["enabled"] else 0)
    c.execute("INSERT INTO schedule_config(schedule_id,timezone,enabled) VALUES(?,?,?) ON CONFLICT(schedule_id) "
              "DO UPDATE SET timezone=excluded.timezone,enabled=excluded.enabled", (sid, entry["timezone"], enabled))
    H.event(c, actor, "routine.updated", sid, {k: v for k, v in changes.items() if v is not None and k != "text"})
    return row(c, sid)


def remove(c, actor, sid, *, at=None):
    """Delete a routine. Its history stays; a task it opened that nobody has claimed closes."""
    at = _clock(at)
    current = row(c, sid)
    if not current:
        return None
    now = stamp(at)
    c.execute("UPDATE schedules SET deleted_at=?,updated_at=?,next_due=NULL WHERE id=?", (now, now, sid))
    cancel_unclaimed(c, sid)
    H.event(c, actor, "routine.deleted", sid, {"bot": current["bot"]})
    return row(c, sid)


def cancel_unclaimed(c, sid):
    _close_unclaimed(c, c.execute("SELECT DISTINCT t.* FROM tasks t JOIN schedule_occurrences o ON o.task_id=t.id "
                                  "WHERE o.schedule_id=? AND t.status='open'", (sid,)).fetchall(),
                     "Routine removed before execution.")


PAUSED_NOTE = "Bot paused before this routine ran; its routines fire again on schedule once it resumes."


def hold_for_paused_bot(c, bot):
    """A paused bot's routine runs nobody has started are closed, so a long pause does not leave a
    backlog that all runs at once on resume. Only timed runs the bot still holds: a person's direct work
    and a person's Run now stay queued for the bot, an event run carries one-off content (a meeting's notes)
    that is never announced again, and a run handed to another bot is that bot's. Each routine keeps its own
    enabled flag, so resuming restores exactly the ones that were on. Returns the closed task ids."""
    found = c.execute("SELECT DISTINCT t.* FROM tasks t JOIN schedule_occurrences o ON o.task_id=t.id "
                      "JOIN schedules s ON s.id=o.schedule_id WHERE s.bot=? AND t.owner=? AND t.status='open' "
                      "AND s.event_name IS NULL AND o.occurrence NOT LIKE 'manual:%'",
                      (bot, H.bot_actor(bot))).fetchall()
    return _close_unclaimed(c, found, PAUSED_NOTE)


def _close_unclaimed(c, tasks, note):
    closed = []
    for task in tasks:
        # Primary jobs, additional turn inputs and carried work all count as claimed.
        claimed = c.execute(
            "SELECT 1 FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
            f"WHERE {H.MESSAGE_TASK_SQL}=? AND (EXISTS "
            "(SELECT 1 FROM jobs j JOIN attempts a ON a.job_id=j.id WHERE j.message_id=m.id) OR EXISTS "
            "(SELECT 1 FROM attempt_inputs i WHERE i.message_id=m.id)) LIMIT 1", (task['id'],)).fetchone()
        carried = task['carried_by'] or c.execute(
            "SELECT 1 FROM events e JOIN json_each(e.detail_json,'$.tasks') v "
            "WHERE e.action='task.next-run.carried' AND v.value=? LIMIT 1", (task['id'],)).fetchone()
        if claimed or carried:
            continue
        H.task_close(c, H.KEEPER, task['id'], note)
        closed.append(task['id'])
        c.execute('UPDATE tasks SET version=version+1 WHERE id=?', (task['id'],))
        task_messages = ("SELECT m.id FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
                         f"WHERE {H.MESSAGE_TASK_SQL}=?")
        c.execute("UPDATE jobs SET state='completed' WHERE state='queued' AND message_id IN (" + task_messages + ")",
                  (task['id'],))
        # Includes the close notice, so the outbox does not wake this removed work.
        c.execute("UPDATE messages SET delivered_at=? WHERE id IN (" + task_messages + ") AND to_actor LIKE 'bot:%' "
                  "AND delivered_at IS NULL", (H.now(), task["id"]))
    return closed


def latest_task(c, sid):
    """The most recent unfinished run; completed history stays Done when the next run opens."""
    return c.execute("SELECT t.id,t.status FROM schedule_occurrences o JOIN tasks t ON t.id=o.task_id "
                     "WHERE o.schedule_id=? AND t.status IN ('open','doing','waiting','review','ready') "
                     "ORDER BY o.occurrence DESC LIMIT 1", (sid,)).fetchone()


def no_input(c, settings, schedule, local):
    """Why a built-in routine's run would have nothing to work on, or None: such a run is skipped rather than
    opening a task its bot can only close."""
    from . import market
    # Only the routine as shipped: a team that changed its text may have given it other work.
    if (schedule["bot"] == market.CURATOR.split(":", 1)[1] and schedule["routine_key"] == "curate-the-market"
            and (schedule["playbook"] or "").strip() == market.CURATE_TEXT.strip()):
        return market.nothing_to_curate(c, settings, local)
    return None


def open_task(c, auth, schedule, heading, body, now):
    owner = "bot:" + schedule["bot"]
    conversation_id = None
    if auth is not None:
        from . import rooms
        conversation_id = rooms.task_conversation_id(c, auth, owner, H.KEEPER)
    task = H.task_create(c, H.KEEPER, heading[:300], body, owner, deduplicate=False,
                         conversation_id=conversation_id)
    c.execute("UPDATE schedules SET last_fired=? WHERE id=?", (now, schedule["id"]))
    return task


def emit(c, kind, subject, payload=None, *, title=None, content="", at=None, auth=None):
    """Something happened in the hub; every routine that runs `on:` it gets one task.

    `subject` is the thing's id (a recording, say): the pair (routine, event, subject) is an
    occurrence, and `schedule_occurrences` makes it fire once however often the event is
    re-emitted. The task body is the routine's text, then the event's facts, then `content`
    (the thing itself, as a delivery task carries a meeting's notes), so the bot has everything
    in the one task it may read. The producer decides what is shareable before calling.
    Caller owns the transaction. Returns the ids of the tasks it opened.
    """
    now = stamp(_clock(at))
    facts = {"event": kind, "subject": subject, **{k: v for k, v in (payload or {}).items() if v not in (None, "")}}
    content = ("\n\n" + content.strip()) if content and content.strip() else ""
    fired = []
    rows = c.execute("SELECT s.* FROM schedules s JOIN bots b ON b.slug=s.bot LEFT JOIN schedule_config sc ON sc.schedule_id=s.id "
                     "WHERE s.event_name=? AND b.state='active' AND coalesce(sc.enabled,1)=1 AND s.deleted_at IS NULL "
                     "AND NOT EXISTS(SELECT 1 FROM bot_config bc WHERE bc.bot=s.bot "
                     "AND coalesce(json_extract(bc.config_json,'$.shared_from'),'')<>'') "
                     # a bot synced in from another platform never sees a task (hubdb._takes_tasks)
                     "AND NOT EXISTS(SELECT 1 FROM bot_config xc WHERE xc.bot=s.bot "
                     "AND json_extract(xc.config_json,'$.harness') IN (" + SYNCED_SQL + ")) "
                     "ORDER BY s.id", (kind,)).fetchall()
    for schedule in rows:
        occurrence = f"{kind}:{subject}"
        if c.execute("SELECT 1 FROM schedule_occurrences WHERE schedule_id=? AND occurrence=?", (schedule["id"], occurrence)).fetchone():
            continue
        body = (schedule["playbook"] or "Handle this event.").rstrip() + "\n\n" + "\n".join(f"{k}: {v}" for k, v in facts.items()) + content
        heading = f"{schedule['title']}: {title}" if title else schedule["title"]
        task = open_task(c, auth, schedule, heading, body, now)
        c.execute("INSERT INTO schedule_occurrences VALUES(?,?,?,?)", (schedule["id"], occurrence, task["id"], "event"))
        H.event(c, H.KEEPER, "schedule.event", schedule["id"], {"event": kind, "subject": subject, "task": task["id"]})
        fired.append(task["id"])
    return fired


def _roster(c):
    found = c.execute("SELECT value_json FROM registry_metadata WHERE key='people'").fetchone()
    if found:
        return P.load(json.loads(found[0]))
    return P.load({"people": H.humans(c)})


MAILBOX = re.compile(r"^[^\s@{}<>]+@[^\s@{}<>]+\.[^\s@{}<>]+$")


def declared_mailbox(config):
    """The address a bot declares: the identity of its `gmail` tool in bot.yaml (BotOps fills it from the
    `Mailbox:` line in the instructions). Empty when there is none, or when `{{mailbox}}` was never filled."""
    for entry in tools_of(config) or []:
        if isinstance(entry, dict) and str(entry.get("service", "")).lower() == "gmail":
            addr = str(entry.get("identity") or "").strip().lower()
            if MAILBOX.fullmatch(addr):
                return addr
    return ""


def set_declared_mailbox(c, bot, address):
    """Record the address `bot` reads, as the identity of its `gmail` tool in the bot's stored config (the owner's
    word, not the bot's repository: a bot cannot widen what its runs may open). Other tools are kept."""
    address = str(address or "").strip().lower()
    if not MAILBOX.fullmatch(address):
        raise Problem("mailbox", "That is not an email address", 422)
    found = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
    if not found:
        raise Problem("not_found", "Bot not found", 404)
    config = json.loads(found["config_json"] or "{}")
    tools = [dict(t) if isinstance(t, dict) else t for t in (config.get("tools") or config.get("access") or [])]
    gmail = next((t for t in tools if isinstance(t, dict) and str(t.get("service", "")).lower() == "gmail"), None)
    if gmail is None:
        tools.append({"service": "gmail", "identity": address, "can": ["read", "draft"]})
    else:
        gmail["identity"] = address
    config.pop("access", None)
    config["tools"] = tools
    c.execute("UPDATE bot_config SET config_json=?,revision=revision+1 WHERE bot=?", (encode(config), bot))


def message_bot_mailboxes(c, roster=None):
    """The mailboxes the message bots manage, one per person who has an `inbox_bot`:
    [{"address", "person_id", "bot", "declared"}]. The address is the bot's declared mailbox, and the
    person's own email only when the bot declares none. People who left (hidden) and archived bots are
    skipped, and so is a mailbox that two bots share (listed once)."""
    roster = roster if roster is not None else _roster(c)
    out, seen = [], set()
    for person in (roster or {}).get("people") or []:
        bot = person.get("inbox_bot")
        if not bot or person.get("hidden"):
            continue
        row = H.bot(c, bot)
        if not row or row["state"] == "archived":
            continue
        found = c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()
        try:
            config = json.loads(found["config_json"] or "{}") if found else {}
        except ValueError:
            config = {}
        declared = declared_mailbox(config)
        address = declared or str(person.get("email") or "").strip().lower()
        if not MAILBOX.fullmatch(address) or address in seen:
            continue
        seen.add(address)
        out.append({"address": address, "person_id": person["id"], "bot": bot, "declared": bool(declared)})
    return out


def mailbox_of(person, boxes):
    """The address that stands for this person: their message bot's mailbox, else their own email."""
    found = next((b["address"] for b in boxes if b["person_id"] == person.get("id")), "")
    return found or str(person.get("email") or "").strip().lower()


def person_for_mailbox(address, boxes, roster):
    """The roster person a mailbox belongs to: by a bot's declared mailbox first, then by email."""
    wanted = str(address or "").strip().lower()
    found = next((b for b in boxes if b["address"] == wanted), None)
    return (P.person(found["person_id"], roster) if found else None) or P.person_by_email(wanted, roster)


def token_mailboxes(c, bot, roster=None):
    """The mailboxes a message bot's turn may ask its runner for a token for: its person's (the mailbox the
    bot declares, else their email), then the people below them in the org chart. Any other bot gets none."""
    roster = roster if roster is not None else _roster(c)
    who = P.inbox_person(bot, roster)
    if not who:
        return []
    boxes = message_bot_mailboxes(c, roster)
    out = []
    for person in P.below(who["id"], roster):
        addr = mailbox_of(person, boxes)
        if addr and "@" in addr and addr not in out:
            out.append(addr)
    return out


def inbox_of(bot, config, roster):
    """Mailboxes this bot may read: declared gmail access plus a roster `inbox_bot`."""
    mailboxes, seen = [], set()
    org_read = False

    def add(addr):
        addr = str(addr or "").strip().lower()
        if addr and "@" in addr and addr not in seen:
            seen.add(addr)
            mailboxes.append(addr)

    who = P.inbox_person(bot, roster)
    if who:
        org_read = True
        for addr in P.mailboxes_below(who["id"], roster):
            add(addr)
    for entry in tools_of(config) or []:
        if not isinstance(entry, dict) or str(entry.get("service", "")).lower() != "gmail":
            continue
        add(entry.get("identity"))
        if entry.get("org_read") is True:
            org_read = True
            person = P.person_by_email(str(entry.get("identity") or "").strip().lower(), roster)
            if person:
                for addr in P.mailboxes_below(person["id"], roster):
                    add(addr)
    return {"mailboxes": mailboxes, "org_read": org_read}


def _inboxes(c, bots):
    if not bots:
        return {}
    roster = _roster(c)
    wanted = tuple(bots)
    marks = ",".join("?" * len(wanted))
    configs = {found["bot"]: json.loads(found["config_json"] or "{}")
               for found in c.execute(f"SELECT bot,config_json FROM bot_config WHERE bot IN ({marks})", wanted)}
    return {bot: inbox_of(bot, configs.get(bot) or {}, roster) for bot in bots}


def _outcome(raw, event=False):
    if raw == "coalesced_into_existing_task":
        return "coalesced"
    if raw == "event" or (event and raw == "created"):
        return "event"
    return raw or "created"


def occurrences(c, schedule_id, limit=OCCURRENCE_LIMIT, *, who, auth):
    """Recent firings of one routine, joined to the task and its latest attempt."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = OCCURRENCE_LIMIT
    limit = max(1, min(limit, OCCURRENCE_LIMIT))
    schedule = c.execute("SELECT event_name FROM schedules WHERE id=?", (schedule_id,)).fetchone()
    event = bool(schedule and schedule["event_name"])
    rows = c.execute(
        "SELECT o.occurrence,o.outcome,o.task_id,t.status,t.title "
        "FROM schedule_occurrences o LEFT JOIN tasks t ON t.id=o.task_id WHERE o.schedule_id=? "
        f"AND (o.task_id IS NULL OR ({auth.task_sql(c, who)})) "
        "ORDER BY o.occurrence DESC LIMIT ?",
        (schedule_id, limit)).fetchall()
    ids = list(dict.fromkeys(found["task_id"] for found in rows if found["task_id"]))
    attempts = {}
    if ids:
        marks = ",".join("?" * len(ids))
        for attempt in c.execute(
                f"SELECT {H.MESSAGE_TASK_SQL} AS task_id,a.id,a.started,a.finished,a.state "
                "FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
                f"JOIN conversations cv ON cv.id=m.conversation_id WHERE {H.MESSAGE_TASK_SQL} IN ({marks}) "
                "ORDER BY a.created DESC", ids):
            if privacy.attempt_readable(c, privacy.actor(who), attempt["id"]):
                attempts.setdefault(attempt["task_id"], dict(attempt))
    result = []
    for found in rows:
        attempt = attempts.get(found["task_id"]) or {}
        why = None
        if found["outcome"] == "skipped":
            # A run skipped for want of input opened no task; its reason is on its occurrence event.
            why = c.execute("SELECT json_extract(detail_json,'$.reason') FROM events WHERE action='schedule.occurrence' "
                            "AND target=? AND json_extract(detail_json,'$.occurrence')=? ORDER BY rowid DESC LIMIT 1",
                            (schedule_id, found["occurrence"])).fetchone()
            why = why[0] if why else None
        started, finished = H.parse_ts(attempt.get("started")), H.parse_ts(attempt.get("finished"))
        duration = max(0, round((finished - started).total_seconds())) if started and finished else None
        result.append({
            "occurrence": found["occurrence"], "outcome": _outcome(found["outcome"], event),
            "task_id": found["task_id"], "status": found["status"] or None, "title": found["title"] or None,
            "started": attempt.get("started"), "finished": attempt.get("finished"),
            "duration_s": duration, "exit": attempt.get("state"), **({"reason": why} if why else {})})
    return result


def listing(c, bot=None, include_deleted=False, summary=False):
    """`summary` drops the text: the overview and employee views only need the schedule, not
    hundreds of KB of prose."""
    rows = c.execute('SELECT s.*,b.state,coalesce(sc.timezone,?) AS timezone,coalesce(sc.enabled,1) AS enabled,'
                     'bc.onboarding_state FROM schedules s JOIN bots b ON b.slug=s.bot '
                     'LEFT JOIN schedule_config sc ON sc.schedule_id=s.id LEFT JOIN bot_config bc ON bc.bot=s.bot '
                     'WHERE (? IS NULL OR s.bot=?) AND (? OR s.deleted_at IS NULL) ORDER BY s.title,s.id',
                     (DEFAULT_ZONE, bot, bot, include_deleted)).fetchall()
    inboxes = _inboxes(c, {found["bot"] for found in rows})
    result = []
    for found in rows:
        # Why an enabled routine is not firing because of its bot, not its own switch: the routine
        # stays enabled and fires again once the bot is active and set up.
        held = None
        if found['enabled'] and not found['deleted_at']:
            held = (found['state'] if found['state'] != 'active'
                    else 'needs_setup' if is_parked(found['onboarding_state']) else None)
        value = {'id': found['id'], 'bot': found['bot'], 'employee': found['bot'], 'key': found['routine_key'],
                 'title': found['title'], 'cron': found['cron'] or '', 'on': found['event_name'] or '',
                 'kind': 'event' if found['event_name'] else 'cron', 'timezone': found['timezone'],
                 'enabled': bool(found['enabled']), 'text': found['playbook'] or '',
                 'last_fired': found['last_fired'], 'next_due': found['next_due'], 'next': found['next_due'],
                 'deleted_at': found['deleted_at'], 'updated_at': found['updated_at'],
                 'inbox': inboxes.get(found['bot']) or {'mailboxes': [], 'org_read': False},
                 'held_by_bot': held,
                 'active': bool(found['enabled']) and not found['deleted_at'] and not held}
        if summary:
            value.pop('text', None)
        result.append(value)
    return result


def retire_open_tasks_checks(c, at=None):
    """Delete every bot's daily open-tasks routine (fewer random daily runs; one
    BotOps sweep finds the stuck work instead). Returns the bots whose routine went."""
    gone = []
    for r in c.execute("SELECT id, bot FROM schedules WHERE routine_key=? AND deleted_at IS NULL",
                       (OPEN_TASKS_KEY,)).fetchall():
        remove(c, H.KEEPER, r["id"], at=at)
        gone.append(r["bot"])
    return gone


def ensure_task_sweep(c, at=None):
    """The fleet maintainer's one daily sweep of stuck tasks. Set once; a person's delete stays."""
    slug = H.FLEET_MAINTAINER
    if not c.execute("SELECT 1 FROM bots WHERE slug=? AND state='active'", (slug,)).fetchone():
        return None
    if c.execute("SELECT 1 FROM schedules WHERE bot=? AND routine_key=?", (slug, SWEEP_KEY)).fetchone():
        return None
    return create(c, H.KEEPER, slug, {"title": "Sweep stuck tasks", "text": SWEEP_TEXT,
                                      "cron": "0 9 * * *", "timezone": "America/Los_Angeles"},
                  key=SWEEP_KEY, at=at)

