# pyright: strict
"""Stage rules whose When is a timer (backend/stage_rules.py, event `timer`), on the never-stuck pass.

A timer is one of:

- `in_stage`: N hours in the stage, not counting time snoozed (never_stuck._spent);
- `quiet`: N hours since the task's last activity: it entered the stage, the engine saw it change (`visits.last_change_at`:
  a move, an answer, a run's progress) or someone other than Tico wrote in its room;
- `waiting_person`: N hours that the first thing it waits on has been a person's (waiting_on.PERSON).

`sweep` reads tables only: no model, no GitHub. A snoozed task, a by-hand stage and an ended task have no timer. Each
rule judges a task once per mark (its visit and the moment its timer started from, `stage_rule_marks`), so a pass done
twice does nothing new, and a task that is active again starts its timer again. At release every timer starts from the
task's last activity, as it would have had the rule always been there.

Then:

- `tell_manager`: the pipeline's manager is told. All of one manager's matches go in one wake, "Stage rules: N tasks",
  and a manager gets one such wake an hour at most: what matches meanwhile waits for the next. A match of Urgent
  support waits (backend/routing.py) wakes its manager in the same pass, in its one wake of that pass: with
  everything waiting when its hour is open, else only the urgent tasks, which do not use up its hour. A rule with a tag condition marks whether the task had the tag, so adding it is noticed. A task already listed as
  Late on this visit is not listed again.
- `tell_owner`: the task's owner is told, on the task.
- `move_back`: the task goes back to `then_params.stage` with no owner, through the wake rules' task move
  (rules.TASK_RUNNERS), and the manager is told as above. It runs as a rule's Action (task_trigger_rules.from_action),
  so the move sets off no other rule.

Each Tell and Move back is written on the task's history (kind `stage_rule`, "Rule <name>: <reason>") and as the event
`stage_rule.told` or `stage_rule.moved_back`.
"""

import logging
from typing import Any

from . import hubdb as _hubdb
from . import task_depends

log = logging.getLogger(__name__)
Row = dict[str, Any]
# hubdb and the modules read lazily below are untyped: they get `Any` here, as never_stuck does.
H: Any = _hubdb


def _mod(name: str) -> Any:
    """A module read lazily (each imports this one, or is a hub), named literally so the PR test map can follow it."""
    # Every name is a literal import: a computed import would make this module, and the never-stuck pass that imports
    # it, depend on every file in the PR test selection (scripts/pr_checks.py DependencyMap).
    if name == "never_stuck":
        from . import never_stuck as found
    elif name == "rules":
        from . import rules as found
    elif name == "stage_rules":
        from . import stage_rules as found
    elif name == "store":
        from . import store as found
    elif name == "task_engine":
        from . import task_engine as found
    elif name == "task_trigger_rules":
        from . import task_trigger_rules as found
    elif name == "task_wakes":
        from . import task_wakes as found
    elif name == "waiting_on":
        from . import waiting_on as found
    else:
        raise ValueError(name)
    return found


KINDS = ("in_stage", "quiet", "waiting_person")
THENS = ("tell_manager", "tell_owner", "move_back")
BATCH_HOURS = 1  # a manager's Stage rules wake: one an hour at most
LISTED = 30  # tasks named in one wake; the rest are counted
SYSTEM = "system"

