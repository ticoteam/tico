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
  and a manager gets one such wake an hour at most: what matches meanwhile waits for the next. A task already listed as
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

# Open work tasks the pass works on: not snoozed, not in a by-hand stage. `active_at` is the last activity; the end of a
# snooze counts, so a task set aside is not quiet the moment its snooze ends and its owner is woken.
_TASKS = (
    "SELECT t.id, t.title, t.number, t.owner_id, t.pipeline_id, t.stage_id, t.conversation_id, "
    "v.id AS visit_id, v.entered_at, v.late_told_at, s.name AS stage_name, "
    "MAX(v.entered_at, COALESCE(v.last_change_at, ''), COALESCE((SELECT MAX(m.created_at) FROM messages m "
    "WHERE m.conversation_id = t.conversation_id AND m.sender_id <> 'keeper'), ''), "
    "COALESCE((SELECT MAX(z.ended_at) FROM snoozes z WHERE z.visit_id = v.id), '')) AS active_at "
    "FROM tasks t JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL JOIN stages s ON s.id = t.stage_id "
    "WHERE t.pipeline_id IS NOT NULL AND t.status = 'open' AND s.kind = 'work' AND s.by_hand = 0 "
    "AND NOT EXISTS (SELECT 1 FROM snoozes z WHERE z.task_id = t.id AND z.ended_at IS NULL)"
)


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
    """One pass over every timer rule, then the managers' batched wakes. Returns counts of what it did."""
    at = str(at or H.now())
    done = {"told": 0, "moved": 0, "wakes": 0}
    found = rules(conn)
    if found:
        tasks: list[Row] = [dict(r) for r in conn.execute(_TASKS).fetchall()]
        for rule in found:
            for task in tasks:
                if _in_scope(rule, task):
                    _judge(conn, rule, task, at, done)
    done["wakes"] += flush(conn, at)
    return done


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


def _judge(conn: Any, rule: Row, task: Row, at: str, done: dict[str, int]) -> None:
    started = _started(conn, rule, task)
    if not started or not _due(conn, rule, task, started, at):
        return
    mark = f"{task['visit_id']}@{started}"
    if conn.execute(
        "SELECT 1 FROM stage_rule_marks WHERE rule_id=? AND task_id=? AND mark=?", (rule["id"], task["id"], mark)
    ).fetchone():
        return
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
    manager = _manager(conn, task) if outcome == "pending" else None
    conn.execute(
        "INSERT OR IGNORE INTO stage_rule_marks(rule_id,task_id,mark,outcome,manager_id,matched_at) VALUES(?,?,?,?,?,?)",
        (rule["id"], task["id"], mark, outcome, manager, at),
    )


# ----------------------------------------------------------------------------- If
def _matches(conn: Any, rule: Row, task: Row) -> bool:
    """The rule's If: `turn` is read here (whose turn the task is, as the never-stuck ladder reads it: a bot's, or a
    person's when the first thing it waits on is a person); every other key by stage_rules.matches."""
    SR, E, W = _mod("stage_rules"), _mod("task_engine"), _mod("waiting_on")
    row = E.task(conn, task["id"])
    if not row:
        return False
    turn = rule["cond"].get("turn")
    if turn:
        waits: list[Any] = W.of(conn, row)
        who: str | None = W.turn(conn, row, waits) or (waits[0].who if waits and waits[0].state == W.PERSON else None)
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
    if task.get("late_told_at"):
        return "late"  # already listed as Late on this visit: the manager has it
    return "pending" if _manager(conn, task) else "no_manager"


