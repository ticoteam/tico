"""Automations (CONTRACT §2): saved flows, their versions and runs, who may change them, and how a trigger runs one.

Routines and wake rules moved here once, at startup (backend/redesign_migration.py `cutover`).

A trigger never runs a flow inside its own write. `fire` checks the trigger's filters there (a run they drop only
counts toward the day, `automation_daily`) and queues a run; a doorbell after the commit has the request that caused
it, or the scheduler's next tick, `drain` the queue. Draining claims one run at a time and hands its flow to the pure
executor (backend/automation_engine.py) with `HubServices`, whose judge calls and actions each take their own short
transaction. A run that waits (an llm turn, a batch time) is saved with its threads; the turn's end
(`turn_done`) or the tick puts it back in the queue.

A run is saved after every step that did something, before the next starts, and each effect is written once: its key
(`<run id>:<step record>`) goes in `automation_effects` in the effect's own transaction, so doing it again answers
what it answered the first time. A run claimed whose server stopped is put back in the queue by the tick and goes on
from its last save (at most MAX_RESUMES times). A turn that ends while its run is still being worked leaves its answer
in `automation_turn_answers`, which the run picks up when it is saved.

Reading mail and Slack is the owner's: an automation hears and finds only what its owner may read (`may_read_inbox`),
checked on save and again when a message arrives. A turn holds its bot's usual cost against the spend cap until it is
priced (`automation_holds`).

The run guard stops spending, never work. An automation whose runs keep coming over time (RUN_GUARD_SUSTAINED runs in
RUN_GUARD_SPAN minutes spread over at least RUN_GUARD_SPREAD of those minutes) or flood in (RUN_GUARD_BURST runs in
RUN_GUARD_MINUTES) is put on hold (`automation_guard_holds`): its queued runs and every new one are kept as `held`, which
the drain never claims, up to RUN_GUARD_BURST of them (past that each is counted as dropped, on the hold too). No rate
tells a slow catch-up from a loop, so a person decides: release the held runs to the queue, or drop them (skipped).
Turning the automation off or deleting it skips them too. A burst of distinct events inside a minute or two is a
backlog and simply queues.

Mail actions are carried out by the computer that reads the mailbox: it claims them (a lease of MAIL_LEASE_MINUTES,
MAIL_TRIES claims at most) and reports each result; their step and run wait until then (`settle_mail`).

The run state keeps every step's outputs for 30 days; after that `prune` keeps the status, cost and times only.
"""
import asyncio
import contextlib
import contextvars
import json
import logging
import threading
import weakref
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import Field

from . import automation_actions as A
from . import automation_due as D
from . import automation_engine as E
from . import automation_flow as F
from . import people as P
from . import report_managers
from . import task_models
from . import task_run_models
from .migrations import renames
from .models import Contract
from . import audit_log, cloud_access
from .store import H, Problem

log = logging.getLogger(__name__)

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS automations(id TEXT PRIMARY KEY, name TEXT NOT NULL, owner_id TEXT NOT NULL, "
    "group_id TEXT, trigger_type TEXT NOT NULL, enabled INTEGER NOT NULL, builtin INTEGER NOT NULL DEFAULT 0, "
    "flow TEXT NOT NULL, version INTEGER NOT NULL, created_at TEXT NOT NULL, created_by_id TEXT NOT NULL, "
    "updated_at TEXT NOT NULL, deleted_at TEXT, next_due_at TEXT, found_since TEXT)",
    "CREATE INDEX IF NOT EXISTS automations_trigger ON automations(trigger_type, enabled) WHERE deleted_at IS NULL",
    "CREATE TABLE IF NOT EXISTS automation_versions(automation_id TEXT NOT NULL, n INTEGER NOT NULL, actor TEXT NOT NULL, "
    "created_at TEXT NOT NULL, flow TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', summary TEXT, "
    "PRIMARY KEY(automation_id, n))",
    "CREATE TABLE IF NOT EXISTS automation_runs(id TEXT PRIMARY KEY, automation_id TEXT NOT NULL, version INTEGER NOT NULL, "
    "trigger_ref TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, "
    "cost REAL NOT NULL DEFAULT 0, saved_state TEXT, claimed_at TEXT, wake_at TEXT, chain TEXT NOT NULL DEFAULT '[]')",
    "CREATE INDEX IF NOT EXISTS automation_runs_of ON automation_runs(automation_id, started_at)",
    "CREATE INDEX IF NOT EXISTS automation_runs_queue ON automation_runs(status, started_at)",
    # The overview reads every visible automation's runs over a time range (backend/automation_overview.py).
    "CREATE INDEX IF NOT EXISTS automation_runs_started ON automation_runs(started_at)",
    "CREATE TABLE IF NOT EXISTS automation_daily(automation_id TEXT NOT NULL, day TEXT NOT NULL, "
    "runs INTEGER NOT NULL DEFAULT 0, dropped INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(automation_id, day))",
    "CREATE TABLE IF NOT EXISTS automation_mail_actions(id TEXT PRIMARY KEY, automation_id TEXT NOT NULL, run_id TEXT, "
    "mailbox TEXT NOT NULL, msg_id TEXT NOT NULL, action TEXT NOT NULL, args TEXT NOT NULL, state TEXT NOT NULL, "
    "created_at TEXT NOT NULL, claimed_at TEXT, claimed_by TEXT, result TEXT, attempts INTEGER NOT NULL DEFAULT 0)",
    "CREATE INDEX IF NOT EXISTS automation_mail_queue ON automation_mail_actions(mailbox, state, created_at)",
    # An inbox's send switch (on unless a row says off): off, send_reply saves a draft instead.
    "CREATE TABLE IF NOT EXISTS automation_inboxes(inbox TEXT PRIMARY KEY, send INTEGER NOT NULL, updated_at TEXT NOT NULL, "
    "updated_by TEXT NOT NULL)",
    # Each effect a run made, by `<run id>:<step record>`: doing it again answers this instead.
    "CREATE TABLE IF NOT EXISTS automation_effects(key TEXT PRIMARY KEY, run_id TEXT NOT NULL, created_at TEXT NOT NULL, "
    "result TEXT NOT NULL)",
    # A turn that ended while its run was being worked: picked up when the run is saved.
    "CREATE TABLE IF NOT EXISTS automation_turn_answers(run_id TEXT NOT NULL, ref TEXT NOT NULL, text TEXT NOT NULL, "
    "cost REAL NOT NULL, ok INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(run_id, ref))",
    # What a started turn is expected to cost until it is priced, against the spend cap.
    "CREATE TABLE IF NOT EXISTS automation_holds(ref TEXT PRIMARY KEY, automation_id TEXT NOT NULL, run_id TEXT NOT NULL, "
    "amount REAL NOT NULL, created_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS automation_holds_of ON automation_holds(automation_id, created_at)",
    # The run guard's hold on an automation, live while `cleared` is null; one row each, the last hold kept so the
    # guard counts only runs since it was cleared. `arrivals`: runs triggered since it started (the one that tripped it
    # included), at most RUN_GUARD_BURST held; `dropped`: triggers past that.
    "CREATE TABLE IF NOT EXISTS automation_guard_holds(automation_id TEXT PRIMARY KEY, rule TEXT NOT NULL, "
    "held_at TEXT NOT NULL, arrivals INTEGER NOT NULL DEFAULT 0, dropped INTEGER NOT NULL DEFAULT 0, task_id TEXT, "
    "cleared_at TEXT)",
)
KEEP_DAYS = 30           # step detail; the daily counts and the run rows stay
TURN_WAIT_DAYS = 1
STALE_MINUTES = 15       # a claimed run not saved by then was cut off: queued again, to go on from its last save
MAX_RESUMES = 3          # a run cut off this often is failed: it may be what stops the server
MAX_CHAIN = 3            # automations started by automations: at most this deep, never the same one twice
RUN_GUARD_MINUTES = 10                  # the window a flood is counted in
RUN_GUARD_BURST = 500                   # runs in RUN_GUARD_MINUTES, any refs: a flood, held; also the held runs kept
RUN_GUARD_SPAN = 20                     # the minutes a sustained loop is watched over
RUN_GUARD_SUSTAINED = 120               # runs in RUN_GUARD_SPAN that make it sustained...
RUN_GUARD_SPREAD = 15                   # ...if they fall in at least this many distinct minutes (a burst does not)
TURN_COST = 0.25         # what a turn holds against the spend cap when its bot has no priced turns
TURN_COST_SAMPLE = 20    # its bot's recent priced turns averaged for the hold
HOLD_STALE_HOURS = 24    # a hold whose turn never ended (its job lost) is swept then, and the sweep recorded
ITEM_TRIES = 2           # a found item whose run fails is tried again once, then passed over (counted as dropped)
LIST_BYTES = 262_144
RUN_BYTES = 524_288
VALUES_BYTES = 60_000    # what a judge call is shown of a run's values
MAIL_LEASE_MINUTES = 5   # a claimed mail action not reported by then goes back to the queue
MAIL_TRIES = 3           # claims a mail action gets before it fails
BOTOPS = "bot:botops"            # builds automations for whoever asks it, with that asker's rights (backend/botops_act.py)
VERSIONED = ("trigger", "steps", "batch")   # what the automation does; a change here is a new version
# The automations a write in this context comes from: their own actions never start them again.
CHAIN = contextvars.ContextVar("automation_chain", default=())
_BELL = threading.Event()


def ensure(c):
    for statement in SCHEMA:
        c.execute(statement)


def _via(who):
    """Who acted for the actor, for the audit row: the Assistant, or a personal token's assistant."""
    return who.via or (f"token:{who.token_label}" if who.via_token and who.token_label else None)


# ---------------------------------------------------------------------------------------------------- the rows
def _row(c, aid):
    row = c.execute("SELECT * FROM automations WHERE id=? AND deleted_at IS NULL", (aid,)).fetchone()
    return dict(row) if row else None


def flow_of(row):
    return json.loads(row["flow"])


def view(c, row, who=None, detail=False, chart=None, jobs=None):
    """An automation as its readers get it: `can_edit` for `who`, and with `detail` each Decide step that names a set
    carries that set's questions (read-only: a write sends `set` or `questions`, never both). `kind` is the trigger as
    the overview groups it (`system` for Tico's own jobs); a system job's mirror takes its last and next run from the
    job (`jobs`, AO.system_jobs, read once for a list)."""
    from . import automation_overview as AO
    flow = flow_of(row)
    if detail:
        flow["steps"] = _with_questions(flow["steps"])
    last = c.execute("SELECT id, status, started_at, finished_at FROM automation_runs WHERE automation_id=? "
                     "ORDER BY started_at DESC LIMIT 1", (row["id"],)).fetchone()
    from . import redesign_migration
    flow["owner_id"] = flow.pop("owner")
    out = {"id": row["id"], **flow, "version": row["version"], "created_at": row["created_at"],
           "created_by_id": row["created_by_id"], "updated_at": row["updated_at"], "last_run": dict(last) if last else None,
           "kind": AO.kind_of(flow),
           # Wake rules that name a step their type does not have: they never fire until one is changed.
           "problems": redesign_migration.wake_problems(c, row["id"], flow) if flow.get("wakes") else []}
    if flow["trigger"]["type"] == "schedule":
        out["next_run"] = row["next_due_at"] or _iso(_due(flow["trigger"], datetime.now(timezone.utc)))
    if flow["trigger"]["type"] == "due":
        # next_due is the next due day (a date); next_run is when its run starts.
        day = _due_day(row["next_due_at"])
        out["next_due_at"], out["next_run"] = (None, None) if not day else (
            day.isoformat(), _iso(D.fire_at(day, D.open_days_of(flow["trigger"]), D.zone_of(flow["trigger"]))))
    if flow.get("enabled") is False:
        # Paused, not just off: the run guard turned it off and nobody has changed it since.
        guard = c.execute("SELECT max(created_at) FROM events WHERE target=? AND action='automation.run_guard'",
                          (row["id"],)).fetchone()[0]
        out["paused"] = bool(guard and guard >= row["updated_at"])
    if hold := hold_of(c, row["id"]):
        out["hold"] = hold
    job = AO.system_key(flow)
    if job:
        known = (jobs if jobs is not None else AO.system_jobs(c)).get(job) or {}
        out["last_run"] = known.get("last") or out["last_run"]
        out["next_run"] = known.get("next")
        out["schedule"] = known.get("schedule") or (flow.get("controls") or {}).get("schedule")
    if detail:
        today = c.execute("SELECT runs, dropped FROM automation_daily WHERE automation_id=? AND day=?",
                          (row["id"], H.now()[:10])).fetchone()
        out["counts"] = {"today": {"runs": today["runs"] if today else 0, "dropped": today["dropped"] if today else 0}}
    if who is not None:
        out["can_edit"] = may_change(c, who, flow, chart)
        out["can_pause"] = may_pause(c, who, flow, out["can_edit"])
    return out


def _with_questions(steps):
    out = []
    for step in steps:
        step = dict(step)
        if step.get("type") == "decide" and step.get("set"):
            try:
                step["questions"] = F.question_set(step["set"])
            except F.Invalid:
                pass   # the set was removed since the save: the step shows its name only
        if step.get("type") == "paths":
            step["paths"] = [{**path, "steps": _with_questions(path.get("steps") or [])} for path in step["paths"]]
            if step.get("otherwise") is not None:
                step["otherwise"] = {"steps": _with_questions(step["otherwise"].get("steps") or [])}
        out.append(step)
    return out


def _versioned(flow):
    return {key: flow[key] for key in VERSIONED if flow.get(key) is not None}


def _next_n(c, aid):
    """Versions are numbered in the order they were written, proposals included, so a proposal keeps its number."""
    row = c.execute("SELECT max(n) FROM automation_versions WHERE automation_id=?", (aid,)).fetchone()
    return (row[0] or 0) + 1


def _due_link(flow):
    trigger = (flow or {}).get("trigger")
    return str(trigger.get("rule_due_id") or "") if isinstance(trigger, dict) else ""


def _check_due_link(c, aid, flow, before):
    """A trigger's `rule_due_id` is its due date's own routine's alone (backend/rule_due_routine.py links it): a save,
    proposal or restore that names a due date this automation is not the routine of is refused, so no automation opens
    tasks that count toward someone else's due date. A link it already had is left as it is (a rename or pause)."""
    due_id = _due_link(flow)
    if not due_id or due_id == _due_link(before):
        return
    from . import rule_due
    if not (H._has_table(c, "rule_due_dates") and rule_due.links(c, due_id, aid)):
        raise Problem("forbidden", "Only the routine a rule's due date links may name it in rule_due_id", 403)


