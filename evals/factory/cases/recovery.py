"""Recovery from restarts and from a poisoned message: the server stops under running turns and starts again on its
database, a computer's runner restarts under its running turns, and one message's claim check raises every time a claim
reaches it. Each run claims the way a runner does (no bot named, its busy lists), so every claim the server answers is
one a computer sent. Times are fixed, as the freezes' are: each event falls on a tick, so each check reads the clock the
run did."""
from evals.factory.scenarios import BASE, Scenario, at, board_keeps_moving, no_uncertain_runs, tickets
from evals.factory.world import START

OUTAGE_AT, OUTAGE_FOR = at(2, 0), at(0, 15)        # the server stops at 2:00 and starts again at 2:15
BACK_AT = OUTAGE_AT + OUTAGE_FOR
SETTLE = at(0, 2)                                   # a computer's claims wait this long once its contact resumes
RESTART_AT = at(2, 0)                               # the runner restarts at 2:00, under its running turns
POISON_AT = at(0, 30)                               # the poisoned message is posted at 0:30
POISON = "Poisoned message: its claim check raises every time it is reached."


def stamp(when):
    """A clock time as the server writes it (the events and attempts columns sort on this text)."""
    return (START + when).strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------------------------- events
def outage(stop_at, back_at):
    """The server stops at `stop_at` and starts again at `back_at`, on the same database. Until then the runner's calls
    and its renewals wait (World.advance and Run.go skip the server while it is down); its turns keep running."""
    def during(run, w, team, i):
        if w.down:
            if w.now() >= START + back_at:
                w.restart()
                # The state each turn is in right after the restart's first pass, before any runner call reaches it.
                ids = list(run.stop_turns)
                marks = ",".join("?" * len(ids))
                run.restart_states = {r["id"]: r["state"] for r in w.sql(
                    f"SELECT id,state FROM attempts WHERE id IN ({marks})", tuple(ids))}
        elif not getattr(run, "stopped", False) and w.now() >= START + stop_at:
            run.stopped = True
            run.running_at_stop = [b.slug for b in team.all if b.running]
            run.stop_turns = {b.attempt["id"]: b.slug for b in team.all if b.running}
            w.go_down()
    return during


def runner_restarts(when):
    """The computer's runner restarts at `when` under its running turns. Each turn had edited a file before the restart,
    so it is not a read-only run: the runner files it as interrupted when it comes back up, as the runner does
    (runner-rs recover_output), and the server resumes it on its next sweep, because a restart cannot have repeated an
    outside action."""
    def during(run, w, team, i):
        if getattr(run, "restarted", False) or w.now() < START + when:
            return
        run.restarted = True
        run.running_at_restart = [b.slug for b in team.all if b.running]
        for bot in team.all:
            if bot.running:
                edit = {"tool": "Edit", "summary": "edited a file", "item_id": "restart-edit"}
                w.post(f"attempts/{bot.attempt['id']}/events",
                       {"events": [{"seq": 1, "kind": "tool", "payload": edit}]}, token=w.runner["token"])
                bot.attempt["last_seq"] = 1
                bot.lose()
                bot.recover()
    return during


def poisoned_message(when, bot):
    """A person's message to `bot` at `when`: the message whose claim check the setup patches to raise."""
    def during(run, w, team, i):
        if getattr(run, "poisoned", False) or w.now() < START + when:
            return
        run.poisoned = True
        w.post("messages", {"to": bot, "text": POISON, "refs": {}})
    return during


def poison_setup(run, w, team):
    """Eight tickets for the team, and the server's job check patched for this run: a claim that reaches the poisoned
    message raises, as a check that trips on one message would. World.patch puts the original back on exit."""
    from backend import execution
    from backend.store import H

    def wrap(original):
        def job_refusal(c, job, *args, **kwargs):
            message = H.message(c, job["message_id"])
            if message and message.get("body") == POISON:
                raise RuntimeError("the claim check raised on the poisoned message")
            return original(c, job, *args, **kwargs)
        return job_refusal
    w.patch(execution, "job_refusal", wrap)
    tickets(run, w, 8)


