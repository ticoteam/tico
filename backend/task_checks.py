"""Task checks: rows of the shared Routines list (backend/task_rules.py) that look at a task before it is created,
before its status changes, or on a schedule.

    WHEN <trigger> FOR <task types | all types> CHECK <plain-code condition | Decide question> THEN <action>

A check row is a `task_rules` row with `check_json` set and `types_json` its FOR scope ([] is all types). Its
department tag and edit rights are the task rule's, checked by the server (`task_rules.require_change`).

- Duplicate (before create, block or warn): plain code picks at most CANDIDATES open tasks of the same type from the
  last WINDOW_DAYS with the same requester or owner whose title shares words with the new one. No near match, no
  model call. An identical title is a match on its own; any other near match is one Tico Decide call through the
  Tico AI Gateway (`questions/covered.json`): which one, or none. Only the Gateway answers it, never a customer's own
  key or the company's own model. Fail open: no Gateway, rehearsal, the spend cap, the caller's daily calls used, an
  error or no answer within BUDGET seconds creates the task and counts one skipped check on the row.
  A block is a plain refusal naming the rule and the matching task (title, status, owner, age) and both ways forward:
  comment there, or create again with a reason, which creates it and logs the override and the reason on the rule.
  A warn creates the task and returns the warning with it.
- Waiting must say on whom or what (before status change, block or warn): the existing waiting validation in
  `hubdb.task_update` (a task its owner asked for itself, moved to waiting with nothing recorded to wait on and no
  note); this row switches it, scopes it and names it. With no row at all, that validation refuses as it always did.
- Quiet waits (on a schedule, nudge then park): a waiting task with nothing on it for the row's hours is nudged once
  (a comment to its owner), and parked (snoozed for the same hours) if it stays quiet as long again. Off, with no
  hours, until a department or task type sets them: there is no company-wide number.

Check now / Check all are dry runs over existing tasks the caller can read: they list what breaks the rule and change
nothing. A person's confirm runs the listed fix on at most FIX_LIMIT tasks, as that person, with their own rights.
Checks never delete, reassign, spend beyond the one Decide call, send outside Tico or publish.
"""
import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone

from clients import judge as J
from .store import H, Problem

LOG = logging.getLogger("tico.task_checks")

DUPLICATE, WAITING, QUIET = "task-check-duplicate", "task-check-waiting", "task-check-quiet-wait"
CANDIDATES = 20
WINDOW_DAYS = 30
SCAN = 500              # the newest tasks plain code reads for candidates or a dry run
NEAR = 0.5              # the share of the shorter title's words two titles must share to be a near match
SAME = 0.8              # a dry run's bar: no model is asked there, so only a close title counts
BUDGET = 1.5            # seconds a create waits for Decide, all of it
FIX_LIMIT = 50
STATS_DAYS = 30
QUESTION_SET = "covered"
STOP = {"the", "and", "for", "with", "from", "into", "onto", "that", "this", "our", "your", "a", "an", "to", "of",
        "on", "in", "at", "by", "is", "it", "be", "or", "as"}

TRIGGERS = {"before_create": "a task is about to be created",
            "before_status": "a task's status is about to change",
            "schedule": "on a schedule"}
ACTIONS = {"block": "block it", "warn": "warn", "nudge": "nudge its owner", "park": "park it"}


def _quiet_words(check):
    hours = check.get("hours")
    return (f"a waiting task has had nothing on it for {hours} hours" if hours
            else "a waiting task has had nothing on it for a time not set yet")


# Each check: its one trigger, the actions it may take, whether it asks Decide, its words and what it does.
CHECKS = {
    "duplicate": {"trigger": "before_create", "actions": ("block", "warn"), "decide": True, "hours": False,
                  "words": lambda check: "it may already be asked for in an open task (Decide)",
                  "what": "Looks for an open task already asking for the same thing. Create again with a reason to "
                          "file it anyway."},
    "waiting_named": {"trigger": "before_status", "actions": ("block", "warn"), "decide": False, "hours": False,
                      "words": lambda check: "a move to waiting says on whom or what it waits",
                      "what": "A task its owner asked for itself names who or what it waits on before it waits."},
    "quiet_wait": {"trigger": "schedule", "actions": ("nudge", "park"), "decide": False, "hours": True,
                   "words": _quiet_words,
                   "what": "Nudges the owner of a quiet waiting task once, then parks it. Unsnooze to undo."},
}

