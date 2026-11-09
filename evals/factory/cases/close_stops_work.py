"""A person closes a ticket while a builder is still running a turn on it (docs/tasks.md, Closing or declining a task stops
the bot work on it).

From hour 1, the first time a builder is running a turn on a ticket, ana closes that ticket. The turn is asked to stop, no
queued work on the ticket is left, the closure writes one history line counting what it stopped, and the turn's answer is
filed on its run rather than posted into the closed ticket's room. The other tickets run on and merge as usual; a closed
ticket counts as finished for the merge check."""

from evals.factory.scenarios import Scenario, at, standard, tickets
from evals.factory.world import START

CLOSE_AT = at(1)


def setup(run, w, team):
    tickets(run, w, 4)
    run.closed = None  # (ticket id, when it closed)
    run.closed_turns = []  # the attempts running on that ticket at the close
    run.closed_ids = set()
    shipped = run.shipped
    run.shipped = lambda t: t["id"] in run.closed_ids or shipped(t)


def running_on(team, task_id):
    return [b.attempt["id"] for b in team.all if b.running and (b.attempt.get("task") or {}).get("id") == task_id]


def during(run, w, team, i):
    now = w.now()
    if run.closed or now < START + CLOSE_AT:
        return
    for t in sorted(w.tickets(), key=lambda t: t["title"]):
        turns = running_on(team, t["id"])
        if not turns:
            continue
        with w.store.transaction() as c:
            w.H.task_close(c, "human:ana", t["id"], note="Done with this one.")
        run.closed = (t["id"], now)
        run.closed_turns = turns
        run.closed_ids.add(t["id"])
        return


def closed_with_turn(run):
    if not run.closed:
        return False, "no ticket closed while a turn ran on it"
    return bool(run.closed_turns), f"{len(run.closed_turns)} turns running on {run.w.title(run.closed[0])} at its close"


def asked_to_stop(run):
    if not run.closed_turns:
        return False, "nothing was running to stop"
    marks = ",".join("?" * len(run.closed_turns))
    rows = run.w.sql(f"SELECT id, stop_requested_at FROM attempts WHERE id IN ({marks})", tuple(run.closed_turns))
    flagged = [r for r in rows if r["stop_requested_at"]]
    return len(flagged) == len(run.closed_turns), f"{len(flagged)} of {len(run.closed_turns)} turns asked to stop"


def one_history_line(run):
    if not run.closed:
        return False, "no close"
    lines = run.w.sql(
        "SELECT new FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='work'", (run.closed[0],)
    )
    said = "; ".join(line["new"] for line in lines) or "no history line"
    return len(lines) == 1 and lines[0]["new"].startswith("Stopped"), said


def no_work_left_queued(run):
    if not run.closed:
        return False, "no close"
    left = run.w.sql(
        "SELECT j.id FROM jobs j JOIN messages m ON m.id=j.message_id JOIN conversations cv ON cv.id=m.conversation_id "
        f"WHERE j.state='queued' AND m.kind IN ('say','ask','steer') AND {run.w.H.MESSAGE_TASK_SQL}=?",
        (run.closed[0],),
    )
    return not left, f"{len(left)} queued runs still on the closed ticket"


def nothing_posted_after_close(run):
    if not run.closed:
        return False, "no close"
    tid, when = run.closed
    posts = run.w.sql(
        "SELECT m.created_at FROM messages m JOIN conversations cv ON cv.id=m.conversation_id "
        "WHERE cv.task_id=? AND m.sender_id LIKE 'bot:%'",
        (tid,),
    )
    late = [p for p in posts if run.w.H.parse_ts(p["created_at"]) > when]
    return not late, f"{len(late)} bot posts into the closed ticket after the close"


def others_merge(run):
    left = [
        run.w.title(t["id"]) for t in run.w.tickets() if t["id"] not in run.closed_ids and t["id"] not in run.merged_at
    ]
    return not left, ", ".join(left)


SCENARIOS = [
    Scenario(
        "close_stops_work",
        "A person closes a ticket while a builder is running a turn on it: the turn is asked to stop, no queued work "
        "is left, one history line counts it, its answer is filed rather than posted, and the other tickets still merge.",
        setup=setup,
        during=during,
        checks=standard(
            ("a ticket closed while a turn ran on it", None, closed_with_turn),
            ("that turn asked to stop by the close", None, asked_to_stop),
            ("one history line counts the stopped work", None, one_history_line),
            ("no queued work left on the closed ticket", None, no_work_left_queued),
            ("no bot posts into the closed room after the close", None, nothing_posted_after_close),
            ("the other tickets still merge", None, others_merge),
        ),
    ),
]