def _save(c, actor, aid, flow, before, *, current=None, summary=None, new_version=False, via=None, audit=True):
    """Write the automation, with its audit row (`audit`: a bot's new draft has none). A change to what it does
    (trigger, steps, batch) is a new version; a rename, pause or cap is not. `current` makes that pending version the
    current one instead; `new_version` writes one even when nothing it does changed (restoring an old version)."""
    _check_due_link(c, aid, flow, before)
    now, prior = H.now(), c.execute("SELECT version FROM automations WHERE id=?", (aid,)).fetchone()
    n = prior["version"] if prior else None
    if current is not None:
        n = current
        c.execute("UPDATE automation_versions SET status='active' WHERE automation_id=? AND n=?", (aid, n))
    elif new_version or prior is None or _versioned(flow) != _versioned(before or {}):
        n = _next_n(c, aid)
        c.execute("INSERT INTO automation_versions(automation_id,n,actor,created_at,flow,status,summary) "
                  "VALUES(?,?,?,?,?,'active',?)", (aid, n, actor, now, json.dumps(_versioned(flow), sort_keys=True),
                                                    summary))
    columns = (flow["name"], flow["owner"], flow.get("group") or None, flow["trigger"]["type"], int(flow["enabled"]),
               int(flow["builtin"]), json.dumps(flow, sort_keys=True), n, now)
    # A new schedule is due at its next time after now, set here, so the time GET shows as next_run is the one the
    # scheduler runs even when its first pass comes after it.
    at = datetime.fromisoformat(now.replace("Z", "+00:00"))
    due = _iso(_due(flow["trigger"], at)) if flow["trigger"]["type"] == "schedule" else None
    if flow["trigger"]["type"] == "due":
        due = _due_text(D.start(flow["trigger"], at))
    if prior:
        # A due time stays through a rename, cap or group change, so the occurrence due now still runs; a new
        # schedule, or one turned back on, starts from the next time after now.
        again = (before or {}).get("trigger") != flow["trigger"] or (flow["enabled"] and not (before or {}).get("enabled"))
        c.execute("UPDATE automations SET name=?,owner_id=?,group_id=?,trigger_type=?,enabled=?,builtin=?,flow=?,version=?,"
                  "updated_at=?,next_due_at=CASE WHEN ? THEN ? ELSE next_due_at END WHERE id=?", (*columns, int(again), due, aid))
    else:
        # A find starts at the automation's creation, so it does not act on what came before it. A stale-PR find is the
        # exception: its clock is each PR's own activity, so the PRs already idle when it is made are its first items.
        idle = any(step.get("type") == "find" and step.get("idle_hours") for step in flow.get("steps") or [])
        c.execute("INSERT INTO automations(name,owner_id,group_id,trigger_type,enabled,builtin,flow,version,updated_at,id,"
                  "created_at,created_by_id,found_since,next_due_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (*columns, aid, now, actor, "" if idle else now, due))
    if audit:
        audit_log.write(c, actor, "automation:" + aid, before, flow, via)
    if before and before.get("enabled") and not flow["enabled"]:
        _unhold(c, aid, actor, "drop", via)   # turned off, by whoever: what it held is skipped, as a delete skips it
    source = flow.get("source")
    if before and bool(before.get("enabled")) != bool(flow["enabled"]) and isinstance(source, dict) \
            and source.get("kind") == "routine" and source.get("id"):
        # A converted routine turned on or off, by whoever: what read the routine's own switch still sees it.
        H.event(c, actor, "routine.updated", source["id"], {"enabled": bool(flow["enabled"]), "automation": aid})
    return _row(c, aid)


def remove(c, actor, row, via=None):
    """Delete an automation: what it holds is dropped, its unfinished runs are skipped (one mid-step finishes that step
    and stops there, `HubServices.on_hold`), and the delete is on record. The row stays, marked deleted."""
    aid = row["id"]
    _unhold(c, aid, actor, "drop", via)
    c.execute("UPDATE automations SET deleted_at=?, enabled=0 WHERE id=?", (H.now(), aid))
    for dropped in c.execute("SELECT id FROM automation_runs WHERE automation_id=? AND status IN "
                             "('queued','waiting','running')", (aid,)).fetchall():
        _cancel_mail(c, dropped["id"])
    c.execute("UPDATE automation_runs SET status='skipped',finished_at=? WHERE automation_id=? "
              "AND status IN ('queued','waiting','running')", (H.now(), aid))
    audit_log.write(c, actor, "automation:" + aid, flow_of(row), None, via)


def follow(c, owner, old, new):
    """A teammate moved group: its automations in the old group go with it (backend/groups.py calls this)."""
    if old == new or not old:
        return
    for row in c.execute("SELECT * FROM automations WHERE owner_id=? AND group_id=? AND deleted_at IS NULL",
                         (owner, "group:" + old)).fetchall():
        before = flow_of(row)
        flow = {k: v for k, v in before.items() if k != "group"} | ({"group": "group:" + new} if new else {})
        _save(c, H.KEEPER, row["id"], flow, before, via="group:" + (new or old))


def regroup(c, old, new):
    """A group was deleted: what is still in it or owned by it (its members' went with them, `follow`) moves to the
    group it was in; with none, the group tag goes and a group-owned automation goes to the top of the org."""
    gone = "group:" + old
    top = top_person(c)
    for row in c.execute("SELECT * FROM automations WHERE (group_id=? OR owner_id=?) AND deleted_at IS NULL",
                         (gone, gone)).fetchall():
        before = flow_of(row)
        flow = {k: v for k, v in before.items() if k != "group"}
        if before.get("group") and before["group"] != gone:
            flow["group"] = before["group"]
        elif new:
            flow["group"] = "group:" + new
        if before["owner_id"] == gone:
            flow["owner"] = "group:" + new if new else "person:" + top if top else H.KEEPER
        _save(c, H.KEEPER, row["id"], flow, before, via="group:" + (new or old))


def _propose(c, actor, aid, flow, before, summary):
    """A bot's change it may not make itself: kept as a pending version that a person with edit rights restores."""
    _check_due_link(c, aid, flow, before)
    n = _next_n(c, aid)
    c.execute("INSERT INTO automation_versions(automation_id,n,actor,created_at,flow,status,summary) "
              "VALUES(?,?,?,?,?,'pending',?)", (aid, n, actor, H.now(), json.dumps(_versioned(flow), sort_keys=True),
                                                summary))
    return n


# ---------------------------------------------------------------------------------------------------- who may
def _key(ref):
    """A person, bot or group ref as the org chart keys it (`p:`, `b:`, `g:`)."""
    kind, _, ident = F.owner_ref(ref).partition(":")
    return {"person": "p:", "bot": "b:", "group": "g:"}.get(kind, "?:") + ident


class Chart:
    """The org chart as the permission rule reads it: who is above whom, and who leads a group."""

    def __init__(self, c):
        from . import views
        self.c = c
        self.roster, self.employees = views.roster(c), views.entries(c)
        self.archived = {row["id"] for row in H.bots(c) if row.get("state") == "archived"}
        self.groups = self.roster.get("org_groups") or {}
        self.bare = True                           # a bare person id in reports_to (backend/people.py org_parent)

    def manages(self, slug):
        """Whether the bot manages its direct reports now: the team rule and its own switch (report_managers.manages)."""
        return report_managers.manages(self.c, slug)

    def up(self, key):
        head, _, rest = key.partition(":")
        if head == "g":
            group = self.groups.get(rest) or {}
            lead = self.lead(rest)
            return lead or ("p:" + group["reports_to"] if group.get("reports_to") else "")
        kind = {"p": "person", "b": "bot"}.get(head)
        return P.org_parent(kind, rest, self.roster, self.employees, self.archived, self.bare) if kind else ""

    def above(self, key):
        """Every key above this one, nearest first."""
        out, at = [], self.up(key)
        while at and at not in out and at != key:
            out.append(at)
            at = self.up(at)
        return out

    def lead(self, gid):
        """The group's lead (CONTRACT §3 `lead`), else the person it reports to."""
        from . import groups as G
        group = self.groups.get(gid) or {}
        lead = G.lead_of(gid, self.roster, self.employees) if group else ""      # a lead who left leads nothing
        if lead:
            lead = F.owner_ref(lead)
            return _key(lead if ":" in lead else "person:" + lead)
        return "p:" + group["reports_to"] if group.get("reports_to") else ""

    def leads(self, gid):
        return [lead for g in P.group_chain(gid, self.roster) for lead in [self.lead(g)] if lead]


def _managers_down(chart, me, head):
    """Whether bot `me` reaches `head` while every bot from the one above `head` up to `me`, `me` included, manages its
    reports (report_managers.manages). People and groups on the way are passed over, as they were before the switch."""
    path = chart.above(head)
    return all(chart.manages(key[2:]) for key in path[:path.index(me) + 1] if key.startswith("b:"))


def may_change(c, who, flow, chart=None):
    """The one rule for the UI, the CLI and MCP: the top of the org; the owner; anyone above the owner; the lead of the
    automation's group (or of a group it is in) and anyone above that lead. A peer is refused. Seeing follows it.
    A bot above it changes it only while the bots between manage their reports (_managers_down).
    A `readonly` built-in (one the migration shows for work Tico still runs elsewhere) is changed by nobody."""
    if flow.get("readonly"):
        return False
    if who.role == "owner":
        return True
    if who.role not in ("human", "bot"):
        return False
    me = _key("person:" + H.actor_id(who.actor) if who.role == "human" else who.actor)
    chart, owner = chart or Chart(c), _key(flow["owner"])
    gid = (flow.get("group") or "").partition(":")[2] or (owner[2:] if owner.startswith("g:") else "")
    heads = ([] if owner.startswith("g:") else [owner]) + (chart.leads(gid) if gid else [])
    return any(me == head or (me in chart.above(head) and (who.role != "bot" or _managers_down(chart, me, head)))
               for head in heads)


def may_pause(c, who, flow, can_edit=None):
    """Who may switch it on or off: a built-in (read-only or not) only the top of the org, anything else whoever may
    change it. The page shows the switch on this, so a read-only built-in still shows it to the top."""
    if flow.get("builtin") or flow.get("readonly"):
        return who.role == "owner"
    return may_change(c, who, flow) if can_edit is None else can_edit


def may_see(c, who, flow):
    """Seeing follows the change rule."""
    return may_change(c, who, {**flow, "readonly": False})


def may_reference(c, who, aid):
    """A chat message may name automation `aid` (refs.automation) when it exists and `who` may see it: the same rule as
    GET /api/v2/automations/{aid}."""
    row = _row(c, aid) if isinstance(aid, str) else None
    return bool(row) and may_see(c, who, flow_of(row))


def _seen_if_builtin(c, who, flow):
    """A built-in one is refused for what it is (409) to anyone who sees it; any other needs the change rule."""
    return may_see(c, who, flow) if flow.get("builtin") else may_change(c, who, flow)


def deletable(c, who, aid):
    """The row `who` may delete, else the refusal the delete route answers (404, 403, a built-in 409). BotOps checks
    it before it hands a person a Confirm card for a delete, so no card fails on the tap."""
    row = _row(c, aid)
    if not row:
        raise Problem("not_found", "No such automation", 404)
    if not _seen_if_builtin(c, who, flow_of(row)):
        raise Problem("forbidden", "Only its owner, the people above its owner or its group's lead may delete this "
                      "automation", 403)
    if row["builtin"]:
        raise Problem("builtin", "A built-in automation cannot be deleted; the top of the org may pause it", 409)
    return row


def _require(c, who, flow, what="change this automation"):
    if not may_change(c, who, flow):
        raise Problem("forbidden", f"Only its owner, the people above its owner or its group's lead may {what}", 403)


def _approved(c, row):
    """Whether the version the automation runs is approved (active). A draft's only version stays pending until someone
    who may set its steps' models turns it on or restores it, and a proposal waits the same way."""
    found = c.execute("SELECT status FROM automation_versions WHERE automation_id=? AND n=?",
                      (row["id"], row["version"])).fetchone()
    return bool(found) and found[0] == "active"


def _stamp_blocked(c, auth, who, flow, before=None):
    """The bot whose model a step of `flow` sets on its task, when `who` may not set that model
    (task_run_models.may_set: whoever may change the bot's model, and never a bot), else None. Only what a save adds
    or alters counts: a rename keeps the stamp it has. `before` is the approved flow whose stamps stand; None when
    nothing is approved, as for a draft, so every stamp counts."""
    kept = _stamps(before) if before else set()
    for _, bot, *_ in sorted(_stamps(flow) - kept, key=str):
        ident = H.actor_id(bot or "")
        if not task_run_models.may_set(c, auth, who, ident):
            return (H.bot(c, ident) or {}).get("slug") or ident     # the name the refusal shows
    return None


def _by_botops(who):
    """Whether BotOps makes this call: for a person or a bot it acts for (the dispatcher lends their identity, via
    botops), or with its own rights."""
    return who.via == "botops" or who.actor == BOTOPS


def _bot_draft(c, who, flow):
    """A new automation saved off, with version 1 pending, for a person with the change rule to turn on: what BotOps
    makes for a bot that may not make it, and what anyone makes that sets a model its tasks run on which they may not
    set (a bot's own call is refused, as before). Like a bot's other proposals it writes no audit row until then."""
    aid = "auto_" + H.new_id()
    row = _save(c, who.actor, aid, {**flow, "enabled": False}, None, audit=False)
    c.execute("UPDATE automation_versions SET status='pending' WHERE automation_id=? AND n=?", (aid, row["version"]))
    return {"id": aid, "pending": row["version"], "can_edit": False}


def _owner_known(c, owner):
    kind, _, ident = owner.partition(":")
    if kind == "person":
        from . import views
        return P.person(ident, views.roster(c)) is not None
    if kind == "bot":
        return H.bot(c, ident) is not None
    from . import views
    return ident in (views.roster(c).get("org_groups") or {})


def _checked(c, flow, settings=None, access=True, saved=None, aid=None, wake_steps=True):
    """The flow as saved: its shape, its owner and group on the team, and (with `access`) what it reads is its
    owner's: the inbox it hears and the Assistant it runs on. `saved` is the flow a change is made to: its steps' models
    are checked only where the change adds or alters them. `aid` is the automation it is saved as, and `wake_steps`
    False leaves a converted wake rule's step names unchecked (the redesign's own writes move rules as they were)."""
    try:
        flow = F.check(flow)
    except F.Invalid as exc:
        raise Problem("invalid", str(exc), 422)
    flow = _bot_ids(c, flow)
    if flow["trigger"]["type"] == "due":
        # Stored on the flow, so a later change to the company's zone does not move the routine.
        flow["trigger"] = {"open_days": D.OPEN_DAYS, "tz": D.zone_of({}), **flow["trigger"]}
    if flow.get("wakes"):   # a converted "What wakes" automation: only edits its wake rules can carry out
        from .redesign_migration import check_wakes
        check_wakes(flow, c if wake_steps else None, saved, aid)
    _checked_run_models(flow, _stamps(saved) if saved else frozenset())
    for ref in (flow["owner"], flow.get("group")):
        if ref and not _owner_known(c, ref):
            raise Problem("invalid", f"{ref} is not on the team", 422)
    if access:
        trigger = flow["trigger"]
        if trigger["type"] == "message" and not may_read_inbox(c, flow["owner"], trigger["inbox"], settings=settings):
            raise Problem("forbidden", f"{flow['owner']} may not read {trigger['inbox']}: a message automation hears "
                          "only its owner's own mail, Slack direct messages or a channel the owner reads", 403)
        for person in _assistants(flow["steps"]):
            if flow["owner"] != "person:" + person:
                raise Problem("forbidden", f"Only {person}'s own automation runs a turn on their Assistant", 403)
        assistant_bot = getattr(settings, "assistant_bot", None)
        for do, args in _actions(flow["steps"]):
            bot = str((args or {}).get("bot") or "")
            if do in ("wake_bot", "note") and bot and "{{" not in bot and not may_tell(c, flow["owner"], bot,
                                                                                      assistant_bot):
                raise Problem("forbidden", f"{flow['owner']} may not {do.replace('_', ' ')} {bot}: an automation "
                              "tells only its owner's own bots, bots in a group its owner leads, or the owner's "
                              "own Assistant", 403)
    return flow


BOT_ARGS = ("bot", "owner")   # action args that may name a bot (a person id or a template is kept as written)


def _bot_ids(c, flow):
    """The flow with every bot it names stored by id: its owner, a task_received trigger's `to`, bot_turn steps' bot,
    an llm step's `as` and the actions' bot/owner args. A person may write a bot's slug; a rename never breaks it."""
    def bot_ref(value, bare=False):
        text = str(value or "")
        if "{{" in text or not text:
            return value
        if text.startswith("bot:"):
            found = H.resolve_bot(c, text)
            return "bot:" + found["id"] if found else value
        if bare:
            found = H.resolve_bot(c, text)
            if found and not (found["id"] != text and H.human(c, text)):
                return found["id"]
        return value

    def steps_of(steps):
        out = []
        for step in steps or []:
            step = dict(step)
            if step.get("type") == "bot_turn" and step.get("bot"):
                bot = str(step["bot"])
                step["bot"] = bot_ref(bot) if bot.startswith("bot:") else bot_ref(bot, bare=True)
            if step.get("type") == "llm" and str(step.get("as") or "").startswith("bot:"):
                step["as"] = bot_ref(step["as"])
            if step.get("type") == "action":
                step["args"] = args_of(step.get("args"))
            if step.get("rules"):
                step["rules"] = [{**rule, "then": [{**a, "args": args_of(a.get("args"))} if a.get("args") else a
                                                   for a in rule.get("then") or []]} for rule in step["rules"]]
            if step.get("paths"):
                step["paths"] = [{**path, "steps": steps_of(path.get("steps"))} for path in step["paths"]]
            if (step.get("otherwise") or {}).get("steps"):
                step["otherwise"] = {**step["otherwise"], "steps": steps_of(step["otherwise"]["steps"])}
            out.append(step)
        return out

    def args_of(args):
        if not isinstance(args, dict):
            return args
        return {k: (bot_ref(v, bare=True) if k in BOT_ARGS and isinstance(v, str) else v) for k, v in args.items()}

    flow = {**flow, "owner": bot_ref(flow["owner"]), "steps": steps_of(flow.get("steps"))}
    trigger = flow.get("trigger") or {}
    if trigger.get("type") == "task_received" and isinstance(trigger.get("to"), str):
        to = trigger["to"]
        if to.startswith("bot:"):
            flow["trigger"] = {**trigger, "to": bot_ref(to)}
        elif ":" not in to and not H.human(c, to) and H.resolve_bot(c, to):
            flow["trigger"] = {**trigger, "to": "bot:" + H.resolve_bot(c, to)["id"]}
    return flow


def _actions(steps):
    """(do, args) of every action in the steps: action steps and filter rules', nested paths included."""
    for step in steps or []:
        if step.get("type") == "action":
            yield step.get("do"), step.get("args") or {}
        for rule in step.get("rules") or []:
            for action in rule.get("then") or []:
                yield action.get("do"), action.get("args") or {}
        for path in step.get("paths") or []:
            yield from _actions(path.get("steps"))
        yield from _actions((step.get("otherwise") or {}).get("steps"))


RUN_MODEL_KEYS = ("model", "effort", "harness")


def _run_model_steps(steps):
    """The bot_turn steps that stamp a model on the task they open (`model`, `effort`, `harness`), nested steps
    included."""
    for step in steps or []:
        if step.get("type") == "bot_turn" and any(step.get(key) for key in RUN_MODEL_KEYS):
            yield step
        for path in step.get("paths") or []:
            yield from _run_model_steps(path.get("steps"))
        yield from _run_model_steps((step.get("otherwise") or {}).get("steps"))


def _stamp(step):
    """What one stamping step sets on its task: (step id, bot, model, effort, harness)."""
    return (step.get("id"), step.get("bot"), step.get("model"), step.get("effort"), step.get("harness"))


def _stamps(flow):
    """What each stamping step sets on its task. A change to one is a change to the model its task runs on, so the
    rights are checked on the ones a save adds or alters."""
    return {_stamp(step) for step in _run_model_steps(flow.get("steps"))}


def _checked_run_models(flow, kept=frozenset()):
    """Each step's model, effort and harness are checked the way a bot's task model is (task_models.choice_of): a model
    the catalog has and that is not retired, a harness that runs it, an effort it takes. An effort or harness needs a
    model to be picked for. A stamp the saved flow already has (`kept`) is not checked again: its model may have been
    retired or taken off the catalog since, and the routine still pauses, renames and saves its other changes."""
    for step in _run_model_steps(flow.get("steps")):
        if _stamp(step) in kept:
            continue
        where = f"Step {step.get('id')}"
        if not step.get("model"):
            raise Problem("invalid", f"{where} needs a model to pick an effort or harness for it", 422)
        try:
            task_models.choice_of(step["model"], step.get("effort"), step.get("harness"))
        except Problem as exc:
            raise Problem("invalid", f"{where}: {exc.detail}", 422)


def _finds(steps):
    """Whether the steps hold a `find` anywhere."""
    return any(step.get("type") == "find" or any(_finds(p.get("steps")) for p in step.get("paths") or [])
               or _finds((step.get("otherwise") or {}).get("steps")) for step in steps or [])


def may_tell(c, owner, bot, assistant_bot=None):
    """Whether an automation of `owner` may wake or leave a note for `bot`: a bot its owner's person operates (and the
    owner itself), a bot in a group the owner leads or one nested under it, or (a person's own) the Assistant."""
    from . import automation_actions as AA
    bot = AA.R._slug(bot)
    bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
    found = H.bot(c, bot) if bot else None
    if not found:
        return False
    kind, _, ident = F.owner_ref(owner).partition(":")
    if kind == "bot" and ident == bot:
        return True
    if bot == assistant_bot and kind == "person":
        return bool(H.human(c, ident))
    person = owner_person(c, owner)
    row = c.execute("SELECT owner_id FROM bot_config WHERE bot=?", (bot,)).fetchone()
    if person and row and row["owner_id"] == person:
        return True
    chart, key = Chart(c), _key(F.owner_ref(owner))
    led = [gid for gid in chart.groups if chart.lead(gid) == key]
    if not led:
        return False
    team = P.team_of(bot, chart.employees, chart.roster)
    return any(team in P.group_and_below(gid, chart.roster) for gid in led)


def _assistants(steps):
    """The people whose Assistant an llm step names, nested paths included."""
    for step in steps or []:
        if step.get("type") == "llm" and str(step.get("as", "")).startswith("assistant:"):
            yield step["as"].partition(":")[2]
        for path in step.get("paths") or []:
            yield from _assistants(path.get("steps"))
        yield from _assistants((step.get("otherwise") or {}).get("steps"))


# ---------------------------------------------------------------------------------------------------- what an owner reads
def owner_person(c, owner):
    """The person an owner stands for: a person, or a bot's operator. A group stands for no one."""
    kind, _, ident = F.owner_ref(owner).partition(":")
    if kind == "bot":
        row = c.execute("SELECT owner_id FROM bot_config WHERE bot=?", (ident,)).fetchone()
        ident = row["owner_id"] if row and row["owner_id"] else ""
    elif kind != "person":
        return None
    return ident if ident and H.human(c, ident) else None


def owner_mailboxes(c, owner):
    """The mailboxes the owner's person reads: their message bot's declared mailbox and their own email."""
    from . import routines
    person = owner_person(c, owner)
    if not person:
        return []
    roster = routines._roster(c)
    found = P.person(person, roster) or {}
    boxes = [b["address"] for b in routines.message_bot_mailboxes(c, roster) if b["person_id"] == person]
    email = str(found.get("email") or "").strip().lower()
    return list(dict.fromkeys(boxes + ([email] if "@" in email else [])))


def _channel_readable(c, owner, name, settings):
    """A Slack channel on the team's list (Settings > Tools > Slack); a bot owner must also be one of its readers."""
    from . import slack_channels as SC
    rows = SC.listing(c, settings) if settings is not None else [SC._entry(r, "app") for r in SC._stored(c)]
    entry = next((r for r in rows if str(r.get("name") or "").lstrip("#").lower() == name), None)
    if not entry:
        return False
    kind, _, ident = F.owner_ref(owner).partition(":")
    return kind != "bot" or ident in (entry.get("readers") or [])


def may_read_inbox(c, owner, inbox, *, sender=None, settings=None):
    """Whether an automation of `owner` may hear `inbox`: an email address only when the owner is that mailbox's
    person (or a bot that person operates), `slack:dm` only the owner's own direct messages (`sender`, when a message
    is in hand), `slack:#<channel>` a channel the owner reads."""
    inbox = str(inbox or "").strip().lower()
    if "@" in inbox:
        return inbox in owner_mailboxes(c, owner)
    if inbox == "slack:dm":
        person = owner_person(c, owner)
        return bool(person) and (sender is None or F.owner_ref(sender) == "person:" + person)
    if inbox.startswith("slack:#"):
        return _channel_readable(c, owner, inbox[len("slack:#"):], settings)
    return False


# ---------------------------------------------------------------------------------------------------- built-ins
# The automations Tico ships, made by the hub on the scheduler's pass (its first runs at startup) once the redesign is
# on, never by POST. Each is {key, name, owner, trigger, steps}. The first is `What wakes bots`, owned by the top of the
# org: the wake rules rules.py keeps in code, shown read-only (`readonly`: the engine never runs it, and pausing it
# leaves those rules working). The system jobs move here with their migration; until then the old jobs run as they do.
# With a Librarian on the team, the market's two: `Market briefing` (daily at 07:00 team time: the Librarian's turn
# writes it and `post_update` posts it to Market and Updates) and one `Market listening` per destination the Librarian
# receives (each match is a Librarian turn, which changes the graph through the market routes). The top of the org
# pauses them; nobody deletes them. Each stands down while the team's own Librarian automation does the same work
# (`_stands_down`), and the briefing starts paused while a Librarian briefing routine still runs (the converter adopts
# that routine into it).
BUILTINS = ()
LIBRARIAN = "librarian"
BRIEFING_KEY, LISTENING_KEY = "market_briefing", "market_listening_"
BRIEFING_PROMPT = ("Write today's market briefing from the graph: what changed since yesterday, what listening heard and "
                   "which bets it touches. Start with a one-line title; then one bullet per point.")
LISTENING_PROMPT = ("A listening match for Market ({{listening.destination}}): {{listening.url}}\n\n{{listening.content}}"
                    "\n\nIf it changes what we know about the market, file it as evidence and change the graph through "
                    "the market routes, citing it. If it changes nothing, say so and stop.")


def briefing_flow(cron="0 7 * * *", tz=None, prompt=BRIEFING_PROMPT):
    """`Market briefing`: the Librarian's turn writes it; the run posts what it wrote to Market and Updates."""
    from .redesign_migration import _zone
    return {"name": "Market briefing", "owner": "bot:" + LIBRARIAN,
            "trigger": {"type": "schedule", "cron": cron, "tz": tz or _zone()},
            "steps": [{"id": "brief", "type": "bot_turn", "bot": "bot:" + LIBRARIAN, "prompt": prompt},
                      {"id": "post", "type": "action", "do": "post_update", "args": {"to": ["market", "updates"]}}]}


def is_briefing_routine(row):
    """A Librarian routine that writes the market briefing on a schedule: the built-in does that now, so the
    converter adopts it (backend/redesign_migration.py) and, while it is live, the built-in starts paused."""
    words = f"{row['title'] or ''} {row['routine_key'] or ''}".lower()
    return row["bot"] == LIBRARIAN and not row["event_name"] and "briefing" in words


def _market_builtins(c, settings=None):
    """The Librarian's built-ins; `settings` adds the destinations listening.yaml names to the ones saved here."""
    found = H.bot(c, LIBRARIAN)
    if not found or found.get("state") == "archived":
        return []
    live = any(is_briefing_routine(r) for r in c.execute(
        "SELECT bot, title, routine_key, event_name FROM schedules WHERE bot=? AND deleted_at IS NULL", (LIBRARIAN,)))
    out = [{**briefing_flow(), "key": BRIEFING_KEY, "enabled": not live}]
    from . import listening
    dests = listening.destinations(settings, c, check_questions=False) if settings is not None else listening.stored(c)
    for key in sorted(k for k, cfg in dests.items() if cfg.get("receiver") == "bot:" + LIBRARIAN):
        out.append({"key": LISTENING_KEY + key, "name": f"Market listening · {key}"[:120], "owner": "bot:" + LIBRARIAN,
                    "trigger": {"type": "listening", "destination": key},
                    "steps": [{"id": "update", "type": "bot_turn", "bot": "bot:" + LIBRARIAN, "prompt": LISTENING_PROMPT}]})
    return out


def _posts_market(steps):
    """Whether a step (nested paths included) posts to Market."""
    for step in steps or []:
        to = (step.get("args") or {}).get("to") if step.get("do") == "post_update" else None
        if to == "market" or (isinstance(to, list) and "market" in to):
            return True
        if any(_posts_market(p.get("steps")) for p in step.get("paths") or []) \
                or _posts_market((step.get("otherwise") or {}).get("steps")):
            return True
    return False


def _stands_down(c, aid, trigger):
    """A market built-in does not run while the team's own Librarian automation does its work: one on the same
    listening destination, or (the briefing) one that posts to Market. One turn per match, one briefing a day."""
    if aid == "auto_builtin_" + BRIEFING_KEY:
        same = lambda flow: _posts_market(flow["steps"])   # noqa: E731
    elif aid.startswith("auto_builtin_" + LISTENING_KEY):
        same = lambda flow: flow["trigger"].get("destination") == trigger.get("destination")   # noqa: E731
    else:
        return False
    return any(same(flow_of(r)) for r in c.execute(
        "SELECT flow FROM automations WHERE builtin=0 AND enabled=1 AND deleted_at IS NULL AND owner_id=? "
        "AND trigger_type=?", ("bot:" + LIBRARIAN, trigger["type"])))


def top_person(c):
    """The top of the org: the stored owner's person, else the roster's default person."""
    from .access import OWNER
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (OWNER,)).fetchone()
    try:
        email = str(json.loads(row[0]).get("email") or "").strip().lower() if row else ""
    except (TypeError, ValueError):
        email = ""
    person = c.execute("SELECT id FROM humans WHERE lower(email)=?", (email,)).fetchone() if email else None
    return person["id"] if person else H.default_human(c)