# The day-one rows: Duplicate and Waiting on, quiet waits present but off with no hours.
SEEDS = [
    (DUPLICATE, "Duplicate", '[{"key":"before_create"}]', '["block"]', 1, "15m", '{"key":"duplicate"}'),
    (WAITING, "Waiting must say on whom or what", '[{"key":"before_status"}]', '["block"]', 1, "15m",
     '{"key":"waiting_named"}'),
    (QUIET, "Nudge, then park, quiet waits", '[{"key":"schedule"}]', '["nudge","park"]', 0, "hourly",
     '{"key":"quiet_wait","hours":null}'),
]
SEED_DEPARTMENT = "engineering"     # as the day-one close rule


def migrate(c):
    """FOR and CHECK on the task rule row, then the day-one checks. Re-running changes nothing."""
    H.add_column(c, "task_rules", "types_json", "TEXT NOT NULL DEFAULT '[]'")
    H.add_column(c, "task_rules", "check_json", "TEXT")
    now = H.now()
    for key, name, triggers, actions, on, preset, check in SEEDS:
        c.execute("INSERT OR IGNORE INTO task_rules(id,name,department,triggers_json,actions_json,enabled,preset,at,"
                  "created_by,created,updated,types_json,check_json) VALUES(?,?,?,?,?,?,?,NULL,'keeper',?,?,'[]',?)",
                  (key, name, SEED_DEPARTMENT, triggers, actions, on, preset, now, now, check))


# ----------------------------------------------------------------------------- the row
def validate(triggers, actions, check, types):
    if not isinstance(check, dict) or check.get("key") not in CHECKS:
        raise Problem("invalid", "Choose an available task check", 422)
    spec = CHECKS[check["key"]]
    if set(check) - {"key", "hours"} or ("hours" in check and not spec["hours"]):
        raise Problem("invalid", "Choose an available task check", 422)
    hours = check.get("hours")
    if hours is not None and (isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 8760):
        raise Problem("invalid", "The quiet time must be 1 to 8760 whole hours", 422)
    if triggers != [{"key": spec["trigger"]}]:
        raise Problem("invalid", f"This check runs when {TRIGGERS[spec['trigger']]}", 422)
    if not actions or len(set(actions)) != len(actions) or any(a not in spec["actions"] for a in actions):
        raise Problem("invalid", "Choose " + " or ".join(ACTIONS[a] for a in spec["actions"]) + " for this check", 422)
    if spec["actions"] == ("block", "warn") and len(actions) != 1:
        raise Problem("invalid", "Choose block or warn", 422)
    if not isinstance(types, list) or len(types) > 20 or any(not isinstance(t, str) or not t for t in types):
        raise Problem("invalid", "Choose up to 20 task types, or all types", 422)


def check_of(rule):
    raw = rule.get("check_json")
    return json.loads(raw) if raw else None


def types_of(rule):
    return json.loads(rule.get("types_json") or "[]")


def covers(rule, type_id):
    types = types_of(rule)
    return not types or (type_id or H.GENERAL_TYPE) in types


def for_words(c, types):
    if not types:
        return "all types"
    names = {r[0]: r[1] for r in c.execute("SELECT id,name FROM task_types")}
    return ", ".join(names.get(t, t) for t in types)


