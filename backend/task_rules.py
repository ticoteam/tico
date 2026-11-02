"""Stored task rules in the shared Routines list. Only catalogued deterministic triggers/actions run.

Department tags come from the org builder's fixed catalog, not arbitrary group names. The tag
controls who edits the rule; it does not narrow the company-wide close rule's task selection.
Every close uses the normal keeper close path, with its audit and ordinary reopen as undo.
"""
import json
from datetime import datetime, timedelta, timezone

from . import groups, people, system_routines as SR
from .store import H, Problem, encode

DEFAULT = "task-rule-close-after-done"
MAX_RULES = 50
CLOSE_LIMIT = 50
SCHEMA = """
CREATE TABLE IF NOT EXISTS task_rules(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, department TEXT NOT NULL,
 triggers TEXT NOT NULL, actions TEXT NOT NULL, enabled INTEGER NOT NULL,
 preset TEXT NOT NULL, daily_at TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
DROP INDEX IF EXISTS tasks_rule_due;
CREATE INDEX IF NOT EXISTS tasks_rule_done_due ON tasks(updated_at,id) WHERE status='done' AND closed_at IS NULL;
INSERT OR IGNORE INTO task_rules VALUES(
 'task-rule-close-after-done','Close 24h after done','engineering',
 '[{"key":"done_untouched","hours":24}]','["close"]',1,'15m',NULL,'keeper',
 strftime('%Y-%m-%dT%H:%M:%fZ','now'),strftime('%Y-%m-%dT%H:%M:%fZ','now'));
"""


def departments(settings):
    from . import recruit, recruit_rank
    import yaml
    # Read the same fixed department source as the org builder without rebuilding its bot catalog.
    value = yaml.safe_load(recruit.departments_file(settings).read_text()) or {}
    return [{"id": d["id"], "name": d["name"]} for d in value.get("departments", []) if d.get("id") in recruit_rank.DEPARTMENT_IDS]


def department_of(c, settings, actor):
    roster = groups._roster(c)
    if H.is_human(actor):
        person = people.person(H.actor_id(actor), roster) or {}
        gid = person.get("team") if not person.get("hidden") else None
    elif H.is_bot(actor):
        gid = people.team_of(H.actor_id(actor), groups._configs(c), roster)
    else:
        return None
    allowed = {d["id"] for d in departments(settings)}
    # A subgroup inherits its fixed department; a similarly named arbitrary group grants nothing.
    return next((g for g in people.group_chain(gid, roster) if g in allowed), None)


def can_change(c, settings, auth, who, department):
    if not who or not auth or who.support or who.role not in ("owner", "human", "bot"):
        return False
    return who.role == "owner" or auth.bot_admin(who) or department_of(c, settings, who.actor) == department


def require_change(c, settings, auth, who, department):
    auth.domain(who)
    if not can_change(c, settings, auth, who, department):
        raise Problem("forbidden", "Only this department's members, the owner or an admin can edit this task rule", 403)


def get(c, key):
    row = c.execute("SELECT * FROM task_rules WHERE id=?", (key,)).fetchone()
    if not row:
        raise Problem("not_found", "No such task rule", 404)
    return dict(row)


def validate(triggers, actions):
    if not triggers or not actions:
        raise Problem("invalid", "Choose at least one trigger and action", 422)
    for trigger in triggers:
        if not isinstance(trigger.get("key"), str) or trigger.get("key") not in TRIGGERS or set(trigger) != {"key", "hours"}:
            raise Problem("invalid", "Choose an available task trigger", 422)
        hours = trigger.get("hours")
        if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 8760:
            raise Problem("invalid", "The wait must be 1 to 8760 whole hours", 422)
    if any(action not in ACTIONS for action in actions):
        raise Problem("invalid", "Only available task actions can be used; v1 offers close", 422)


def save(c, settings, auth, who, body, key=None):
    before = get(c, key) if key else None
    if before:
        require_change(c, settings, auth, who, before["department"])
    own = department_of(c, settings, who.actor)
    department = body.department if body.department is not None else before["department"] if before else own
    if department not in {d["id"] for d in departments(settings)}:
        raise Problem("invalid", "Choose a department from the org chart's department list", 422)
    require_change(c, settings, auth, who, department)
    if not before and c.execute("SELECT count(*) FROM task_rules").fetchone()[0] >= MAX_RULES:
        raise Problem("conflict", "Edit an existing rule; the task rule list is full", 409)
    triggers = body.triggers if body.triggers is not None else json.loads(before["triggers"]) if before else [{"key": "done_untouched", "hours": 24}]
    actions = body.actions if body.actions is not None else json.loads(before["actions"]) if before else ["close"]
    validate(triggers, actions)
    name = body.name if body.name is not None else before["name"] if before else "Close after done"
    if not name:
        raise Problem("invalid", "Give the rule a name", 422)
    on = body.on if body.on is not None else bool(before["enabled"]) if before else True
    preset = body.preset or (before["preset"] if before else SR.FIFTEEN)
    at = body.at if body.at is not None else before["daily_at"] if before else None
    if preset == SR.DAILY and (not at or not SR.AT_RE.fullmatch(at)):
        raise Problem("invalid", "Choose a daily time", 422)
    key, now = key or "task-rule-" + H.new_id(), H.now()
    c.execute("INSERT INTO task_rules VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
              "name=excluded.name,department=excluded.department,triggers=excluded.triggers,"
              "actions=excluded.actions,enabled=excluded.enabled,preset=excluded.preset,daily_at=excluded.daily_at,updated_at=excluded.updated_at",
              (key, name, department, encode(triggers), encode(actions), int(on), preset, at,
               before["created_by"] if before else who.actor, before["created_at"] if before else now, now))
    H.event(c, who.actor, "task_rule.updated" if before else "task_rule.created", key,
            {"before": before, "after": get(c, key)})
    return row(c, settings, get(c, key), who, auth)