def _catalog(c, settings=None):
    from .redesign_migration import WAKES_BUILTIN, wakes_builtin
    top = top_person(c)
    wakes = [{**wakes_builtin("person:" + top), "key": WAKES_BUILTIN[len("auto_builtin_"):]}] if top else []
    return [*wakes, *BUILTINS, *_market_builtins(c, settings)]


def ensure_builtins(c, catalog=None, settings=None):
    """Make each built-in that does not exist yet; one already made is left as its people changed it."""
    made = []
    for spec in _catalog(c, settings) if catalog is None else catalog:
        aid = "auto_builtin_" + spec["key"]
        if c.execute("SELECT 1 FROM automations WHERE id=?", (aid,)).fetchone():
            continue
        flow = F.check({**{k: v for k, v in spec.items() if k != "key"}, "builtin": True,
                        "enabled": spec.get("enabled", True)})
        _save(c, H.KEEPER, aid, flow, None)
        made.append(aid)
    return made


# ---------------------------------------------------------------------------------------------------- triggers
def _count(c, aid, field):
    c.execute(f"INSERT INTO automation_daily(automation_id,day,{field}) VALUES(?,?,1) "
              f"ON CONFLICT(automation_id,day) DO UPDATE SET {field}={field}+1", (aid, H.now()[:10]))


def _ring(c):
    if hasattr(c, "after_commit"):
        c.after_commit.append(_BELL.set)
    else:
        _BELL.set()


def _guard(c, row):
    """The backstop for loops the chain cannot see (a webhook that calls back with a new id each time, a mail rule
    answered by mail): the rule that holds now, with what it counted, or None. It counts the runs already started,
    before the new one is added, so the run past each count is the one held: `flood`, more than RUN_GUARD_BURST in the
    last RUN_GUARD_MINUTES; `sustained`, more than RUN_GUARD_SUSTAINED in the last RUN_GUARD_SPAN minutes, the ones
    before it falling in at least RUN_GUARD_SPREAD distinct minutes of it (a loop keeps going over time; a backlog lands
    in a minute or two and does not spread). Exact repeats are not counted here: redelivery is deduped per source, and a
    bot's own writes are stopped by the chain. Manual runs are not counted. Only runs since its last hold was cleared
    count, so runs a person released do not put it straight back on hold."""
    now = H.now()
    last = c.execute("SELECT cleared_at FROM automation_guard_holds WHERE automation_id=?", (row["id"],)).fetchone()
    floor = (last and last["cleared_at"]) or ""
    flood_since = max(H.shift(now, minutes=-RUN_GUARD_MINUTES), floor)
    span_since = max(H.shift(now, minutes=-RUN_GUARD_SPAN), floor)
    flood = c.execute("SELECT count(*) FROM automation_runs WHERE automation_id=? AND started_at>=? "
                      "AND trigger_ref<>'manual'", (row["id"], flood_since)).fetchone()[0]
    span, minutes = c.execute("SELECT count(*), count(DISTINCT substr(started_at,1,16)) FROM automation_runs "
                              "WHERE automation_id=? AND started_at>=? AND trigger_ref<>'manual'",
                              (row["id"], span_since)).fetchone()
    rule = ("flood" if flood >= RUN_GUARD_BURST else
            "sustained" if span >= RUN_GUARD_SUSTAINED and minutes >= RUN_GUARD_SPREAD else "")
    return (rule, {"runs": span, "minutes": minutes, "flood": flood}) if rule else None


def _hold_row(c, aid):
    return c.execute("SELECT * FROM automation_guard_holds WHERE automation_id=? AND cleared_at IS NULL", (aid,)).fetchone()


# Where a run goes when it can run again (a wake comes, a turn answers, a step is ready, a cut-off run resumes):
# `queued`, or `held` while its automation is on hold, its state kept so a release resumes it where it was.
RUNNABLE = ("CASE WHEN EXISTS(SELECT 1 FROM automation_guard_holds g WHERE g.automation_id=automation_runs.automation_id "
            "AND g.cleared_at IS NULL) THEN 'held' ELSE 'queued' END")


def _runnable(c, aid):
    return "held" if _hold_row(c, aid) else "queued"


def hold_of(c, aid):
    """The automation's live hold as its readers get it ({rule, since, held, dropped, task}), or None."""
    row = _hold_row(c, aid)
    if not row:
        return None
    held = c.execute("SELECT count(*) FROM automation_runs WHERE status='held' AND automation_id=?", (aid,)).fetchone()[0]
    return {"rule": row["rule"], "since": row["since"], "held": held, "dropped": row["dropped"], "task": row["task_id"]}


def _hold(c, row, rule):
    """Put the automation on hold: what it has queued is held now, so a loop stops spending at once."""
    c.execute("INSERT INTO automation_guard_holds(automation_id,rule,held_at) VALUES(?,?,?) ON CONFLICT(automation_id) "
              "DO UPDATE SET rule=excluded.rule, held_at=excluded.held_at, arrivals=0, dropped=0, task_id=NULL, "
              "cleared_at=NULL",
              (row["id"], rule, H.now()))
    c.execute("UPDATE automation_runs SET status='held' WHERE automation_id=? AND status='queued'", (row["id"],))


def _hold_told(c, row, rule, counted, held):
    """Once a hold: a task in its owner's Needs you (closed when the hold ends) and a notice to the Health Monitor."""
    flow = flow_of(row)
    why = {"flood": f"it queued {RUN_GUARD_BURST} runs in {RUN_GUARD_MINUTES} minutes",
           "sustained": f"it kept queuing runs for {RUN_GUARD_SPAN} minutes"}[rule]
    words = (f"Automation {flow['name']} is on hold: {why}. That is most often a loop. {held} runs are waiting. "
             "Release them if this was real work, or drop them, after fixing what starts it.")
    refs = {"kind": "run_guard", "automation": row["id"]}
    H.event(c, H.KEEPER, "automation.run_guard", row["id"], {"rule": rule, **counted, "held": held})
    lead = A.acts_as(c, {"flow": flow})
    person = owner_person(c, flow["owner"]) or (lead[6:] if lead.startswith("human:") else "")
    if person:
        task = H.task_create(c, H.KEEPER, f"Automation {flow['name']} on hold"[:300], words, "human:" + person,
                             lint=False, deduplicate=False, wake_refs=refs)
        c.execute("UPDATE automation_guard_holds SET task_id=? WHERE automation_id=?", (task["id"], row["id"]))
    from . import health_monitor
    if health_monitor.active(c):   # the optional Health Monitor, when it is on
        H.notice(c, H.KEEPER, "bot:" + health_monitor.SLUG, words, refs=refs)


def _unhold(c, aid, actor, how, via=None):
    """End the hold: its held runs go back to the queue (`release`) or are skipped (`drop`, and a turn off or delete),
    its Needs-you task is closed, and the change is on record. Returns how many runs it moved, or None with no hold."""
    hold = _hold_row(c, aid)
    if not hold:
        return None
    now = H.now()
    if how == "release":
        n = c.execute("UPDATE automation_runs SET status='queued' WHERE automation_id=? AND status='held'", (aid,)).rowcount
        _ring(c)
    else:
        # A run mid-step is dropped too: it finishes that step (`HubServices.on_hold`) and nothing after it runs.
        for run in c.execute("SELECT id FROM automation_runs WHERE automation_id=? AND status IN ('held','running')",
                             (aid,)).fetchall():
            _cancel_mail(c, run["id"])
        n = c.execute("UPDATE automation_runs SET status='skipped',finished_at=? WHERE automation_id=? "
                      "AND status IN ('held','running')", (now, aid)).rowcount
    c.execute("UPDATE automation_guard_holds SET cleared_at=? WHERE automation_id=?", (now, aid))
    task = H.task(c, hold["task_id"]) if hold["task_id"] else None
    if task and task["status"] not in ("closed", "done", "declined"):
        H.task_close(c, H.KEEPER, task["id"], note="Released." if how == "release" else "Dropped.", quiet=True)
    before = {"hold": {"rule": hold["rule"], "held_at": hold["held_at"], "dropped": hold["dropped"]}}
    H.event(c, actor, "automation.hold_" + ("released" if how == "release" else "dropped"), aid,
            {"runs": n, "rule": hold["rule"], "dropped": hold["dropped"]})
    audit_log.write(c, actor, "automation:" + aid, before, {"hold": None, how: n}, via)
    return n


def _absorbed(c, row):
    """A schedule keeps the routine's one-open-run rule (CONTRACT section 4, Overlap), converted or made new: an
    occurrence while the task its last run opened is unfinished starts no run, so hours missed while its bot was held
    arrive as one turn, not one each. It counts as dropped and its owner bot is told, as the routine told it
    (backend/scheduler.py), so the open task is never left in silence. A schedule that finds works on the tasks it
    found, not one of its own, and runs one at a time instead (`queue`)."""
    flow = flow_of(row)
    if (flow.get("trigger") or {}).get("type") != "schedule" or _finds(flow["steps"]):
        return False
    last = c.execute("SELECT json_extract(refs,'$.task') AS task FROM messages "
                     "WHERE json_extract(refs,'$.automation')=? ORDER BY created_at DESC LIMIT 1", (row["id"],)).fetchone()
    open_task = c.execute("SELECT id FROM tasks WHERE id=? AND status IN ('open','doing','waiting','review','ready')",
                          (last["task"],)).fetchone() if last and last["task"] else None
    if not open_task:
        return False
    _count(c, row["id"], "dropped")
    owner = str(flow.get("owner") or "")
    if owner.startswith("bot:"):
        H.notice(c, H.KEEPER, owner, f"Scheduled occurrence {H.now()[:16].replace('T', ' ')}Z of {flow.get('name')} "
                                     "absorbed into this open task; continue it or mark it done.",
                 refs={"task": open_task["id"], "wake": "occurrence"})
    return True


def _bot_away(c, row):
    """Why a schedule owned by a bot that is not active (paused, being set up) is passed over until it is back, as the
    routine scheduler passed over its routines, or None."""
    owner = flow_of(row).get("owner") or ""
    found = H.bot(c, owner[4:]) if owner.startswith("bot:") else None
    return f"its bot is {found.get('state')}" if found and found.get("state") != "active" else None


