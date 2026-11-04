"""The enabled schedules' runs over the last seven days, as the scheduler and the bots' computers ran them.

Each schedule's occurrences in the window are played at their own times. At each time the scheduler's pass runs for
the schedules due then: a due schedule fires through `automations.fire` unless it is passed over (its bot is not
active, the company's routines are paused, or the run has nothing to work on), and its next time is worked out from
that time. The bot's computer then claims the job the run queued, starts it, and completes it with its answer and
what it spent: `attempts/{aid}/complete` prices the turn and charges the run.

`automations.tick` is not called: it also creates Tico's built-in automations, which the demo has never shown. The
pass above is its schedule loop with the same checks. The one write outside it is the first next-due time of each
schedule, set before the window starts (the bookkeeping the scheduler keeps, as the nightly run does).
"""

import json
from datetime import timedelta, timezone

from .. import automations, cloud_access, demo_content as D, judge, routines, strategy_bot
from .. import runner_states
from ..scheduler import next_due
from ..store import H
from .automations_learnings import _keep_focus

WINDOW = timedelta(days=7)
START_LAG = timedelta(seconds=12)  # a run's start after its time: the pass fires it and the run's step starts its turn
CLAIM_POLL = timedelta(seconds=5)   # the gap between two claim polls on the demo clock (a computer takes work after 120 s)
CLAIM_TRIES = 60                    # polls a computer makes for a bot's job before it gives up
# Occurrences this close to the build's now are not played: their runs (a few minutes each, one after another) would
# finish after now (the three at 9:00 Los Angeles on a weekday take up to 25 minutes). Their schedule's next time is
# still its first one after now.
SETTLE = timedelta(hours=1)

# Routines whose summaries are not in D.ROUTINES: the Strategy, Librarian and BotOps bots.
EXTRA_SUMMARIES = {
    "goal-manager:kpi-pass": ("Checked forty goals; the Q4 bookings goal moved to amber.",
                              "All forty goal colours unchanged.",
                              "Checked forty goals; two need an update from their owner."),
    "librarian:curate-the-market": ("Curated four market items; one went to the weekly delta.",
                                    "No new market items; yesterday's list stands.",
                                    "Curated six items for the weekly delta; two were duplicates."),
    "librarian:refresh-the-map": ("Refreshed the docs map: 214 pages, three moved.",
                                  "Docs map unchanged; no pages moved.",
                                  "Refreshed the docs map; two broken links fixed."),
    "botops:task-sweep": ("Swept every bot: two stuck tasks handed to their owners.",
                          "No stuck tasks on either computer.",
                          "One task is waiting on a login; flagged to Ben."),
}
SUMMARIES = {f"{bot}:{key}": quiet for bot, key, _title, _cron, _text, quiet in D.ROUTINES} | EXTRA_SUMMARIES


def seed(b):
    """Sweep stuck tasks is set up as BotOps' fleet maintainer sets it up at boot (routines.ensure_task_sweep). Then
    each enabled schedule with no run in the window plays its occurrences in order."""
    start = b.now - WINDOW
    b.clock.at = start
    b.write(lambda c: routines.ensure_task_sweep(c, at=start))
    b.write(_keep_monday_note_off)
    contact = {}
    b.write(lambda c: contact.update(saved=_contact_before(c, start)))
    box = {}
    b.write(lambda c: box.update(rows=_pending_schedules(c, start)))
    plan = {}
    for row in box["rows"]:
        for index, due in enumerate(_occurrences(row["trigger"], start, b.now - SETTLE)):
            plan.setdefault(due, []).append((row, index))
    b.write(lambda c: _set_next_due(c, box["rows"], start))
    # The jobs the bots' queues already hold (the history's assignments, never claimed) are held while the window's
    # runs are answered, so each computer claim is handed one of the runs' jobs; they are queued again after it.
    queued = b.write(_hold_queued)
    # A reply's rule check (org_rule_check) would ask the model; the build asks none. With the bots' daily cap at 0 the
    # check is "unchecked" before any call, as it is when the cap is spent, so the build makes no provider call.
    cap = judge.daily_cap(b.store.settings, "bot")
    b.store.settings.decisions_daily_calls_bot = 0
    try:
        for due in sorted(plan):
            b.clock.at = due
            b.write(lambda c, due=due: _pass(c, b.store, due, [row["id"] for row, _ in plan[due]]))
            b.clock.at = due + START_LAG
            b.store.automations.drain()
            due_rows = {row["id"]: (row["flow"], index) for row, index in plan[due]}
            _work(b, _runs_at(b, due, list(due_rows)), due_rows)
            b.store.automations.drain()
    finally:
        b.store.settings.decisions_daily_calls_bot = cap
    b.write(lambda c: _release(c, queued))
    b.write(lambda c: _contact_after(c, contact["saved"]))
    # The occurrences before now are played; each schedule's next time is its first one after now.
    b.write(lambda c: _set_next_due(c, box["rows"], b.now))
    if b.clock.at > b.now:
        raise RuntimeError("a schedule run finished after the build's now")


