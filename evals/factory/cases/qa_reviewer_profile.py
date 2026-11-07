# pyright: strict
"""A QA bot's run is interrupted by a runner restart (runner-rs records the turn interrupted). QA bots run on the
reviewer profile (their name makes them reviewers, profiles.REVIEWER_SLUGS): such a bot takes review and QA requests
only, its roundup lists it its questions and its own follow-ups, not the sweep lines (profiles.SPEC: sweeps off). So an
interrupted check has no wake and no sweep line to come back by: the QA reviewer-profile stall. The check the restart
interrupted must be worked again within the late hours."""

from evals.factory.scenarios import STUCK_HOURS, Scenario, standard, tickets

GAP = "QA reviewer-profile stall"


def resumed_qa(run):
    """Every QA turn a restart interrupted is followed by a turn on the same task, or by the task's end, within the late
    hours."""
    interrupted, late = 0, []
    for bot in run.t.qa:
        for r in bot.turns:
            if r["kind"] != "restart" or not r.get("task"):
                continue
            interrupted += 1
            when = r["at"]
            later = [x["at"] for x in run.turns() if x.get("task") == r["task"] and x["at"] > when and x.get("action")]
            status = run.w.sql("SELECT status FROM tasks WHERE id=?", (r["task"],))
            done = bool(status) and status[0]["status"] in ("done", "closed", "declined")
            if not done and (not later or (min(later) - when).total_seconds() / 3600 > STUCK_HOURS):
                late.append(f"{run.w.title(r['task'])} ({bot.slug} at {when:%H:%M})")
    return (
        not late,
        f"{len(late)} of {interrupted} interrupted QA runs not worked again within {STUCK_HOURS} h: "
        + ", ".join(late[:4]),
    )


SCENARIOS = [
    Scenario(
        "qa_reviewer_profile",
        "Half of the QA runs are interrupted by a runner restart: the check must be worked again, on the reviewer profile.",
        setup=lambda run, w, team: tickets(run, w, 6),
        styles={"qa": {"restart_mid_turn": 0.5}},
        checks=standard(("every QA run a restart interrupted is worked again within the late hours", GAP, resumed_qa)),
    ),
]