def _no_input(c, row, settings, at, zone):
    """Why a converted routine's scheduled run would have nothing to work on (backend/routines.py `no_input`: the
    Strategy Bot's KPI pass with the regular checks off, the Librarian's curate run with nothing new), or None."""
    flow = flow_of(row)
    source = flow.get("source")
    if not isinstance(source, dict) or source.get("kind") != "routine" or settings is None:
        return None
    schedule = c.execute("SELECT * FROM schedules WHERE id=?", (source.get("id"),)).fetchone()
    if not schedule:
        return None
    # Its words are the automation's now: a team that rewrote them (or added steps) gave it other work.
    steps = flow.get("steps") or []
    words = steps[0].get("prompt") if len(steps) == 1 and steps[0].get("type") == "bot_turn" else None
    from . import routines
    try:
        return routines.no_input(c, settings, {**dict(schedule), "playbook": words}, at.astimezone(ZoneInfo(zone)))
    except Exception:  # noqa: BLE001 - a check that fails lets the run go ahead
        log.exception("no_input check failed for %s", row["id"])
        return None


def queue(c, row, payload, ref="", *, manual=False, chain=()):
    """A queued run of this automation's current version, or None: an automation with a `find` runs one at a time (an
    occurrence while one is unfinished is passed over, counted as dropped), a schedule absorbs an occurrence
    while its last task is open (`_absorbed`), and on hold (the run guard's, `_guard`) the run is kept as `held`, the
    one that trips it included; past RUN_GUARD_BURST runs since the hold started it is dropped (the queued runs moved
    to held when it started do not count). A manual run never trips the guard and is refused while on hold."""
    if (not manual and _finds(flow_of(row)["steps"]) and c.execute(
            "SELECT 1 FROM automation_runs WHERE automation_id=? AND status IN ('queued','running','waiting','held') "
            "LIMIT 1", (row["id"],)).fetchone()):
        _count(c, row["id"], "dropped")
        return None
    if not manual and _absorbed(c, row):
        return None
    if str(ref).startswith("due:") and c.execute(
            "SELECT 1 FROM automation_runs WHERE automation_id=? AND trigger_ref=? AND status!='failed' LIMIT 1",
            (row["id"], str(ref)[:200])).fetchone():
        # One due day, one run: a repeated or concurrent pass for the same day starts nothing new.
        return None
    if str(ref).startswith("slack:") and c.execute(
            "SELECT 1 FROM automation_runs WHERE automation_id=? AND trigger_ref=? LIMIT 1",
            (row["id"], str(ref)[:200])).fetchone():
        # One Slack message, one run: a channel message offered once may come back as an `@Tico` event.
        return None
    held = _hold_row(c, row["id"])
    if held and manual:
        return None
    tripped = None if held or manual else _guard(c, row)
    if tripped:
        _hold(c, row, tripped[0])
    on_hold = bool(held or tripped)
    arrivals = held["arrivals"] if held else 0
    rid = None
    if on_hold and arrivals >= RUN_GUARD_BURST:
        _count(c, row["id"], "dropped")
        c.execute("UPDATE automation_guard_holds SET dropped=dropped+1 WHERE automation_id=?", (row["id"],))
    else:
        rid = "run_" + H.new_id()
        state = E.start(payload, manual=manual)
        c.execute("INSERT INTO automation_runs(id,automation_id,version,trigger_ref,started_at,status,saved_state,chain) "
                  "VALUES(?,?,?,?,?,?,?,?)", (rid, row["id"], row["version"], str(ref or "")[:200], H.now(),
                                              "held" if on_hold else "queued", json.dumps(state, default=str),
                                              json.dumps(list(chain))))
        _count(c, row["id"], "runs")
        if on_hold:
            c.execute("UPDATE automation_guard_holds SET arrivals=arrivals+1 WHERE automation_id=?", (row["id"],))
        else:
            _ring(c)
    if tripped:
        _hold_told(c, row, tripped[0], tripped[1], hold_of(c, row["id"])["held"])
    return rid


def _turn_chain(c):
    """The chain of the bot turn this write comes from, when an automation's notice or task started it: what the
    automation wrote carries `automation_chain` in its refs, and the turn's writes inherit it."""
    attempt = H.RUN.get()
    return _attempt_chain(c, attempt) if attempt else ()


def _attempt_chain(c, attempt):
    """The chain on the message that started this attempt's job (empty when no automation started the turn)."""
    found = c.execute("SELECT m.refs FROM attempts a JOIN jobs j ON j.id=a.job_id JOIN messages m ON m.id=j.message_id "
                      "WHERE a.id=?", (attempt,)).fetchone()
    try:
        return tuple((json.loads(found[0] or "{}") if found else {}).get("automation_chain") or ())
    except (TypeError, ValueError):
        return ()


@contextlib.contextmanager
def acting_for(chain):
    """What a runner does for a turn (its claim, start and end) acts with the chain of the automation that started the
    turn, as the turn's own writes do, so the change it makes (a task's state, say) cannot start that automation again.
    Without it, a turn that moves its own task re-runs the automation on task.state_changed, and each run's turn does the
    same. An empty chain changes nothing."""
    token = CHAIN.set(tuple(chain) or CHAIN.get())
    try:
        yield
    finally:
        CHAIN.reset(token)


def for_attempt(c, attempt):
    """The chain of the attempt's turn, for `acting_for` around the runner's start or end of it."""
    return acting_for(_attempt_chain(c, attempt))


def turn_source(c):
    """The automation run behind the bot turn this write comes from (`H.RUN`), as a market change names it:
    {turn: {attempt, bot, task}, automation, run, match}; `match` is the listening post that started the run, when
    one did. None for a write no automation's turn made."""
    attempt = H.RUN.get()
    found = c.execute("SELECT a.bot_id, m.refs FROM attempts a JOIN jobs j ON j.id=a.job_id "
                      "JOIN messages m ON m.id=j.message_id WHERE a.id=?", (attempt,)).fetchone() if attempt else None
    try:
        refs = json.loads(found["refs"] or "{}") if found else {}
    except (TypeError, ValueError):
        refs = {}
    run = c.execute("SELECT automation_id, saved_state FROM automation_runs WHERE id=?",
                    (refs.get("automation_run"),)).fetchone() if refs.get("automation_run") else None
    if not run:
        return None
    out = {"turn": {"attempt": attempt, "bot": found["bot_id"], "task": refs.get("task")},
           "automation": run["automation_id"], "run": refs["automation_run"]}
    try:
        heard = (json.loads(run["state"] or "{}").get("payload") or {}).get("listening")
    except (TypeError, ValueError, AttributeError):
        heard = None
    if isinstance(heard, dict):
        out["match"] = {key: heard.get(key) for key in ("id", "destination", "url", "source")}
    return out


def fire(c, kind, payload, ref="", match=None, access=None, only=None):
    """Queue a run of every enabled automation on a `kind` trigger (or the one `only`) that `match(trigger)` takes,
    that may read what arrived (`access(row)`, its owner's rights now) and whose trigger filters hold; one dropped
    counts toward its day. Returns the queued run ids. Never raises: an automation problem must not undo the write
    that triggered it."""
    try:
        chain = CHAIN.get() or _turn_chain(c)
        if len(chain) >= MAX_CHAIN:
            return []
        queued = []
        for row in c.execute("SELECT * FROM automations WHERE deleted_at IS NULL AND enabled=1 AND trigger_type=? "
                             + ("AND id=? " if only else "") + "ORDER BY created_at",
                             (kind, *((only,) if only else ()))).fetchall():
            row = dict(row)
            if row["id"] in chain:
                continue
            flow = flow_of(row)
            if flow.get("readonly"):   # a built-in that shows work Tico runs itself
                continue
            trigger = flow["trigger"]
            if match and not match(trigger):
                continue
            if row["builtin"] and _stands_down(c, row["id"], trigger):
                continue
            if (access and not access(row)) or not F.all_hold(trigger.get("filters") or [], payload):
                _count(c, row["id"], "dropped")
                continue
            rid = queue(c, row, payload, ref, chain=(*chain, row["id"]))
            if rid:
                queued.append(rid)
        return queued
    except Exception:  # noqa: BLE001 - logged, never raised into the trigger's write
        import logging
        logging.getLogger(__name__).warning("automation trigger %s failed", kind, exc_info=True)
        return []


def hub_checks(c, task):
    """Hook point for the task flow's own checks before a task_received automation runs (CONTRACT §2). Nothing yet:
    every task passes."""
    return True


def on_event(c, type_, subject, data, actor):
    """A catalog event (backend/event_catalog.py): `event` automations on it, and `task_received` on a task that now
    has an owner."""
    from . import event_catalog as CAT
    name = CAT.canonical(type_) or type_
    facts = data() if callable(data) else (data or {})
    payload = {"event": {"name": name, "subject": subject, "actor": actor, **({"data": facts} if facts else {})}}
    # A trigger may name an event by an alias (a converted routine's `release_published`): it hears the canonical name.
    fire(c, "event", payload, f"{name}:{subject}",
         lambda t: CAT.matches(t["name"], name) or (CAT.canonical(t["name"]) or t["name"]) == name)
    if name in ("task.created", "task.assigned") and isinstance(facts, dict) and facts.get("owner_id"):
        task = H.task(c, subject)
        if task and not H.task_private(c, task) and hub_checks(c, task):
            # task.assigned is told before the owner column is written: the new owner is the event's.
            task = _received_task(task, facts["owner_id"])
            owner = F.owner_ref(facts["owner_id"])
            fire(c, "task_received", {"task": task, "event": {"name": name}}, f"task:{subject}:{name}",
                 lambda t: F.owner_ref(t["to"]) == owner)


def _received_task(task, owner):
    """The task as a `task_received` automation hears it, keyed by the names its flow reads. The row's columns are read
    under this code's names, so a column renamed since the row was read still fills its field."""
    fields = ("id", "title", "body", "status", "requester", "step_id", "type")
    return {**{k: task.get(renames.current_column("tasks", k)) for k in fields}, "owner": owner}


def on_mail(c, mailbox, message):
    """A new message in a person's mailbox (the mail connector's copy), for the automations of that mailbox's
    person: whose it is is checked again now, since a person may have lost it since the save."""
    attachments = [a.get("name") if isinstance(a, dict) else str(a) for a in getattr(message, "attachments", None) or []]
    payload = {"message": {"source": "mail", "mailbox": mailbox, "id": message.msg_id, "thread": message.thread_id,
                           "from": message.from_addr, "from_name": getattr(message, "from_header", "") or "",
                           "to": list(message.to or []), "cc": list(getattr(message, "cc", None) or []),
                           "subject": message.subject, "snippet": message.snippet, "body": (message.body or "")[:8000],
                           "labels": list(message.labels or []), "list_id": message.list_id,
                           "unsubscribe": bool(getattr(message, "has_unsubscribe", False)),
                           "attachments": [a for a in attachments if a],
                           "internal": bool(message.is_internal), "date": message.date}}
    return fire(c, "message", payload, f"mail:{mailbox}:{message.msg_id}",
                lambda t: str(t["inbox"]).lower() == str(mailbox).lower(),
                access=lambda row: may_read_inbox(c, row["owner_id"], mailbox))


def on_slack(c, event, person, channel=None, settings=None):
    """A Slack message the gateway let in: queued for a `message` automation on its inbox, `slack:dm` for a direct
    message (the owner's own only) or `slack:#<channel>` (a channel the owner reads). Returns the run ids; any means
    the automation handles it and the gateway's own routing does not."""
    channel = channel or {}
    dm = event.get("channel_kind") == "im" or channel.get("kind") == "im"
    inbox = "slack:dm" if dm else "slack:#" + str(channel.get("name") or event.get("channel") or "").lstrip("#").lower()
    payload = {"message": {"source": "slack", "inbox": inbox, "channel": str(event.get("channel") or ""),
                           "ts": event.get("ts"), "thread_ts": event.get("thread_ts") or event.get("ts"),
                           "from": "person:" + person["id"], "text": str(event.get("text") or "")[:8000]}}
    return fire(c, "message", payload, f"slack:{event.get('channel')}:{event.get('ts')}",
                lambda t: str(t["inbox"]).lower() == inbox,
                access=lambda row: may_read_inbox(c, row["owner_id"], inbox, sender="person:" + person["id"],
                                                  settings=settings))


def on_webhook(c, trigger_id, name, type_, delivery, payload, content):
    """A call to an incoming trigger (backend/triggers.py), after its secret, dedupe and rate limit."""
    queued = fire(c, "webhook", {"webhook": {"id": trigger_id, "name": name, "type": type_, "delivery": delivery,
                                             "data": payload, "content": content}},
                  f"webhook:{trigger_id}:{delivery}", lambda t: t["id"] == trigger_id)
    held = {r[0] for r in c.execute("SELECT id FROM automation_runs WHERE status='held' AND id IN "
                                    "(SELECT value FROM json_each(?))", (json.dumps(queued),))} if queued else set()
    return [{"outcome": "automation", "target": rid, "ok": True, "detail": "run held" if rid in held else "run queued"}
            for rid in queued]


def on_listening(c, intake, post):
    """A listening post routed to a destination (backend/listening.py `route`)."""
    payload = {"listening": {"id": intake["id"], "destination": intake["destination"], "url": post.get("url"),
                             "author": post.get("author"), "content": str(post.get("content") or "")[:8000],
                             "source": post.get("source")}}
    return fire(c, "listening", payload, f"listening:{intake['id']}", lambda t: t["destination"] == intake["destination"])


def turn_done(c, attempt, outcome, text):
    """A bot's turn ended (after `H.turn_finish` priced it). The run that started it (its notice carries
    `automation_run`) is charged its cost, which counts toward the spend cap, and a run waiting on it takes its answer
    and is queued. A run being worked now finds the answer when it is saved."""
    job = c.execute("SELECT message_id FROM jobs WHERE id=?", (attempt["job_id"],)).fetchone() if attempt["job_id"] else None
    msg = H.message(c, job["message_id"]) if job and job["message_id"] else None
    refs = (msg or {}).get("refs") or {}
    rid = refs.get("automation_run")
    run = c.execute("SELECT * FROM automation_runs WHERE id=?", (rid,)).fetchone() if rid else None
    if not run:
        return
    # A turn the run started is its task's; a notice (wake_bot, note) is its message.
    ref = refs.get("task") if refs.get("automation_step") else msg["id"]
    priced = c.execute("SELECT est_cost_usd FROM turns WHERE id=?", (attempt["id"],)).fetchone()
    cost, ok = float((priced and priced[0]) or 0), outcome == "completed"
    if run["status"] == "running" or (run["status"] == "skipped" and run["claimed_at"]):
        # Still being worked (a run dropped mid-step is too, until its step is saved): the save takes the answer.
        c.execute("INSERT OR REPLACE INTO automation_turn_answers(run_id,ref,text,cost,ok,created_at) VALUES(?,?,?,?,?,?)",
                  (rid, ref, text or "", cost, int(ok), H.now()))
        return
    state = json.loads(run["state"]) if run["state"] else None
    c.execute("DELETE FROM automation_holds WHERE ref=?", (ref,))
    if state is None:
        c.execute("UPDATE automation_runs SET cost=round(cost+?,6) WHERE id=?", (cost, rid))
        return
    answered = _take(state, ref, text, cost, ok)
    status = (_runnable(c, run["automation_id"]) if answered and run["status"] in ("waiting", "queued")
              else run["status"])
    c.execute("UPDATE automation_runs SET status=?,saved_state=?,cost=?,wake_at=CASE WHEN ? IN ('queued','held') "
              "THEN NULL ELSE wake_at END WHERE id=?", (status, json.dumps(state, default=str), state["cost"], status, rid))
    if status == "queued":
        _ring(c)


def _take_parked(c, rid, state):
    """Turns that ended while the run was being worked: their answers and costs on its state, their spend holds
    released."""
    for answer in c.execute("SELECT * FROM automation_turn_answers WHERE run_id=? ORDER BY created_at", (rid,)).fetchall():
        if state.get("threads") is not None:
            _take(state, answer["ref"], answer["text"], answer["cost"], bool(answer["ok"]))
        else:
            state["cost"] = round(state.get("cost", 0) + answer["cost"], 6)
        c.execute("DELETE FROM automation_holds WHERE ref=?", (answer["ref"],))
    c.execute("DELETE FROM automation_turn_answers WHERE run_id=?", (rid,))


def _take(state, ref, text, cost, ok):
    """A turn's end on the run's state: the answer of a waiting step, else the cost of a step it started, else the
    cost on the run. Returns whether a waiting step took it."""
    if E.answer(state, ref, text or "", cost, ok=ok):
        return True
    if not E.charge(state, ref, cost):
        state["cost"] = round(state.get("cost", 0) + cost, 6)
    return False


# ---------------------------------------------------------------------------------------------------- running
def _next_batch(flow, now):
    zone = ZoneInfo((flow.get("batch") or {}).get("tz") or "UTC")
    local = now.astimezone(zone)
    times = sorted((flow.get("batch") or {}).get("times") or ["00:00"])
    for days in (0, 1):
        day = (local + timedelta(days=days)).date()
        for t in times:
            hour, minute = map(int, t.split(":"))
            at = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
            if at > local:
                return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    return H.now()


def _trim(values, cap=VALUES_BYTES):
    """A run's values as the judge sees them: long text cut until the whole is under `cap` bytes."""
    values = json.loads(json.dumps(values, default=str))
    limit = 4000
    while len(json.dumps(values)) > cap and limit > 50:
        values = _cut(values, limit)
        limit //= 2
    return values


def _cut(node, limit):
    if isinstance(node, str):
        return node if len(node) <= limit else node[:limit] + " … (cut)"
    if isinstance(node, dict):
        return {k: _cut(v, limit) for k, v in node.items()}
    if isinstance(node, list):
        return [_cut(v, limit) for v in node[:50]]
    return node


def _spent(c, aid, rid):
    """What the automation's other runs spent in the last 30 minutes, and what its turns not priced yet hold: a hold
    counts until its turn is priced or ends, however long that takes (the tick sweeps one lost for a day)."""
    since = H.shift(H.now(), minutes=-30)
    ran = c.execute("SELECT coalesce(sum(cost),0) FROM automation_runs WHERE automation_id=? AND id<>? AND started_at>=?",
                    (aid, rid, since)).fetchone()[0]
    held = c.execute("SELECT coalesce(sum(amount),0) FROM automation_holds WHERE automation_id=?", (aid,)).fetchone()[0]
    return float(ran or 0) + float(held or 0)


def _usual_cost(c, bot):
    """What a turn of `bot` usually costs: its last TURN_COST_SAMPLE priced turns' average, else TURN_COST."""
    rows = [r[0] for r in c.execute("SELECT est_cost_usd FROM turns WHERE bot=? AND est_cost_usd IS NOT NULL "
                                    "ORDER BY started_at DESC LIMIT ?", (bot, TURN_COST_SAMPLE))]
    return round(sum(rows) / len(rows), 6) if rows else TURN_COST


