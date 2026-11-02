"""The redesign's converter (PLAN §8, CONTRACT §4): today's routines, wake rules, system jobs, inboxes, limits and
budgets become automations and the new amounts, once per install, at startup (`cutover`).

- **Cutover** runs once (registry_metadata `redesign_cutover`): every object is planned, then converted one per
  transaction: the automation is saved with its `source`, and in the same write the old row stops (a routine's row
  is soft-deleted, a wake rule's row removed). A schedule automation takes the routine's own `next_due`, so an
  occurrence due across the cutover fires once, from one system. Each object is recorded in `redesign_migrated` as it
  goes, so a cutover a restart cut off goes on from where it was. An object that cannot move (its bot is gone, its
  text too long, no person to own it) or that failed stays as it was and is listed once in a task for the top of the
  org; the rest move.
- **Adopt**: after the cutover, a routine or wake rule something still writes the old way (`routines.create`, a Dev
  tickets type's hand-off rows, a Custom profile's rows) moves to an automation in the same write.

What stays where it is: task rules, system jobs and the Health Monitor show as read-only built-in automations
(`readonly`, with the job's `controls`) and keep running from their own tables. A converted wake rule keeps deciding
wakes from rules.py, which reads it from its "What wakes" automation (`wake_rows`), so an edit there (a rule's step
turned off, deleted, its event or outcome changed) is what wakes the bot. Old tables keep their rows.
"""
import json
import logging
import math
import sys

from . import automation_flow as F
from .store import H, Problem

log = logging.getLogger("tico.redesign_migration")
CUTOVER = "redesign_cutover"                 # registry_metadata: {state: running|done, migration, at, ...}
WAKES_PREFIX = "auto_wakes_"                  # custom "What wakes" automations: rules.py reads its rows from these
GUARD = {"turns_per_hour": 300, "turns_per_day": 2500}   # CONTRACT §1 run guard defaults
STRATEGY_NAME = "Strategy Bot"
DEFAULT_PROMPT = ("Read this message for {name}. Archive what needs nothing, label what belongs somewhere, draft a "
                  "reply where one is wanted, and leave the rest for {name}.")
OUTCOME_DO = {"wake": "wake_bot", "batch": "wake_bot", "action": "wake_bot", "note": "note", "ignore": "archive",
              "ask": "leave_for_you"}
# The message the inbox automation runs on, handed to the Assistant the way message automations do: each `{{field}}`
# becomes a quoted data block after the instructions (automation_flow.render_prompt), never read as instructions.
MESSAGE_PART = ("\n\nThe message to handle: id {{message.id}} in thread {{message.thread}}, from {{message.from}} "
                "({{message.from_name}}), subject {{message.subject}}. Its body:\n{{message.body}}")
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS redesign_migrated(kind TEXT NOT NULL, id TEXT NOT NULL, migration TEXT NOT NULL, "
    "automation TEXT, PRIMARY KEY(kind, id))",
)


def ensure(c):
    for statement in SCHEMA:
        c.execute(statement)
    # CONTRACT §1 30-minute limits (wave 2 adds the same columns; both are idempotent).
    from .hubdb import add_column
    from . import usage_limits
    usage_limits.ensure(c)
    add_column(c, "usage_limits", "per_30m_usd", "REAL")
    add_column(c, "usage_pools", "per_30m_usd", "REAL")
    # What became of each object (`outcome` converted | kept | failed, `why`), and when the owner was told of it.
    for column in ("outcome", "why", "title", "migrated_at", "told"):
        add_column(c, "redesign_migrated", column, "TEXT")


# ---------------------------------------------------------------------------------------------------- reading
def _now():
    return H.now()


def _json(raw, default):
    try:
        value = json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _meta(c, key):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(c, key, raw):
    if raw is None:
        c.execute("DELETE FROM registry_metadata WHERE key=?", (key,))
    else:
        c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, raw))


def _table(c, name):
    return bool(H._has_table(c, name))


def _columns(c, table):
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def half(value):
    """$X an hour is $X/2 per 30 minutes, rounded up to $0.10 (PLAN decision 16)."""
    return round(math.ceil(round(float(value) / 2 * 10, 6)) / 10, 6)


def _flag(obj_id, kind, text, keeps=False):
    return {"id": f"{obj_id}#{kind}", "kind": kind, "text": text, **({"keeps": True} if keeps else {})}


def _owner_person(who):
    return "person:" + H.actor_id(who.actor) if who.role in ("owner", "human") and H.actor_id(who.actor) else None


def _bot_name(c, slug):
    row = H.bot(c, slug) or {}
    return (row.get("display_name") or "").strip() or slug


def _cron(preset, at):
    """A system job's preset as a five-field cron, in company time."""
    hour, minute = (int(p) for p in (at or "09:00").split(":"))
    return {"15m": "*/15 * * * *", "hourly": "0 * * * *", "4h": "0 */4 * * *", "daily": f"{minute} {hour} * * *",
            "business": "0 8-17 * * 1-5", "weekly": f"{minute} {hour} * * 1"}.get(preset or "hourly", "0 * * * *")


def _zone():
    """Company time (system jobs run in it) as a zone name."""
    from . import system_routines as SR
    zone = SR.zone()
    return str(getattr(zone, "key", zone))


def _mirror(name, owner, key, controls, source, words=""):
    """A read-only built-in automation that shows something Tico still runs from its own table."""
    return {"name": name[:120], "owner": owner, "builtin": True, "readonly": True, "enabled": bool(controls.get("on", True)),
            "trigger": {"type": "event", "name": "tico.builtin." + key},
            "steps": [], "controls": controls, "source": source, **({"description": words[:500]} if words else {})}


# ---------------------------------------------------------------------------------------------------- the plan
def plan(c, who):
    """Every object the converter knows, in report order: {kind, id, title, becomes, flags, _do}. `_do` is what convert
    does with it and never leaves the module."""
    ensure_read = _table(c, "redesign_migrated")
    done = {(r["kind"], r["id"]) for r in c.execute("SELECT kind, id FROM redesign_migrated")} if ensure_read else set()
    me = _owner_person(who)
    out = []
    out += _routines(c, done)
    out += _wake_rules(c, done, me)
    out += _builtins(c, done, me)
    out += _inboxes(c, done)
    out += _bots(c, done)
    out += _unchanged(c)
    out += _limits(c, done)
    out += _budgets(c, done)
    return out


def _routines(c, done, fresh=False):
    """The routines still to move. `fresh`: one written after the cutover (`adopt`), such as a template's routine Tico
    seeds while it builds the bot; a bot still `planned` (being set up, not away) keeps the routine's own switch."""
    from . import automations, event_catalog as CAT, routines, triggers
    rows = c.execute("SELECT s.*, coalesce(sc.timezone, ?) AS timezone, coalesce(sc.enabled, 1) AS enabled "
                     "FROM schedules s LEFT JOIN schedule_config sc ON sc.schedule_id=s.id "
                     "WHERE s.deleted_at IS NULL ORDER BY s.bot, s.id", (routines.DEFAULT_ZONE,)).fetchall()
    inbox_bots = {b["bot"] for b in routines.message_bot_mailboxes(c)}
    out = []
    for row in rows:
        row = dict(row)
        sid, bot = row["id"], row["bot"]
        if ("routine", sid) in done:
            continue
        flags, text = [], row["playbook"] or routines.DEFAULT_TEXT
        found = H.bot(c, bot)
        event = row["event_name"] or ""
        if event.startswith(triggers.EVENT):
            trigger = {"type": "webhook", "id": event[len(triggers.EVENT):]}
            if not triggers.exists(c, event):
                flags.append(_flag(sid, "no_trigger", "Its incoming webhook is gone; it stays a routine", keeps=True))
        elif event:
            trigger = {"type": "event", "name": event}
            if not CAT.canonical(event):
                flags.append(_flag(sid, "unknown_event", f"Tico does not list the event {event}; the automation "
                                                         "runs only if something sends it"))
        else:
            trigger = {"type": "schedule", "cron": row["cron"], "tz": row["timezone"] or routines.DEFAULT_ZONE}
        enabled = bool(row["enabled"])
        if not found or found.get("state") == "archived":
            flags.append(_flag(sid, "no_bot", f"Its bot {bot} is gone; it stays a routine", keeps=True))
        elif found.get("state") != "active" and not (fresh and found.get("state") == "planned"):
            # It keeps its own switch: the tick passes over a schedule whose bot is away (automations `_bot_away`).
            flags.append(_flag(sid, "bot_not_active", f"{_bot_name(c, bot)} is {found.get('state')}: it runs again "
                                                      "when the bot is back"))
        if bot in inbox_bots and enabled:
            enabled = False
            flags.append(_flag(sid, "inbox_bot", f"{_bot_name(c, bot)} reads an inbox and retires with it: this "
                                                 "routine starts off"))
        if len(text) > F.MAX_PROMPT:
            # Nothing runs a routine left behind, so it moves off, its text cut to fit, for its owner to finish.
            text, enabled = text[:F.MAX_PROMPT], False
            flags.append(_flag(sid, "too_long", f"Its text was longer than {F.MAX_PROMPT:,} characters: it moved "
                                                "off with the text cut to fit; shorten it and turn it on"))
        if "{{" in text:
            flags.append(_flag(sid, "placeholders", "Its text has {{placeholders}}: a Bot turn fills them from the "
                                                    "automation's values, which may differ from the routine's"))
        flow = {"name": (row["title"] or "Routine")[:120], "owner": "bot:" + bot, "enabled": enabled,
                "trigger": trigger, "steps": [{"id": "turn", "type": "bot_turn", "bot": "bot:" + bot, "prompt": text}],
                "source": {"kind": "routine", "id": sid}}
        do = "routine"
        if automations.is_briefing_routine(row) and trigger["type"] == "schedule":
            # The hub's Market briefing does this now: the routine becomes that built-in, on the routine's own time
            # and words, so the two never both run.
            flow = {**automations.briefing_flow(trigger["cron"], trigger["tz"], text), "builtin": True,
                    "enabled": enabled, "source": flow["source"]}
            do = "adopt_briefing"
        out.append({"kind": "routine", "id": sid, "title": row["title"], "becomes": flow, "flags": flags,
                    "_do": (do, {"next_due_at": row["next_due_at"]})})
    return out


