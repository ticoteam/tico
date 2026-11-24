# pyright: strict
"""Stage rules whose When is a timer (backend/stage_rules.py, event `timer`), on the never-stuck pass.

A timer is one of:

- `in_stage`: N hours in the stage, not counting time snoozed (`_spent`), from when it entered or a person last answered
  on it, whichever is later: a person's turn is not the bot's time. Each stage's expected time is such a rule, "Late"
  (stage_rules.late_rule), told to the owner;
- `quiet`: N hours since the task's last activity: it entered the stage, the engine saw it change (`visits.last_change_at`:
  a move, an answer, a run's progress) or someone other than Tico wrote in its room;
- `waiting_person`: N hours that the first thing it waits on has been a person's (waiting_on.PERSON).

`sweep` reads tables only: no model, no GitHub. A snoozed task, a by-hand stage and an ended task have no timer. Each
rule judges a task once per mark (its visit and the moment its timer started from, `stage_rule_marks`), so a pass done
twice does nothing new, and a task that is active again starts its timer again. At release every timer starts from the
task's last activity, as it would have had the rule always been there.

A task blocked by one it depends on (task_depends) is judged too, as its owner's turn: it counts as stalled only once
every task it waits on has also had no change for the rule's hours (`_quiet_blockers`), and then once per visit (the
mark `<visit>@blocked`). A blocker that moved within the hours leaves it alone. The tell names each blocker and how long
it has been quiet, so the agent chases it, takes the link off, reassigns or re-plans.

Only agents hear: a Tell goes to the owner bot, or to the pipeline's manager bot. A person who owns the task, or a
pipeline with no manager bot, gets nothing: the task keeps its place in that person's own lists.

A task a rule caught on its visit, with no change since, shows Late (`caught`).

Then:

- `tell_manager`: the pipeline's manager is told. All of one manager's matches go in one wake, "Stage rules: N tasks",
  and a manager gets one such wake an hour at most: what matches meanwhile waits for the next. A match of Urgent
  support waits (backend/routing.py) wakes its manager in the same pass, in its one wake of that pass: with
  everything waiting when its hour is open, else only the urgent tasks, which do not use up its hour. A rule with a tag
  condition marks whether the task had the tag, so adding it is noticed.
- `tell_owner`: the task's owner bot is woken, on the task.
- `move_back`: the task goes back to `then_params.stage` with no owner, through the wake rules' task move
  (rules.TASK_RUNNERS), and the manager is told as above. It runs as a rule's Action (task_trigger_rules.from_action),
  so the move sets off no other rule.

Each Tell and Move back is written on the task's history (kind `stage_rule`, "Rule <name>: <reason>") and as the event
`stage_rule.told` or `stage_rule.moved_back`, and each wake carries the rule's id and name (`refs`), so wakes are counted
per rule.
"""

import json
import logging
from collections.abc import Iterable, Sequence
from typing import Any

from . import hubdb as _hubdb
from . import task_calls, task_depends

log = logging.getLogger(__name__)
Row = dict[str, Any]
# hubdb and the modules read lazily below are untyped: they get `Any` here, as never_stuck does.
H: Any = _hubdb


def _mod(name: str) -> Any:
    """A module read lazily (each imports this one, or is a hub), named literally so the PR test map can follow it."""
    # Every name is a literal import: a computed import would make this module, and the never-stuck pass that imports
    # it, depend on every file in the PR test selection (scripts/pr_checks.py DependencyMap).
    if name == "rules":
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