def _mark(value):
    """The `find` watermark: the (time, id) cursor of the last item finished in order, the items finished past it
    (`done`, while an earlier one waits for its retry) and how often each item past it failed (`tried`). Older rows
    hold [time, id] or the time alone."""
    try:
        mark = json.loads(value)
    except (TypeError, ValueError):
        mark = [value or "", ""]
    if isinstance(mark, list):
        mark = {"at": mark[0] if mark else "", "id": mark[1] if len(mark) > 1 else ""}
    if not isinstance(mark, dict):
        mark = {"at": str(value or ""), "id": ""}
    return {"at": str(mark.get("at") or ""), "id": str(mark.get("id") or ""),
            "done": [tuple(map(str, p)) for p in mark.get("done") or []], "tried": dict(mark.get("tried") or {})}


def _cursor(value):
    mark = _mark(value)
    return mark["at"], mark["id"]


def _found_done(c, aid, state, ended=False):
    """Move the automation's `find` watermark past the items finished in order. An item still being worked stops it
    (the next run finds it again if this one stops). An item whose thread failed, or that a run ended without
    finishing (`ended`), stops it too and is found again by the next run, once: failing again it is passed over and
    counted as dropped. Items finished past a stop are kept in `done`, so the retry never repeats them."""
    found = c.execute("SELECT found_since FROM automations WHERE id=?", (aid,)).fetchone()
    mark = _mark(found[0] if found else "")
    finished = {p: True for p in mark["done"]}
    for thread in (t for t in state.get("threads") or [] if t.get("item") is not None):
        value = thread.get("value") if isinstance(thread.get("value"), dict) else {}
        place, ident = (str(value.get("at") or ""), str(value.get("id") or "")), str(value.get("id") or "")
        if thread["state"] == E.DONE:
            mark["tried"].pop(ident, None)
            finished[place] = True
        elif thread["state"] == E.FAILED or ended:
            if not thread.get("counted"):   # once per thread, however often the run is saved
                thread["counted"] = True
                tries = int(mark["tried"].get(ident) or 0) + 1
                if tries >= ITEM_TRIES:
                    mark["tried"].pop(ident, None)
                    thread["passed_over"] = True
                    _count(c, aid, "dropped")
                else:
                    mark["tried"][ident] = tries
            finished[place] = bool(thread.get("passed_over"))
        else:
            finished.setdefault(place, False)
    cursor = (mark["at"], mark["id"])
    for place in sorted(finished):
        if place <= cursor:
            continue
        if not finished[place]:
            break
        cursor = place
    after = {"at": cursor[0], "id": cursor[1], "done": [list(p) for p in sorted(finished) if p > cursor and finished[p]],
             "tried": mark["tried"]}
    if not found or _mark(found[0]) != _mark(json.dumps(after)):
        c.execute("UPDATE automations SET found_since=? WHERE id=?", (json.dumps(after), aid))


def _held(c, aid, rid):
    """The ids of items the automation's other unfinished runs are working on: a `find` leaves them to those runs."""
    out = set()
    for row in c.execute("SELECT saved_state FROM automation_runs WHERE automation_id=? AND id<>? "
                         "AND status IN ('queued','running','waiting','held')", (aid, rid)):
        for thread in (json.loads(row[0] or "{}").get("threads") or []):
            if thread.get("item") is not None and isinstance(thread.get("value"), dict):
                out.add(str(thread["value"].get("id")))
    return out


# The automation steps whose words go to someone, and the arguments that hold them, checked against org rules
# (backend/org_rule_check.py) when the automation acts as a bot.
RULE_CHECKED = {"create_task": ("title", "body"), "assign": ("text", "note"), "label": ("text", "note"),
                "note": ("text", "note"), "wake_bot": ("text", "prompt"), "post_update": ("text", "body"),
                "send_reply": ("body", "text")}


def _rule_key(do, args):
    """The argument a warned step's reader gets, which its warning line goes on: the first checked argument the step
    sets; a new task, its body (a title is kept whole); a post of the turn's own output, its `text` (what
    `_post_update` posts); an assign, its hand-off `note`."""
    if do == "create_task":
        return "body"
    keys = RULE_CHECKED.get(do) or ()
    key = next((k for k in keys if args.get(k)), None)
    if key:
        return key
    if do == "post_update":
        return "text"
    if do == "assign" and args.get("owner"):
        return "note"
    return None


def _reply_addresses(values):
    """The addresses a mail reply goes to: the message's sender and the others it was sent to."""
    message = A._message(values) or {}
    if message.get("source") == "slack":
        return []
    mailbox = str(message.get("mailbox") or "").strip().lower()
    found = [str(a).strip().lower() for a in [message.get("from"), *(message.get("to") or []), *(message.get("cc") or [])]
             if a and "@" in str(a)]
    return sorted({a for a in found if a != mailbox})


def _reply_audience(c, auto, args, values):
    """Who a reply step's words reach. In Slack: the room its bot posts in (the operator, or a shared room's people)
    and the person who wrote. By mail: the roster people it is addressed to."""
    from . import org_rule_check
    message = A._message(values) or {}
    if message.get("source") != "slack":
        return org_rule_check.mail_recipients(c, _reply_addresses(values))
    out = [str(message.get("from") or "").replace("person:", "human:")]
    bot = A.R._slug(args.get("bot")) or A._owner_bot(c, auto)
    bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
    if bot:
        from . import rooms
        if rooms.thread_mode(c, bot) == rooms.SHARED:
            room = c.execute("SELECT id FROM conversations WHERE scope='shared' AND room_key=? AND closed_at IS NULL "
                             "ORDER BY COALESCE(last_message_at,created_at) DESC LIMIT 1", (bot,)).fetchone()
            out += (H.conversation(c, room["id"]) or {}).get("participants") or [] if room else []
        operator = c.execute("SELECT owner_id FROM bot_config WHERE bot=?", (bot,)).fetchone()
        if operator and operator["owner_id"]:
            out.append(H.human_actor(operator["owner_id"]))
    return out


def _rule_audience(c, auto, do, args, values):
    """Who a step's words reach, for a rule's With: a new task's owner, the run's task's owner and requester, the bot
    a note or wake goes to, a reply's people, and the whole company for an update."""
    from . import org_rule_check
    out = []
    try:
        task = A.task_of(c, auto, values)
    except ValueError:
        task = None
    if do == "send_reply":
        out += _reply_audience(c, auto, args, values)
    if do == "post_update":
        out += org_rule_check.company(c)
    if do in ("create_task", "assign") and args.get("owner"):
        owner = str(args["owner"]).replace("person:", "human:")
        out.append(H.resolve_actor(c, owner) or (owner if ":" in owner else "bot:" + owner))
    if do in ("assign", "label", "note") and task:
        out += [task["owner_id"], task["requester_id"]]
    if do in ("note", "wake_bot") and args.get("bot"):
        bot = str(args["bot"]).removeprefix("bot:")
        out.append("bot:" + (H.resolve_bot(c, bot) or {}).get("id", bot))
    if do == "wake_bot" and not args.get("bot") and A._owner_bot(c, auto):
        out.append("bot:" + A._owner_bot(c, auto))
    return sorted({a for a in out if a and a not in ("human:", "bot:")})


def _warned_args(c, auto, gate, do, args, key, values):
    """A warned step's arguments: the warning line on the argument its reader gets (`key`, from `_rule_key`): a post
    of the turn's output posts that output with the line first; an assign with no note of its own puts the line
    before the note the task already has, which stays whole."""
    from . import org_rule_check
    if not (gate and gate.get("warn") and key):
        return args
    said = args.get(key)
    if not said and do == "post_update":
        said = A._turn_output(values).get("text")
    if not said and do == "assign":
        try:
            note = A.task_of(c, auto, values).get("note") or ""
        except ValueError:
            note = ""
        return {**args, key: org_rule_check.warned_note(gate, note)}
    return {**args, key: org_rule_check.warned_text(gate, str(said or "")).rstrip()}


class TaskMoved(Exception):
    """The task a step was checked on changed before its write: nothing is written, and the step checks again."""


RULE_TRIES = 3          # checks of one step whose task keeps changing under it, before the step fails


def replay_step(c, bot, hold, replay, ingest, gate=None):
    """Send anyway on a held automation step: the step runs once, as its automation acts now, with no second check of
    the rules it was held on (a newer rule's Warn, `gate`, goes on the step's text)."""
    row = c.execute("SELECT * FROM automations WHERE id=?", (replay["automation"],)).fetchone()
    if not row:
        raise Problem("not_found", "The automation is gone", 409)
    auto = {"id": row["id"], "flow": flow_of(row), "assistant_bot": "", "chain": replay.get("chain") or []}
    if A.acts_as(c, auto) != hold["author"]:
        # The output was approved as the held bot's: run as anyone else (a new owner, a group's new lead), it would
        # write under another bot's permissions and rules. That is a new, checked write.
        raise Problem("changed", "Who this automation acts as changed since it was held; it stays held. Run it again "
                      "to check it.", 409)
    args = _warned_args(c, auto, gate, replay["do"], replay["args"], _rule_key(replay["do"], replay["args"]),
                        replay["values"])
    chain, via = CHAIN.set(tuple(auto["chain"])), H.VIA.set("automation:" + row["id"])
    try:
        return A.run(c, auto, replay["run"], replay["do"], args, replay["values"], step=replay.get("step"))
    finally:
        H.VIA.reset(via)
        CHAIN.reset(chain)


def replay_audience(c, bot, replay):
    """Who a held step would reach now (org_rule_check._same_audience), counted as its check counted them."""
    row = c.execute("SELECT * FROM automations WHERE id=?", (replay["automation"],)).fetchone()
    if not row:
        raise Problem("not_found", "The automation is gone", 409)
    auto = {"id": row["id"], "flow": flow_of(row), "assistant_bot": "", "chain": replay.get("chain") or []}
    return _rule_audience(c, auto, replay["do"], replay["args"], replay["values"])


class HubServices:
    """The executor's services in the hub. Each call with an effect takes its own short transaction, acting as the
    automation's owner with `via: automation:<id>` on what it logs and its chain on what it writes."""

    def __init__(self, hub, row, flow, run_id, chain):
        self.hub, self.row, self.flow, self.run_id, self.chain = hub, row, flow, run_id, chain
        self.auto = {"id": row["id"], "flow": flow, "assistant_bot": hub.settings.assistant_bot, "chain": list(chain)}
        self.dirty = False

    def _write(self, fn):
        chain, via = CHAIN.set(self.chain), H.VIA.set("automation:" + self.row["id"])
        try:
            with self.hub.store.transaction() as c:
                return fn(c)
        finally:
            H.VIA.reset(via)
            CHAIN.reset(chain)

    def _once(self, ctx, fn):
        """An effect done once per `<run id>:<step record>`: a resumed run that repeats the step gets the first
        answer back, written in the same transaction as the effect."""
        key = f"{self.run_id}:{ctx['key']}"
        self.dirty = True

        def work(c):
            done = c.execute("SELECT result FROM automation_effects WHERE key=?", (key,)).fetchone()
            if done:
                return json.loads(done[0])
            result = fn(c)
            c.execute("INSERT INTO automation_effects(key,run_id,created_at,result) VALUES(?,?,?,?)",
                      (key, self.run_id, H.now(), json.dumps(result, default=str)))
            return result
        return self._write(work)

    def now(self):
        return H.now()

    def next_batch(self):
        return _next_batch(self.flow, datetime.now(timezone.utc))

    def spent_30m(self):
        with self.hub.store.read() as c:
            return _spent(c, self.row["id"], self.run_id)

    def question_set(self, ref):
        return F.question_set(ref)

    def state_fields(self, ref):
        from clients import judge as J
        try:
            return list(J.load_set(str(ref).partition("@")[0]).get("state") or []) or None
        except (J.JudgeError, OSError, ValueError):
            return None

    def judge(self, values, questions, label, level):
        from clients import judge as J
        from .judge import ask, audit_detail, server_engine
        store = self.hub.store
        with store.read() as c:
            engine = server_engine(store, c, self.hub.app.state.judge)
        if engine is None:   # no decision model, or the month's decision cap is reached
            return None
        J.validate(values, questions, label)
        result = ask(engine, _trim(values), questions, label, level)
        self.dirty = True
        self._write(lambda c: H.event(c, A.acts_as(c, self.auto) or H.KEEPER, "judge.call", label, {
            "label": label, "automation": self.row["id"], "run": self.run_id, "level": level,
            **audit_detail(result), "answers": J.summary(result["answers"])}))
        return result

    def _screened(self, bot, values):
        """A webhook run's call, screened for prompt injection before a bot gets work from it (backend/screening.py);
        None for any other run. Outside every transaction: it may wait on the decision model."""
        if self.row.get("trigger_type") != "webhook" or not bot:
            return None
        from . import screening
        return screening.automation_check(self.hub.store, self.hub.app.state.judge, values, bot)

    def act(self, do, args, ctx):
        found = None
        if do == "create_task":
            owner = str(args.get("owner") or "")
            bot = owner[4:] if owner.startswith("bot:") else "" if ":" in owner else owner
            if bot:
                with self.hub.store.read() as c:
                    bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
            found = self._screened(bot, ctx["values"]) if bot else None
            if found and found["action"] != "pass":
                args = self._screened_task(found, bot, args, ctx["values"])
        for _ in range(RULE_TRIES):
            rule = self._rule_check(do, args, ctx["values"], ctx.get("step"))
            try:
                result = self._once(ctx, lambda c, rule=rule: self._ruled(c, rule, do, args, ctx["values"],
                                                                         step=ctx.get("step")))
                break
            except TaskMoved:
                continue                   # rolled back, nothing written or recorded: check it as it is now
        else:
            raise RuntimeError("The task kept changing while this step's rule check ran; nothing was done")
        if found and found["action"] != "pass" and isinstance(result, dict) and result.get("task"):
            from . import screening
            self._write(lambda c: screening.note(c, found, "webhook", result["task"], bot=bot,
                                                 to=args.get("owner") if found["action"] == "hold" else None))
        return result

    def _rule_text(self, c, do, args, values):
        """What a step says, as the rule check reads it, and the argument a warning line goes on. A task handed to a
        new owner (`assign`) says its title and body to them, with or without a note."""
        keys = RULE_CHECKED.get(do)
        if not keys:
            return "", None
        text = "\n\n".join(str(args[k]) for k in keys if args.get(k))
        if do == "post_update" and not text:
            text = str(A._turn_output(values).get("text") or "")
        if do == "assign" and args.get("owner"):
            try:
                task = A.task_of(c, self.auto, values)
            except ValueError:
                task = None
            if task:
                text = "\n\n".join(filter(None, (text, f"Assigned: {task['title']}\n\n{task.get('body') or ''}".rstrip())))
        return text, _rule_key(do, args)

    def _rule_check(self, do, args, values, step=None):
        """The org rule check of what this step writes, when the automation acts as a bot (backend/org_rule_check.py):
        outside every transaction, as Decide's own calls are. A person's automation is never checked."""
        from . import org_rule_check
        from .auth import Identity
        store = self.hub.store
        with store.read() as c:
            if do not in RULE_CHECKED or not org_rule_check.in_force(c):
                return None             # no rule in force: the step's audience (a roster read) is never resolved
            text, _ = self._rule_text(c, do, args, values)
            if not text:
                return None
            author = A.acts_as(c, self.auto)
            if not author or not H.is_bot(author):
                return None
            recipients = _rule_audience(c, self.auto, do, args, values)
            task = None
            if do == "assign":
                try:
                    task = A.task_of(c, self.auto, values)
                except ValueError:
                    pass
        addresses = _reply_addresses(values) if do == "send_reply" else []
        context = {"automation": self.flow.get("name") or self.row["id"], "step": do, "step_id": step or "",
                   **({"to": addresses} if addresses else {})}
        return org_rule_check.preflight(store, self.hub.app.state.judge, Identity(author, "bot"), "automation", text,
                                        recipients, context=context,
                                        obj=f"{task['id']}@{task['version']}" if task else "")

    def _ruled(self, c, rule, do, args, values, step=None):
        """The step inside its write: held by a rule, nothing is done and the run goes on with the step failed. A task
        an assign was checked on that changed since (its version) is still held by a verdict that held; any other
        verdict is stale, so nothing is written (`TaskMoved`) and `act` checks the task again outside the write."""
        if rule:
            from . import org_rule_check
            text, key = self._rule_text(c, do, args, values)
            gate = org_rule_check.apply(c, rule, text, {"automation": self.row["id"], "run": self.run_id, "do": do,
                                                        "args": args, "values": _trim(values), "step": step,
                                                        "chain": list(self.chain)}, tell=True,
                                        obj_now=org_rule_check.current_obj(c, rule.get("obj")))
            if gate and gate.get("moved"):
                raise TaskMoved()
            if gate and gate.get("held"):
                return {"ok": False, "detail": "held by a rule", "held": gate["held"]["id"]}
            args = _warned_args(c, self.auto, gate, do, args, key, values)
        return A.run(c, self.auto, self.run_id, do, args, values, step=step)

    def _screened_task(self, found, bot, args, values):
        """A create_task step's arguments after screening: held for a person, or with the warning line first."""
        from . import screening
        body = str(args.get("body") or args.get("title") or "")
        if found["action"] == "hold":
            with self.hub.store.read() as c:
                person = screening.automation_person(c, values, self.flow.get("owner"), bot)
                name = screening._name(c, bot)
            if person:
                return {**args, "owner_id": person, "title": screening.held_title(args.get("title")),
                        "body": screening.held_body(found, bot, name, body)}
            found["action"] = "warn"
        return {**args, "body": screening.warning_line(found) + "\n\n" + body}

    def start_turn(self, who, prompt, ctx, wait):
        """The cap check and the start in one write: the turns of runs going at once cannot each see room under the
        cap. A started turn holds its bot's usual cost until it is priced. On a webhook's run the call is screened
        first: at `hold` the turn is not started and its prompt is a held task for a person (`held` in the answer)."""
        cap = (self.flow.get("spend_cap") or {}).get("per_30m")
        bot = who[4:] if who.startswith("bot:") else ""
        if bot:
            with self.hub.store.read() as c:
                bot = (H.resolve_bot(c, bot) or {}).get("id", bot)
        found = self._screened(bot, ctx["values"])
        person = None
        if found and found["action"] == "hold":
            from . import screening
            with self.hub.store.read() as c:
                person = screening.automation_person(c, ctx["values"], self.flow.get("owner"), bot)
            if not person:
                found["action"] = "warn"
        if found and found["action"] == "warn":
            from . import screening
            prompt = screening.warning_line(found) + "\n\n" + prompt

        def work(c):
            if person:
                from . import screening
                name = str(self.flow.get("name") or self.row["id"])
                task = H.task_create(c, A._actor(c, self.auto), screening.held_title(name),
                                     screening.held_body(found, bot, screening._name(c, bot), prompt), person,
                                     lint=False, deduplicate=False)
                screening.note(c, found, "webhook", task["id"], bot=bot, to=person)
                return {"ref": task["id"], "held": True}
            if cap is not None and _spent(c, self.row["id"], self.run_id) + float(ctx.get("cost") or 0) >= float(cap):
                return None
            started = A.start_turn(c, self.auto, self.run_id, ctx["step"], who, prompt, self.hub.settings.assistant_bot,
                                   ctx["key"], label=ctx.get("label"), run_model=ctx.get("run_model"),
                                   occurrence=ctx["values"].get("due"))
            if found and found["action"] == "warn":
                from . import screening
                screening.note(c, found, "webhook", started["ref"], bot=bot)
            c.execute("INSERT OR REPLACE INTO automation_holds(ref,automation_id,run_id,amount,created_at) VALUES(?,?,?,?,?)",
                      (started["ref"], self.row["id"], self.run_id, _usual_cost(c, started["turn"]["bot"]), H.now()))
            return started
        return self._once(ctx, work)

    def find(self, source, query, limit, idle_hours=None):
        """Items after the watermark, which moves only as items are finished (`_found_done`)."""
        self.dirty = True
        with self.hub.store.read() as c:
            found = c.execute("SELECT found_since FROM automations WHERE id=?", (self.row["id"],)).fetchone()
            mark = _mark(found[0] if found else "")
            return A.find(c, self.auto, source, query, limit, (mark["at"], mark["id"]),
                          mailboxes=owner_mailboxes(c, self.flow["owner"]) if source == "mail" else (),
                          skip=_held(c, self.row["id"], self.run_id) | {ident for _, ident in mark["done"]},
                          idle_hours=idle_hours)

    def on_hold(self):
        """Whether no further step may start: the run guard put the automation on hold since this run was claimed, or
        the run was dropped meanwhile (it is no longer `running`)."""
        with self.hub.store.read() as c:
            run = c.execute("SELECT status FROM automation_runs WHERE id=?", (self.run_id,)).fetchone()
            return _hold_row(c, self.row["id"]) is not None or not run or run["status"] != "running"

    def checkpoint(self, state):
        """Save the run after a step that did something, before the next one starts."""
        if not self.dirty:
            return
        self.dirty = False

        def work(c):
            _found_done(c, self.row["id"], state)
            c.execute("UPDATE automation_runs SET saved_state=?,cost=?,claimed_at=? WHERE id=? AND status='running'",
                      (json.dumps(state, default=str), state.get("cost", 0), H.now(), self.run_id))
        self._write(work)