# ---------------------------------------------------------------------------------------------- checks
def lost_none(run):
    """The restart ended no turn that was running when the server stopped. Its state is read right after the restart's
    first pass: a turn whose lease was extended at the start (grace_leases) is still running then, and its runner's
    renewals keep it. A turn the pass expired is no longer running, and its job goes to review or back to the queue.
    Needs at least one turn running at the stop, else nothing was tested."""
    turns = getattr(run, "stop_turns", {})
    states = getattr(run, "restart_states", None)
    if not turns or states is None:
        return False, "no turn was running when the server stopped, so nothing was tested"
    if len(states) != len(turns):
        return False, f"could not read {len(turns) - len(states)} of the {len(turns)} turns after the restart"
    ended = [f"{turns[aid]} ({state})" for aid, state in states.items() if state not in ("leased", "running")]
    if ended:
        return False, f"the restart ended {len(ended)} of {len(turns)} running turns: " + ", ".join(ended[:6])
    return True, f"all {len(turns)} turns running at the stop were still running after the restart: " + \
        ", ".join(sorted(turns.values())[:6])


def leases_extended(run):
    """The outage was longer than a lease, so the restart found running leases lapsed and extended each one (the server's
    startup, grace_leases). An outage shorter than a lease would show nothing about recovery, so it must be longer."""
    lease = run.w.settings.lease_seconds
    extended = run.w.sql("SELECT count(*) n FROM events WHERE action='attempt.lease-grace' AND ts>=? AND ts<?",
                         (stamp(BACK_AT), stamp(BACK_AT + at(0, 1))))[0]["n"]
    longer = OUTAGE_FOR.total_seconds() > lease
    return longer and extended > 0, (f"{extended} lapsed leases extended at the restart; the outage was "
                                     f"{OUTAGE_FOR.total_seconds():.0f} s against a {lease} s lease")


def runner_rerun(run):
    """Each turn the runner's restart interrupted ran again to completion: a later attempt on its job completed."""
    rows = run.w.sql("SELECT a.bot, (SELECT count(*) FROM attempts b WHERE b.job_id=a.job_id AND b.state='completed' "
                     "AND b.started>=a.finished) AS reran FROM attempts a WHERE a.state='interrupted' AND a.finished>=?",
                     (stamp(RESTART_AT),))
    missing = [r["bot"] for r in rows if not r["reran"]]
    if not rows:
        return False, "the restart interrupted no turn, so nothing was tested"
    if missing:
        return False, f"{len(missing)} of {len(rows)} interrupted turns never ran again: " + ", ".join(missing[:6])
    return True, f"all {len(rows)} interrupted turns ran again to completion"


SCENARIOS = [
    Scenario("recover_server_restart",
             "The server stops at 2:00 for a quarter hour, longer than a lease, under the turns its computer is running, "
             "and starts again on the same database: the turns settle, and the board moves on.",
             setup=lambda run, w, team: tickets(run, w, 8), during=outage(OUTAGE_AT, BACK_AT), claim="runner", hours=6,
             checks=[*BASE,
                     ("the restart ended no turn running at the stop", None, lost_none),
                     ("the restart extended the lapsed leases", None, leases_extended),
                     ("the board keeps moving after the restart", None,
                      board_keeps_moving(BACK_AT, waits=((BACK_AT, BACK_AT + SETTLE),))),
                     ("no run is left uncertain", None, no_uncertain_runs)]),
    Scenario("recover_runner_restart",
             "The computer's runner restarts at 2:00 under its running turns, each of which had edited a file: it files each "
             "one interrupted, and the server resumes each one on its next sweep, so each runs again.",
             setup=lambda run, w, team: tickets(run, w, 8), during=runner_restarts(RESTART_AT), claim="runner", hours=6,
             checks=[*BASE,
                     ("each turn the restart interrupted ran again", None, runner_rerun),
                     ("the board keeps moving after the restart", None, board_keeps_moving(RESTART_AT)),
                     ("no run is left uncertain", None, no_uncertain_runs)]),
    Scenario("contain_poison_job",
             "A person's message to a builder at 0:30 makes its claim check raise every time a claim reaches it. The "
             "message is contained: the other work keeps being handed out.",
             setup=poison_setup, during=poisoned_message(POISON_AT, "builder-claude"), claim="runner", hours=6,
             checks=[*BASE,
                     ("the board keeps moving once the poisoned message is queued", None,
                      board_keeps_moving(POISON_AT))]),
]