def _keep_monday_note_off(c):
    """The Weekly goals review stays off, as the demo has always shown it. The server starts it at startup unless its one
    go has been taken (strategy_bot.reconcile), so that go is marked taken here; nothing else about the note changes."""
    record = strategy_bot._record(c)
    if not record.get("review_settled"):
        strategy_bot._save(c, {**record, "review_settled": True})


def _stamp(at):
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _pending_schedules(c, start):
    """The enabled schedules that have no run since the window started. The nightly and the weekly numbers keep
    their own runs, so they are left alone."""
    rows = []
    for row in c.execute("SELECT id, flow FROM automations WHERE trigger_type='schedule' AND enabled=1 "
                         "AND deleted_at IS NULL ORDER BY id").fetchall():
        if c.execute("SELECT 1 FROM automation_runs WHERE automation_id=? AND started_at>=? LIMIT 1",
                     (row["id"], _stamp(start))).fetchone():
            continue
        flow = json.loads(row["flow"])
        rows.append({"id": row["id"], "flow": flow, "trigger": flow["trigger"]})
    return rows


def _occurrences(trigger, start, end):
    """The schedule's occurrences after `start` and before `end`, in UTC (the demo clock reads UTC)."""
    zone = trigger.get("tz") or "UTC"
    due = next_due(trigger["cron"], start, zone)
    while due < end:
        yield due.astimezone(timezone.utc)
        due = next_due(trigger["cron"], due, zone)


def _hold_queued(c):
    """Holds the jobs queued now (raw SQL: `held` is the flag the claim's candidates skip). Returns their ids."""
    ids = [row[0] for row in c.execute("SELECT id FROM jobs WHERE state='queued' AND held=0").fetchall()]
    c.executemany("UPDATE jobs SET held=1 WHERE id=?", [(job,) for job in ids])
    return ids


def _release(c, ids):
    c.executemany("UPDATE jobs SET held=0 WHERE id=?", [(job,) for job in ids])


def _contact_before(c, start):
    """Each computer's contact stamps as the build left them, and each stamp later than the window's start is set to a
    day before it (raw SQL: the runner's own contact columns). The build's history stamps the computers at its end,
    after the window, and a claim takes work only from a computer whose stretch of contact began before its clock, so
    a claim in the window would never be handed a job; the first claim then starts the stretch, as after a silence.
    Returns the saved stamps for _contact_after."""
    saved, before = {}, _stamp(start - timedelta(days=1))
    for row in c.execute("SELECT id, last_seen_at, awake_since FROM runners_with_state").fetchall():
        saved[row["id"]] = (row["last_seen_at"], row["awake_since"])
        if any(stamp and stamp > _stamp(start) for stamp in saved[row["id"]]):
            runner_states.save(c, row["id"], last_seen_at=before, awake_since=before)
    return saved


def _contact_after(c, saved):
    """Each computer's stamps after the window: the window's last claim, unless the build's own stamp is later."""
    for rid, (seen, awake) in saved.items():
        now = c.execute("SELECT last_seen_at FROM runners_with_state WHERE id=?", (rid,)).fetchone()["last_seen_at"]
        if seen and (now is None or now < seen):
            runner_states.save(c, rid, last_seen_at=seen, awake_since=awake)


def _set_next_due(c, rows, after):
    """Each schedule's next time is its first occurrence after `after` (raw SQL: the scheduler's next-due column)."""
    for row in rows:
        trigger = row["trigger"]
        first = next_due(trigger["cron"], after, trigger.get("tz") or "UTC")
        c.execute("UPDATE automations SET next_due_at=? WHERE id=?", (_stamp(first), row["id"]))


def _pass(c, store, due, ids):
    """The scheduler's schedule loop at `due` for the schedules due then (automations.tick, without its built-ins)."""
    settings = getattr(store, "settings", None)
    stamp = _stamp(due)
    for aid in ids:
        row = c.execute("SELECT * FROM automations WHERE id=? AND deleted_at IS NULL AND enabled=1", (aid,)).fetchone()
        trigger = automations.flow_of(row)["trigger"]
        zone = trigger.get("tz") or "UTC"
        if why := (cloud_access.routines_paused(c) or automations._bot_away(c, row)
                   or automations._no_input(c, row, settings, due, zone)):
            automations._count(c, aid, "dropped")
            H.event(c, H.KEEPER, "automation.skipped", aid, {"at": stamp, "reason": why})
        else:
            automations.fire(c, "schedule", {"schedule": {"at": stamp, "cron": trigger["cron"], "tz": zone}},
                             f"schedule:{stamp}", only=aid)
        c.execute("UPDATE automations SET next_due_at=? WHERE id=?",
                  (_stamp(next_due(trigger["cron"], due, zone)), aid))