# A task's last activity: it entered its stage, the engine saw it change (a move, an answer, a run's progress), someone
# other than Tico wrote in its room, or a snooze on this visit ended (a task set aside is not quiet the moment its snooze
# ends). `t` and `v` are the task and its open visit.
_ACTIVE = (
    "MAX(v.entered_at, COALESCE(v.last_change_at, ''), COALESCE((SELECT MAX(m.created_at) FROM messages m "
    "WHERE m.conversation_id = t.conversation_id AND m.sender_id <> 'keeper'), ''), "
    # task_id first: snoozes is indexed on it (snoozes_task), not on visit_id.
    "COALESCE((SELECT MAX(z.ended_at) FROM snoozes z WHERE z.task_id = t.id AND z.visit_id = v.id), ''))"
)
# Open work tasks the pass works on: not snoozed, not in a by-hand stage. A blocked one is read too (`blocked`).
_TASKS = (
    "SELECT t.id, t.title, t.number, t.owner_id, t.pipeline_id, t.stage_id, t.conversation_id, "
    f"v.id AS visit_id, v.entered_at, s.name AS stage_name, {_ACTIVE} AS active_at, "
    f"{task_depends.BLOCKED % 't'} AS blocked, "
    # An auto dream (backend/dream_auto.py), read with the rest: Quiet bot tasks passes it over (`_quiet_rule`).
    "EXISTS(SELECT 1 FROM events e WHERE e.action = 'dream.auto' AND e.target = t.id) AS auto_dream "
    "FROM tasks t JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL JOIN stages s ON s.id = t.stage_id "
    "WHERE t.pipeline_id IS NOT NULL AND t.status = 'open' AND s.kind = 'work' AND s.by_hand = 0 "
    "AND NOT EXISTS (SELECT 1 FROM snoozes z WHERE z.task_id = t.id AND z.ended_at IS NULL)"
)
# Each blocker's last activity, as `_ACTIVE` reads it: on its open visit, else (a Done task held until its release) when
# it last changed.
_BLOCKER_ACTIVE = (
    "SELECT t.id, t.number, COALESCE(t.prefix, p.prefix), "
    f"COALESCE((SELECT {_ACTIVE} FROM visits v WHERE v.task_id = t.id AND v.left_at IS NULL), t.updated_at) "
    "FROM tasks t LEFT JOIN pipelines p ON p.id = t.pipeline_id WHERE t.id IN (SELECT value FROM json_each(?))"
)
BLOCKED_ASK = "Chase it, take the link off, reassign or re-plan."
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
        if any(rule["timer_kind"] == "in_stage" for rule in found):
            # In-stage clocks start at a person's last answer: one read for every task, not one per task.
            answered = last_answers(conn, [(str(t["visit_id"]), str(t["id"]), str(t["entered_at"])) for t in tasks])
            for task in tasks:
                task["answered_at"] = answered.get(str(task["visit_id"]))
        moved: set[str] = set()  # moved by a rule this pass: its new visit's clock starts now, not on this pass's read
        for rule in found:
            for task in tasks:
                if task["id"] not in moved and _in_scope(rule, task):
                    before = done["moved"]
                    manager = _judge(conn, rule, task, at, done)
                    if done["moved"] != before:
                        moved.add(task["id"])
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
        # When the task entered the stage, or a person's last answer on it since: after the answer the bot has the
        # rule's whole time again.
        entered, answered = str(task["entered_at"]), task.get("answered_at")
        return str(answered) if answered and str(answered) > entered else entered
    E, W = _mod("task_engine"), _mod("waiting_on")
    row = E.task(conn, task["id"])
    waits: list[Any] = W.of(conn, row) if row else []
    if not waits or waits[0].state != W.PERSON:
        return None
    return max(str(waits[0].since or ""), task["entered_at"])


def _due(conn: Any, rule: Row, task: Row, started: str, at: str) -> bool:
    seconds = rule["hours"] * 3600
    start = H.parse_ts(started)
    now = H.parse_ts(at)
    if not start or not now or float((now - start).total_seconds()) < seconds:
        return False
    # In the stage: less the time it was snoozed (read only for a task the plain clock already finds due).
    return rule["timer_kind"] != "in_stage" or _spent(conn, task, started, at) >= seconds


def _judge(conn: Any, rule: Row, task: Row, at: str, done: dict[str, int]) -> str | None:
    """Judge one task under one rule; the manager a tell now waits for, if any."""
    if task.get("auto_dream") and _quiet_rule(rule):
        return None
    started = _started(conn, rule, task)
    if not started or not _due(conn, rule, task, started, at):
        return None
    if _quiet_rule(rule) and _mod("task_wakes").live_run(conn, task, task["owner_id"], at):
        return None  # Quiet bot tasks only: its owner is mid-run (a run may last a day), and working is not quiet
    mark = f"{task['visit_id']}@{started}"
    if task.get("blocked"):
        quiet = _quiet_blockers(conn, task, rule["hours"], at)
        if not quiet:
            return None  # a task it waits on moved within the rule's hours: it is not stalled
        task = {**task, "quiet_blockers": quiet}
        mark = f"{task['visit_id']}@blocked"  # once per visit, however often its blockers go quiet again
    if rule["timer_kind"] == "in_stage":
        # Once per visit, blocked or not: a person's answer restarts its clock (`_started`), not its one catch.
        mark = f"{task['visit_id']}@visit"
    label = str(rule["cond"].get("label") or "")
    if label and _mod("stage_rules")._tagged(conn, {"id": task["id"]}, label):
        mark += f"#{label.lower()}"  # a tag added to a task judged without it is judged again
    # A rule that did not match is judged again once the task has another owner or anything happens on it (a person's
    # answer ends their turn, a reassign, a message): a person's task handed to a bot in the same visit is still caught.
    # A match, told or not, stays once per visit (or quiet period) whoever owns it next.
    # An edit of the rule (its revision) judges it again too.
    unmatched = f"{mark}~{task['owner_id'] or ''}~{task['active_at']}~r{rule.get('revision') or 1}"
    if conn.execute(
        "SELECT 1 FROM stage_rule_marks WHERE rule_id=? AND task_id=? AND mark IN (?,?)",
        (rule["id"], task["id"], mark, unmatched),
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
        (rule["id"], task["id"], unmatched if outcome == "no" else mark, outcome, manager, at),
    )
    return manager