def _rule_step(n, row):
    """One wake rule as its own Filter step: the event (and the rule's simple conditions) as `when`, its outcome as the
    action, and the whole row in the action's args, which is what rules.py reads back."""
    when = _json(row["condition"], {})
    params = _json(row["params"], {})
    conds = [] if row["event"] == "*" else [{"field": "event", "op": "is", "value": row["event"]}]
    for key, value in sorted(when.items()):
        if isinstance(value, (str, int, float, bool)) and F.FIELD.fullmatch(str(key)) and len(conds) < F.MAX_CONDS:
            conds.append({"field": "task." + str(key), "op": "is", "value": value})
    rule = {k: row[k] for k in ("id", "scope_kind", "scope_id", "event", "position", "note", "created_by",
                                 "revision") if k in row.keys()}
    # Saved inside the automation's flow under these keys since the cutover; the columns are created_at and updated_at.
    rule.update({saved: row[column] for saved, column in (("created", "created_at"), ("updated", "updated_at"))
                 if column in row.keys()})
    rule["action_by"] = dict(row).get("action_by")
    rule["when"] = when
    do = OUTCOME_DO.get(row["then_outcome"], "wake_bot")
    if row["then_outcome"] == "action" and params.get("action") in F.ACTIONS:
        do = params["action"]
    return {"id": f"r{n}", "type": "filter", "enabled": bool(row["enabled"]),
            "rules": [{"when": conds, "match": "all", "stop": True,
                       "then": [{"do": do, "args": {"outcome": row["then_outcome"], "params": params, "rule": rule}}]}]}


def _wakes_name(c, scope, ident, me):
    """A "What wakes" automation's name and owner for one scope."""
    if scope == "bot" and H.bot(c, ident):
        return f"What wakes {_bot_name(c, ident)}", "bot:" + ident
    words = {"team": "every bot", "department": f"the {ident} bots", "type": f"bots on {ident} tasks",
             "task": f"bots on task {ident}"}.get(scope, f"{scope} {ident}".strip())
    return f"What wakes {words}", me


def _wake_rules(c, done, me):
    """The table's wake rules, one "What wakes" automation per scope (a flow holds at most F.MAX_STEPS steps, so a
    longer list becomes more than one)."""
    from . import tico_notices
    rows = [r for r in c.execute("SELECT * FROM rules ORDER BY scope_kind, scope_id, position, created_at, id").fetchall()
            if not tico_notices.is_builtin_rule(r["id"]) and ("wake_rule", r["id"]) not in done]
    groups = {}
    for row in rows:
        groups.setdefault((row["scope_kind"], row["scope_id"] or ""), []).append(row)
    out = []
    for (scope, ident), listed in groups.items():
        base, owner = _wakes_name(c, scope, ident, me)
        for part in range(0, len(listed), F.MAX_STEPS):
            members = listed[part:part + F.MAX_STEPS]
            name = f"{base[:110]} ({part // F.MAX_STEPS + 1})" if part else base
            flow = {"name": name[:120], "owner": owner, "enabled": True,
                    "wakes": {"scope_kind": scope, "scope_id": ident}, "trigger": {"type": "event", "name": "tico.wake"},
                    "steps": [_rule_step(n + 1, row) for n, row in enumerate(members)],
                    "source": [{"kind": "wake_rule", "id": row["id"]} for row in members]}
            group = {"scope_kind": scope, "scope_id": ident, "part": part, "rules": [row["id"] for row in members]}
            for row in members:
                flags = []
                if owner != "bot:" + ident:
                    flags.append(_flag(row["id"], "scope", f"A {scope} rule, not one bot's: it goes in \"{name}\", "
                                                           "owned by you"))
                if not owner:
                    flags.append(_flag(row["id"], "no_owner", "No person to own it; it stays a rule", keeps=True))
                out.append({"kind": "wake_rule", "id": row["id"], "title": f"{row['event']} → {row['then_outcome']}",
                            "becomes": flow, "flags": flags, "_do": ("wakes", group)})
    return out


WAKES_BUILTIN = "auto_builtin_wakes"   # the hub's own "What wakes bots" (automations.ensure_builtins)


def wakes_builtin(owner):
    """`What wakes bots`: the built-in wake rules (rules.py, which keeps deciding by them) shown read-only, a Filter
    step per ten rules. Its `event` trigger is never emitted and `readonly` keeps the engine off it."""
    from . import rules
    listed = rules._builtins()
    steps = []
    for start in range(0, len(listed), F.MAX_RULES):
        chunk = listed[start:start + F.MAX_RULES]
        steps.append({"id": f"b{start // F.MAX_RULES + 1}", "type": "filter", "rules": [
            {"when": [] if r["event"] == "*" else [{"field": "event", "op": "is", "value": r["event"]}],
             "then": [{"do": OUTCOME_DO.get(r["then"], "wake_bot"), "args": {"outcome": r["then"], "rule": r["id"],
                                                                           "words": r.get("words", "")[:300]}}],
             "stop": True} for r in chunk]})
    return {**_mirror("What wakes bots", owner, "wakes", {"on": True}, {"kind": "builtin", "id": "wake-builtins"}),
            "steps": steps}


def _builtins(c, done, me):
    """The built-in wake rules, task rules, system jobs and the Health Monitor: read-only built-ins. The hub makes
    `What wakes bots` itself once the redesign is on; the converter then only records it (`adopt`)."""
    from . import system_routines as SR
    out = []
    if ("builtin", "wake-builtins") not in done and me:
        # Always the hub's own `What wakes bots` (made here when the hub has not made it yet), never a second one.
        out.append({"kind": "builtin", "id": "wake-builtins", "title": "Built-in wake rules", "becomes": wakes_builtin(me),
                    "flags": [], "_do": ("adopt", {})})
    if _table(c, "task_rules"):
        for row in c.execute("SELECT * FROM task_rules ORDER BY created_at, id").fetchall():
            if ("task_rule", row["id"]) in done or not me:
                continue
            controls = {"on": bool(row["enabled"]), "preset": row["preset"], "daily_at": row["daily_at"],
                        "department": row["department"], "triggers": _json(row["triggers"], []),
                        "actions": _json(row["actions"], [])}
            flow = _mirror(row["name"], me, "task_rule", controls, {"kind": "task_rule", "id": row["id"]})
            out.append({"kind": "task_rule", "id": row["id"], "title": row["name"], "becomes": flow, "flags": [],
                        "_do": ("mirror", {})})
    cfg = SR.config(c)
    for job in SR.JOBS:
        oid = "job:" + job.key
        if ("system_job", oid) in done or not me:
            continue
        controls = {"on": True, "schedule": job.schedule, **(cfg.get(job.key) or {})}
        if job.key == "stale_review":
            control = cfg[job.key]
            flow = {"name": job.name, "owner": me, "enabled": bool(control["on"]),
                    "trigger": {"type": "schedule", "cron": _cron(control["preset"], control["at"]), "tz": _zone()},
                    "steps": [{"id": "tasks", "type": "find", "source": "tasks", "query": "open tasks that look finished "
                               "or forgotten", "limit": 20},
                              {"id": "verdict", "type": "decide", "set": "stale-review@1", "out": "d1", "level": "low"},
                              {"id": "route", "type": "paths", "paths": [
                                  {"name": "Suggest to the owner", "when": [{"field": "d1.verdict", "op": "is_not",
                                                                             "value": "leave"}],
                                   "steps": [{"id": "suggest", "type": "action", "do": "leave_for_you", "args": {}}]}]}],
                    "source": {"kind": "system_job", "id": oid}}
            out.append({"kind": "system_job", "id": oid, "title": job.name, "becomes": flow, "flags": [],
                        "_do": ("stale_review", {"on": bool(control["on"])})})
            continue
        flow = _mirror(job.name, me, "job." + job.key, controls, {"kind": "system_job", "id": oid}, job.what)
        out.append({"kind": "system_job", "id": oid, "title": job.name, "becomes": flow, "flags": [],
                    "_do": ("mirror", {})})
    health = H.bot(c, "health-monitor")
    if health and ("health_monitor", "bot:health-monitor") not in done and me:
        flow = _mirror("Health Monitor", me, "health", {"on": health.get("state") == "active", "bot": "health-monitor"},
                       {"kind": "bot", "id": "health-monitor"}, "Finds install problems and files them for BotOps.")
        out.append({"kind": "health_monitor", "id": "bot:health-monitor", "title": "Health Monitor", "becomes": flow,
                    "flags": [], "_do": ("mirror", {})})
    return out