def config(rule):
    return {"on": bool(rule["enabled"]), "preset": rule["preset"], "daily_at": rule["daily_at"]}


def row(c, settings, rule, who=None, auth=None, now=None, last=None):
    now = now or datetime.now(timezone.utc)
    triggers, actions = json.loads(rule["triggers"]), json.loads(rule["actions"])
    validate(triggers, actions)
    chart = {d["id"]: d["name"] for d in departments(settings)}
    department = rule["department"]
    when = [TRIGGERS[t["key"]][0](t) for t in triggers]
    then = [ACTIONS[a][0] for a in actions]
    if last is None:
        found = c.execute("SELECT * FROM system_runs WHERE job=? ORDER BY started_at DESC LIMIT 1", (rule["id"],)).fetchone()
        last = dict(found) if found else None
    cfg = config(rule)
    following = SR.next_due(cfg, H.parse_ts(last["started_at"]) if last else None, now) if cfg["on"] else None
    return {"key": rule["id"], "kind": "task_rule", "name": rule["name"],
            "what": "Closes eligible done tasks across the company. Reopen a task to undo.",
            "when": when, "then": then, "department": {"id": department, "name": chart.get(department, department), "in_chart": department in chart},
            "triggers": triggers, "actions": actions, "schedule": SR.PRESETS[cfg["preset"]][0].format(at=cfg["daily_at"] or ""),
            "ai": False, "needed": False, "records": True, "controllable": False, "group": "task_rules",
            "switch": True, "on": cfg["on"], "preset": cfg["preset"], "daily_at": cfg["daily_at"], "owner_only": False,
            "presets": [{"key": p, "label": SR.PRESETS[p][0].format(at="a time")} for p in SR.EVERY_PRESET],
            "can_change": can_change(c, settings, auth, who, department),
            "last": {k: bool(last[k]) if k == "ok" else last[k] for k in ("started_at", "finished_at", "ok", "result", "runs")} if last else None,
            "next": SR._stamp(following) if following else None,
            "search": " ".join([rule["name"], *when, *then, chart.get(department, department), "done close tasks"]).lower()}


def listing(c, settings, who=None, auth=None, now=None):
    return [row(c, settings, dict(r), who, auth, now) for r in c.execute("SELECT * FROM task_rules ORDER BY created_at,id")]


HISTORY_ACTIVITY_SQL = "NOT (actor='keeper' AND field='github')"
MESSAGE_ACTIVITY_SQL = "m.sender_id<>'keeper'"


def untouched(c, task, hours, now):
    # Task timestamps cover ordinary edits/comments. History also fences step-only changes and
    # scoped messages in shared conversations; unrelated chat must not restart a task's wait.
    times = [task.get(k) for k in ("created_at", "updated_at", "done_at", "checkpoint_at")]
    # Observed GitHub facts have history, but viewing them and keeper notices are not work on the task.
    times += [r[0] for r in c.execute(f"SELECT max(created_at) FROM task_events WHERE task_id=? AND {HISTORY_ACTIVITY_SQL}", (task["id"],))]
    message = c.execute("SELECT max(max(m.created_at,coalesce(m.edited_at,m.created_at),coalesce(m.deleted_at,m.created_at))) "
                        "FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
                        f"WHERE m.conversation_id=? AND {H.MESSAGE_TASK_SQL}=? AND {MESSAGE_ACTIVITY_SQL}",
                        (task["conversation_id"], task["id"])).fetchone()[0]
    times.append(message)
    parsed = [H.parse_ts(t) for t in times if t]
    return task["status"] == "done" and bool(parsed) and max(parsed) <= now - timedelta(hours=hours)


def safe(c, task):
    if task.get("waiting_on") or task.get("snoozed_until") or task.get("pinned"):
        return False
    # Pin tags are a durable task-level pin; the rail's pinned filtered views are only bookmarks.
    if {str(x).lower() for x in json.loads(task.get("labels") or "[]")} & {"pin", "pinned"}:
        return False
    if c.execute("SELECT 1 FROM task_tags tt JOIN tags g ON g.id=tt.tag_id WHERE tt.task_id=? AND lower(g.key) IN ('pin','pinned') LIMIT 1", (task["id"],)).fetchone():
        return False
    if c.execute("SELECT 1 FROM approvals WHERE task_id=? AND decision IS NULL LIMIT 1", (task["id"],)).fetchone():
        return False
    return not H.open_task_asks(c, task)