# ----------------------------------------------------------------------------- If
def _matches(conn: Any, rule: Row, task: Row) -> bool:
    """The rule's If: `turn` is read here (whose turn the task is, as the never-stuck ladder reads it: a bot's, also
    once the ladder has rested on it and wakes nobody, or a person's when the first thing it waits on is a person; a
    blocked task's is its owner's, who chases what it waits on); every other key by stage_rules.matches."""
    SR, E, W = _mod("stage_rules"), _mod("task_engine"), _mod("waiting_on")
    row = E.task(conn, task["id"])
    if not row or not _in_scope(rule, row):
        return False  # gone, or moved out of the rule's stages earlier in this pass
    turn = rule["cond"].get("turn")
    if turn and task.get("blocked"):
        # Blocked: its owner's turn, who chases what it waits on, unless a person's OK or answer is still asked.
        asked = [w.who for w in W.of(conn, row) if w.state == W.PERSON]
        if _kind(asked[0] if asked else row.get("owner_id")) != turn:
            return False
    elif turn:
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
    """What the history says: "Rule <name>: <its timer>", and on a blocked task what it waits on, and for how long that
    has not changed (`_quiet_blockers`)."""
    hours = float(rule["hours"])
    span = f"{hours:g} h"
    what = {"quiet": "quiet", "in_stage": f"in {task['stage_name']}", "waiting_person": "waiting on a person"}
    said = f"Rule {rule['name']}: {span} {what[rule['timer_kind']]}."
    quiet: list[tuple[str, float]] = task.get("quiet_blockers") or []
    if quiet:
        named = ", ".join(f"{ref}, no change on it for {_hours(h)}" for ref, h in quiet)
        said += f" Blocked by {named}. {BLOCKED_ASK}"
    return said


def _hours(hours: float) -> str:
    return f"{hours / 24:.0f} days" if hours >= 72 else f"{hours:.0f} h"


def _quiet_blockers(conn: Any, task: Row, hours: float, at: str) -> list[tuple[str, float]]:
    """(name, hours quiet) of each task this blocked one waits on, when every one of them has had no change for
    `hours` (since the dependency was written, at the earliest); [] when one has, or it waits on none."""
    blockers = task_depends.blockers(conn, str(task["id"]))
    if not blockers:
        return []
    since = dict(blockers)
    now = H.parse_ts(at)
    out: list[tuple[str, float]] = []
    for blocker_id, number, prefix, active in conn.execute(_BLOCKER_ACTIVE, (json.dumps(list(since)),)).fetchall():
        last = H.parse_ts(max(str(active or ""), str(since.get(blocker_id) or "")))
        if not now or not last:
            return []
        quiet = float((now - last).total_seconds()) / 3600
        if quiet < hours:
            return []
        # Kept in the history and the wake: named only if it is not private, as the dependency's own lines are.
        ref = task_depends.ref({"id": blocker_id, "number": number, "prefix": prefix})
        out.append((task_calls.named(conn, blocker_id, ref), quiet))
    return sorted(out, key=lambda one: -one[1])


def _then(conn: Any, rule: Row, task: Row, done: dict[str, int]) -> str:
    then = str(rule["then"])
    said = _said(rule, task)
    if then == "move_back":
        _move_back(conn, rule, task, said)
        done["moved"] += 1
        then = "tell_manager"
    if then == "tell_owner":
        return "told" if _tell_owner(conn, rule, task, said, done) else "no_owner"
    # Only a manager bot hears: with none, nobody is told (a timer never messages a person).
    return "pending" if _manager(conn, task, own=_support_rule(rule)) else "no_manager"


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
    task.update(stage_id=moved.get("stage_id"))
    SR.record(
        conn,
        task["id"],
        rule,
        said,
        H.TICO_ACTOR,
        "moved_back",
        {"from": task["stage_name"], "to": moved.get("stage_id")},
    )