def _inboxes(c, done):
    from . import people as P, routines, views
    roster = views.roster(c)
    out = []
    for box in routines.message_bot_mailboxes(c):
        oid = "inbox:" + box["address"]
        if ("inbox", oid) in done:
            continue
        person = P.person(box["person_id"], roster) or {}
        name = person.get("name") or box["person_id"]
        found = c.execute("SELECT content FROM mail_agent_instructions WHERE bot=?", (box["bot"],)).fetchone() \
            if _table(c, "mail_agent_instructions") else None
        prompt = (found["content"] if found and (found["content"] or "").strip() else DEFAULT_PROMPT.format(name=name))
        prompt = prompt.rstrip() + MESSAGE_PART
        flags = []
        if len(prompt) > F.MAX_PROMPT:
            flags.append(_flag(oid, "too_long", f"The Inbox Manager's instructions are longer than {F.MAX_PROMPT:,} "
                                                "characters; the inbox stays with its bot until they are shorter",
                               keeps=True))
        bot = H.bot(c, box["bot"]) or {}
        flow = {"name": f"{name}'s inbox"[:120], "owner": "person:" + box["person_id"], "enabled": True,
                "trigger": {"type": "message", "inbox": box["address"]},
                "steps": [{"id": "read", "type": "llm", "as": "assistant:" + box["person_id"], "prompt": prompt[:F.MAX_PROMPT],
                           "out": "triage"}],
                "source": [{"kind": "inbox", "id": box["address"]}, {"kind": "bot", "id": box["bot"]}]}
        out.append({"kind": "inbox", "id": oid, "title": f"{name}'s inbox ({_bot_name(c, box['bot'])})",
                    "becomes": flow, "flags": flags,
                    "_do": ("inbox", {"bot": box["bot"], "state": bot.get("state") or "active"})})
    return out


def _bots(c, done):
    from . import strategy_bot
    out = []
    row = H.bot(c, strategy_bot.SLUG)
    if row and ("bot", "bot:" + strategy_bot.SLUG) not in done:
        current = (row.get("display_name") or "").strip()
        default = current in ("", strategy_bot.NAME, STRATEGY_NAME, *strategy_bot.OLD_NAMES)
        flags = [] if default else [_flag("bot:" + strategy_bot.SLUG, "custom_name",
                                          f"Someone named it {current}; it keeps that name and becomes the Strategy "
                                          "bot")]
        out.append({"kind": "bot", "id": "bot:" + strategy_bot.SLUG, "title": current or strategy_bot.NAME,
                    "becomes": {"slug": strategy_bot.SLUG, "display_name": STRATEGY_NAME if default else current,
                                "role": "the strategy bot"},
                    "flags": flags, "_do": ("rename", {"before": row.get("display_name"), "rename": default})})
    return out


def _unchanged(c):
    out = []
    if _table(c, "listening_destinations"):
        for row in c.execute("SELECT key, receiver FROM listening_destinations ORDER BY key").fetchall():
            out.append({"kind": "destination", "id": "destination:" + row["key"], "title": row["key"],
                        "becomes": "unchanged", "flags": [], "_do": None})
    if _table(c, "goals"):
        for row in c.execute("SELECT id, title FROM goals ORDER BY created_at, id").fetchall():
            out.append({"kind": "goal", "id": "goal:" + row["id"], "title": row["title"], "becomes": "unchanged",
                        "flags": [], "_do": None})
    return out


def _limit(oid, title, hourly, where):
    amount = half(hourly)
    flags = []
    if amount * 2 > float(hourly) + 1e-9:
        flags.append(_flag(oid, "rounding", f"${float(hourly):.2f} an hour is ${amount:.2f} per 30 minutes after "
                                            f"rounding up: up to ${amount * 2:.2f} an hour"))
    return {"kind": "spend_limit", "id": oid, "title": title, "becomes": {"per_30m_usd": amount, "hourly_usd": None},
            "flags": flags, "_do": ("limit", {**where, "per_30m_usd": amount})}


def _limits(c, done):
    from . import usage_limits
    out = []
    if _table(c, "usage_limits") and "hourly_usd" in _columns(c, "usage_limits"):
        for row in c.execute("SELECT bot, hourly_usd FROM usage_limits WHERE hourly_usd IS NOT NULL ORDER BY bot"):
            if ("spend_limit", "limit:" + row["bot"]) not in done and row["hourly_usd"] > 0:
                out.append(_limit("limit:" + row["bot"], f"{row['bot']} hourly limit", row["hourly_usd"],
                                  {"table": "usage_limits", "key": row["bot"]}))
    if _table(c, "usage_pools"):
        for row in c.execute("SELECT scope, hourly_usd FROM usage_pools WHERE hourly_usd IS NOT NULL ORDER BY scope"):
            oid = "limit:pool:" + row["scope"]
            if ("spend_limit", oid) not in done and row["hourly_usd"] > 0:
                out.append(_limit(oid, f"{row['scope']} hourly limit", row["hourly_usd"],
                                  {"table": "usage_pools", "key": row["scope"]}))
    stored = _json(_meta(c, usage_limits.KEY), {})
    hourly = usage_limits._amount(stored.get("hourly_usd"))
    if hourly and ("spend_limit", "limit:company-default") not in done:
        out.append(_limit("limit:company-default", "Company default hourly limit", hourly, {"table": "registry"}))
    return out


def _raised(values):
    """A budget raised to the guard, unless set higher or to 0 (no limit)."""
    return {k: (GUARD[k] if isinstance(v, int) and 0 < v < GUARD[k] else v) for k, v in values.items()}


def _budgets(c, done):
    from . import turn_budgets as TB
    out = []
    stored = _json(_meta(c, TB.TEAM_KEY), {})
    team = TB.team(c)
    after = _raised(team)
    if ("turn_budget", "budget:team") not in done:
        out.append({"kind": "turn_budget", "id": "budget:team", "title": "Team turn budgets", "becomes": after,
                    "flags": [], "_do": ("budget_team", {"raise": after != team and bool(stored)})})
    for row in c.execute("SELECT bot, config FROM bot_config ORDER BY bot").fetchall():
        mine = {k: v for k, v in TB.own(_json(row["config"], {})).items() if v is not None}
        if not mine or ("turn_budget", "budget:" + row["bot"]) in done:
            continue
        raised = _raised(mine)
        out.append({"kind": "turn_budget", "id": "budget:" + row["bot"], "title": f"{row['bot']} turn budget",
                    "becomes": raised if raised != mine else "unchanged", "flags": [],
                    "_do": ("budget_bot", {"bot": row["bot"], "values": raised}) if raised != mine else None})
    return out


# ---------------------------------------------------------------------------------------------------- convert
def _save(c, who, flow):
    from . import automations
    aid = (WAKES_PREFIX if flow.get("wakes") else "auto_mig_") + H.new_id()
    checked = automations._checked(c, flow, wake_steps=False)
    via = "migration" if not who.via else f"{who.via}, migration"
    automations._save(c, who.actor, aid, checked, None, via=via)
    return aid


def _adopt(c, who, flow):
    """The Librarian's briefing routine as the hub's Market briefing built-in: made if the hub has not made it yet
    (or a restore took it away), then given the routine's time, words and on/off."""
    from . import automations
    aid = "auto_builtin_" + automations.BRIEFING_KEY
    row = c.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone()
    if row and row["deleted_at"]:
        c.execute("UPDATE automations SET deleted_at=NULL WHERE id=?", (aid,))
    via = "migration" if not who.via else f"{who.via}, migration"
    automations._save(c, who.actor, aid, automations._checked(c, flow, wake_steps=False),
                      automations.flow_of(row) if row else None, via=via)
    return aid