# Open work tasks the pass works on: not snoozed or blocked, not in a by-hand stage. `active_at` is the last activity; the end of a
# snooze counts, so a task set aside is not quiet the moment its snooze ends and its owner is woken.
_TASKS = (
    "SELECT t.id, t.title, t.number, t.owner_id, t.pipeline_id, t.stage_id, t.conversation_id, "
    "v.id AS visit_id, v.entered_at, v.late_told_at, s.name AS stage_name, "
    "MAX(v.entered_at, COALESCE(v.last_change_at, ''), COALESCE((SELECT MAX(m.created_at) FROM messages m "
    "WHERE m.conversation_id = t.conversation_id AND m.sender_id <> 'keeper'), ''), "
    # task_id first: snoozes is indexed on it (snoozes_task), not on visit_id.
    "COALESCE((SELECT MAX(z.ended_at) FROM snoozes z WHERE z.task_id = t.id AND z.visit_id = v.id), '')) "
    "AS active_at, "
    # An auto dream (backend/dream_auto.py), read with the rest: Quiet bot tasks passes it over (`_quiet_rule`).
    "EXISTS(SELECT 1 FROM events e WHERE e.action = 'dream.auto' AND e.target = t.id) AS auto_dream "
    "FROM tasks t JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL JOIN stages s ON s.id = t.stage_id "
    "WHERE t.pipeline_id IS NOT NULL AND t.status = 'open' AND s.kind = 'work' AND s.by_hand = 0 "
    "AND NOT EXISTS (SELECT 1 FROM snoozes z WHERE z.task_id = t.id AND z.ended_at IS NULL) "
    # Blocked by a task it depends on: nobody's turn, so no timer; its end sets last_change_at (task_depends.release).
    f"AND NOT {task_depends.BLOCKED % 't'}"
)
# An auto dream (backend/dream_auto.py): nobody waits on it, so Quiet bot tasks never tells a manager about one; its
# release policy and the auto pass end it (an index probe on events_action_target).
_AUTO_DREAM = "SELECT 1 FROM events WHERE action='dream.auto' AND target=? LIMIT 1"


def _quiet_rule(rule: Row) -> bool:
    """Whether this is the built-in Quiet bot tasks rule, which passes auto dreams over."""
    return _mod("stage_rules").QUIET_BOT_TASKS in (rule.get("id"), rule.get("builtin"))


def _quiet_on_auto_dream(conn: Any, rule: Row, task_id: str) -> bool:
    """Whether this is Quiet bot tasks on an auto dream (a tell queued for one is dropped when it is flushed)."""
    return _quiet_rule(rule) and conn.execute(_AUTO_DREAM, (task_id,)).fetchone() is not None


def rules(conn: Any) -> list[Row]:
    """The timer rules in force: active, not retired, with a timer this module knows."""
    SR = _mod("stage_rules")
    if not SR.has_table(conn):
        return []
    out: list[Row] = []
    for row in conn.execute(
        "SELECT * FROM stage_rules WHERE event='timer' AND status='active' AND retired_at IS NULL ORDER BY created_at, id"
    ).fetchall():
        rule: Row = SR.view(row)
        timer: Row = rule["timer"]
        try:
            hours = float(timer["hours"])
        except (KeyError, TypeError, ValueError):
            continue
        if timer.get("kind") not in KINDS or hours <= 0 or rule["then"] not in THENS:
            continue
        out.append({**rule, "timer_kind": timer["kind"], "hours": hours})
    return out


def sweep(conn: Any, at: str | None = None) -> dict[str, int]:
    """One pass over every timer rule, then the managers' batched wakes. A manager told of a match of Urgent support
    waits is woken now, once, whatever its last wake was; every other manager waits for its hour. Returns counts of
    what it did."""
    at = str(at or H.now())
    done = {"told": 0, "moved": 0, "wakes": 0}
    found = rules(conn)
    now: set[str] = set()  # managers with an urgent match this pass
    if found:
        tasks: list[Row] = [dict(r) for r in conn.execute(_TASKS).fetchall()]
        for rule in found:
            for task in tasks:
                if _in_scope(rule, task):
                    manager = _judge(conn, rule, task, at, done)
                    if manager and _urgent_rule(rule):
                        now.add(manager)
    for manager in sorted(now):
        # One wake per manager per pass: everything waiting when its hour is open, else only the urgent tasks.
        only = "" if not _had_hour(conn, manager, at) else _mod("stage_rules").URGENT_SUPPORT
        if _wake_manager(conn, manager, at, only):
            done["wakes"] += 1
    done["wakes"] += flush(conn, at, skip=now)
    return done