def _runs_at(b, due, ids):
    """The runs the scheduler fired at `due` for the schedules `ids` (their trigger is the schedule's time): run id ->
    automation id. Another run at the same time (a routine's run the growth section wrote) is not one of them."""
    with b.store.read() as c:
        rows = c.execute(f"SELECT id, automation_id FROM automation_runs WHERE trigger_ref=? "
                         f"AND automation_id IN ({','.join('?' * len(ids))})",
                         (f"schedule:{_stamp(due)}", *ids)).fetchall()
    return {row["id"]: row["automation_id"] for row in rows}


def _work(b, runs, occurrences):
    """Each run started now is answered by its bot's computer: the bot's next job is claimed, started and completed.
    `occurrences` maps an automation id to its (flow, index) at this time. A job the claim hands out for a run that
    did not start now is a problem in the seed, not a run to answer."""
    pending = dict(runs)
    while pending:
        slug = _bot_of(occurrences[next(iter(pending.values()))][0])
        claim = _claim(b, slug)
        if claim is None:
            raise RuntimeError(f"{slug} was handed no job for its run")
        run_id = ((claim.get("message") or {}).get("refs") or {}).get("automation_run")
        if run_id not in pending:
            raise RuntimeError(f"{slug} was handed a job that is not a run started now: {run_id}")
        flow, index = occurrences[pending.pop(run_id)]
        token = _computer(b, slug)["token"]
        b.call("POST", f"attempts/{claim['id']}/started", {"thread_id": claim["conversation"]["id"]}, token=token)
        minutes, body = _turn(flow, slug, index)
        b.clock.at += timedelta(minutes=minutes)
        b.call("POST", f"attempts/{claim['id']}/complete", body, token=token)
        # Every run of a routine is a task, and the bot closes it when it is done; until then the schedule absorbs
        # its next occurrence (automations._absorbed), so the bot closes it with its answer as its note.
        if task := (claim.get("task") or {}).get("id"):
            b.write(lambda c, slug=slug, task=task, note=body["text"]:
                    H.task_update(c, H.bot_actor(slug), task, status="done", note=note))
        _keep_focus(b, slug)


def _bot_of(flow):
    return flow["steps"][0]["bot"].split(":", 1)[1]


def _computer(b, slug):
    """The computer the bot is assigned to (the Linux box hosts the Gateway bots, the Mac the rest)."""
    with b.store.read() as c:
        row = c.execute("SELECT runner_id FROM assignments WHERE bot=?", (slug,)).fetchone()
    return b.linux if row and row["runner_id"] == b.linux["runner_id"] else b.mac


def _claim(b, slug):
    """The bot's computer's next job for the bot, polled as the runner polls: an empty claim is a pass, and the next
    poll comes CLAIM_POLL later on the demo clock."""
    for _ in range(CLAIM_TRIES):
        claim = b.call("POST", "jobs/claim", {"bot": slug}, token=_computer(b, slug)["token"])["attempt"]
        if claim:
            return claim
        b.clock.at += CLAIM_POLL
    return None


def _turn(flow, slug, index):
    """A run's minutes and its complete body: the bot's answer for this occurrence, and what the run spent (its
    tokens at the bot's model; the product prices them)."""
    source = (flow.get("source") or {}).get("id") or flow["name"]
    texts = SUMMARIES.get(source, (f"{flow['name']} ran with nothing to report.",))
    text = texts[(index + len(source)) % len(texts)]
    model, _provider, billing = D.USAGE.get(slug, ("claude-haiku-5-5", "anthropic", "api"))
    uncached = (14000 + ((index * 9 + len(source)) % 7) * 3500) * D.RUN_REQUESTS
    cached = uncached * 2 // 3
    output = (900 + ((index * 3 + len(source)) % 5) * 400) * D.RUN_REQUESTS
    minutes = 2 + (index + len(source)) % 5
    body = {"outcome": "completed", "text": text, "last_seq": 0,
            "usage": {"input_tokens": uncached, "cached_tokens": cached, "output_tokens": output,
                      "model": model, "billing": billing}}
    return minutes, body