def stats(c, key, now=None):
    """What the row has done lately, in words: catches, overrides and skipped checks over STATS_DAYS."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=STATS_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    counts = dict(c.execute("SELECT action,count(*) FROM events WHERE target=? AND action IN "
                            "('task_check.caught','task_check.override','task_check.skipped') AND ts>=? GROUP BY action",
                            (key, since)).fetchall())

    def n(action, one, many):
        k = counts.get(action, 0)
        return f"{k} {one if k == 1 else many}" if k else f"no {many}"
    return (f"Last {STATS_DAYS} days: {n('task_check.caught', 'catch', 'catches')}, "
            f"{n('task_check.override', 'override', 'overrides')}, {n('task_check.skipped', 'skipped check', 'skipped checks')}")


def row_fields(c, rule, now=None):
    """The check's part of its Routines row (task_rules.row)."""
    check, types = check_of(rule), types_of(rule)
    spec = CHECKS[check["key"]]
    then = [ACTIONS[a] for a in json.loads(rule["actions_json"])]
    if check["key"] == "quiet_wait" and len(then) == 2:
        then = [then[0], "if it stays quiet, " + then[1]]
    scheduled = spec["trigger"] == "schedule"
    return {"check": check, "types": types, "for": for_words(c, types), "check_words": spec["words"](check),
            "decide": spec["decide"], "what": spec["what"], "when": [TRIGGERS[spec["trigger"]]], "then": then,
            "stats": stats(c, rule["id"], now), "scheduled": scheduled, "checkable": True}


def catalog():
    """What a new check row may be: the editor's choices."""
    return [{"key": key, "trigger": spec["trigger"], "when": TRIGGERS[spec["trigger"]],
             "words": spec["words"]({"key": key, "hours": None}), "hours": spec["hours"], "decide": spec["decide"],
             "actions": [{"key": a, "words": ACTIONS[a]} for a in spec["actions"]]} for key, spec in CHECKS.items()]


# ----------------------------------------------------------------------------- records on the rule
def record(c, action, rule_id, detail):
    H.event(c, H.KEEPER, action, rule_id, detail)


def caught(c, rule, task):
    """A refusal rolled back its write; the catch is written after it (store.refused)."""
    record(c, "task_check.caught", rule, {"task": task})


def _rules(c, key):
    try:
        rows = c.execute("SELECT * FROM task_rules WHERE check_json IS NOT NULL ORDER BY created,id").fetchall()
    except Exception:          # a bare hubdb database (unit tests) has no task rules
        return None
    return [dict(r) for r in rows if (check_of(dict(r)) or {}).get("key") == key]


def rule_for(c, key, type_id):
    """The first switched-on check of this kind that covers `type_id`, or None."""
    return next((r for r in _rules(c, key) or [] if r["enabled"] and covers(r, type_id)), None)


# ----------------------------------------------------------------------------- waiting must say on whom or what
def waiting(conn, row):
    """(action, rule) for a move to waiting that the existing validation would refuse: ("block", None) with no row at
    all (the validation as it always was), (None, rule) when the row is off or does not cover this type."""
    rules = _rules(conn, "waiting_named")
    if not rules:
        return "block", None
    rule = next((r for r in rules if r["enabled"] and covers(r, row.get("type_id"))), None)
    if not rule:
        return None, rules[0]
    return json.loads(rule["actions_json"])[0], rule


def tag(exc, rule, task, extra=None):
    """Mark a refusal as this check's catch: store.refused writes it on the rule after the rollback."""
    exc.task_check = {"rule": rule["id"], "task": task}
    exc.extra = {"task_check": {"rule": rule["id"], "name": rule["name"], **(extra or {})}}
    return exc


# ----------------------------------------------------------------------------- duplicate
def words(title):
    return {w for w in re.findall(r"[a-z0-9]+", str(title or "").lower()) if len(w) > 1 and w not in STOP}


def overlap(a, b):
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def same_title(a, b):
    return " ".join(str(a or "").lower().split()) == " ".join(str(b or "").lower().split())


def candidates(c, actor, owner, type_id, title, at=None, limit=CANDIDATES, readable=None):
    """Open tasks of the same type from the last WINDOW_DAYS with the same requester or owner whose titles share
    words with `title`, closest first, at most `limit`. Only tasks the caller may read (`readable(c, row)`: the API's
    auth.task, with bot Read access and a second principal) are kept, so a task the caller cannot read is never
    named, never sent to Decide, and never blocks the create."""
    readable = readable or (lambda c, row: H.task_private_readable(c, actor, row))
    at = at or H.now()
    mine = words(title)
    if not mine:
        return []
    statuses = H.ACTIVE_STATUSES
    rows = c.execute(f"SELECT * FROM tasks WHERE status IN ({','.join('?' * len(statuses))}) AND closed_at IS NULL "
                     "AND coalesce(type_id,?)=? AND created>=? AND (requester=? OR owner=?) "
                     "ORDER BY created DESC LIMIT ?",
                     (*statuses, H.GENERAL_TYPE, type_id or H.GENERAL_TYPE, H.shift(at, days=-WINDOW_DAYS),
                      actor, owner, SCAN)).fetchall()
    scored = []
    for r in rows:
        score = overlap(mine, words(r["title"]))
        if score >= NEAR and readable(c, dict(r)):
            scored.append((score, dict(r)))
    scored.sort(key=lambda s: -s[0])
    return [r for _, r in scored[:limit]]