def _told_urgently(conn: Any, rule: Row, task: Row, mark: str) -> bool:
    """Whether this is Support waits on a task Urgent support waits told (or is telling) its manager about in this same
    quiet period: both read the same start, so the urgent mark is this one with the tag."""
    SR = _mod("stage_rules")
    if SR.SUPPORT_WAITS not in (rule.get("id"), rule.get("builtin")):
        return False
    base = mark.split("#", 1)[0]
    return (
        conn.execute(
            "SELECT 1 FROM stage_rule_marks WHERE rule_id=? AND task_id=? AND mark IN (?, ?) "
            "AND outcome IN ('pending', 'told')",
            (SR.URGENT_SUPPORT, task["id"], base, base + "#urgent"),
        ).fetchone()
        is not None
    )


def _support_rule(rule: Row) -> bool:
    """Support waits or Urgent support waits (backend/routing.py)."""
    SR = _mod("stage_rules")
    return bool({SR.SUPPORT_WAITS, SR.URGENT_SUPPORT} & {rule.get("id"), rule.get("builtin")})


def _urgent_rule(rule: Row) -> bool:
    return _mod("stage_rules").URGENT_SUPPORT in (rule.get("id"), rule.get("builtin"))


def _in_scope(rule: Row, task: Row) -> bool:
    if rule.get("pipeline_id") and rule["pipeline_id"] != task["pipeline_id"]:
        return False
    return not rule["stages"] or task["stage_id"] in rule["stages"]


def _started(conn: Any, rule: Row, task: Row) -> str | None:
    """When this task's timer started, or None when it has none now (it waits on no person)."""
    kind = rule["timer_kind"]
    if kind == "quiet":
        return str(task["active_at"])
    if kind == "in_stage":
        return str(task["entered_at"])
    E, W = _mod("task_engine"), _mod("waiting_on")
    row = E.task(conn, task["id"])
    waits: list[Any] = W.of(conn, row) if row else []
    if not waits or waits[0].state != W.PERSON:
        return None
    return max(str(waits[0].since or ""), task["entered_at"])


def _due(conn: Any, rule: Row, task: Row, started: str, at: str) -> bool:
    seconds = rule["hours"] * 3600
    if rule["timer_kind"] == "in_stage":
        spent = _mod("never_stuck")._spent(
            conn, {"id": task["visit_id"], "task_id": task["id"], "entered_at": started}, at
        )
        return float(spent) >= seconds
    start = H.parse_ts(started)
    now = H.parse_ts(at)
    if not start or not now:
        return False
    return float((now - start).total_seconds()) >= seconds


def _judge(conn: Any, rule: Row, task: Row, at: str, done: dict[str, int]) -> str | None:
    """Judge one task under one rule; the manager a tell now waits for, if any."""
    if task.get("auto_dream") and _quiet_rule(rule):
        return None
    started = _started(conn, rule, task)
    if not started or not _due(conn, rule, task, started, at):
        return None
    mark = f"{task['visit_id']}@{started}"
    label = str(rule["cond"].get("label") or "")
    if label and _mod("stage_rules")._tagged(conn, {"id": task["id"]}, label):
        mark += f"#{label.lower()}"  # a tag added to a task judged without it is judged again
    if conn.execute(
        "SELECT 1 FROM stage_rule_marks WHERE rule_id=? AND task_id=? AND mark=?", (rule["id"], task["id"], mark)
    ).fetchone():
        return None
    if _told_urgently(conn, rule, task, mark):
        # One quiet period, one tell: Urgent support waits already told the manager about this one.
        conn.execute(
            "INSERT OR IGNORE INTO stage_rule_marks(rule_id,task_id,mark,outcome,manager_id,matched_at) "
            "VALUES(?,?,?,'urgent',NULL,?)",
            (rule["id"], task["id"], mark, at),
        )
        return None
    problem: Any = _mod("store").Problem
    outcome = "no"
    conn.execute("SAVEPOINT stage_rule_timer")
    try:
        if _matches(conn, rule, task):
            outcome = _then(conn, rule, task, done)
        conn.execute("RELEASE stage_rule_timer")
    except (H.Refused, problem, ValueError, KeyError):
        # A move Tico refuses, or a rule it cannot carry out: undone, marked failed, and the pass goes on.
        conn.execute("ROLLBACK TO stage_rule_timer")
        conn.execute("RELEASE stage_rule_timer")
        log.warning("Stage rule %s failed on task %s", rule["id"], task["id"], exc_info=True)
        outcome = "failed"
    manager = _manager(conn, task, own=_support_rule(rule)) if outcome == "pending" else None
    conn.execute(
        "INSERT OR IGNORE INTO stage_rule_marks(rule_id,task_id,mark,outcome,manager_id,matched_at) VALUES(?,?,?,?,?,?)",
        (rule["id"], task["id"], mark, outcome, manager, at),
    )
    return manager