def _mark(c, kind, ident, mid, aid, outcome="converted", why="", title=""):
    c.execute("INSERT OR REPLACE INTO redesign_migrated(kind, id, migration, automation, outcome, why, title, migrated_at) "
              "VALUES(?,?,?,?,?,?,?,?)", (kind, ident, mid, aid, outcome, why[:1000], (title or "")[:300], _now()))


def _convert_one(c, who, mid, obj, group_done):
    """One object, in the caller's transaction. Returns the automation id (or None) it made."""
    from . import strategy_bot, team_rules, turn_budgets as TB, usage_limits
    kind, args = obj["_do"]
    now = _now()
    if kind in ("routine", "adopt_briefing"):
        live = c.execute("SELECT next_due_at FROM schedules WHERE id=? AND deleted_at IS NULL", (obj["id"],)).fetchone()
        if not live:
            return None
        aid = _save(c, who, obj["becomes"]) if kind == "routine" else _adopt(c, who, obj["becomes"])
        if obj["becomes"]["trigger"]["type"] == "schedule" and live["next_due_at"]:
            # The routine's own next fire: the occurrence it would have run is the automation's, once.
            c.execute("UPDATE automations SET next_due_at=? WHERE id=?", (live["next_due_at"], aid))
        c.execute("UPDATE schedules SET deleted_at=?, next_due_at=NULL, updated_at=? WHERE id=?", (now, now, obj["id"]))
        H.event(c, who.actor, "routine.converted", obj["id"], {"automation": aid})
        _mark(c, "routine", obj["id"], mid, aid)
        return aid
    if kind == "wakes":
        key = (args["scope_kind"], args["scope_id"], args.get("part", 0))
        if key in group_done:
            return group_done[key]
        aid = _save(c, who, obj["becomes"])
        for rid in args["rules"]:
            c.execute("DELETE FROM rules WHERE id=?", (rid,))
            _mark(c, "wake_rule", rid, mid, aid)
        group_done[key] = aid
        return aid
    if kind == "adopt":   # the hub's own built-in, made under its own id if the hub has not made it yet
        from . import automations
        automations.ensure_builtins(c, [{**obj["becomes"], "key": WAKES_BUILTIN[len("auto_builtin_"):]}])
        _mark(c, "builtin", obj["id"], mid, WAKES_BUILTIN)
        return WAKES_BUILTIN
    if kind == "mirror":
        aid = _save(c, who, obj["becomes"])
        _mark(c, {"builtin": "builtin", "task_rule": "task_rule", "system_job": "system_job",
                  "health_monitor": "health_monitor"}[obj["kind"]], obj["id"], mid, aid)
        return aid
    if kind == "stale_review":
        aid = _save(c, who, obj["becomes"])
        if args["on"]:
            team_rules.save(c, who.actor, {"stale_review": False})   # the automation runs it now, not the job
        _mark(c, "system_job", obj["id"], mid, aid)
        return aid
    if kind == "inbox":
        aid = _save(c, who, obj["becomes"])
        if args["state"] == "active":
            H.status_set(c, who.actor, args["bot"], state="paused",
                         reason="Retired by the redesign: its inbox is an automation now")
        _mark(c, "inbox", obj["id"], mid, aid)
        return aid
    if kind == "rename":
        if args["rename"]:
            strategy_bot._rename(c, who.actor, STRATEGY_NAME)
        _mark(c, "bot", obj["id"], mid, None)
        return None
    if kind == "limit":
        if args["table"] == "registry":
            stored = _json(_meta(c, usage_limits.KEY), {})
            stored.update(per_30m_usd=args["per_30m_usd"], hourly_usd=None)
            _set_meta(c, usage_limits.KEY, json.dumps(stored, sort_keys=True))
        else:
            column = "bot" if args["table"] == "usage_limits" else "scope"
            c.execute(f"UPDATE {args['table']} SET per_30m_usd=?, hourly_usd=NULL WHERE {column}=?",
                      (args["per_30m_usd"], args["key"]))
        _mark(c, "spend_limit", obj["id"], mid, None)
        return None
    if kind == "budget_team":
        if args["raise"]:
            stored = _json(_meta(c, TB.TEAM_KEY), {})
            stored.update(obj["becomes"])
            _set_meta(c, TB.TEAM_KEY, json.dumps(stored, sort_keys=True))
        _mark(c, "turn_budget", obj["id"], mid, None)
        return None
    if kind == "budget_bot":
        row = c.execute("SELECT config FROM bot_config WHERE bot=?", (args["bot"],)).fetchone()
        declared = _json(row["config"] if row else None, {})
        declared.update(args["values"])
        c.execute("UPDATE bot_config SET config=?, revision=revision+1 WHERE bot=?",
                  (json.dumps(declared, sort_keys=True), args["bot"]))
        _mark(c, "turn_budget", obj["id"], mid, None)
        return None
    raise AssertionError(kind)


# ---------------------------------------------------------------------------------------------------- cutover
class _System:
    """Who the cutover acts as: the top of the org (owner rights, so scope rules have a person to own them), or the
    keeper on an install with nobody yet."""
    def __init__(self, actor, role):
        self.actor, self.role, self.via = actor, role, "cutover"


def system_who(c):
    from . import automations
    top = automations.top_person(c)
    return _System("human:" + top, "owner") if top else _System(H.KEEPER, "keeper")


def state(c):
    """The cutover's record ({} before it started)."""
    return _json(_meta(c, CUTOVER), {})


def _why(obj, failure=None):
    texts = [f["text"] for f in obj["flags"] if f.get("keeps")] or ([failure] if failure else [])
    return "; ".join(t for t in texts if t) or "it did not convert"


PEOPLES = ("routine", "wake_rule", "inbox")


def _tell_owner(c, who, mid):
    """Once, to the top of the org: what moved, and what did not (and why). A task when something needs a look (an
    object that stayed as it was or failed, or moved with a change); otherwise one notice. Every object it covers is
    marked told, so a later pass (`catch_up`) never repeats it. Nothing while there is nobody to tell."""
    person = H.actor_id(who.actor) if who.role == "owner" else ""
    if not person:
        return None
    rows = [dict(r) for r in c.execute("SELECT * FROM redesign_migrated WHERE migration=? AND told IS NULL "
                                       "ORDER BY rowid", (mid,))]
    if not rows:
        return None
    c.execute("UPDATE redesign_migrated SET told=? WHERE migration=? AND told IS NULL", (_now(), mid))
    converted = [r for r in rows if _outcome(r) == "converted"]
    kept = [r for r in rows if _outcome(r) in ("kept", "failed")]
    changed = [r for r in converted if r.get("why")]
    # What a person made counts; the hub's own read-only mirrors (system jobs, built-ins) and amounts go unsaid.
    moved = [r for r in converted if r["kind"] in PEOPLES]
    summary = f"Moved to Automations: {len(moved)}. Not moved: {len(kept)}."
    if not (kept or changed):
        if not moved:
            return None
        found = H.notice(c, H.KEEPER, "human:" + person, "Routines and wake rules now run as Automations. " + summary,
                         refs={"redesign_migration": mid})
        return "message:" + found["id"]
    lines = [summary]
    for heading, listed in (("Not moved (no longer runs):", kept), ("Moved, with a change:", changed)):
        if listed:
            lines += ["", heading, *[f"- {r.get('title') or r['kind'] + ' ' + r['id']}: {r.get('why') or 'it did not convert'}"
                                     for r in listed[:100]]]
            if len(listed) > 100:
                lines.append(f"- and {len(listed) - 100} more")
    title = (f"{len(kept)} routine{'s' if len(kept) != 1 else ''} or rule{'s' if len(kept) != 1 else ''} did not move "
             "to Automations") if kept else "Check what moved to Automations"
    task = H.task_create(c, H.KEEPER, title[:300], "\n".join(lines), "human:" + person, lint=False, deduplicate=False)
    return task["id"]


def _outcome(mark):
    """A mark's outcome; one written before outcomes were recorded is converted when it names an automation."""
    return mark.get("outcome") or ("converted" if mark.get("automation") else "kept")


def _title(obj):
    return f"{obj['kind'].replace('_', ' ')} {obj.get('title') or obj['id']}"


def _done(c, obj):
    """Whether another pass already took this object (a wake rule: any member of its group)."""
    ids = obj["_do"][1]["rules"] if obj["_do"] and obj["_do"][0] == "wakes" else [obj["id"]]
    kind = "wake_rule" if obj["kind"] == "wake_rule" else obj["kind"]
    return any(c.execute("SELECT 1 FROM redesign_migrated WHERE kind=? AND id=?", (kind, i)).fetchone() for i in ids)


def _waits_for_owner(obj):
    """Whether an object only waits for a person to own it (a team-wide wake rule on an install with nobody yet): it is
    left for `catch_up`, and its rule keeps working from the rules table meanwhile."""
    keeps = [f for f in obj["flags"] if f.get("keeps")]
    return bool(keeps) and all(f["kind"] == "no_owner" for f in keeps)