class Hub:
    """What draining needs: the store, the settings and the app's judge."""

    def __init__(self, app, store):
        # The app weakly: the store outlives its app in places (tico_storage keeps each live store), and a strong
        # link here would keep every app and its thread pools alive with it.
        self._app, self.store, self.settings = weakref.ref(app), store, store.settings
        self.lock = threading.Lock()

    @property
    def app(self):
        return self._app()

    def claim(self):
        with self.store.transaction() as c:
            row = c.execute("SELECT id FROM automation_runs WHERE status='queued' ORDER BY started_at LIMIT 1").fetchone()
            if not row:
                return None
            c.execute("UPDATE automation_runs SET status='running',claimed_at=? WHERE id=? AND status='queued'",
                      (H.now(), row["id"]))
            return row["id"]

    def drain(self, limit=200):
        """Run queued runs until none is left (or `limit`, so one request never drains forever)."""
        done = 0
        while done < limit:
            rid = self.claim()
            if not rid:
                return done
            self.go(rid)
            done += 1
        return done

    def go(self, rid):
        with self.store.read() as c:
            run = dict(c.execute("SELECT * FROM automation_runs WHERE id=?", (rid,)).fetchone())
            row = c.execute("SELECT * FROM automations WHERE id=?", (run["automation_id"],)).fetchone()
            version = c.execute("SELECT flow FROM automation_versions WHERE automation_id=? AND n=?",
                                (run["automation_id"], run["version"])).fetchone()
        state = json.loads(run["saved_state"] or "{}")
        try:
            # What it does comes from the run's version; its owner, cap and the rest are as they are now.
            flow = {**{k: v for k, v in flow_of(row).items() if k not in VERSIONED}, **json.loads(version["flow"])}
            services = HubServices(self, dict(row), flow, rid, tuple(json.loads(run["chain"] or "[]")))
            state = E.advance(flow, state, services)
        except Exception as exc:  # noqa: BLE001 - a bug in a run fails that run, not the drain
            state = {**state, "status": "failed", "error": (str(exc) or type(exc).__name__)[:300]}
        with self.store.transaction() as c:
            _take_parked(c, rid, state)
            settle_mail(c, state)   # in the same write as the save, so a result reported meanwhile is not lost
            if c.execute("SELECT status FROM automation_runs WHERE id=?", (rid,)).fetchone()[0] == "skipped":
                # Dropped (or its automation turned off or deleted) during the step: what the step did is saved (with
                # the answers and costs of turns that ended meanwhile, taken above), the run stays skipped, and
                # nothing it queued is sent.
                state["status"] = "skipped"
                _cancel_mail(c, rid, state)
                c.execute("UPDATE automation_runs SET cost=?,saved_state=?,claimed_at=NULL WHERE id=?",
                          (state.get("cost", 0), json.dumps(state, default=str), rid))
                return
            status = state["status"]
            if any(t.get("saved_state") == E.READY for t in state.get("threads") or []) and status not in ("failed",):
                # Put on hold while a step ran: it finished that step and stopped, and waits held, not queued.
                status = state["status"] = _runnable(c, run["automation_id"])
            if status == "failed":
                _cancel_mail(c, rid, state)
            _found_done(c, run["automation_id"], state, ended=status not in ("waiting", "queued", "held"))
            waits = [t["wait"]["until"] for t in state.get("threads", []) if (t.get("wait") or {}).get("kind") == "batch"]
            if status == "waiting":
                state["waiting_since"] = H.now()    # a turn's day to answer runs from here, not from the run's start
            c.execute("UPDATE automation_runs SET status=?,cost=?,saved_state=?,finished_at=?,wake_at=?,claimed_at=NULL "
                      "WHERE id=?", (status, state.get("cost", 0), json.dumps(state, default=str),
                                     None if status in ("waiting", "queued", "held") else H.now(),
                                     min(waits) if waits and status == "waiting" else None, rid))
            if status == "failed":
                _due_failed(c, rid, _run_error(state))
            if status == "queued":
                _ring(c)


# ---------------------------------------------------------------------------------------------------- mail actions
def _entries(record):
    """The action results in a step record: an action step's own, a filter rule's each."""
    outputs = record.get("outputs") or {}
    acts = outputs.get("actions") if record.get("type") == "filter" else [outputs]
    return [a for a in acts or [] if isinstance(a, dict)]


def settle_mail(c, state):
    """The run's queued mail actions as their computer reported them: a step waits while one of its actions is out
    and is ok or failed by the results, and a run with nothing else to wait for waits while a step does. Returns the
    run's status."""
    pending = failed = False
    for record in state.get("steps") or []:
        acts = _entries(record)
        mail = [a for a in acts if a.get("mail_action")]
        if not mail or record.get("status") not in ("ok", "waiting", "failed"):
            continue
        for entry in mail:
            row = c.execute("SELECT state, result FROM automation_mail_actions WHERE id=?",
                            (entry["mail_action"],)).fetchone()
            now = row["state"] if row else "failed"
            entry["queued"] = now in ("queued", "claimed")
            if entry["queued"]:
                continue
            result = json.loads((row and row["result"]) or "{}")
            entry["ok"] = now == "done"
            entry["detail"] = ("send_off" if entry["ok"] and result.get("send_off")
                               else "cancelled: the run failed" if now == "cancelled"
                               else str(result.get("detail") or ("done" if entry["ok"] else "no such mail action")))
            entry.update({k: result[k] for k in ("message", "draft", "thread", "send_off") if result.get(k)})
        record["status"] = ("waiting" if any(a["queued"] for a in mail)
                            else "failed" if any(not a.get("ok") for a in acts) else "ok")
        pending |= record["status"] == "waiting"
        failed |= record["status"] == "failed"
    if state.get("status") in ("ok", "waiting") and not any(t.get("state") == E.WAITING
                                                            for t in state.get("threads") or []):
        state["status"] = "waiting" if pending else "failed" if failed else "ok"
    return state.get("status")


def _cancel_mail(c, run_id, state=None):
    """A run that ended failed (or was dropped) sends no mail later: its actions not yet claimed are cancelled, and
    one out on a lease is cancelled when the lease runs out or its computer asks to retry."""
    n = c.execute("UPDATE automation_mail_actions SET state='cancelled' WHERE run_id=? AND state='queued'",
                  (run_id,)).rowcount
    if n and state is not None:
        settle_mail(c, state)
    return n


def _requeue(c, row):
    """Where a mail action goes when its lease runs out or its computer asks to retry: back in the queue, unless
    its run has ended failed meanwhile."""
    run = c.execute("SELECT status FROM automation_runs WHERE id=?", (row["run_id"],)).fetchone() if row["run_id"] else None
    return "cancelled" if run and run["status"] in ("failed", "skipped") else "queued"


def _settle_run(c, run_id):
    """A mail action's result reached its run. A run being advanced settles as it is saved (Hub.go)."""
    run = c.execute("SELECT status, saved_state, finished_at FROM automation_runs WHERE id=?", (run_id,)).fetchone()
    if not run or not run["saved_state"] or run["status"] in ("queued", "running", "held"):
        return   # a held run settles once it is released and runs
    state = json.loads(run["saved_state"])
    status = settle_mail(c, state)
    if run["status"] == "skipped":
        # Dropped (or turned off) while an action was out: the result is on record, the run stays skipped.
        state["status"] = "skipped"
        c.execute("UPDATE automation_runs SET saved_state=? WHERE id=?", (json.dumps(state, default=str), run_id))
        return
    status = "failed" if run["status"] == "failed" else status   # the tick gave up on it: it stays failed
    if status == "failed":
        _cancel_mail(c, run_id, state)
    c.execute("UPDATE automation_runs SET status=?, saved_state=?, finished_at=? WHERE id=?",
              (status, json.dumps(state, default=str),
               run["finished_at"] or (None if status == "waiting" else H.now()), run_id))
    if status == "failed" and run["status"] != "failed":
        _due_failed(c, run_id, _run_error(state))


def restate(c, run_id, key, value, changes):
    """Amend a step result the run recorded (the one whose `key` is `value`), when what happened changed after
    the step: a Slack reply held by the send switch."""
    run = c.execute("SELECT saved_state FROM automation_runs WHERE id=?", (run_id,)).fetchone() if run_id else None
    if not run or not run["saved_state"]:
        return
    state = json.loads(run["saved_state"])
    for record in state.get("steps") or []:
        for entry in _entries(record):
            if entry.get(key) == value:
                entry.update({k: v for k, v in changes.items() if v is not None})
                c.execute("UPDATE automation_runs SET saved_state=? WHERE id=?", (json.dumps(state, default=str), run_id))
                return


def _mail_report(c, row, state, result):
    c.execute("UPDATE automation_mail_actions SET state=?, result=? WHERE id=?",
              (state, json.dumps(result, default=str), row["id"]))
    if state in ("done", "failed") and row["run_id"]:
        _settle_run(c, row["run_id"])


def tick(store, at=None):
    """The scheduler's pass: due schedules fire, batch waits whose time came and answered turns go back in the
    queue, runs cut off go back in it to go on from their last save, and the queue is drained."""
    hub = getattr(store, "automations", None)
    from . import redesign_migration
    try:
        redesign_migration.catch_up(store)      # a new install's first person: the built-ins they own
    except Exception:  # noqa: BLE001 - the cutover's catch-up must not stop the automations
        log.exception("redesign catch-up failed")
    at = at or datetime.now(timezone.utc)
    now = at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    with store.transaction() as c:
        ensure_builtins(c, settings=getattr(store, "settings", None))
        from .scheduler import next_due
        for row in c.execute("SELECT * FROM automations WHERE deleted_at IS NULL AND enabled=1 "
                             "AND trigger_type='schedule'").fetchall():
            trigger = flow_of(row)["trigger"]
            zone = trigger.get("tz") or "UTC"
            if not row["next_due_at"]:
                due = next_due(trigger["cron"], at, zone)
                c.execute("UPDATE automations SET next_due_at=? WHERE id=?", (_iso(due), row["id"]))
                continue
            if row["next_due_at"] > now:
                continue
            # A Tico Cloud company out of credit or locked passes its schedules over (backend/cloud_access.py).
            if why := (cloud_access.routines_paused(c) or _bot_away(c, row)
                       or _no_input(c, row, getattr(store, "settings", None), at, zone)):
                # A converted built-in routine with nothing to work on is passed over, as the routine was.
                _count(c, row["id"], "dropped")
                H.event(c, H.KEEPER, "automation.skipped", row["id"], {"at": row["next_due_at"], "reason": why})
            else:
                # Through `fire`, so the trigger's filters hold the schedule's run as they hold any other.
                fire(c, "schedule", {"schedule": {"at": row["next_due_at"], "cron": trigger["cron"], "tz": zone}},
                     f"schedule:{row['next_due_at']}", only=row["id"])
            c.execute("UPDATE automations SET next_due_at=? WHERE id=?", (_iso(next_due(trigger["cron"], at, zone)), row["id"]))
        for row in c.execute("SELECT * FROM automations WHERE deleted_at IS NULL AND enabled=1 "
                             "AND trigger_type='due'").fetchall():
            _due_pass(c, row, at)
        c.execute(f"UPDATE automation_runs SET status={RUNNABLE},wake_at=NULL WHERE status='waiting' AND wake_at<=?",
                  (now,))
        # A run cut off (the server stopped mid-run) goes on from its last save; its effects are not done twice.
        for run in c.execute("SELECT id, saved_state FROM automation_runs WHERE status='running' AND claimed_at<?",
                             (H.shift(now, minutes=-STALE_MINUTES),)).fetchall():
            state = json.loads(run["saved_state"] or "{}")
            state["resumes"] = int(state.get("resumes") or 0) + 1
            if not state.get("threads") or state["resumes"] > MAX_RESUMES:
                _fail(c, run["id"], state, now, "it was cut off more than %d times" % MAX_RESUMES)
            else:
                c.execute(f"UPDATE automation_runs SET status={RUNNABLE},claimed_at=NULL,saved_state=? WHERE id=?",
                          (json.dumps(state, default=str), run["id"]))
        # One dropped mid-step whose step was never saved (the server stopped first): it is not resumed, but what its
        # turns cost meanwhile is charged to it and their holds released.
        for run in c.execute("SELECT id, saved_state FROM automation_runs WHERE status='skipped' "
                             "AND claimed_at IS NOT NULL AND claimed_at<?",
                             (H.shift(now, minutes=-STALE_MINUTES),)).fetchall():
            state = {**json.loads(run["saved_state"] or "{}"), "status": "skipped"}
            _take_parked(c, run["id"], state)
            c.execute("UPDATE automation_runs SET cost=?,saved_state=?,claimed_at=NULL WHERE id=?",
                      (state.get("cost", 0), json.dumps(state, default=str), run["id"]))
        # A turn (or a mail computer) that never answered would hold the run waiting for ever.
        cutoff = H.shift(now, days=-TURN_WAIT_DAYS)
        for run in c.execute("SELECT id, started_at, saved_state FROM automation_runs WHERE status='waiting' "
                             "AND wake_at IS NULL AND started_at<?", (cutoff,)).fetchall():
            state = json.loads(run["saved_state"] or "{}")
            if (state.get("waiting_since") or run["started_at"]) < cutoff:
                _fail(c, run["id"], state, now, "nothing answered for a day")
        # A hold whose turn never ended (its job lost) stops counting after a day; the sweep is on record.
        for hold in c.execute("SELECT * FROM automation_holds WHERE created_at<?",
                              (H.shift(now, hours=-HOLD_STALE_HOURS),)).fetchall():
            c.execute("DELETE FROM automation_holds WHERE ref=?", (hold["ref"],))
            H.event(c, H.KEEPER, "automation.hold_swept", hold["automation_id"],
                    {"run": hold["run_id"], "turn": hold["ref"], "amount": hold["amount"], "held_since": hold["created_at"]})
    return hub.drain() if hub else 0


def _due_day(value):
    """The cursor's due day as a date, or None (none left, or not yet set)."""
    return date.fromisoformat(value) if value else None


def _due_text(day):
    """A due day as the cursor is stored: its ISO date, or "" once none is left (NULL means not yet set)."""
    return day.isoformat() if day else ""


DUE_RETRY = timedelta(hours=1)    # a due day whose run failed is run again no sooner than this after the failure
DUE_TRIES = 3                     # failed runs of one due day before the routine gives that day up


def due_input(trigger, day):
    """The payload a due occurrence's run starts with, for its due day `day`: what `_due_pass` fires, and what a manual
    run without input is given. Its `due` object makes the task's due and occurrence (backend/automation_actions.py)."""
    zone, open_days = D.zone_of(trigger), D.open_days_of(trigger)
    return {"due": {"due_on": day.isoformat(), "first_due": trigger["first_due"], "repeats": trigger["repeats"],
                    "open_days": open_days, "tz": zone, "rule_due_id": trigger.get("rule_due_id"),
                    "run_at": _iso(D.fire_at(day, open_days, zone))}}


def _next_due_day(row, trigger, at):
    """The routine's next due day not yet queued (its cursor), or None when none is left."""
    return _due_day(row["next_due_at"]) if row["next_due_at"] is not None else D.start(trigger, at)


def _same_trigger(c, aid, version, trigger):
    """Whether a run's version had this due trigger (its days and zone): a run made under a trigger since changed, or
    before the routine was a due one, is not an occurrence of the routine any more."""
    row = c.execute("SELECT flow FROM automation_versions WHERE automation_id=? AND n=?", (aid, version)).fetchone()
    if not row:
        return False
    was = json.loads(row[0]).get("trigger") or {}
    return (was.get("type") == "due" and all(was.get(k) == trigger.get(k) for k in ("first_due", "repeats"))
            and D.open_days_of(was) == D.open_days_of(trigger) and D.zone_of(was) == D.zone_of(trigger))