# ----------------------------------------------------------------------------- If
def _matches(conn: Any, rule: Row, task: Row) -> bool:
    """The rule's If: `turn` is read here (whose turn the task is, as the never-stuck ladder reads it: a bot's, also
    once the ladder has rested on it and wakes nobody, or a person's when the first thing it waits on is a person);
    every other key by stage_rules.matches."""
    SR, E, W = _mod("stage_rules"), _mod("task_engine"), _mod("waiting_on")
    row = E.task(conn, task["id"])
    if not row:
        return False
    turn = rule["cond"].get("turn")
    if turn:
        waits: list[Any] = W.of(conn, row)
        who: str | None = W.turn(conn, row, waits, resting=True) or (
            waits[0].who if waits and waits[0].state == W.PERSON else None
        )
        if _kind(who) != turn:
            return False
    return bool(SR.matches(conn, rule, row)[0])


def _kind(actor: str | None) -> str | None:
    return "bot" if H.is_bot(actor) else "person" if H.is_human(actor) else None


# ----------------------------------------------------------------------------- Then
def _said(rule: Row, task: Row) -> str:
    """What the history says: "Rule <name>: <reason>", the reason being the rule's own or its timer."""
    hours = float(rule["hours"])
    span = f"{hours:g} h"
    what = {"quiet": "quiet", "in_stage": f"in {task['stage_name']}", "waiting_person": "waiting on a person"}
    return str(_mod("stage_rules")._line(rule, f"{span} {what[rule['timer_kind']]}"))


def _then(conn: Any, rule: Row, task: Row, done: dict[str, int]) -> str:
    then = str(rule["then"])
    said = _said(rule, task)
    if then == "move_back":
        _move_back(conn, rule, task, said)
        done["moved"] += 1
        then = "tell_manager"
    if then == "tell_owner":
        _tell_owner(conn, rule, task, said)
        done["told"] += 1
        return "told"
    # Late's own mark dedupes a manager's tell only where Late told the manager; the Support timers are routed
    # work's one manager tell (a person's task Late by its due date is a label alone), so only their marks dedupe them.
    late = bool(task.get("late_told_at")) and not _support_rule(rule)
    outcome = "late" if late else "pending" if _manager(conn, task, own=_support_rule(rule)) else "no_manager"
    if outcome != "pending":
        # No manager will hear of it: a task the ladder stopped on asks a person instead (never_stuck.ask_person).
        E, NS = _mod("task_engine"), _mod("never_stuck")
        row: Row | None = E.task(conn, task["id"])
        visit: Row = (row or {}).get("visit") or {}
        if row and visit.get("level") == "person":
            NS.ask_person(conn, row)
    return outcome