def age(created, at=None):
    then, now = H.parse_ts(created), H.parse_ts(at or H.now())
    if not then or not now:
        return "age unknown"
    hours = int((now - then).total_seconds() // 3600)
    if hours < 1:
        return "under an hour old"
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''} old"
    return f"{hours // 24} days old"


def describe(c, task):
    return {"id": task["id"], "title": task["title"], "status": task["status"],
            "owner": H.person_name(c, task["owner"]) if H.is_human(task["owner"]) else H.actor_id(task["owner"]),
            "age": age(task["created"])}


def _call(step, state, questions, label, budget):
    """Ask on a thread of its own and wait at most `budget` seconds: (result, error). A late answer is dropped."""
    box, done = {}, threading.Event()

    def work():
        try:
            with J.within(budget):
                box["result"] = step(state, questions, label)
        except J.JudgeError as exc:
            box["error"] = J.error_code(exc)
        except Exception as exc:          # a fake, a provider bug: the create goes on
            LOG.warning("Duplicate check failed: %s", type(exc).__name__)
            box["error"] = "error"
        finally:
            done.set()
    threading.Thread(target=work, name="task-check", daemon=True).start()
    if not done.wait(budget):
        return None, "timeout"
    return box.get("result"), box.get("error")


def _decide(store, primary, who, title, body, found):
    """One Decide call through the Gateway: ("match", task) | ("none", None) | ("skipped", why)."""
    from .judge import audit_detail, daily_cap, live, metered, over_spend_cap, used_today
    settings = store.settings
    try:
        qset = J.load_set(QUESTION_SET)
    except J.JudgeError:
        return "skipped", "unconfigured"
    options = {f"task{i + 1}": f"{t['title']} ({t['status']})" for i, t in enumerate(found)}
    questions = {"covered": J.with_options(qset["questions"]["covered"], options)}
    state = {"candidate": {"title": title, "body": str(body or "")[:1500]},
             "existing": [{"option": k, "title": t["title"], "status": t["status"], "created": t["created"]}
                          for k, t in zip(options, found)]}
    with store.read() as c:
        if settings.rehearsal:
            return "skipped", "rehearsal"
        if over_spend_cap(c, settings):
            return "skipped", "spend_cap"
        try:
            if used_today(c, who.actor) >= daily_cap(settings, who.role):
                return "skipped", "budget"
        except (AttributeError, TypeError, ValueError):
            pass
        # Only the Gateway answers: no customer key and no company model in the create path.
        steps = [s for s in live(primary, c, settings) if getattr(s, "provider", J.GATEWAY_PROVIDER) == J.GATEWAY_PROVIDER]
    if not steps:
        return "skipped", "unconfigured"
    step, label = steps[0], qset["label"]
    model = getattr(step, "model", "") or J.GATEWAY_MODEL
    result, error = _call(lambda s, q, l: metered(store, [model], s, q, l, lambda: step(s, q, l)),
                          state, questions, label, BUDGET)
    answer = None
    if result is not None and not error:
        try:
            answer = J.choice(J.check_answers(result.get("answers"), questions), "covered")
        except (J.JudgeError, AttributeError, KeyError, TypeError, ValueError):
            error = "shape"
    detail = {"label": label, "questions": 1, "types": ["choice"], "check": "duplicate", "candidates": len(found)}
    if isinstance(result, dict):
        detail.update(audit_detail(result))
    if error:
        detail["error"] = error
    elif answer:
        detail["answers"] = {"covered": {"choice": answer[0], "confidence": round(answer[1], 3)}}
    try:
        with store.transaction() as c:
            H.event(c, who.actor, "judge.call", label, detail)
    except Exception as exc:
        LOG.warning("Duplicate check audit failed: %s", type(exc).__name__)
    if error:
        return "skipped", error
    if answer and answer[0] in options and answer[1] >= qset["thresholds"].get("same", 1.01):
        return "match", found[list(options).index(answer[0])]
    return "none", None


