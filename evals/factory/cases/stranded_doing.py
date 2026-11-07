# pyright: strict
"""A builder's turn ends with its ticket still In Progress: nothing moved, nothing said, and no hourly sweep to notice
(the Dev board's own wakes are status wakes at 2 h and the task roundup at 24 h quiet, and the roundup runs three times
a day). This is the stranded work of #2875: on main nothing resumes the ticket, so the stuck-work invariant fails. With
the restart fix (#2875: a sweep every ten minutes that resumes a quiet owned task with no job) every stranded ticket is
picked up again within the late hours.

The scenario runs without the hourly sweep of Head of Engineering: a team's board has the roundup, not an hourly
sweep, so the sweep must not be what rescues the work here."""

from dataclasses import replace
from datetime import timedelta

from evals.factory.board import dev_board
from evals.factory.scenarios import STUCK_HOURS, Scenario, standard, tickets

GAP = "#2875"


def board():
    return replace(dev_board(), lead_sweep=False)


def later_event(run, task_id, when):
    """The first event on the task after `when`: a history event or a turn that did something on it."""
    H = run.w.H
    times = [
        H.parse_ts(r["created_at"])
        for r in run.w.sql(
            "SELECT created_at FROM history_events WHERE subject_type='task' AND subject_id=?", (task_id,)
        )
    ]
    times += [r["at"] for bot in run.t.all for r in bot.turns if r.get("task") == task_id and r.get("action")]
    after = [t for t in times if t > when]
    return min(after) if after else None


def trace_of(run, task_id, start, end):
    """The resume's answers on a task from `start` to `end` (sim.trace_resume): the one in force at `start`, then each
    change before `end`, as clock time."""
    held, changes = None, []
    for when, why in run.resume_trace.get(task_id, []):
        if when <= start:
            held = why
        elif when <= end:
            changes.append(f"{when:%H:%M} {why}")
    head = [f"{start:%H:%M} {held}"] if held else []
    return " | ".join(line[:150] for line in (head + changes)[:6])


def stranded_resumed(run):
    """Every ticket a builder's turn left in progress gets an event within the late hours of that turn. A turn that ended
    less than the late hours before the run ended is not judged: the run ended before they came."""
    stranded, late = 0, []
    for bot in run.t.builders:
        for when, task_id in bot.left:
            first = later_event(run, task_id, when)
            if first is None and run.w.now() - when < timedelta(hours=STUCK_HOURS):
                continue
            stranded += 1
            if first is None or (first - when).total_seconds() / 3600 > STUCK_HOURS:
                end = first or run.w.now()
                late.append(
                    f"{run.w.title(task_id)} ({bot.slug}, {(first - when).total_seconds() / 3600 if first else 'never'} h)"
                    f" [trace: {trace_of(run, task_id, when, end)}]"
                )
    return (
        not late,
        f"{len(late)} of {stranded} left in progress were not picked up within {STUCK_HOURS} h: " + ", ".join(late[:4]),
    )


SCENARIOS = [
    Scenario(
        "stranded_doing",
        "A builder's turn often ends with its ticket still In Progress, and nothing wakes it (no hourly sweep here).",
        setup=lambda run, w, team: tickets(run, w, 6),
        board=board,
        styles={"builder": {"leave_doing": 0.5}},
        checks=standard(
            ("every ticket a turn left in progress is picked up within the late hours", GAP, stranded_resumed)
        ),
    ),
]