def _owed(c, row, trigger):
    """The due days whose runs failed and are still owed a try, oldest first, as (trigger_ref, last failure): fewer than
    DUE_TRIES failed runs, no run that stands (ok, unfinished or skipped), made under this due trigger. Each day counts
    its own failures, so the days of one catch-up that failed each keep their own tries."""
    settled = {r[0] for r in c.execute("SELECT DISTINCT trigger_ref FROM automation_runs WHERE automation_id=? "
                                       "AND trigger_ref LIKE 'due:%' AND status<>'failed'", (row["id"],))}
    seen = {}
    for r in c.execute("SELECT trigger_ref, version, finished_at FROM automation_runs WHERE automation_id=? "
                       "AND trigger_ref LIKE 'due:%' AND status='failed' ORDER BY started_at", (row["id"],)):
        if r["trigger_ref"] in settled:
            continue
        count = seen.get(r["trigger_ref"], (0, "", 0))[0]
        seen[r["trigger_ref"]] = (count + 1, r["finished_at"] or "", r["version"])
    return [(ref, last) for ref, (count, last, version) in sorted(seen.items())
            if count < DUE_TRIES and _same_trigger(c, row["id"], version, trigger)]


def _held_by_find(c, row):
    """A find runs one at a time: a day waits while another run of the routine is unfinished."""
    return bool(_finds(flow_of(row)["steps"]) and c.execute(
        "SELECT 1 FROM automation_runs WHERE automation_id=? AND status IN ('queued','running','waiting','held') "
        "LIMIT 1", (row["id"],)).fetchone())


def _due_pass(c, row, at):
    """A routine's due days whose run time has come, one run each: the cursor (`next_due`) walks forward, queueing each
    day (`queue` refuses a second run for it). A failed day is owed another try (`_owed`), at most hourly, until
    DUE_TRIES failed runs; the cursor is never moved back for it, so several failed days each keep their own tries.
    A pause (access, or the bot not active) holds every day, with one `automation.deferred` event per reason and day,
    and nothing is dropped: the next pass after it runs them. A find waits for its unfinished run."""
    flow = flow_of(row)
    trigger = flow["trigger"]
    cursor = _next_due_day(row, trigger, at)
    zone, open_days = D.zone_of(trigger), D.open_days_of(trigger)
    owed = _owed(c, row, trigger)
    if why := cloud_access.routines_paused(c) or _bot_away(c, row):
        waiting = [date.fromisoformat(ref[4:]) for ref, _ in owed]
        if cursor and D.fire_at(cursor, open_days, zone) <= at:
            waiting.append(cursor)
        if waiting:
            _deferred(c, row, min(waiting).isoformat(), why)
        return
    cutoff = _iso(at - DUE_RETRY)
    for ref, last in owed:
        if last and last > cutoff:
            continue    # a failed day is tried again no sooner than an hour after it failed
        if _held_by_find(c, row):
            break
        _due_fire(c, row, trigger, date.fromisoformat(ref[4:]), ref)
    day, passed = cursor, 0
    while day and passed < D.MAX_CATCH_UP and D.fire_at(day, open_days, zone) <= at:
        if _held_by_find(c, row):
            break
        _due_fire(c, row, trigger, day, f"due:{day.isoformat()}")
        day = D.next_after(D.first_due(trigger), trigger["repeats"], day)
        passed += 1
    c.execute("UPDATE automations SET next_due_at=? WHERE id=?", (_due_text(day), row["id"]))


def _due_fire(c, row, trigger, day, ref):
    """One due day's run, or, for a routine linked to a rule's due date (`rule_due_id`) whose day someone already marked
    Satisfied by hand, a skipped run in its place: it opens no task, and the day counts as done."""
    from . import rule_due
    if trigger.get("rule_due_id") and rule_due.marked(c, trigger["rule_due_id"], day.isoformat()):
        if not c.execute("SELECT 1 FROM automation_runs WHERE automation_id=? AND trigger_ref=? AND status!='failed' "
                         "LIMIT 1", (row["id"], ref)).fetchone():
            state = {**E.start(due_input(trigger, day)), "status": "skipped"}
            now = H.now()
            c.execute("INSERT INTO automation_runs(id,automation_id,version,trigger_ref,started_at,finished_at,status,"
                      "saved_state,chain) VALUES(?,?,?,?,?,?,?,?,?)",
                      ("run_" + H.new_id(), row["id"], row["version"], ref, now, now, "skipped",
                       json.dumps(state, default=str), "[]"))
            H.event(c, H.KEEPER, "automation.skipped", row["id"], {"due_on": day.isoformat(), "reason": "satisfied"})
        return
    fire(c, "due", due_input(trigger, day), ref, only=row["id"])


def _deferred(c, row, day, why):
    """A due day a pause holds: one `automation.deferred` event per reason and day, not one per pass."""
    last = c.execute("SELECT detail FROM events WHERE action='automation.deferred' AND target=? "
                     "ORDER BY rowid DESC LIMIT 1", (row["id"],)).fetchone()
    detail = {"due_on": day, "reason": why}
    if last and json.loads(last[0] or "{}") == detail:
        return
    H.event(c, H.KEEPER, "automation.deferred", row["id"], detail)


def _run_error(state):
    """Why a run failed, in words: the run's own error, else the first failed step's detail (an action's refusal)."""
    if state.get("error"):
        return str(state["error"])
    for record in state.get("steps") or []:
        if record.get("status") == "failed":
            found = next((a.get("detail") or a.get("error") for a in _entries(record) if not a.get("ok", True)), None)
            return str(found or record.get("error") or "a step failed")
    return "failed"


def _due_failed(c, rid, error=""):
    """A due day's run has failed (once, as the run turns failed). Nothing moves: the day stays owed its next try
    (`_owed`), and the cursor stays where it is. The try that makes DUE_TRIES failures gives the day up, with one
    `automation.due_gave_up` event. A run under another trigger, or a routine that is no longer a due one, says nothing."""
    run = c.execute("SELECT automation_id, trigger_ref, version FROM automation_runs WHERE id=?", (rid,)).fetchone()
    ref = str(run["trigger_ref"] or "") if run else ""
    if not ref.startswith("due:"):
        return
    try:
        day = date.fromisoformat(ref[4:])
    except ValueError:
        return
    aid = run["automation_id"]
    auto = c.execute("SELECT trigger_type, flow FROM automations WHERE id=?", (aid,)).fetchone()
    if not auto or auto["trigger_type"] != "due":
        return
    trigger = json.loads(auto["flow"])["trigger"]
    if not _same_trigger(c, aid, run["version"], trigger):
        return
    failed = c.execute("SELECT count(*) FROM automation_runs WHERE automation_id=? AND trigger_ref=? AND status='failed'",
                       (aid, ref)).fetchone()[0]
    if failed == DUE_TRIES:
        H.event(c, H.KEEPER, "automation.due_gave_up", aid, {"due_on": day.isoformat(), "error": str(error or "")[:300]})


def _fail(c, rid, state, now, why):
    """The tick gives up on a run: failed, its unclaimed mail cancelled, its unfinished items left for a retry."""
    run = c.execute("SELECT automation_id FROM automation_runs WHERE id=?", (rid,)).fetchone()
    if state:
        state.update(status="failed", error=why)
        _cancel_mail(c, rid, state)
        _found_done(c, run["automation_id"], state, ended=True)
    else:
        _cancel_mail(c, rid)
    was = c.execute("SELECT status FROM automation_runs WHERE id=?", (rid,)).fetchone()
    c.execute("UPDATE automation_runs SET status='failed',finished_at=?,claimed_at=NULL,saved_state=coalesce(?,saved_state) "
              "WHERE id=?", (now, json.dumps(state, default=str) if state else None, rid))
    if was and was["status"] != "failed":
        _due_failed(c, rid, why)


def _due(trigger, at):
    """The schedule's next time after `at`, in its zone's wall clock, so 09:00 stays 09:00 across a DST change."""
    from .scheduler import next_due
    return next_due(trigger["cron"], at, trigger.get("tz") or "UTC")


def _iso(at):
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def prune(c, now):
    """Step detail keeps KEEP_DAYS: older finished runs keep their status, cost and times."""
    old = H.shift(now, days=-KEEP_DAYS)
    c.execute("UPDATE automation_runs SET saved_state=NULL WHERE saved_state IS NOT NULL AND finished_at IS NOT NULL "
              "AND finished_at<?", (old,))
    c.execute("DELETE FROM automation_effects WHERE created_at<?", (old,))
    c.execute("DELETE FROM automation_turn_answers WHERE created_at<?", (old,))


# ---------------------------------------------------------------------------------------------------- routes
class AutomationIn(Contract):
    name: str = Field(min_length=1, max_length=120)
    owner: str = Field(min_length=1, max_length=80)
    group: str | None = Field(default=None, max_length=80)
    enabled: bool = True
    builtin: bool = False
    spend_cap: dict | None = None
    batch: dict | None = None
    trigger: dict
    steps: list = Field(default_factory=list)


class AutomationPatch(Contract):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    owner: str | None = Field(default=None, min_length=1, max_length=80)
    group: str | None = Field(default=None, max_length=80)
    enabled: bool | None = None
    spend_cap: dict | None = None
    batch: dict | None = None
    trigger: dict | None = None
    steps: list | None = None
    summary: str | None = Field(default=None, max_length=200)   # a few words on a new version or proposal


class Empty(Contract):
    pass


class InboxSend(Contract):
    send: bool


class HeldAction(Contract):
    action: str = Field(pattern="^(release|drop)$")   # release: held runs are queued; drop: they are skipped


class RunNow(Contract):
    input: dict = Field(default_factory=dict)   # shaped like the trigger's output: message, task, item, event, body


FLOW_KEYS = ("name", "owner", "group", "enabled", "builtin", "spend_cap", "batch", "trigger", "steps")


def page(rows, limit, offset, cap):
    out, used = [], 256
    for row in rows:
        size = len(json.dumps(row, default=str).encode()) + 1
        if len(out) >= limit or used + size > cap:
            return out, offset + len(out)
        used += size
        out.append(row)
    return out, None


def _summary(state):
    """A few words for the run's row: where it failed, else the paths it took, else what the cap stopped."""
    steps = state.get("steps") or []
    failed = next((s["id"] for s in steps if s["status"] == "failed"), None)
    if failed:
        return f"failed at {failed}"
    taken = list(dict.fromkeys(str(s["outputs"].get("path")) for s in steps
                               if s["type"] == "paths" and s["outputs"].get("path")))
    capped = sum(s["status"] == "spend_cap" for s in steps)
    words = (["path " + ", ".join(taken)] if taken else []) + ([f"{capped} stopped by the spend cap"] if capped else [])
    return "; ".join(words)[:120] or None


def run_view(run, detail=True):
    state = json.loads(run["state"]) if run["state"] else None
    out = {"id": run["id"], "automation_id": run["automation_id"], "version": run["version"],
           "trigger_ref": run["trigger_ref"], "started_at": run["started_at"], "finished_at": run["finished_at"],
           "status": run["status"], "cost": run["cost"]}
    if state:
        out["summary"] = _summary(state)
    if detail:
        out["steps"] = (state or {}).get("steps") if state else None   # None: pruned after KEEP_DAYS
        if state and state.get("error"):
            out["error"] = state["error"]
    return out


def _input_readable(c, owner, given):
    """A manual run's input names only what the owner may read: a task it may not see, or mail in someone else's
    mailbox, is refused before any run is queued."""
    task = given.get("task")
    tid = task.get("id") if isinstance(task, dict) else None
    if tid:
        row = H.task(c, tid)
        actor = A.acts_as(c, {"flow": {"owner": owner}})
        if row and not (actor and H.task_private_readable(c, actor, row)):
            raise Problem("forbidden", f"{owner} may not read task {tid}", 403)
    message = given.get("message")
    box = message.get("mailbox") if isinstance(message, dict) else None
    if box and not may_read_inbox(c, owner, box):
        raise Problem("forbidden", f"{owner} may not read {box}", 403)