def before_create(store, primary, who, owner, type_id, title, body, readable=None):
    """Before the create's write: {"rule", "match"} | {"rule", "skipped"} | None. Reads, and at most one Decide call
    off the write lock."""
    with store.read() as c:
        rule = rule_for(c, "duplicate", type_id)
        if not rule:
            return None
        found = candidates(c, who.actor, owner, type_id, title, readable=readable)
    if not found:
        return None                         # no near match: no Decide call
    exact = next((t for t in found if same_title(t["title"], title)), None)
    if exact:
        return {"rule": rule, "match": exact}
    outcome, value = _decide(store, primary, who, title, body, found)
    if outcome == "match":
        return {"rule": rule, "match": value}
    if outcome == "skipped":
        return {"rule": rule, "skipped": value}
    return None


def on_create(c, actor, check, reason, readable=None):
    """In the create's write, once the create's own checks have passed: refuses a block with no reason (the savepoint
    takes the new task back); returns the warning for a warn (or None). The rule and the matching task are read again: a rule switched off or a match closed since lets it by."""
    if not check or not check.get("match"):
        return None
    rule = c.execute("SELECT * FROM task_rules WHERE id=?", (check["rule"]["id"],)).fetchone()
    match = H.task(c, check["match"]["id"])
    if (not rule or not rule["enabled"] or not match or match["status"] not in H.ACTIVE_STATUSES
            or (readable and not readable(c, match))):
        check.pop("match", None)
        return None
    rule, seen = dict(rule), describe(c, match)
    action = json.loads(rule["actions_json"])[0]
    # A private match is never named: the refusal is also the audit row a company owner reads (hubdb.refuse), so it
    # keeps no title or id of a task only its two parties may read, as hubdb's exact-duplicate guard does for BotOps.
    hidden = H.task_private(c, match)
    said = ("an existing private task you are on" if hidden else
            f"task {match['id'][:8]} \"{seen['title']}\" ({seen['status']}, owner {seen['owner']}, {seen['age']})")
    if action == "block" and not str(reason or "").strip():
        detail = (f"Not created: the task check '{rule['name']}' found this already asked for in {said}. "
                  f"Comment on that task instead, or create it again with a reason (hub task create --reason \"...\"; "
                  f"MCP: reason) to file it anyway; the reason is logged on the check.")
        try:
            H.refuse(c, actor, "task_check", detail)
        except H.Refused as exc:
            raise tag(exc, rule, None if hidden else match["id"], {"match": {"private": True} if hidden else seen})
    if action == "warn" and not str(reason or "").strip():
        return f"Task check '{rule['name']}': this may already be asked for in {said}. Comment there if it is."
    return None


def after_create(c, actor, check, task_id, reason):
    """In the create's write, once the task is made: the override and its reason, or a skipped check, on the rule. A
    private match's id stays out of the rule's history."""
    if not check:
        return
    if check.get("match"):
        match = H.task(c, check["match"]["id"])
        check = {**check, "match": {"id": None if not match or H.task_private(c, match) else match["id"]}}
    if check.get("match") and str(reason or "").strip():
        record(c, "task_check.override", check["rule"]["id"],
               {"task": task_id, "match": check["match"]["id"], "by": actor, "reason": str(reason).strip()[:500]})
    elif check.get("match"):
        record(c, "task_check.caught", check["rule"]["id"], {"task": task_id, "match": check["match"]["id"], "warn": True})
    elif check.get("skipped"):
        record(c, "task_check.skipped", check["rule"]["id"], {"task": task_id, "why": check["skipped"]})


