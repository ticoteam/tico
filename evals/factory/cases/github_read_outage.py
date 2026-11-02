"""GitHub reads fail for three hours (timeouts, then 5xx, then 429) while builders hand tickets to gated review steps.

The hub has the GitHub App connected, so every move onto a gated step reads the PR's head and checks afresh, and the
reads fail. Each refused move is held and remembered; Tico retries it every five minutes (backend/move_retry.py,
docs/tasks.md) and makes it once GitHub reads again. A temporary failure never files a task, and the owner hears of
each refusal and each real move once. One held ticket also gets a person's open child task while it waits: that other
wait keeps it Blocked after GitHub recovers, so the retry must stay quiet until the child closes."""
import json

from evals.factory.scenarios import Scenario, at, standard, tickets
from evals.factory.world import START

OUTAGE_START, OUTAGE_HOURS = at(0, 30), 3
FAULTS = ["timeout", 502, 429]       # one per hour of the outage: what GitHub does to the reads
CHILD_OPEN_AFTER_RECOVERY = at(1)    # how long the person's child task stays open once GitHub is back
OUTAGE_EVERY = at(0, 5)              # backend/move_retry.OUTAGE_EVERY: docs/tasks.md promises a 5-minute retry
RECOVERED = START + OUTAGE_START + at(OUTAGE_HOURS)


def setup(run, w, team):
    w.connect_github()
    run.child = None          # (ticket id, child id, when it closed)
    tickets(run, w, 8)


def during(run, w, team, i):
    since = w.now() - START - OUTAGE_START
    w.github_fault = FAULTS[int(since / at(1))] if at(0) <= since < at(OUTAGE_HOURS) else None
    if w.github_fault and run.child is None:
        # A person files a task under the first ticket the outage holds: a question for them to answer.
        held = w.sql("SELECT id FROM tasks WHERE type_id=? AND EXISTS (SELECT 1 FROM task_events e WHERE e.task_id=tasks.id AND e.field='move_retry') ORDER BY created_at, id LIMIT 1",
                     (w.dev["id"],))
        if held:
            child = w.post("tasks", {"owner_id": "ana", "title": "Confirm the wording with support",
                                     "body": "Check the new wording before this goes to review.",
                                     "relations": [{"task": held[0]["id"], "kind": "parent"}]})
            run.child = [held[0]["id"], child.get("task", child)["id"], None]
    if run.child and run.child[2] is None and w.now() >= RECOVERED + CHILD_OPEN_AFTER_RECOVERY:
        w.update(run.child[1], status="done")
        run.child[2] = w.now()


# ---------------------------------------------------------------------------------------------- checks
def _outage_refusals(run):
    """{ticket id: (first refusal ts, the step it was going to)} for review moves refused while GitHub was unreadable."""
    out = {}
    for e in run.w.sql("SELECT task_id, new, created_at FROM task_events WHERE field='move_retry' ORDER BY created_at, rowid"):
        detail = json.loads(e["new"] or "{}")
        if detail.get("outage") and e["task_id"] not in out:
            out[e["task_id"]] = (e["created_at"], detail.get("step"))
    return out


def outage_refused_moves(run):
    """The outage really refused review moves, the held ticket among them: else the other checks prove nothing."""
    refused = _outage_refusals(run)
    child = run.child and run.child[0] in refused
    return len(refused) >= 3 and bool(child), (f"{len(refused)} tickets refused, {len(run.w.github_failed)} failed reads"
                                               + ("" if child else "; no held ticket got a person's child task"))


def no_task_for_temporary_failure(run):
    """A timeout, 5xx or 429 never files a task (for DevOps, or the board's manager when there is none)."""
    rows = run.w.sql("SELECT e.target, t.owner_id FROM events e LEFT JOIN tasks t ON t.id=json_extract(e.detail,'$.task') "
                     "WHERE e.action='blocker.routed'")
    return not rows, ", ".join(f"{r['owner_id']}: {r['target']}" for r in rows[:4])


def moved_once_github_reads(run):
    """Each refused move is made within one retry interval (and a tick for the pass to run) of what held it clearing:
    GitHub reading again, and for the held ticket also the person's child task closing."""
    w, hold = run.w, run.w.board.hold_step
    slack = OUTAGE_EVERY + 2 * at(0, run.scenario.tick_minutes)
    late = []
    for task_id, (first, target) in _outage_refusals(run).items():
        clears = RECOVERED
        if run.child and run.child[0] == task_id:
            clears = max(clears, run.child[2] or run.end)
        moved = w.sql("SELECT e.created_at FROM task_events e JOIN task_steps s ON s.id=e.new WHERE e.task_id=? "
                      "AND e.field='step' AND e.created_at>? AND s.name<>? ORDER BY e.created_at, e.rowid LIMIT 1",
                      (task_id, first, hold))
        when = w.H.parse_ts(moved[0]["created_at"]) if moved else None
        if not when or when > clears + slack:
            late.append(f"{w.title(task_id)} " + (f"moved {(when - clears).total_seconds() / 60:.0f} min after"
                                                   if when else "never moved"))
    return not late, ", ".join(late[:6])


def told_once_per_move(run):
    """The owner hears of a refusal once per head and of a retried move only when the ticket moved: a retry kept on
    Blocked by another wait says nothing (it once said "Tico moved this ticket" every minute for three days)."""
    w, hold = run.w, run.w.board.hold_step
    bad = []
    notices = w.sql("SELECT json_extract(refs,'$.task') task, json_extract(refs,'$.review_gate') gate, "
                    "json_extract(refs,'$.move_retry') moved, created_at FROM messages WHERE sender_id='keeper' AND "
                    "(json_extract(refs,'$.review_gate') IS NOT NULL OR json_extract(refs,'$.move_retry'))")
    gates = [(n["task"], n["gate"]) for n in notices if n["gate"]]
    bad += [f"{w.title(t)} refusal told {gates.count((t, g))}x" for t, g in sorted(set(gates)) if gates.count((t, g)) > 1]
    for n in (n for n in notices if n["moved"]):
        real = w.sql("SELECT 1 FROM task_events e JOIN task_steps s ON s.id=e.new WHERE e.task_id=? AND e.field='step' "
                     "AND e.created_at=? AND s.name<>?", (n["task"], n["created_at"], hold))
        if not real:
            bad.append(f"{w.title(n['task'])} told of a move at {n['created_at'][11:16]} that was not made")
    return not bad, ", ".join(bad[:4]) + (f" (+{len(bad) - 4})" if len(bad) > 4 else "")


SCENARIOS = [
    Scenario("github_read_outage",
             "GitHub reads fail for 3h (timeouts, 5xx, 429) at the review gate; one held ticket also waits on a "
             "person's child task.",
             setup=setup, during=during,
             checks=standard(("the outage refused review moves", None, outage_refused_moves),
                             ("no task filed for a temporary GitHub failure", None, no_task_for_temporary_failure),
                             ("each refused move made within a retry of GitHub reading again", None,
                              moved_once_github_reads),
                             ("the owner told once per refusal and per real move", None, told_once_per_move))),
]