def _move_back(conn: Any, rule: Row, task: Row, said: str) -> None:
    R, SR, E, TTR = _mod("rules"), _mod("stage_rules"), _mod("task_engine"), _mod("task_trigger_rules")
    stage = str(rule["then_params"].get("stage") or "")
    if not stage:
        raise ValueError("Move back names no stage")
    with TTR.from_action(rule["id"]):
        R.TASK_RUNNERS["move"](conn, task["id"], {"stage": stage}, said)
        now: Row = H.task(conn, task["id"]) or {}
        if now.get("owner_id"):
            E.reassign(conn, task["id"], None, H.TICO_ACTOR, said)
    moved: Row = H.task(conn, task["id"]) or {}
    task.update(stage_id=moved.get("stage_id"), late_told_at=None)
    SR.record(
        conn,
        task["id"],
        rule,
        said,
        H.TICO_ACTOR,
        "moved_back",
        {"from": task["stage_name"], "to": moved.get("stage_id")},
    )


def _tell_owner(conn: Any, rule: Row, task: Row, said: str) -> None:
    SR, E, task_wakes = _mod("stage_rules"), _mod("task_engine"), _mod("task_wakes")
    owner = task["owner_id"]
    row = E.task(conn, task["id"])
    if not owner or not row:
        return
    if H.is_bot(owner):
        key = f"{task['id']}:{task['visit_id']}:stage_rule:{rule['id']}:{task['active_at']}"
        task_wakes.add(conn, row, owner, row["visit"], task_wakes.body(conn, row, said, to=owner), wake_key=key)
    elif H.task_private_readable(conn, owner, row):
        conv = H.conversation(conn, task["conversation_id"])
        H._write_message(conn, H.TICO_ACTOR, owner, said, conv, "notice", {"task": task["id"]}, None, None)
    SR.record(conn, task["id"], rule, said, H.TICO_ACTOR, "told", {"to": owner})


def _manager(conn: Any, task: Row, own: bool = False) -> str | None:
    """The pipeline's manager, when it may read the task and is not its owner: a manager is not told about its own
    task, as the engine's own tells to the manager skip it (task_engine._tell_manager). With `own` (the Support timers,
    routed work's one overdue tell), a manager who owns the task is told too: the company's person can be both."""
    found = conn.execute("SELECT manager_id FROM pipelines WHERE id=?", (task["pipeline_id"],)).fetchone()
    manager: str | None = found[0] if found else None
    if not manager or (manager == task.get("owner_id") and not own):
        return None
    row = H.task(conn, task["id"])
    return manager if row and H.task_private_readable(conn, manager, row) else None


# ----------------------------------------------------------------------------- the managers' wakes
def _had_hour(conn: Any, manager: str, at: str) -> bool:
    """Whether the manager had a routine wake in the last hour (an urgent wake does not use up its hour)."""
    since = H.shift(at, hours=-BATCH_HOURS)
    return (
        conn.execute(
            "SELECT 1 FROM stage_rule_marks WHERE manager_id=? AND told_at > ? AND rule_id <> ? LIMIT 1",
            (manager, since, _mod("stage_rules").URGENT_SUPPORT),
        ).fetchone()
        is not None
    )


def flush(conn: Any, at: str | None = None, skip: set[str] | None = None) -> int:
    """One wake per manager with tells waiting, unless it had one in the last hour or was woken in this pass (`skip`).
    Returns the wakes sent."""
    at = str(at or H.now())
    if not H._has_table(conn, "stage_rule_marks"):
        return 0
    sent = 0
    managers: list[str] = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT manager_id FROM stage_rule_marks WHERE outcome='pending' AND manager_id IS NOT NULL "
            "ORDER BY manager_id"
        ).fetchall()
    ]
    for manager in managers:
        if manager in (skip or set()) or _had_hour(conn, manager, at):
            continue
        if _wake_manager(conn, manager, at):
            sent += 1
    return sent


def _named(row: Row) -> str:
    return f"#{row['number']} {row.get('title')}" if row.get("number") else str(row.get("title"))