# ----------------------------------------------------------------------------- quiet waits
def _quiet_rows(c, rule, cutoff, limit):
    types = types_of(rule)
    scope = f" AND coalesce(t.type_id,'{H.GENERAL_TYPE}') IN ({','.join('?' * len(types))})" if types else ""
    return [dict(r) for r in c.execute(
        "SELECT t.* FROM tasks t WHERE t.status='waiting' AND t.closed_at IS NULL "
        "AND (t.snoozed_until IS NULL OR t.snoozed_until<=?) AND t.updated<=? "
        "AND NOT EXISTS (SELECT 1 FROM task_events e WHERE e.task_id=t.id AND e.ts>?)" + scope +
        " ORDER BY t.updated,t.id LIMIT ?", (H.now(), cutoff, cutoff, *types, limit))]


def _nudged(c, task):
    """Whether this rule's nudge is the last thing that happened on the task."""
    last = c.execute("SELECT max(ts) FROM events WHERE target=? AND action='task_check.nudged'", (task["id"],)).fetchone()[0]
    return bool(last) and task["updated"] <= last


def nudge(c, actor, task, hours=None):
    why = H.waiting_for(c, task)
    words = (f"This task has waited on {why}" if why else "This task has waited") + \
            (f" with nothing on it for {hours} hours" if hours else "") + \
            ": chase whoever it waits on, say who or what with `--on`, or move it on."
    H.task_comment(c, actor, task["id"], words)
    H.event(c, actor, "task_check.nudged", task["id"], {})


def park(c, actor, task, hours):
    from . import task_snooze
    until = H.shift(H.now(), hours=min(int(hours), task_snooze.MAX_DAYS * 24))
    task_snooze.snooze(c, actor, task["id"], until, mover=actor == H.KEEPER)


def quiet_pass(c, rule, now, remaining):
    """One scheduled pass of a quiet-waits row: (count, words). A task is nudged once, then parked."""
    check, actions = check_of(rule), json.loads(rule["actions_json"])
    hours = check.get("hours")
    if not hours:
        return 0, "No quiet time set"
    from . import system_routines as SR
    cutoff = SR._stamp(now - timedelta(hours=hours))
    count = nudged = parked = 0
    for task in _quiet_rows(c, rule, cutoff, remaining):
        if "nudge" in actions and not _nudged(c, task):
            nudge(c, H.KEEPER, task, hours)
            nudged, did = nudged + 1, "nudge"
        elif "park" in actions:
            park(c, H.KEEPER, task, hours)
            parked, did = parked + 1, "park"
        else:
            continue
        record(c, "task_check.caught", rule["id"], {"task": task["id"], "action": did})
        count += 1
    if not count:
        return 0, "No quiet waiting tasks"
    return count, ", ".join(x for x in (f"{nudged} nudged" if nudged else "", f"{parked} parked" if parked else "") if x)


# ----------------------------------------------------------------------------- check now / fix
FIXES = {"duplicate": "close it as a duplicate and link it",
         "waiting_named": "ask the owner to name who or what it waits on",
         "quiet_wait": "nudge its owner"}