def close(c, task, rule):
    # Only the status event carries the history note. task_close also records step consistency.
    H.task_close(c, H.KEEPER, task["id"], quiet=True)
    note = f"Closed by rule '{rule['name']}'"
    c.execute("UPDATE task_events SET note=? WHERE task_id=? AND field='status' AND new='closed' "
              "AND rowid=(SELECT max(rowid) FROM task_events WHERE task_id=? AND field='status')", (note, task["id"], task["id"]))
    H.event(c, H.KEEPER, "task_rule.closed", task["id"], {"rule": rule["id"], "name": rule["name"]})


# Catalog entries bind their words and evaluator; adding an entry leaves the row schema unchanged.
TRIGGERS = {"done_untouched": (lambda t: f"a task has been done and untouched for {t['hours']} hours", untouched)}
ACTIONS = {"close": ("close it", close)}


# The partial index covers only finished tasks, so it cannot displace the active-task reminder index.
DUE_SQL = (
    "SELECT t.* FROM tasks t WHERE t.status='done' AND t.closed_at IS NULL "
    "AND t.updated_at<=? AND coalesce(t.done_at,t.updated_at)<=? AND coalesce(t.checkpoint_at,t.updated_at)<=? "
    "AND t.waiting_on IS NULL AND t.snoozed_until IS NULL "
    "AND NOT EXISTS (SELECT 1 FROM task_tags tt JOIN tags g ON g.id=tt.tag_id "
    "WHERE tt.task_id=t.id AND lower(g.key) IN ('pin','pinned')) "
    "AND NOT EXISTS (SELECT 1 FROM json_each(t.labels) WHERE lower(value) IN ('pin','pinned')) "
    "AND NOT EXISTS (SELECT 1 FROM approvals a WHERE a.task_id=t.id AND a.decision IS NULL) "
    f"AND NOT EXISTS (SELECT 1 FROM task_events WHERE task_id=t.id AND {HISTORY_ACTIVITY_SQL} AND created_at>?) "
    "AND NOT EXISTS (SELECT 1 FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
    f"WHERE m.conversation_id=t.conversation_id AND {H.MESSAGE_TASK_SQL}=t.id "
    f"AND ((m.kind='ask' AND {H.OPEN_ASK_SQL}) OR ({MESSAGE_ACTIVITY_SQL} AND "
    "max(m.created_at,coalesce(m.edited_at,m.created_at),coalesce(m.deleted_at,m.created_at))>?))) "
    "ORDER BY t.updated_at,t.id LIMIT ?")


def tick(store, now, failures):
    remaining = CLOSE_LIMIT
    try:
        with store.read() as c:
            keys = [r[0] for r in c.execute("SELECT id FROM task_rules WHERE enabled=1 ORDER BY created_at,id")]
    except Exception as exc:
        failures.append({"task_rules": type(exc).__name__})
        return
    for key in keys:
        run = SR.Run(key, started=SR._stamp(now))
        try:
            # The due test, latest switch/edits, eligibility, closes and receipt share one writer
            # transaction. Turning off cannot race a later batch or a stale list of candidates.
            with store.transaction() as c:
                rule = get(c, key)
                last = c.execute("SELECT max(started_at) FROM system_runs WHERE job=?", (key,)).fetchone()[0]
                if not rule["enabled"] or not SR.due(config(rule), now, H.parse_ts(last)):
                    continue
                triggers, actions = json.loads(rule["triggers"]), json.loads(rule["actions"])
                validate(triggers, actions)
                hours = max(t["hours"] for t in triggers)
                cutoff = SR._stamp(now - timedelta(hours=hours))
                candidates = c.execute(DUE_SQL, (cutoff,cutoff,cutoff,cutoff,cutoff,remaining)).fetchall()
                count = 0
                for candidate in candidates:
                    if count >= remaining:
                        break
                    task = dict(candidate)
                    if not safe(c, task) or not all(TRIGGERS[t["key"]][1](c, task, t["hours"], now) for t in triggers):
                        continue
                    for action in dict.fromkeys(actions):
                        ACTIONS[action][1](c, task, rule)
                    count += 1
                run.did(SR.count_words(count, "task closed", "tasks closed") if count else "No eligible done tasks", quiet=not count)
                SR.save(c, run)
            remaining -= count
        except Exception as exc:
            run.error = SR.failure_words(exc)
            failures.append({"task_rules": type(exc).__name__})
            SR.write(store, run)
        if not remaining:
            break


def install(app, store, auth, mutate):
    from fastapi import Request
    from . import models as M

    @app.post("/api/v2/task-rules")
    def create_task_rule(request: Request, body: M.TaskRuleUpdate):
        who = request.state.identity
        return mutate(request, body, lambda c: {"job": save(c, store.settings, auth, who, body)})

    @app.put("/api/v2/task-rules/{key}")
    def update_task_rule(key: str, request: Request, body: M.TaskRuleUpdate):
        who = request.state.identity
        return mutate(request, body, lambda c: {"job": save(c, store.settings, auth, who, body, key)})