def _move_back(conn: Any, rule: Row, task: Row, said: str) -> None:
    R, SR, E, TTR = _mod("rules"), _mod("stage_rules"), _mod("task_engine"), _mod("task_trigger_rules")
    stage = str(rule["then_params"].get("stage") or "")
    if not stage:
        raise ValueError("Move back names no stage")
    with TTR.from_action(rule["id"]):
        R.TASK_RUNNERS["move"](conn, task["id"], {"stage": stage}, said)
        now: Row = H.task(conn, task["id"]) or {}
        if now.get("owner_id"):
            E.reassign(conn, task["id"], None, H.KEEPER, said)
    moved: Row = H.task(conn, task["id"]) or {}
    task.update(stage_id=moved.get("stage_id"), late_told_at=None)
    SR.record(
        conn, task["id"], rule, said, H.KEEPER, "moved_back", {"from": task["stage_name"], "to": moved.get("stage_id")}
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
        H._write_message(conn, H.KEEPER, owner, said, conv, "notice", {"task": task["id"]}, None, None)
    SR.record(conn, task["id"], rule, said, H.KEEPER, "told", {"to": owner})


def _manager(conn: Any, task: Row) -> str | None:
    """The pipeline's manager, when it may read the task and is not its owner: a manager is not told about its own
    task, as the engine's own tells to the manager skip it (task_engine._tell_manager)."""
    found = conn.execute("SELECT manager_id FROM pipelines WHERE id=?", (task["pipeline_id"],)).fetchone()
    manager: str | None = found[0] if found else None
    if not manager or manager == task.get("owner_id"):
        return None
    row = H.task(conn, task["id"])
    return manager if row and H.task_private_readable(conn, manager, row) else None


# ----------------------------------------------------------------------------- the managers' wakes
def flush(conn: Any, at: str | None = None) -> int:
    """One wake per manager with tells waiting, unless it had one in the last hour. Returns the wakes sent."""
    at = str(at or H.now())
    if not H._has_table(conn, "stage_rule_marks"):
        return 0
    since = H.shift(at, hours=-BATCH_HOURS)
    sent = 0
    managers: list[str] = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT manager_id FROM stage_rule_marks WHERE outcome='pending' AND manager_id IS NOT NULL "
            "ORDER BY manager_id"
        ).fetchall()
    ]
    for manager in managers:
        if conn.execute(
            "SELECT 1 FROM stage_rule_marks WHERE manager_id=? AND told_at > ? LIMIT 1", (manager, since)
        ).fetchone():
            continue
        if _wake_manager(conn, manager, at):
            sent += 1
    return sent


def _named(row: Row) -> str:
    return f"#{row['number']} {row.get('title')}" if row.get("number") else str(row.get("title"))


def _wake_manager(conn: Any, manager: str, at: str) -> bool:
    """One wake, "Stage rules: N tasks", naming every task waiting to be told to this manager, each once."""
    R, SR = _mod("rules"), _mod("stage_rules")
    pending: list[Any] = conn.execute(
        "SELECT k.rule_id, k.task_id, k.mark FROM stage_rule_marks k LEFT JOIN tasks t ON t.id = k.task_id "
        "WHERE k.outcome='pending' AND k.manager_id=? ORDER BY k.matched_at, t.number, k.task_id",
        (manager,),
    ).fetchall()
    listed: list[tuple[Row, Row]] = []
    for rule_id, task_id, _mark in pending:
        row: Row | None = H.task(conn, task_id)
        rule: Row | None = SR.get(conn, rule_id)
        if (
            not row
            or not rule
            or row.get("status") != "open"
            or task_id in {r["id"] for r, _ in listed}
            or not H.task_private_readable(conn, manager, row)
        ):
            continue
        listed.append((row, rule))
    if not listed:
        _settle(conn, pending, "dropped", None)  # every task ended or can no longer be read by this manager
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
        conv = H._pair_conversation(conn, H.KEEPER, manager, "notice", allow_held=True)
        ok = bool(H._write_message(conn, H.KEEPER, manager, text, conv, "notice", refs, None, None))
    if not ok:
        # The manager cannot be told (archived, gone): nothing says it was, and its next match tries again.
        _settle(conn, pending, "failed", None)
        return False
    _settle(conn, pending, "told", at)
    name = str(H.actor_name(conn, manager))
    for row, rule in listed:
        SR.record(conn, row["id"], rule, f"Rule {rule['name']}: {name} was told", H.KEEPER, "told", {"to": manager})
    return True


def _settle(conn: Any, pending: list[Any], outcome: str, told_at: str | None) -> None:
    conn.executemany(
        "UPDATE stage_rule_marks SET outcome=?, told_at=? WHERE rule_id=? AND task_id=? AND mark=?",
        [(outcome, told_at, rule_id, task_id, mark) for rule_id, task_id, mark in pending],
    )
