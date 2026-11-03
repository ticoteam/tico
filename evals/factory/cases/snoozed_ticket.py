"""A person snoozes a ticket mid-flight and unsnoozes a parked one early (docs/tasks.md, Snoozing a task).

At hour 1 ana snoozes, for three hours, the first ticket out for a check: its verdict lands during the snooze and
reaches the builder quietly, so the builder runs on it only once the "Snooze over" notice comes. She also snoozes a
ticket she had parked for its builder's next run (`next_run`), then unsnoozes it at hour 2: that one wake must start
the parked work (the actionable notice releases the next-run deferral, #1615), not leave it waiting for a run that
never carries it on."""
from evals.factory.scenarios import Scenario, at, standard, tickets
from evals.factory.world import START

SNOOZE_AT, SNOOZE_HOURS, UNSNOOZE_AT = at(1), 3, at(2)


def setup(run, w, team):
    tickets(run, w, 5)
    parked = w.post("tasks", {"owner": team.builders[-1].slug, "title": "Parked ticket", "body": "Build it.",
                              "type": w.dev["id"], "step": w.board.first().name, "next_run": True})
    run.parked = parked.get("task", parked)["id"]
    run.snoozed = {}      # ticket id -> [snoozed at, until, unsnoozed at or None]


def stamp(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def busy_on(team, task_id):
    return any(b.running and (b.attempt.get("task") or {}).get("id") == task_id for b in team.all)


def during(run, w, team, i):
    now = w.now()
    if now == START + SNOOZE_AT:
        w.post(f"tasks/{run.parked}/snooze", {"until": stamp(now + at(24))})
        run.snoozed[run.parked] = [now, now + at(24), None]
    if now == START + UNSNOOZE_AT:
        w.post(f"tasks/{run.parked}/unsnooze", {})
        run.snoozed[run.parked][2] = now
    if now < START + SNOOZE_AT or len(run.snoozed) > 1:
        return
    # The ticket mid-flight: out for a check, its check still open, no run of its own going.
    for t in sorted(w.tickets(), key=lambda t: t["title"]):
        step = w.board.step(t["step"])
        if t["id"] == run.parked or not step or not step.check or not str(t["owner_id"]).startswith("bot:") \
                or busy_on(team, t["id"]) or not any(c["status"] not in ("done", "closed", "declined")
                                                      for c in w.checks(t["id"])):
            continue
        until = now + at(SNOOZE_HOURS)
        w.post(f"tasks/{t['id']}/snooze", {"until": stamp(until)})
        run.snoozed[t["id"]] = [now, until, None]
        return


def mid_flight(run):
    return next((tid for tid in run.snoozed if tid != run.parked), None)


def owner_turns(run, tid, since, until=None):
    owner = run.w.sql("SELECT owner_id FROM tasks WHERE id=?", (tid,))[0]["owner_id"]
    return [r for r in run.turns(since=since, until=until, bots={owner[4:]}) if r["task"] == tid]


def notices(run, tid, prefix):
    """Tico's notices to the ticket's owner starting `prefix`, with whether each queued a job (woke it)."""
    return run.w.sql("SELECT m.created_at, m.recipient_id, EXISTS(SELECT 1 FROM jobs j WHERE j.message_id=m.id) woke "
                     "FROM messages m JOIN tasks t ON t.id=json_extract(m.refs,'$.task') "
                     "WHERE t.id=? AND m.recipient_id=t.owner_id AND m.sender_id='keeper' AND m.body LIKE ? "
                     "ORDER BY m.created_at", (tid, prefix + "%"))


def both_snoozed(run):
    return len(run.snoozed) == 2, f"{len(run.snoozed)} of 2 tickets snoozed"


def quiet_while_snoozed(run):
    """The owner starts no run on a ticket while it is snoozed: its notices, the verdict among them, wait."""
    found = []
    for tid, (since, until, early) in run.snoozed.items():
        for r in owner_turns(run, tid, since, early or until):
            found.append(f"{run.w.title(tid)} {r['at']:%H:%M} {r['news']}")
    return not found, ", ".join(found[:6])


def verdict_while_snoozed(run):
    """The mid-flight ticket's check gave its verdict during the snooze (what the snooze has to hold back)."""
    tid = mid_flight(run)
    if not tid:
        return False, "no ticket snoozed mid-flight"
    since, until, _ = run.snoozed[tid]
    got = [c for c in run.w.checks(tid) if c["verdict"] and any(
        since <= run.w.H.parse_ts(e["created_at"]) < until for e in run.w.sql(
            "SELECT created_at FROM task_events WHERE task_id=? AND field='status' AND new='done'", (c["id"],)))]
    return bool(got), f"{len(got)} verdicts in the snooze"


def snooze_over_once(run):
    """One "Snooze over" notice wakes the owner at the date (the scheduler pass after it), and the owner runs on it;
    the ticket unsnoozed early gets none."""
    tid = mid_flight(run)
    if not tid:
        return False, "no ticket snoozed mid-flight"
    _, until, _ = run.snoozed[tid]
    over = notices(run, tid, "Snooze over:")
    slack = at(0, 2 * run.scenario.tick_minutes)
    ok = len(over) == 1 and over[0]["woke"] and until <= run.w.H.parse_ts(over[0]["created_at"]) <= until + slack
    ran = owner_turns(run, tid, until, until + at(2))
    early = notices(run, run.parked, "Snooze over:")
    return ok and bool(ran) and not early, (f"{len(over)} at the date ({', '.join(o['created_at'][11:16] for o in over)}), "
                                            f"{len(ran)} owner runs after it, {len(early)} on the unsnoozed ticket")


def unsnooze_wakes_once(run):
    """Unsnoozing early wakes the owner once, and that run takes the parked ticket off its first step, long before
    the date it was snoozed to. The run may wait for the owner's turn in hand to end."""
    _, until, early = run.snoozed.get(run.parked, (None, None, None))
    if not early:
        return False, "the parked ticket was not unsnoozed"
    woke = notices(run, run.parked, "Unsnoozed:")
    ran = owner_turns(run, run.parked, early, until)
    moved = run.w.sql("SELECT 1 FROM task_events WHERE task_id=? AND field='step' AND created_at>=?",
                      (run.parked, early.strftime("%Y-%m-%dT%H:%M:%S.%fZ")))
    ok = len(woke) == 1 and woke[0]["woke"] and bool(ran) and bool(moved)
    return ok, f"{len(woke)} unsnooze notices, " + \
        (f"owner ran on it {(ran[0]['at'] - early).total_seconds() / 60:.0f} min after, " if ran else "no owner run, ") + \
        ("moved on" if moved else "still at its first step")


def snoozed_merge(run):
    left = [run.w.title(tid) for tid in run.snoozed if tid not in run.merged_at]
    return not left, ", ".join(left)


SCENARIOS = [
    Scenario("snoozed_ticket", "A person snoozes a ticket mid-review for 3h and unsnoozes a parked one early: "
                               "no run while snoozed, one wake each after, both still merge.",
             setup=setup, during=during,
             checks=standard(("a mid-flight and a parked ticket snoozed", None, both_snoozed),
                             ("a verdict arrives during the snooze", None, verdict_while_snoozed),
                             ("no owner run on a snoozed ticket", None, quiet_while_snoozed),
                             ("one Snooze over wake at the date", None, snooze_over_once),
                             ("the early unsnooze wakes the owner once", None, unsnooze_wakes_once),
                             ("both snoozed tickets merge", None, snoozed_merge))),
]