def _run(store, who, mid, objects):
    """Convert each object in its own write. Returns (converted, kept, changed, failed, waiting)."""
    converted, kept, changed, failed, waiting, groups = 0, [], [], [], 0, {}
    for obj in objects:
        if not obj["_do"]:
            continue
        if _waits_for_owner(obj):
            waiting += 1
            continue
        holds_back = any(f.get("keeps") for f in obj["flags"])
        try:
            with store.transaction() as c:
                if _done(c, obj):
                    continue
                if holds_back:
                    _mark(c, obj["kind"], obj["id"], mid, None, "kept", _why(obj), _title(obj))
                    kept.append({"kind": obj["kind"], "id": obj["id"], "title": _title(obj), "why": _why(obj)})
                    continue
                _convert_one(c, who, mid, obj, groups)
                why = "; ".join(f["text"] for f in obj["flags"] if f["kind"] != "scope")
                c.execute("UPDATE redesign_migrated SET why=?, title=? WHERE kind=? AND id=?",
                          (why[:1000], _title(obj)[:300], obj["kind"], obj["id"]))
            converted += 1
            if why:
                changed.append({"kind": obj["kind"], "id": obj["id"], "title": _title(obj), "why": why})
        except Exception as exc:   # one object's surprise must not stop the rest
            log.exception("redesign cutover: %s %s did not convert", obj["kind"], obj["id"])
            detail = exc.detail if isinstance(exc, Problem) else type(exc).__name__
            failed.append({"kind": obj["kind"], "id": obj["id"], "title": _title(obj), "why": _why(obj, detail)})
            with store.transaction() as c:
                _mark(c, obj["kind"], obj["id"], mid, None, "failed", _why(obj, detail), _title(obj))
    # A wake rule moves with the rest of its scope: each member's mark gets its own title for the report.
    with store.transaction() as c:
        for obj in objects:
            if obj["_do"] and obj["_do"][0] == "wakes":
                c.execute("UPDATE redesign_migrated SET title=? WHERE kind=? AND id=? AND coalesce(title,'')=''",
                          (_title(obj)[:300], obj["kind"], obj["id"]))
    return converted, kept, changed, failed, waiting


def totals(c, mid):
    """What a migration did so far, from its marks: {converted, kept, failed}."""
    out = {"converted": 0, "kept": 0, "failed": 0}
    for r in c.execute("SELECT * FROM redesign_migrated WHERE migration=?", (mid,)):
        out[_outcome(dict(r))] += 1
    return out


def _printed_to_stderr():
    """Whether a log warning already reaches stderr (the report CLI's basicConfig, or Python's last-resort handler)."""
    handlers = [h for logger in (log, logging.getLogger()) for h in logger.handlers]
    return not handlers or any(getattr(h, "stream", None) is sys.stderr for h in handlers)


def _finish(c, who, mid, waiting):
    """Record a pass, log it and tell the owner once. With nobody to tell yet, `owner_wait` keeps it for `catch_up`."""
    told = _tell_owner(c, who, mid)
    previous = state(c)
    total = totals(c, mid)
    untold = c.execute("SELECT 1 FROM redesign_migrated WHERE migration=? AND told IS NULL LIMIT 1", (mid,)).fetchone()
    record = {"state": "done", "migration": mid, "migrated_at": _now(), **total, "told": told or previous.get("told"),
              "owner_wait": who.role != "owner" or bool(waiting) or bool(untold)}
    _set_meta(c, CUTOVER, json.dumps(record, sort_keys=True))
    line = (f"redesign cutover {mid}: {total['converted']} moved to automations, {total['kept']} kept as they were, "
            f"{total['failed']} failed, {waiting} wait for an owner; owner told: "
            f"{told or ('no one yet' if record['owner_wait'] else 'nothing new')}")
    log.warning("%s", line)
    # The server's log handler is the in-app ring (diagnostics.py), so the operator's container log gets it from here.
    if not _printed_to_stderr():
        print("tico: " + line, file=sys.stderr, flush=True)
    if told:
        from . import audit_log
        audit_log.write(c, who.actor, "redesign_migration:" + mid, None, total, "cutover")
        H.event(c, who.actor, "redesign.cutover", mid, {**total, "told": told})
    return told


def cutover(store):
    """Once per install, at startup (before the scheduler starts): every routine, wake rule, system job, inbox, limit
    and budget moves to the redesign. Each object moves in its own write and is marked in the same write, so a restart
    part way goes on where it stopped and nothing moves twice. An object with a `keeps` flag, or one that fails,
    stays as it was (marked, never tried again) and is listed once for the owner. What needs a person to own it, on an
    install with nobody yet, waits for `catch_up`. Returns the result, or None when it already ran."""
    with store.transaction() as c:
        ensure(c)
        record = state(c)
        if record.get("state") == "done":
            return None
        who = system_who(c)
        mid = record.get("migration") or "mig_" + H.new_id()
        if not record:
            now = _now()
            _set_meta(c, CUTOVER, json.dumps({"state": "running", "migration": mid, "at": now}, sort_keys=True))
        else:
            log.warning("redesign cutover %s: resuming after a restart", mid)
        objects = plan(c, who)
    converted, kept, changed, failed, waiting = _run(store, who, mid, objects)
    with store.transaction() as c:
        told = _finish(c, who, mid, waiting)
    return {"converted": converted, "kept": kept, "failed": failed, "changed": changed, "waiting": waiting,
            "migration": mid, "told": told}


def catch_up(store):
    """After a cutover that ran with nobody to own things (a new install): once a person is at the top of the org,
    the system jobs' and built-ins' read-only automations are made, the team-wide wake rules move, and the owner is
    told what the cutover did. Cheap when there is nothing to do; called by the automations tick."""
    with store.read() as c:
        try:
            record = state(c)
        except Exception:
            return None
        if record.get("state") != "done" or not record.get("owner_wait"):
            return None
        who = system_who(c)
        if who.role != "owner":
            return None
    with store.transaction() as c:
        record = state(c)
        if record.get("state") != "done" or not record.get("owner_wait"):
            return None
        _set_meta(c, CUTOVER, json.dumps({**record, "state": "running"}, sort_keys=True))
        objects = plan(c, who)
    mid = record["migration"]
    converted, kept, changed, failed, waiting = _run(store, who, mid, objects)
    with store.transaction() as c:
        told = _finish(c, who, mid, waiting)
    return {"converted": converted, "kept": kept, "failed": failed, "changed": changed, "waiting": waiting,
            "migration": mid, "told": told}


def _wakes_of(c, scope_kind, scope_id):
    """The "What wakes" automations of one scope, oldest first."""
    from . import automations
    out = []
    for row in c.execute("SELECT * FROM automations WHERE id>=? AND id<? AND deleted_at IS NULL ORDER BY created_at, id",
                         (WAKES_PREFIX, WAKES_PREFIX[:-1] + "`")).fetchall():
        flow = automations.flow_of(row)
        if (flow.get("wakes") or {}) == {"scope_kind": scope_kind, "scope_id": scope_id}:
            out.append((row, flow))
    return out


def adopt(c, kind, ident):
    """After the cutover, in the caller's write: a routine (`kind` "routine", its schedule id) or one scope's wake
    rules (`kind` "wake_rules", `ident` (scope_kind, scope_id)) written the old way moves to an automation. Wake rules
    join the scope's "What wakes" automation while it has room. Before the cutover this does nothing (the cutover
    takes them). Returns the automation ids."""
    record = state(c)
    if not record:
        return []
    from . import automations
    who, mid = system_who(c), record["migration"]
    done = {(r["kind"], r["id"]) for r in c.execute("SELECT kind, id FROM redesign_migrated")}
    if kind == "routine":
        objects = [o for o in _routines(c, done, fresh=True) if o["id"] == ident]
        made = []
        for obj in objects:
            if any(f.get("keeps") for f in obj["flags"]):
                log.warning("redesign adopt: routine %s stays as it was: %s", obj["id"], _why(obj))
                _mark(c, "routine", obj["id"], mid, None, "kept", _why(obj), _title(obj))
                continue
            made.append(_convert_one(c, who, mid, obj, {}))
        return [m for m in made if m]
    scope_kind, scope_id = ident
    me = _owner_person(who)
    objects = [o for o in _wake_rules(c, done, me)
               if (o["_do"][1]["scope_kind"], o["_do"][1]["scope_id"]) == (scope_kind, scope_id)]
    if not objects:
        return []
    if any(f.get("keeps") for o in objects for f in o["flags"]):
        return []                     # no person to own them: they stay rules, which rules.py still reads
    rows = {r["id"]: r for r in c.execute("SELECT * FROM rules WHERE scope_kind=? AND coalesce(scope_id,'')=?",
                                          (scope_kind, scope_id))}
    ids = list(dict.fromkeys(rid for o in objects for rid in o["_do"][1]["rules"]))
    for row, flow in reversed(_wakes_of(c, scope_kind, scope_id)):
        if len(flow["steps"]) + len(ids) > F.MAX_STEPS:
            continue
        start = len(flow["steps"])
        after = {**flow, "steps": [*flow["steps"], *[{**_rule_step(start + n + 1, rows[rid]), "id": f"r{start + n + 1}"}
                                                      for n, rid in enumerate(ids)]],
                 "source": [*(flow.get("source") or []), *[{"kind": "wake_rule", "id": rid} for rid in ids]]}
        taken = {s["id"] for s in flow["steps"]}
        for n, step in enumerate(after["steps"][start:]):
            while step["id"] in taken:
                step["id"] = f"r{start + n + 1}x{H.new_id()[:4]}"
            taken.add(step["id"])
        automations._save(c, who.actor, row["id"], automations._checked(c, after, wake_steps=False), flow,
                          via="cutover, migration")
        for rid in ids:
            c.execute("DELETE FROM rules WHERE id=?", (rid,))
            _mark(c, "wake_rule", rid, mid, row["id"])
        return [row["id"]]
    groups, made = {}, []
    for obj in objects:
        made.append(_convert_one(c, who, mid, obj, groups))
    return list(dict.fromkeys(m for m in made if m))