def _wake_manager(conn: Any, manager: str, at: str, only_rule: str = "") -> bool:
    """One wake, "Stage rules: N tasks", naming every task waiting to be told to this manager (only `only_rule`'s,
    when given: an urgent wake leaves routine tells for the hour), each once. A task that ended, that the manager may
    no longer read, or that Late listed while it waited is not named."""
    R, SR = _mod("rules"), _mod("stage_rules")
    pending: list[Any] = conn.execute(
        "SELECT k.rule_id, k.task_id, k.mark, v.late_told_at FROM stage_rule_marks k LEFT JOIN tasks t "
        "ON t.id = k.task_id LEFT JOIN visits v ON v.task_id = k.task_id AND v.left_at IS NULL "
        "WHERE k.outcome='pending' AND k.manager_id=? AND (? = '' OR k.rule_id = ?) "
        "ORDER BY k.matched_at, t.number, k.task_id",
        (manager, only_rule, only_rule),
    ).fetchall()
    listed: list[tuple[Row, Row]] = []
    outcome: dict[tuple[str, str, str], str] = {}  # each pending row's settling: named, late or dropped
    for rule_id, task_id, mark, late_told_at in pending:
        key = (str(rule_id), str(task_id), str(mark))
        row: Row | None = H.task(conn, task_id)
        rule: Row | None = SR.get(conn, rule_id)
        if (
            not row
            or not rule
            or row.get("status") != "open"
            or not H.task_private_readable(conn, manager, row)
            or _quiet_on_auto_dream(conn, rule, str(task_id))
        ):
            outcome[key] = "dropped"
        elif late_told_at and not _support_rule(rule):
            outcome[key] = "late"  # Late listed it while it waited for this wake: the manager has it already
        else:
            outcome[key] = "named"
            if task_id not in {r["id"] for r, _ in listed}:
                listed.append((row, rule))
    _settle(conn, [k for k, o in outcome.items() if o == "dropped"], "dropped", None)
    _settle(conn, [k for k, o in outcome.items() if o == "late"], "late", None)
    named = [k for k, o in outcome.items() if o == "named"]
    if not listed:
        return False
    n = len(listed)
    body = [f"Stage rules: {n} task{'' if n == 1 else 's'}."]
    body += [f"- {_named(row)} (Rule {rule['name']})" for row, rule in listed[:LISTED]]
    if n > LISTED:
        body.append(f"- and {n - LISTED} more")
    body.append("Look at each: do it, reassign it, ask a person, snooze it or close it.")
    text = "\n".join(body)
    refs: Row = {"wake": "stage_rules", "tasks": [row["id"] for row, _ in listed]}
    if H.is_bot(manager):
        ok = R._tell_bot(conn, "stage-rules", H.actor_id(manager), text, refs, wake=True)[0]
    else:
        conv = H._pair_conversation(conn, H.TICO_ACTOR, manager, "notice", allow_held=True)
        ok = bool(H._write_message(conn, H.TICO_ACTOR, manager, text, conv, "notice", refs, None, None))
    if not ok:
        # The manager cannot be told (archived, gone): nothing says it was, and its next match tries again.
        _settle(conn, named, "failed", None)
        return False
    _settle(conn, named, "told", at)
    name = str(H.actor_name(conn, manager))
    for row, rule in listed:
        SR.record(conn, row["id"], rule, f"Rule {rule['name']}: {name} was told", H.TICO_ACTOR, "told", {"to": manager})
    return True


def _settle(conn: Any, keys: list[tuple[str, str, str]], outcome: str, told_at: str | None) -> None:
    conn.executemany(
        "UPDATE stage_rule_marks SET outcome=?, told_at=? WHERE rule_id=? AND task_id=? AND mark=?",
        [(outcome, told_at, rule_id, task_id, mark) for rule_id, task_id, mark in keys],
    )