def dry_run(c, rule, visible, at=None):
    """What breaks the rule among existing tasks the caller can read (`visible(c, row)`), at most FIX_LIMIT. Writes
    nothing. Each: the task, why, and the fix a confirm would run."""
    at = at or H.now()
    check = check_of(rule)
    key, found = check["key"], []
    statuses = H.ACTIVE_STATUSES
    types = types_of(rule)
    scope = f" AND coalesce(type_id,'{H.GENERAL_TYPE}') IN ({','.join('?' * len(types))})" if types else ""
    if key == "duplicate":
        rows = [dict(r) for r in c.execute(
            f"SELECT * FROM tasks WHERE status IN ({','.join('?' * len(statuses))}) AND closed_at IS NULL "
            "AND created>=?" + scope + " ORDER BY created DESC LIMIT ?",
            (*statuses, H.shift(at, days=-WINDOW_DAYS), *types, SCAN))]
        rows = [r for r in rows if visible(c, r)]
        taken = set()
        for newer in rows:                      # newest first: the newer one is the duplicate
            if newer["id"] in taken:
                continue
            mine = words(newer["title"])
            older = next((o for o in rows if o["id"] != newer["id"] and o["id"] not in taken
                          and o["created"] < newer["created"]
                          and (o.get("type_id") or H.GENERAL_TYPE) == (newer.get("type_id") or H.GENERAL_TYPE)
                          and (o["requester"] == newer["requester"] or o["owner"] == newer["owner"])
                          and overlap(mine, words(o["title"])) >= SAME), None)
            if older:
                taken.update((newer["id"], older["id"]))
                found.append({"task": describe(c, newer), "duplicate_of": describe(c, older),
                              "why": f"looks like task {older['id'][:8]} \"{older['title']}\"", "fix": FIXES[key]})
            if len(found) >= FIX_LIMIT:
                break
    elif key == "waiting_named":
        for r in c.execute("SELECT * FROM tasks WHERE status='waiting' AND closed_at IS NULL" + scope +
                           " ORDER BY updated LIMIT ?", (*types, SCAN)):
            r = dict(r)
            if r["owner"] == r["requester"] and not str(r.get("note") or "").strip() and not H.waiting_for(c, r) \
                    and visible(c, r):
                found.append({"task": describe(c, r), "why": "waits with nothing named", "fix": FIXES[key]})
                if len(found) >= FIX_LIMIT:
                    break
    elif key == "quiet_wait":
        hours = check.get("hours")
        if not hours:
            raise Problem("invalid", "Set the quiet time on this check first", 422)
        cutoff = H.shift(at, hours=-hours)
        for r in _quiet_rows(c, rule, cutoff, SCAN):
            if visible(c, r):
                found.append({"task": describe(c, r), "why": f"nothing on it for {hours} hours", "fix": FIXES[key]})
                if len(found) >= FIX_LIMIT:
                    break
    return {"key": rule["id"], "name": rule["name"], "found": found, "fix": FIXES[key]}


def fix(c, who, rule, items, readable):
    """Run the listed fix on at most FIX_LIMIT tasks, as the confirming person with their own rights. Both the task and
    the one it duplicates must be readable to them (`readable(c, task_id)`: the row, or None); a missing task and one
    they cannot read get the same answer. Each task's outcome: done, or the refusal in words; one refused task does
    not stop the rest."""
    if len(items) > FIX_LIMIT:
        raise Problem("invalid", f"Fix at most {FIX_LIMIT} tasks at a time", 422)
    key = check_of(rule)["key"]
    done = []
    for item in items:
        task_id = item.get("task")
        c.execute("SAVEPOINT task_check_fix")
        try:
            row = readable(c, task_id) if task_id else None
            if not row:
                raise Problem("not_found", "Task not found", 404)
            if key == "duplicate":
                other = item.get("duplicate_of")
                if not other:
                    raise Problem("invalid", "Name the task it duplicates", 422)
                if not readable(c, other):
                    raise Problem("not_found", "Task not found", 404)
                from . import task_relations as TR
                TR.relate(c, who.actor, task_id, other, "duplicate_of")
                # The note goes into status history and the close wake to the owner and requester, who may not read
                # the other task; the duplicate_of link names it, filtered per reader.
                H.task_close(c, who.actor, task_id, note="Closed as a duplicate")
            elif key == "waiting_named":
                H.task_comment(c, who.actor, task_id, "Name who or what this task waits on (`hub task update "
                               "--status waiting --on <person>`, a blocker or a subtask), or move it out of waiting.")
            else:
                nudge(c, who.actor, row, check_of(rule).get("hours"))
            c.execute("RELEASE task_check_fix")
            done.append({"task": task_id, "ok": True})
        except (Problem, H.Refused) as exc:
            c.execute("ROLLBACK TO task_check_fix")
            c.execute("RELEASE task_check_fix")
            done.append({"task": task_id, "ok": False, "detail": getattr(exc, "detail", str(exc))})
    fixed = sum(1 for d in done if d["ok"])
    if fixed:
        record(c, "task_check.fixed", rule["id"], {"by": who.actor, "tasks": [d["task"] for d in done if d["ok"]]})
    return {"key": rule["id"], "fixed": fixed, "results": done}