def install(app, store, auth, mutate):
    from fastapi import Request
    from . import automation_overview as AO

    hub = Hub(app, store)
    store.automations = hub

    @app.middleware("http")
    async def automation_doorbell(request, call_next):
        response = await call_next(request)
        if _BELL.is_set():
            _BELL.clear()
            await asyncio.to_thread(hub.drain)
        return response

    def found(c, who, aid, what="see this automation", rule=None):
        auth.domain(who)
        row = _row(c, aid)
        if not row:
            raise Problem("not_found", "No such automation", 404)
        if not (rule or may_change)(c, who, flow_of(row)):
            raise Problem("forbidden", f"Only its owner, the people above its owner or its group's lead may {what}", 403)
        return row

    @app.get("/api/v2/automations")
    def automations_list(request: Request, owner: str = "", limit: int = 50, cursor: int = 0):
        """The automations the caller may change (the same rule decides what they see), newest first; up to 500 a page,
        and `next` is the `cursor` for the page after."""
        from . import automation_overview as AO
        who = request.state.identity
        with store.read() as c:
            auth.domain(who)
            rows = c.execute("SELECT * FROM automations WHERE deleted_at IS NULL"
                             + (" AND owner_id=?" if owner else "") + " ORDER BY created_at DESC",
                             ((F.owner_ref(owner),) if owner else ())).fetchall()
            pending = {r[0]: r[1] for r in c.execute("SELECT automation_id, COUNT(*) FROM automation_versions "
                                                     "WHERE status='pending' GROUP BY automation_id")}
            chart = Chart(c) if who.role in ("human", "bot") else None   # one chart for every row's rule
            jobs = AO.system_jobs(c, store.settings)
            visible = [{**view(c, dict(r), who, chart=chart, jobs=jobs), "pending": pending.get(r["id"], 0)}
                       for r in rows if may_change(c, who, {**flow_of(r), "readonly": False}, chart)]
        shown, more = page(visible[max(cursor, 0):], max(1, min(limit, 500)), max(cursor, 0), LIST_BYTES)
        return {"automations": shown, "next": more}

    @app.get("/api/v2/automations/question-sets")
    def automations_question_sets(request: Request):
        """The saved question sets a Decide step may name, each with its questions."""
        from clients import judge as J
        auth.domain(request.state.identity)
        sets = []
        for listed in J.list_sets():
            if listed.get("error"):
                continue
            data = J.load_set(listed["id"])
            sets.append({"id": f"{data['id']}@{data['version']}", "summary": data["summary"],
                         "questions": data["questions"]})
        return {"sets": sets}

    @app.get("/api/v2/automations/guide")
    def automations_guide(request: Request):
        """How BotOps builds and edits automations, with the flow format generated from the code."""
        auth.domain(request.state.identity)
        return {"text": F.guide_text()}

    @app.post("/api/v2/automations")
    def automations_create(request: Request, body: AutomationIn):
        """Save a new automation as version 1. The caller must be allowed to change an automation of that owner; a bot
        that may not proposes it instead (off, version 1 pending). What BotOps builds is saved off unless `enabled` is
        sent."""
        who = request.state.identity

        def work(c):
            auth.domain(who)
            fields = {k: v for k, v in body.model_dump().items() if v is not None}
            if _by_botops(who) and "enabled" not in body.model_fields_set:
                fields["enabled"] = False   # what BotOps builds waits for a Turn on, unless the request said on
            flow = _checked(c, fields, store.settings)
            if flow["builtin"]:
                raise Problem("forbidden", "Built-in automations come with Tico; the hub makes them", 403)
            # A step that sets its task's model is saved as a draft for a person who may set that model, as a bot's is.
            blocked = _stamp_blocked(c, auth, who, flow)
            if (who.role == "bot" and _by_botops(who) and not may_change(c, who, flow)) or (
                    blocked and may_change(c, who, flow)):
                return _bot_draft(c, who, flow)
            _require(c, who, flow, "make an automation for " + flow["owner"])
            return view(c, _save(c, who.actor, "auto_" + H.new_id(), flow, None, via=_via(who)), who, detail=True)
        return mutate(request, body, work)

    AO.install(app, store, auth)   # /runs and /stats, before /{aid} reads them as ids

    @app.get("/api/v2/automations/{aid}")
    def automations_get(request: Request, aid: str):
        who = request.state.identity
        with store.read() as c:
            return view(c, found(c, who, aid, rule=may_see), who, detail=True)

    def _resume_task_triggers(c, aid, flow):
        """Turning a "What wakes" automation on resumes its task triggers that ran too often (50 an hour)."""
        from . import redesign_migration
        try:
            ids = [r["id"] for r in redesign_migration.wake_rules_of(aid, flow)]
        except ValueError:
            return
        if ids and H._has_table(c, "rule_task_state"):
            c.execute(f"UPDATE rule_task_state SET paused_at=NULL, pause_reason=NULL WHERE rule_id IN "
                      f"({','.join('?' * len(ids))})", ids)

    @app.patch("/api/v2/automations/{aid}")
    def automations_update(request: Request, aid: str, body: AutomationPatch):
        """Change an automation. A change to its trigger, steps or batch times is a new version. A bot that may not
        change it proposes instead: a pending version a person restores. BotOps acting for a person changes it as that
        person could. Pausing a built-in one is the top of the org's alone."""
        who = request.state.identity

        def work(c):
            row = found(c, who, aid, "change this automation", rule=lambda c, who, flow: True)
            before = flow_of(row)
            changes = body.model_dump(exclude_unset=True)
            summary = changes.pop("summary", None)
            if "spend_cap" in changes and changes["spend_cap"] is None:
                changes.pop("spend_cap")
                before_cap = {k: v for k, v in before.items() if k != "spend_cap"}
            else:
                before_cap = before
            # What it reads is checked when that changes; pausing or renaming one whose owner lost access still works.
            merged = {**before_cap, **changes}
            flow = _checked(c, merged, store.settings,
                            access=any(merged.get(k) != before.get(k) for k in ("trigger", "steps", "owner")),
                            saved=before, aid=aid)
            if before.get("readonly"):
                # Tico's own rules, shown read-only: the top of the org may pause or resume it, and nothing else.
                if who.role != "owner" or set(changes) - {"enabled"}:
                    raise Problem("forbidden", "This built-in shows rules Tico runs itself; only the top of the org "
                                  "pauses it, and nobody edits it", 403)
                if changes.get("enabled", before["enabled"]) != before["enabled"]:
                    row = _save(c, who.actor, aid, {**before, "enabled": changes["enabled"]}, before, via=_via(who))
                warning = ("This is a built-in automation: the rules it shows are Tico's own and keep working while "
                           "it is paused.") if changes.get("enabled") is False else None
                return {**view(c, row, who, detail=True), **({"warning": warning} if warning else {})}
            # What a save makes live is checked against the steps already approved: a draft's are not, until someone who
            # may set their models turns it on, so turning it on by anyone else is refused.
            approved = _approved(c, row)
            turns_on = changes.get("enabled") is True and not before.get("enabled")
            versioned = bool(set(changes) & set(VERSIONED))
            blocked = _stamp_blocked(c, auth, who, flow, before if approved else None) \
                if versioned or turns_on else None
            if blocked and turns_on and not versioned:
                raise Problem("forbidden", f"Only whoever may change {blocked}'s model may turn on a routine that sets "
                              "it", 403)
            # A bot never sets the model a step's task runs on, and a person who may change the automation but not that
            # model proposes it: either way the change is a pending version a person with the rights restores.
            proposed = who.role == "bot" and not may_change(c, who, before)
            if proposed or (blocked and may_change(c, who, before)):
                if not changes or set(changes) - set(VERSIONED):
                    raise Problem("forbidden", "A bot may only propose a change to an automation's trigger, steps "
                                  "or batch times" if proposed else "A change to the model a step's task runs on is "
                                  "proposed as a version: send only its trigger, steps or batch times", 403)
                n = _propose(c, who.actor, aid, flow, before, summary)
                if may_see(c, who, before):
                    return {**view(c, row, who, detail=True), "pending": n}
                return {"id": aid, "pending": n, "can_edit": False}
            _require(c, who, before, "change this automation")
            _require(c, who, flow, "give this automation to " + flow["owner"])
            warning = None
            if before.get("builtin") and changes.get("enabled") is False:
                if who.role != "owner":
                    raise Problem("forbidden", "Only the top of the org pauses a built-in automation", 403)
                warning = "This is a built-in automation: the work it does stops until it is turned back on."
            if flow != before:
                # Turning a draft on is its approval, as restoring its version is: that version is active from now on.
                approve = turns_on and not approved and _versioned(flow) == _versioned(before)
                row = _save(c, who.actor, aid, flow, before, summary=summary, via=_via(who),
                            current=row["version"] if approve else None)
            if changes.get("enabled") is True and flow.get("wakes"):
                _resume_task_triggers(c, aid, flow)
            return {**view(c, row, who, detail=True), **({"warning": warning} if warning else {})}
        return mutate(request, body, work)

    def delete(request, aid, body):
        who = request.state.identity

        def work(c):
            auth.domain(who)
            row = deletable(c, who, aid)
            remove(c, who.actor, row, _via(who))
            return {"id": aid, "deleted": True}
        return mutate(request, body, work)

    @app.delete("/api/v2/automations/{aid}")
    def automations_delete(request: Request, aid: str, body: Empty | None = None):
        return delete(request, aid, body or Empty())

    @app.post("/api/v2/automations/{aid}/delete")
    def automations_delete_post(request: Request, aid: str, body: Empty | None = None):
        """The same delete for clients that only POST (the hub CLI and MCP)."""
        return delete(request, aid, body or Empty())

    @app.post("/api/v2/automations/{aid}/run")
    def automations_run(request: Request, aid: str, body: RunNow | None = None):
        """Run it now, whatever its trigger and batch times say; `input` stands in for the trigger's output (the page's
        "Test with sample"). Answers with the run once it has gone as far as it can now."""
        who, body = request.state.identity, body or RunNow()

        def work(c):
            row = found(c, who, aid, "run this automation",
                        rule=lambda c, who, flow: (may_see if flow.get("readonly") else may_change)(c, who, flow))
            flow = flow_of(row)
            if flow.get("readonly"):
                raise Problem("readonly", "This built-in shows work Tico runs itself; it has nothing to run", 409)
            if _hold_row(c, aid):
                raise Problem("on_hold", "The automation is on hold: release or drop its held runs first", 409)
            if not _approved(c, row) and (blocked := _stamp_blocked(c, auth, who, flow)):
                raise Problem("forbidden", f"Only whoever may change {blocked}'s model may run a routine that sets it "
                              "before it is turned on", 403)
            _input_readable(c, flow["owner"], body.input)
            if body.input and not F.all_hold(flow["trigger"].get("filters") or [], body.input):
                # A sample the trigger's filters drop is dropped as the trigger would drop it: counted, no run row.
                _count(c, aid, "dropped")
                return {"run": None, "dropped": True}
            start = dict(body.input)
            if not start and flow["trigger"].get("type") == "due":
                # A due routine run by hand with no sample starts as its scheduler would for its next due day.
                day = _next_due_day(row, flow["trigger"], datetime.now(timezone.utc)) or D.first_due(flow["trigger"])
                start = due_input(flow["trigger"], day)
            rid = queue(c, row, {**start, "manual": {"by": who.actor}}, "manual", manual=True, chain=(aid,))
            if not rid:
                raise Problem("on_hold", "The automation is on hold: release or drop its held runs first", 409)
            return {"run": rid}
        done = mutate(request, body, work)
        if done.get("dropped"):
            return done
        rid = done["run"]
        hub.drain()
        with store.read() as c:
            return {"run": run_view(c.execute("SELECT * FROM automation_runs WHERE id=?", (rid,)).fetchone())}

    @app.post("/api/v2/automations/{aid}/held")
    def automations_held(request: Request, aid: str, body: HeldAction):
        """End the run guard's hold: `release` queues its held runs, oldest first; `drop` skips them. Whoever may
        change the automation may do either."""
        who = request.state.identity

        def work(c):
            row = found(c, who, aid, "release or drop its held runs")
            n = _unhold(c, aid, who.actor, body.action, _via(who))
            if n is None:
                raise Problem("not_on_hold", "The automation is not on hold", 409)
            return {**view(c, row, who, detail=True), "moved": n}   # released runs drain after the commit (_ring)
        return mutate(request, body, work)

    @app.get("/api/v2/automations/{aid}/runs")
    def automations_runs(request: Request, aid: str, limit: int = 25, before: str = ""):
        """Its runs, newest first, each with its steps' status, outputs and cost (kept 30 days); `next` is the `before`
        for the page after. `daily` holds the counts, which include what the trigger's filters dropped. A system job's
        built-in shows the job's own runs (no steps; `count` the quiet runs folded into one)."""
        limit = max(1, min(limit, 100))
        with store.read() as c:
            row = found(c, request.state.identity, aid, rule=may_see)
            job = AO.system_key(flow_of(row))
            if job:
                rows, more = AO.system_runs_of(c, job, limit, before)
                runs = [AO.system_run_view(r, aid) for r in rows]
                return {"runs": runs, "next": runs[-1]["id"] if more and runs else None, "daily": []}
            at = c.execute("SELECT started_at, id FROM automation_runs WHERE id=? AND automation_id=?",
                           (before, aid)).fetchone() if before else None
            if before and not at:
                raise Problem("not_found", "No such run", 404)
            rows = c.execute("SELECT * FROM automation_runs WHERE automation_id=?"
                             + (" AND (started_at<? OR (started_at=? AND id<?))" if at else "")
                             + " ORDER BY started_at DESC, id DESC LIMIT ?",
                             (aid, *((at["started_at"], at["started_at"], at["id"]) if at else ()), limit + 1)).fetchall()
            days = [dict(r) for r in c.execute("SELECT day, runs, dropped FROM automation_daily WHERE automation_id=? "
                                               "ORDER BY day DESC LIMIT 30", (aid,))]
        runs, cut = page([run_view(r) for r in rows], limit, 0, RUN_BYTES)
        return {"runs": runs, "next": runs[-1]["id"] if cut is not None and runs else None, "daily": days}

    @app.get("/api/v2/automations/{aid}/versions")
    def automations_versions(request: Request, aid: str, status: str = "", limit: int = 50, cursor: int = 0):
        """Its versions, newest first; `status=pending` for the proposals waiting on a person."""
        with store.read() as c:
            found(c, request.state.identity, aid, rule=may_see)
            rows = [{"n": r["n"], "actor": r["actor"], "created_at": r["created_at"], "status": r["status"],
                     **({"summary": r["summary"]} if r["summary"] else {}), "flow": json.loads(r["flow"])}
                    for r in c.execute("SELECT * FROM automation_versions WHERE automation_id=?"
                                       + (" AND status=?" if status else "") + " ORDER BY n DESC",
                                       (aid, *((status,) if status else ())))]
        shown, more = page(rows[max(cursor, 0):], max(1, min(limit, 200)), max(cursor, 0), LIST_BYTES)
        return {"versions": shown, "next": more}

    @app.post("/api/v2/automations/{aid}/versions/{n}/restore")
    def automations_restore(request: Request, aid: str, n: int, body: Empty | None = None):
        """A pending version becomes the current one and keeps its number; an older one is saved as a new version.
        Either way its trigger, steps and batch times replace the current ones."""
        who, body = request.state.identity, body or Empty()

        def work(c):
            row = found(c, who, aid, "change this automation")
            old = c.execute("SELECT * FROM automation_versions WHERE automation_id=? AND n=?", (aid, n)).fetchone()
            if not old:
                raise Problem("not_found", f"No version {n}", 404)
            before = flow_of(row)
            pending = old["status"] == "pending"
            # A draft (off, with no version live yet) is turned on by its first pending version: Turn on.
            draft = pending and not before["enabled"] and not c.execute(
                "SELECT 1 FROM automation_versions WHERE automation_id=? AND status='active'", (aid,)).fetchone()
            flow = _checked(c, {**{k: v for k, v in before.items() if k not in VERSIONED}, **json.loads(old["flow"]),
                                **({"enabled": True} if draft else {})}, store.settings, saved=before, aid=aid)
            _require(c, who, flow, "restore this version")
            # A draft's row already holds the steps its pending version turns on, so nothing of it counts as kept.
            blocked = _stamp_blocked(c, auth, who, flow, before if _approved(c, row) else None)
            if blocked:
                raise Problem("forbidden", f"Only whoever may change {blocked}'s model may restore a version that "
                              "sets the model its tasks run on", 403)
            saved = _save(c, who.actor, aid, flow, before, current=n if pending else None, new_version=not pending,
                          via=_via(who))
            return view(c, saved, who, detail=True)
        return mutate(request, body, work)

    @app.delete("/api/v2/automations/{aid}/versions/{n}")
    def automations_discard(request: Request, aid: str, n: int, body: Empty | None = None):
        """Discard a pending version (a proposal); any other version stays (409)."""
        who, body = request.state.identity, body or Empty()

        def work(c):
            row = found(c, who, aid, "discard a proposal")
            old = c.execute("SELECT status FROM automation_versions WHERE automation_id=? AND n=?", (aid, n)).fetchone()
            if not old:
                raise Problem("not_found", f"No version {n}", 404)
            if old["status"] != "pending":
                raise Problem("not_pending", "Only a pending version can be discarded", 409)
            c.execute("DELETE FROM automation_versions WHERE automation_id=? AND n=?", (aid, n))
            return view(c, row, who, detail=True)
        return mutate(request, body, work)

    # An inbox's send switch: on by default; off, send_reply saves a draft and logs send_off.
    def inbox_rights(c, who, inbox):
        auth.domain(who)
        if who.role == "owner":
            return
        person = H.human(c, H.actor_id(who.actor)) if who.role == "human" else None
        if not (person and "@" in inbox and str(person.get("email") or "").lower() == inbox):
            raise Problem("forbidden", "Only the inbox's person or the top of the org sets its send switch", 403)

    @app.get("/api/v2/inboxes/{inbox}")
    def inbox_view(request: Request, inbox: str):
        inbox = inbox.strip().lower()
        with store.read() as c:
            inbox_rights(c, request.state.identity, inbox)
            return {"inbox": inbox, "send": A.send_on(c, inbox)}

    @app.patch("/api/v2/inboxes/{inbox}")
    def inbox_update(request: Request, inbox: str, body: InboxSend):
        who, inbox = request.state.identity, inbox.strip().lower()

        def work(c):
            inbox_rights(c, who, inbox)
            before = {"inbox": inbox, "send": A.send_on(c, inbox)}
            c.execute("INSERT INTO automation_inboxes(inbox,send,updated_at,updated_by) VALUES(?,?,?,?) "
                      "ON CONFLICT(inbox) DO UPDATE SET send=excluded.send,updated_at=excluded.updated_at,"
                      "updated_by=excluded.updated_by", (inbox, int(body.send), H.now(), who.actor))
            after = {"inbox": inbox, "send": body.send}
            if after != before:
                audit_log.write(c, who.actor, "inbox:" + inbox, before, after, _via(who))
            return after
        return mutate(request, body, work)

    @app.get("/api/v2/people/{person}/assistant")
    def person_assistant(request: Request, person: str):
        """The bot an `assistant:<person>` step runs on, once one has run (null before)."""
        from . import assistant
        with store.read() as c:
            auth.domain(request.state.identity)
            if not H.human(c, person):
                raise Problem("not_found", "No such person", 404)
            return {"bot": hub.settings.assistant_bot if assistant.find_room(c, "human:" + person) else None}

    # The computer that reads a mailbox carries out the mail actions queued for it (runner/connectors.py).
    def mail_computer(c, who, mailbox):
        """Only the computer that reads this mailbox claims and reports its actions: by the check mail publishing uses
        (backend/connectors.py `publisher`), and, once the mailbox has been published, only the runner that did."""
        from .connectors import human_of
        runner = app.state.execution.runner(c, who)
        if not runner or runner["owner_id"] not in (store.settings.processing_operators or (auth.owner_id(c),)):
            raise Problem("forbidden", "This machine is not assigned the private app-connector capability", 403)
        box = c.execute("SELECT runner_id FROM mail_mailboxes WHERE address=?", (mailbox,)).fetchone()
        if not box:   # not published yet: no computer has shown it reads this mailbox
            raise Problem("forbidden", "No computer has published this mailbox yet", 403)
        if not human_of(c, mailbox) or (box["runner_id"] and box["runner_id"] != runner["id"]):
            raise Problem("forbidden", "Another computer reads this mailbox", 403)
        return runner

    @app.post("/api/v2/automations/mail-actions/claim")
    def automation_mail_claim(request: Request, body: MailClaim):
        """A lease of up to 20 of the mailbox's queued actions for MAIL_LEASE_MINUTES. A send is read against the
        inbox's send switch now, not when it was queued: off, a send or forward is claimed as a draft and its step
        shows send_off."""
        who = request.state.identity

        def work(c):
            mailbox = body.mailbox.strip().lower()
            runner = mail_computer(c, who, mailbox)
            now = H.now()
            for row in c.execute("SELECT * FROM automation_mail_actions WHERE mailbox=? AND state='claimed' "
                                 "AND claimed_at<?", (mailbox, H.shift(now, minutes=-MAIL_LEASE_MINUTES))).fetchall():
                if row["attempts"] >= MAIL_TRIES:
                    _mail_report(c, row, "failed", {**json.loads(row["result"] or "{}"), "ok": False,
                                                    "detail": f"no result after {MAIL_TRIES} tries"})
                else:
                    c.execute("UPDATE automation_mail_actions SET state=? WHERE id=?", (_requeue(c, row), row["id"]))
            rows, spent = [], 0
            for r in c.execute("SELECT * FROM automation_mail_actions WHERE state='queued' AND mailbox=? "
                               "ORDER BY created_at LIMIT 20", (mailbox,)):
                size = len(r["args"] or "") + len(r["result"] or "")
                if rows and spent + size > LIST_BYTES:
                    break                  # the rest stay queued for the next claim
                spent += size
                rows.append(dict(r))
            sending = A.send_on(c, mailbox)
            out = []
            for r in rows:
                action, result, args = r["action"], json.loads(r["result"] or "{}"), json.loads(r["args"])
                if action in ("send", "forward") and not sending:
                    # Both send mail out: a reply becomes a draft reply, a forward a draft to its target.
                    action, result = "draft" if action == "send" else action, {**result, "send_off": True}
                    args = {**args, "draft_only": True} if action == "forward" else args
                    owner = c.execute("SELECT owner_id FROM automations WHERE id=?", (r["automation_id"],)).fetchone()
                    H.event(c, A.actor_of(owner["owner_id"] if owner else ""), "automation.send_off", r["automation_id"],
                            {"run": r["run_id"], "mailbox": mailbox, "msg_id": r["msg_id"]})
                c.execute("UPDATE automation_mail_actions SET state='claimed',claimed_at=?,claimed_by=?,"
                          "attempts=attempts+1,action=?,result=? WHERE id=?",
                          (now, runner["id"], action, json.dumps(result) if result else None, r["id"]))
                out.append({"id": r["id"], "mailbox": r["mailbox"], "msg_id": r["msg_id"], "action": action,
                            "args": args, "attempt": r["attempts"] + 1})
            return {"actions": out, "lease_seconds": MAIL_LEASE_MINUTES * 60}
        return mutate(request, body, work)

    @app.post("/api/v2/automations/mail-actions/{mid}/result")
    def automation_mail_result(request: Request, mid: str, body: MailResult):
        """What the computer did. Idempotent by action id: once done or failed, a repeat (a lost answer, a late
        report after the lease ran out) changes nothing. A failure it may retry goes back to the queue until
        MAIL_TRIES claims; a late success counts, so the action is never carried out again."""
        who = request.state.identity

        def work(c):
            row = c.execute("SELECT * FROM automation_mail_actions WHERE id=?", (mid,)).fetchone()
            if not row:
                raise Problem("not_found", "No such mail action", 404)
            mail_computer(c, who, row["mailbox"])
            if row["state"] in ("done", "failed", "cancelled"):
                return {"id": mid, "state": row["state"]}
            state = ("done" if body.ok else _requeue(c, row) if body.retry and row["attempts"] < MAIL_TRIES else "failed")
            result = {**json.loads(row["result"] or "{}"), "ok": body.ok, "detail": body.detail,
                      **{k: v for k, v in (("message", body.message), ("draft", body.draft), ("thread", body.thread))
                         if v}}
            _mail_report(c, row, state, result)
            return {"id": mid, "state": state}
        return mutate(request, body, work)


class MailClaim(Contract):
    mailbox: str = Field(min_length=3, max_length=320)


class MailResult(Contract):
    ok: bool
    detail: str = Field(default="", max_length=1000)
    retry: bool = True                                      # a failure worth another claim (not: no such message)
    message: str = Field(default="", max_length=200)        # the Gmail message sent
    draft: str = Field(default="", max_length=200)          # the Gmail draft made
    thread: str = Field(default="", max_length=200)