def drop_wake_rules(c, scope_kind, scope_id, created_by):
    """Take out of a scope's "What wakes" automations the rules `created_by` made (a Custom profile's copied rows,
    when the bot changes profile), the way deleting their table rows did before. Returns how many went."""
    from . import automations
    who, gone = system_who(c), 0
    for row, flow in _wakes_of(c, scope_kind, scope_id):
        keep = [s for s in flow["steps"]
                if not any(((t.get("args") or {}).get("rule") or {}).get("created_by") == created_by
                           for r in s.get("rules") or [] for t in r.get("then") or []
                           if isinstance((t.get("args") or {}).get("rule"), dict))]
        if len(keep) == len(flow["steps"]):
            continue
        gone += len(flow["steps"]) - len(keep)
        if keep:
            kept = {**flow, "steps": keep}
            automations._save(c, who.actor, row["id"], automations._checked(c, kept, wake_steps=False), flow,
                              via="profile")
        else:
            c.execute("UPDATE automations SET deleted_at=?, enabled=0 WHERE id=?", (_now(), row["id"]))
    return gone


def rename_steps(c, type_id, lower):
    """A type's steps were renamed ({lowercased old name: new name}): its "What wakes" rules that name a step by its
    old name name the new one, as rules.steps_renamed does for rows still in the table."""
    import copy
    from . import automations

    def moved(value):
        if isinstance(value, list):
            return [lower.get(str(v).lower(), v) for v in value]
        return lower.get(str(value).lower(), value) if isinstance(value, str) else value

    who = None
    for row, flow in _wakes_of(c, "type", type_id):
        after = copy.deepcopy(flow)
        for step in after.get("steps") or []:
            for rule in step.get("rules") or []:
                for cond in rule.get("when") or []:
                    if cond.get("field") == "task.step" and cond.get("op") == "is":
                        cond["value"] = moved(cond.get("value"))
                for then in rule.get("then") or []:
                    source = (then.get("args") or {}).get("rule")
                    if isinstance(source, dict) and isinstance(source.get("when"), dict) and "step" in source["when"]:
                        source["when"]["step"] = moved(source["when"]["step"])
        if after != flow:
            who = who or system_who(c)
            automations._save(c, who.actor, row["id"], automations._checked(c, after, saved=flow, aid=row["id"]),
                              flow, via="steps renamed")


def wake_rules_by(c, scope_kind, scope_id, created_by):
    """How many rules `created_by` made are in a scope's "What wakes" automations."""
    return sum(1 for _, flow in _wakes_of(c, scope_kind, scope_id) for s in flow["steps"]
               for r in s.get("rules") or [] for t in r.get("then") or []
               if isinstance((t.get("args") or {}).get("rule"), dict)
               and t["args"]["rule"].get("created_by") == created_by)


# ---------------------------------------------------------------------------------------------------- hooks
def holds(c, kind, ident):
    """Whether a converted object is held: a routine the convert soft-deleted is never brought back by a `create` with
    its key (a built-in routine re-seeded at startup), so it cannot fire beside its automation."""
    try:
        return c.execute("SELECT 1 FROM redesign_migrated WHERE kind=? AND id=?", (kind, ident)).fetchone() is not None
    except Exception:   # sqlite3.OperationalError: a database from before the converter
        return False


def converted(c):
    """Whether the cutover started (it may still be going on)."""
    try:
        return bool(state(c))
    except Exception:   # sqlite3.OperationalError: a database from before the converter
        return False


DO_OUTCOME = {"wake_bot": "wake", "note": "note", "archive": "ignore", "leave_for_you": "ask"}


def _scalar(key, value):
    return isinstance(value, (str, int, float, bool)) and bool(F.FIELD.fullmatch(str(key)))


def wake_rules_of(aid, flow):
    """A "What wakes" automation's rules, read from what it shows: each Filter rule's `event is` condition is the
    event, its `task.<field> is <value>` conditions the rule's conditions, its action the outcome. What a rule
    carries out of sight is only its id, scope and history, and the conditions a filter cannot show (a list, a
    range). Raises ValueError (why) for a flow a wake rule cannot express, which `check_wakes` refuses on save."""
    scope = flow.get("wakes") or {}
    trigger = flow.get("trigger") or {}
    if trigger.get("type") != "event" or trigger.get("name") != "tico.wake":
        raise ValueError("its trigger stays the wake rules' own (event tico.wake)")
    out = []
    for n, step in enumerate(flow.get("steps") or []):
        if step.get("type") != "filter":
            raise ValueError(f"step {step.get('id')} is a {step.get('type')}: it holds wake rules, one Filter step each")
        for i, rule in enumerate(step.get("rules") or []):
            where = f"step {step.get('id')} rule {i + 1}"
            then = rule.get("then") or []
            if len(then) != 1:
                raise ValueError(f"{where}: a wake rule does one thing (wake, note, archive or leave for you)")
            if rule.get("stop") is False:
                raise ValueError(f"{where}: a wake rule stops at the first match; keep 'stop' on")
            action = then[0]
            args = action.get("args") or {}
            source = args.get("rule") if isinstance(args.get("rule"), dict) else {}
            event, when = None, {}
            conds = rule.get("when") or []
            if len(conds) > 1 and rule.get("match", "all") != "all":
                raise ValueError(f"{where}: a wake rule matches all of its conditions")
            for cond in conds:
                field, op = str(cond.get("field") or ""), cond.get("op")
                if field == "event" and op == "is" and event is None and isinstance(cond.get("value"), str):
                    event = cond["value"]
                elif field.startswith("task.") and op == "is" and _scalar(field[5:], cond.get("value")):
                    when[field[5:]] = cond["value"]
                else:
                    raise ValueError(f"{where}: a wake rule's conditions are 'event is' and 'task.<field> is'")
            # The conditions it never showed (not a simple value) stay; every one it showed is as the filter says.
            when = {**{k: v for k, v in (source.get("when") or {}).items() if not _scalar(k, v)}, **when}
            do, outcome = action.get("do"), args.get("outcome")
            params = args.get("params") if isinstance(args.get("params"), dict) else {}
            # What the converter showed for the rule's own outcome (_rule_step): kept, with its params, while shown.
            was = params["action"] if outcome == "action" and params.get("action") in F.ACTIONS \
                else OUTCOME_DO.get(outcome, "wake_bot")
            if not (outcome and source and do == was):
                if do not in DO_OUTCOME:
                    raise ValueError(f"{where}: a wake rule wakes, notes, archives or leaves for you, not {do}")
                outcome, params = DO_OUTCOME[do], {}
            out.append({"id": source.get("id") or f"{aid}.{step.get('id')}.{i + 1}",
                        "scope_kind": scope.get("scope_kind") or source.get("scope_kind") or "bot",
                        "scope_id": scope.get("scope_id") or source.get("scope_id") or "", "event": event or "*",
                        "condition": json.dumps(when, sort_keys=True), "then_outcome": outcome,
                        "params": json.dumps(params, sort_keys=True),
                        "enabled": 0 if step.get("enabled") is False else 1,
                        "position": source.get("position") if source.get("position") is not None else n + 1,
                        "note": source.get("note") or "", "created_by": source.get("created_by") or "",
                        "created_at": source.get("created") or "", "updated_at": source.get("updated") or "",
                        "revision": source.get("revision") or 1, "action_by": source.get("action_by")})
    return out


def _steps_of(c, type_id):
    """A task type's steps in board order, as (id, name)."""
    return [(r[0], r[1]) for r in c.execute("SELECT id, name FROM task_steps WHERE type_id=? ORDER BY position, id",
                                            (type_id,))]