def _tell_owner(conn: Any, rule: Row, task: Row, said: str, done: dict[str, int]) -> bool:
    """Wake the owner bot on the task, the wake naming the rule (`refs`). A person who owns it is not told: whether it
    was."""
    SR, E, task_wakes = _mod("stage_rules"), _mod("task_engine"), _mod("task_wakes")
    owner = task["owner_id"]
    row = E.task(conn, task["id"])
    if not H.is_bot(owner) or not row or (row["visit"].get("level") or "owner") == "person":
        return False  # at level person no run starts (task_wakes.may_start): telling it would say what did not happen
    key = f"{task['id']}:{task['visit_id']}:{task_wakes.STAGE_RULE}:{rule['id']}:{task['active_at']}"
    body = task_wakes.body(conn, row, said, to=owner)
    if not task_wakes.add(conn, row, owner, row["visit"], body, wake_key=key, refs=_rule_refs([rule])):
        return False
    SR.record(conn, task["id"], rule, said, H.TICO_ACTOR, "told", {"to": owner})
    done["told"] += 1
    return True


def _rule_refs(rules: Iterable[Row]) -> Row:
    """What a wake carries of the stage rules that sent it, so wakes are counted per rule: `stage_rule` and
    `stage_rule_name` for one, and every one's id in `stage_rules`. (A wake's `rule` is a wake rule's id.)"""
    found = list({str(r["id"]): r for r in rules}.values())
    one: Row = {"stage_rule": found[0]["id"], "stage_rule_name": found[0]["name"]} if len(found) == 1 else {}
    return {**one, "stage_rules": [r["id"] for r in found]}


def _manager(conn: Any, task: Row, own: bool = False) -> str | None:
    """The pipeline's manager bot, when it may read the task and is not its owner: a manager is not told about its own
    task, as the engine's own tells to the manager skip it (task_engine._tell_manager). With `own` (the Support timers,
    routed work's one overdue tell), a manager who owns the task is told too. A person who manages is never told."""
    found = conn.execute("SELECT manager_id FROM pipelines WHERE id=?", (task["pipeline_id"],)).fetchone()
    manager: str | None = found[0] if found else None
    if not H.is_bot(manager) or (manager == task.get("owner_id") and not own):
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
    when given: an urgent wake leaves routine tells for the hour), each once. A task that ended, or that the manager may
    no longer read, is not named. The wake carries the rules it names (`_rule_refs`)."""
    R, SR = _mod("rules"), _mod("stage_rules")
    pending: list[Any] = conn.execute(
        "SELECT k.rule_id, k.task_id, k.mark FROM stage_rule_marks k LEFT JOIN tasks t ON t.id = k.task_id "
        "WHERE k.outcome='pending' AND k.manager_id=? AND (? = '' OR k.rule_id = ?) "
        "ORDER BY k.matched_at, t.number, k.task_id",
        (manager, only_rule, only_rule),
    ).fetchall()
    listed: list[tuple[Row, Row]] = []
    told: list[Row] = []  # every rule a named mark settles, though a task is listed once
    outcome: dict[tuple[str, str, str], str] = {}  # each pending row's settling: named or dropped
    for rule_id, task_id, mark in pending:
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
        else:
            outcome[key] = "named"
            told.append(rule)
            if task_id not in {r["id"] for r, _ in listed}:
                listed.append((row, rule))
    _settle(conn, [k for k, o in outcome.items() if o == "dropped"], "dropped", None)
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
    refs: Row = {"wake": "stage_rules", "tasks": [row["id"] for row, _ in listed], **_rule_refs(told)}
    # Only a bot is told (`_manager`); a person who became the manager since is not, and nothing says they were.
    ok = H.is_bot(manager) and R._tell_bot(conn, "stage-rules", H.actor_id(manager), text, refs, wake=True)[0]
    if not ok:
        # The manager cannot be told (archived, gone, a person): nothing says it was, and its next match tries again.
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


# ----------------------------------------------------------------------------- the in-stage clock
def last_answers(conn: Any, visits: Sequence[tuple[str, str, str]]) -> dict[str, str]:
    """{visit id: when a person last answered on it} for (visit id, task id, entered at) rows, the visits with an answer
    only: a person's answer to an OK or a question (answers; a bot's own "done" is not one), or the end of a ❓
    (question_marks: the reply, or its person can no longer answer it). Two reads for any number of visits."""
    if not visits:
        return {}
    out: dict[str, str] = {}
    rows = json.dumps([list(v) for v in visits])
    for visit_id, at in conn.execute(
        "SELECT a.visit_id, MAX(a.created_at) FROM answers a WHERE a.by_id LIKE 'human:%' AND a.visit_id IN "
        "(SELECT json_extract(value, '$[0]') FROM json_each(?)) GROUP BY a.visit_id",
        (rows,),
    ).fetchall():
        out[str(visit_id)] = str(at)
    for visit_id, at in conn.execute(
        "SELECT json_extract(j.value, '$[0]'), MAX(q.cleared_at) FROM json_each(?) j JOIN question_marks q "
        "ON q.task_id = json_extract(j.value, '$[1]') AND q.cleared_at IS NOT NULL "
        "AND q.cleared_at >= json_extract(j.value, '$[2]') GROUP BY json_extract(j.value, '$[0]')",
        (rows,),
    ).fetchall():
        if at and str(at) > out.get(str(visit_id), ""):
            out[str(visit_id)] = str(at)
    return out


def _spent(conn: Any, task: Row, start: str, at: str) -> float:
    """Seconds from `start` to `at` on the task's open visit, not counting the time it was snoozed, each moment once."""
    now, begun = H.parse_ts(at), H.parse_ts(start)
    if not now or not begun:
        return 0.0
    aside: list[tuple[str, str | None]] = [
        (str(created), ended)
        for created, ended in conn.execute(
            "SELECT created_at, ended_at FROM snoozes WHERE task_id=? AND visit_id=?", (task["id"], task["visit_id"])
        ).fetchall()
    ]
    return float((now - begun).total_seconds()) - _covered(aside, start, at)