def _named(when):
    """The step values a rule's `when` names (one name or id, or a list of them), or None when it names no step."""
    if "step" not in when:
        return None
    value = when["step"]
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def _missing_steps(c, scope, when):
    """[(value, step names)] for each step a type-scoped rule names that its type does not have. A step is matched as
    rules.py matches it: by name or by id, ignoring case. A rule scoped to a team, department or bot names no one
    type's steps, so nothing is checked for it."""
    named = _named(when)
    if named is None or scope.get("scope_kind") != "type":
        return []
    steps = _steps_of(c, scope.get("scope_id") or "")
    known = {str(s).lower() for ident, name in steps for s in (ident, name)}
    return [(value, [name for _, name in steps]) for value in named if value.lower() not in known]


def _missing_why(value, names):
    return f'The wake rule names step "{value}", which its task type does not have. Its steps are: ' \
           f'{", ".join(names) or "none"}. Use one of those names, or its id.'


def wake_problems(c, aid, flow):
    """The wake rules of one "What wakes" automation that name a step its type does not have: [{rule, step, why}].
    Read for every automation payload, so a rule that stopped working says so; nothing here raises."""
    try:
        rows = wake_rules_of(aid, flow)
    except ValueError:
        return []
    return [{"rule": row["id"], "step": value, "why": _missing_why(value, names)}
            for row in rows for value, names in _missing_steps(c, flow.get("wakes") or {}, _json(row["condition"], {}))]


def check_wakes(flow, c=None, saved=None, aid=None):
    """On save (automations._checked): a "What wakes" automation is refused (422) when an edit is one its wake rules
    cannot carry out, rather than saved to show one thing and do another. With `c`, a rule that names a step its type
    does not have is refused too, with the type's real steps; a rule that already named that missing step in `saved`
    (the version it changes) is not, so an unrelated edit does not trip over a rule that is already flagged."""
    if not flow.get("wakes") or flow.get("readonly"):
        return
    try:
        rows = wake_rules_of(aid or "check", flow)
    except ValueError as exc:
        raise Problem("invalid", f"A What wakes automation holds wake rules: {exc}", 422)
    if c is None:
        return
    kept = set()
    if saved and aid and saved.get("wakes"):
        try:
            kept = {(row["id"], value.lower()) for row in wake_rules_of(aid, saved)
                    for value, _ in _missing_steps(c, saved["wakes"], _json(row["condition"], {}))}
        except ValueError:
            kept = set()
    for row in rows:
        for value, names in _missing_steps(c, flow["wakes"], _json(row["condition"], {})):
            if (row["id"], value.lower()) not in kept:
                raise Problem("invalid", _missing_why(value, names), 422)


def wakes_naming(c, type_id, steps):
    """The wake rules of a type's "What wakes" automations that name one of `steps` ([(id, name)], say the steps a save
    deletes), as "<automation name>: <step>" strings. A rule names a step by its name or its id."""
    named = {str(s).lower() for step in steps for s in step}
    try:
        found = _wakes_of(c, "type", type_id)
    except Exception:   # sqlite3.OperationalError: a database from before the automations table
        return []
    out = []
    for row, flow in found:
        try:
            rules = wake_rules_of(row["id"], flow)
        except ValueError:      # not readable as wake rules: nothing it shows can run
            continue
        for rule in rules:
            for value in _named(_json(rule["condition"], {})) or []:
                if value.lower() in named:
                    out.append(f"{row['name']}: {value}")
    return out


def wake_rows(c, event=None):
    """The converted wake rules, read back from the enabled "What wakes" automations as rules-table rows (rules.py adds
    them to its own). `event` keeps rows on that event or `*`; None keeps every row."""
    try:
        found = c.execute("SELECT id, flow FROM automations WHERE id>=? AND id<? AND deleted_at IS NULL "
                          "AND enabled=1", (WAKES_PREFIX, WAKES_PREFIX[:-1] + "`")).fetchall()
    except Exception:   # sqlite3.OperationalError: no automations table yet
        return []
    out = []
    for row in found:
        try:
            rows = wake_rules_of(row["id"], _json(row["flow"], {}))
        except ValueError:   # saved before the check (or by hand): nothing it shows can run
            log.warning("redesign migration: %s cannot be read as wake rules", row["id"])
            continue
        out += [r for r in rows if r["enabled"] and (event is None or r["event"] in (event, "*"))]
    return out


# ---------------------------------------------------------------------------------------------------- the report
# What each kind still runs from once it moved: a mirror only shows a job Tico still runs from its own table.
RUNS_FROM = {"routine": "Automations", "wake_rule": "rules.py, read from its What wakes automation",
             "inbox": "Automations", "builtin": "rules.py (built in); shown read-only",
             "task_rule": "the task rules engine; shown read-only", "system_job": "the scheduler; shown read-only",
             "health_monitor": "its bot; shown read-only"}
GONE_JOBS = {"job:routine_timer": "Bot routine timer: the automations tick runs schedules now",
             "job:propose_demotions": "Suggest quieter rules: rule suggestions went with the rules editor"}


def inventory(c):
    """Every object the old system ran or set, and what it is now, in report order: [{kind, id, title, now, automation,
    name, why}]. `now` is moved | kept | failed (both: no longer run, listed for the owner) | runs as before | pending
    (the cutover has not taken it yet: would move | would stay | waits for an owner) | unchanged | removed."""
    from . import rules, system_routines as SR, tico_notices
    out = []
    marked = []
    if _table(c, "redesign_migrated"):
        marked = [dict(r) for r in c.execute(
            "SELECT m.*, a.name AS automation_name, a.enabled AS automation_on, a.deleted_at AS automation_gone "
            "FROM redesign_migrated m LEFT JOIN automations a ON a.id=m.automation ORDER BY m.rowid")]
    for m in marked:
        outcome = _outcome(m)
        now = {"converted": "moved"}.get(outcome, outcome)
        if outcome == "converted" and m["id"] == "job:stale_review":
            now = "moved"
        elif outcome == "converted" and m["kind"] in ("builtin", "task_rule", "system_job", "health_monitor"):
            now = "runs as before"
        if m["id"] in GONE_JOBS:
            continue
        state_of = "" if not m["automation"] else ("deleted" if m["automation_gone"] else
                                                     "on" if m["automation_on"] else "off")
        out.append({"kind": m["kind"], "id": m["id"], "title": m.get("title") or m["id"], "now": now,
                    "automation": m["automation"], "name": m.get("automation_name"), "state": state_of,
                    "runs_from": ("Automations" if m["id"] == "job:stale_review" else RUNS_FROM.get(m["kind"], ""))
                    if outcome == "converted" else "",
                    "why": m.get("why") or ""})
    seen = {(m["kind"], m["id"]) for m in marked}
    who = system_who(c)
    for obj in plan(c, who):
        if (obj["kind"], obj["id"]) in seen:
            continue
        if not obj["_do"]:
            now, why = "unchanged", ""
        elif _waits_for_owner(obj):
            now, why = "pending: waits for an owner", _why(obj)
        elif any(f.get("keeps") for f in obj["flags"]):
            now, why = "pending: would stay", _why(obj)
        else:
            now, why = "pending: would move", "; ".join(f["text"] for f in obj["flags"] if f["kind"] != "scope")
        becomes = obj["becomes"] if isinstance(obj["becomes"], dict) else {}
        out.append({"kind": obj["kind"], "id": obj["id"], "title": obj.get("title") or obj["id"], "now": now,
                    "automation": None, "name": becomes.get("name") or "",
                    "state": ("on" if becomes.get("enabled", True) else "off") if becomes.get("name") else "",
                    "runs_from": "", "why": why})
    seen |= {(r["kind"], r["id"]) for r in out}
    for job in SR.JOBS:
        if ("system_job", "job:" + job.key) not in seen:
            out.append({"kind": "system_job", "id": "job:" + job.key, "title": job.name, "now": "runs as before",
                        "automation": None, "name": "", "state": "", "runs_from": "the scheduler",
                        "why": "its read-only automation waits for a person" if who.role != "owner" else ""})
    for oid, why in GONE_JOBS.items():
        out.append({"kind": "system_job", "id": oid, "title": why.split(":")[0], "now": "removed", "automation": None,
                    "name": "", "state": "", "runs_from": "", "why": why.split(": ", 1)[1]})
    if _table(c, "rules"):
        for r in c.execute("SELECT id, event, then_outcome FROM rules ORDER BY id"):
            if tico_notices.is_builtin_rule(r["id"]):
                out.append({"kind": "wake_rule", "id": r["id"], "title": f"{r['event']} → {r['then_outcome']}",
                            "now": "runs as before", "automation": None, "name": "", "state": "",
                            "runs_from": "rules.py (Tico Team notice rule)", "why": ""})
    if not any(r["kind"] == "builtin" for r in out):
        out.append({"kind": "builtin", "id": "wake-builtins", "title": f"Built-in wake rules ({len(rules._builtins())})",
                    "now": "runs as before", "automation": None, "name": "", "state": "", "runs_from": "rules.py",
                    "why": "its read-only automation waits for a person" if who.role != "owner" else ""})
    return out


def report(c):
    """The cutover's record and the inventory, as the CLI prints it."""
    items = inventory(c)
    counts = {}
    for item in items:
        counts[item["now"]] = counts.get(item["now"], 0) + 1
    return {"cutover": state(c) if _table(c, "registry_metadata") else {}, "counts": counts, "items": items}


def _text(found):
    record, lines = found["cutover"], [found["error"]] if found.get("error") else []
    lines.append("Cutover: " + (f"{record.get('state')} ({record.get('migration')}, {record.get('at')}); owner told: "
                                f"{record.get('told') or 'not yet'}" if record else "not run on this database"))
    lines.append("Counts: " + ", ".join(f"{k} {v}" for k, v in sorted(found["counts"].items())))
    lines.append("")
    for item in found["items"]:
        became = ""
        if item["automation"]:
            became = f" -> {item['automation']} \"{item['name'] or ''}\" ({item['state']})"
        elif item["name"]:
            became = f" -> \"{item['name']}\"" + (f" ({item['state']})" if item["state"] else "")
        where = f" [runs from {item['runs_from']}]" if item["runs_from"] else ""
        why = f" — {item['why']}" if item["why"] else ""
        lines.append(f"{item['now']:<28} {item['kind']:<14} {item['id']}: {item['title']}{became}{where}{why}")
    return "\n".join(lines)


def _copy(source, directory):
    """A private copy of `source` by sqlite's online backup. Nothing beside the source is created or changed: with no
    -wal it is opened immutable; with one (a live server's) it is only read, as any reader does."""
    import os
    import sqlite3
    from pathlib import Path
    from urllib.parse import quote
    target = Path(directory) / "rehearsal.db"
    live = Path(str(source) + "-wal").exists()
    src = sqlite3.connect(f"file:{quote(str(source))}?{'mode=ro' if live else 'immutable=1'}", uri=True)
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    os.chmod(target, 0o600)
    return target


def main(argv=None):
    """`python -m backend.redesign_migration report --db PATH [--json]`: the cutover's report for an install. The
    database is never written: it is copied (sqlite backup), the copy is brought up to this version (schema and the
    cutover, as the server does at startup, with nothing scheduled), the report is read from the copy and the copy is
    deleted. Before an upgrade it is the dry run (what would move, stay or wait); after, the record of what did. It
    writes nothing: only the copy moves."""
    import argparse
    import os
    import sys
    import tempfile
    from pathlib import Path
    from .config import Settings
    from .store import Store
    parser = argparse.ArgumentParser(prog="python -m backend.redesign_migration")
    parser.add_argument("command", choices=("report",))
    parser.add_argument("--db", default=os.environ.get("TICO_DB"), help="the install's database (only read)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = parser.parse_args(argv)
    if not args.db:
        raise SystemExit("redesign_migration: name the database with --db (or TICO_DB)")
    source = Path(args.db).expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"redesign_migration: no database at {source}")
    with tempfile.TemporaryDirectory(prefix="tico-cutover-") as directory:
        os_dir = Path(directory)
        copy = _copy(source, os_dir)
        try:
            with sqlite_ro(copy) as c:
                before = report(c)
        except Exception as exc:   # a database from an older version: its tables are not this version's yet
            before = {"cutover": {}, "counts": {}, "items": [], "error": f"not readable as it is ({exc}); "
                                                                         "the rehearsal below is the dry run"}
        waits = waiting(copy)
        registry = os_dir / "registry"
        registry.mkdir()
        store = Store(Settings(db_path=copy, registry_dir=registry, migrate_to_automations=True))
        store.initialize(seed_market=False)
        with store.read() as c:
            after = report(c)
        for c in store.pool:
            c.close()
    found = {"database": str(source), "waiting": waits, "before": before, "after": after}
    if args.json:
        json.dump(found, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        moves = ("nothing to move" if not waits
                 else "moves " + ", ".join(f"{k} {v}" for k, v in sorted(waits.items())))
        print(f"Database: {source}\nOn the next start: {moves}\n\n== As it is now ==\n"
              f"{_text(before)}\n\n== After this version starts on it (rehearsed on a copy) ==\n{_text(after)}")
    return found


def pending(c):
    """What the cutover would still change in this database, counted by kind ({} for nothing). Read only, and from
    plain SQL so a database an older release wrote is read as it is: routines, wake rules (not Tico Team's notice
    rules), inboxes, hourly spend limits, turn budgets below the guard, the stale review job when on, and the Strategy
    bot's rename. Nothing once the cutover started or finished; read-only mirrors of jobs Tico
    keeps running count for nothing."""
    from . import strategy_bot, team_rules, tico_notices, turn_budgets as TB, usage_limits
    if not _table(c, "registry_metadata") or state(c):
        return {}
    done = ({(r[0], r[1]) for r in c.execute("SELECT kind, id FROM redesign_migrated")}
            if _table(c, "redesign_migrated") else set())
    found = {}

    def add(kind, n):
        if n:
            found[kind] = found.get(kind, 0) + n

    if _table(c, "schedules"):
        live = " WHERE deleted_at IS NULL" if "deleted_at" in _columns(c, "schedules") else ""
        add("routine", sum(("routine", r[0]) not in done for r in c.execute("SELECT id FROM schedules" + live)))
    if _table(c, "rules"):
        add("wake_rule", sum(not tico_notices.is_builtin_rule(r[0]) and ("wake_rule", r[0]) not in done
                             for r in c.execute("SELECT id FROM rules")))
    people = _json(_meta(c, "people"), {}).get("people") or []
    bots = ({r[0]: r[1] for r in c.execute("SELECT id, state FROM bots")} if _table(c, "bots") else {})
    add("inbox", sum(bool(p.get("inbox_bot")) and not p.get("hidden") and bots.get(p["inbox_bot"]) not in (None, "archived")
                     for p in people if isinstance(p, dict)))
    for table, key in (("usage_limits", "bot"), ("usage_pools", "scope")):
        if _table(c, table) and "hourly_usd" in _columns(c, table):
            add("spend_limit", c.execute(f"SELECT count(*) FROM {table} WHERE hourly_usd > 0").fetchone()[0])
    if usage_limits._amount(_json(_meta(c, usage_limits.KEY), {}).get("hourly_usd")):
        add("spend_limit", 1)

    def low(values):
        return any(isinstance(values.get(k), int) and 0 < values[k] < GUARD[k] for k in GUARD)

    add("turn_budget", int(low(_json(_meta(c, TB.TEAM_KEY), {}))))
    if _table(c, "bot_config"):
        add("turn_budget", sum(low(_json(r[0], {})) for r in c.execute("SELECT config FROM bot_config")))
    if team_rules.load(c).get("stale_review") and ("system_job", "job:stale_review") not in done:
        add("system_job", 1)
    row = bots.get(strategy_bot.SLUG)
    if row and row != "archived" and ("bot", "bot:" + strategy_bot.SLUG) not in done:
        name = (c.execute("SELECT "
                          "display_name FROM bots WHERE id=?", (strategy_bot.SLUG,)).fetchone()[0] or "").strip() \
            if "display_name" in _columns(c, "bots") else ""
        add("bot", int(name in ("", strategy_bot.NAME, STRATEGY_NAME, *strategy_bot.OLD_NAMES)))
    return found


def waiting(path):
    """`pending` for the database at `path` ({} for no file or an empty one: a new install). Nothing beside it is
    created or changed, not even SQLite's -wal and -shm files: a database with no -wal is opened immutable, and one
    with a -wal (a server that stopped without closing it) is read from a private copy of the two."""
    import shutil
    import sqlite3
    import tempfile
    from pathlib import Path
    from urllib.parse import quote
    path = Path(path)
    if str(path) == ":memory:" or not path.is_file() or path.stat().st_size == 0:
        return {}
    wal = Path(str(path) + "-wal")
    if not wal.exists():
        c = sqlite3.connect(f"file:{quote(str(path.resolve()))}?immutable=1", uri=True)
        try:
            c.row_factory = sqlite3.Row
            return pending(c)
        finally:
            c.close()
    with tempfile.TemporaryDirectory(prefix="tico-waiting-") as directory:
        copy = Path(directory) / "hub.db"
        shutil.copyfile(path, copy)
        shutil.copyfile(wal, str(copy) + "-wal")
        with sqlite_ro(copy) as c:
            return pending(c)


class sqlite_ro:
    """A read-only connection to `path` (the file is never written, a live server may hold it)."""
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        import sqlite3
        self.c = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        self.c.row_factory = sqlite3.Row
        return self.c

    def __exit__(self, *exc):
        self.c.close()


if __name__ == "__main__":
    main()