def _covered(spans: list[tuple[str, str | None]], start: str, end: str) -> float:
    """Seconds of [start, end] inside at least one of `spans` ((from, to); a None `to` runs to `end`)."""
    lo, hi = H.parse_ts(start), H.parse_ts(end)
    if not lo or not hi:
        return 0.0
    cut: list[tuple[Any, Any]] = []
    for begun, ended in spans:
        a, b = H.parse_ts(begun), H.parse_ts(ended or end)
        if a and b:
            a, b = max(a, lo), min(b, hi)
            if b > a:
                cut.append((a, b))
    total, reach = 0.0, None
    for a, b in sorted(cut):
        if reach is not None and a < reach:
            a = reach
        if b > a:
            total += float((b - a).total_seconds())
            reach = b if reach is None else max(reach, b)
    return total


# ----------------------------------------------------------------------------- Late, as a list reads it
# A task a stall rule caught on its open visit, and that has not changed since: an agent was told (or waits to be) on a
# mark of this visit after its last change. `%s` is the task's alias; `CAUGHT_SQL` is the condition the Late filter
# reads, `caught` the rows a list or a task's own read shows. A rule timing a person's wait is not Late: the person
# still owes the answer, though its manager bot is told.
_NOT_PERSON_WAIT = "COALESCE(json_extract(r.timer, '$.kind'), '') <> 'waiting_person'"
CAUGHT_SQL = (
    "EXISTS (SELECT 1 FROM visits cv JOIN stage_rule_marks ck ON ck.task_id = cv.task_id "
    "AND ck.mark >= cv.id || '@' AND ck.mark < cv.id || 'A' LEFT JOIN stage_rules r ON r.id = ck.rule_id "
    "WHERE cv.task_id = %s.id AND cv.left_at IS NULL AND ck.outcome IN ('told', 'pending') "
    f"AND ck.matched_at >= COALESCE(cv.last_change_at, cv.entered_at) AND {_NOT_PERSON_WAIT})"
)


def caught(conn: Any, task_ids: Iterable[str]) -> dict[str, Row]:
    """{task id: {since, rule}}: the Late a task shows, when a stall rule caught it on its open visit and nothing
    changed since (`CAUGHT_SQL`); `since` is the first such catch and `rule` its rule's name."""
    ids = sorted({str(t) for t in task_ids if t})
    if not ids:
        return {}
    out: dict[str, Row] = {}
    for task_id, at, name in conn.execute(
        "SELECT cv.task_id, ck.matched_at, COALESCE(r.name, '') FROM visits cv JOIN stage_rule_marks ck "
        "ON ck.task_id = cv.task_id AND ck.mark >= cv.id || '@' AND ck.mark < cv.id || 'A' "
        "LEFT JOIN stage_rules r ON r.id = ck.rule_id "
        "WHERE cv.task_id IN (SELECT value FROM json_each(?)) AND cv.left_at IS NULL "
        "AND ck.outcome IN ('told', 'pending') AND ck.matched_at >= COALESCE(cv.last_change_at, cv.entered_at) "
        f"AND {_NOT_PERSON_WAIT} ORDER BY ck.matched_at",
        (json.dumps(ids),),
    ).fetchall():
        out.setdefault(str(task_id), {"since": at, "rule": name})
    return out
