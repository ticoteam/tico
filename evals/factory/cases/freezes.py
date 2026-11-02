"""Board freezes: a usage limit that holds every claim, and a board that has to keep moving once the limit lifts or a
fallback is ready. The runs claim the way a real runner does (no bot named, its busy lists), so each claim the server
answers is one a computer sends. Limits, holds and pauses are set through the product's routes and the computer's
sign-in state. A group is a roster entry the sim writes itself (World.add_group), and the limit a turn meets is the error
line its runtime gives (`hit`). The sim also loses a probe turn (its computer goes off, or its job is cancelled before it
finishes), and marks that where it happens."""
import datetime as dt

from evals.factory.scenarios import BASE, Scenario, at, board_keeps_moving, both, tickets
from evals.factory.world import START

WEEKLY = "You've hit your weekly limit · resets Oct 27, 9am (UTC)"      # a reset days away: the login is held for it
SESSION = "You've hit your session limit · resets 10:30am (UTC)"        # a reset within the hour
TWO_DAYS = "You've hit your session limit · resets Oct 22, 9am (UTC)"   # a second login, held two days
PACKET_BOT = "fleet-01"                                                 # the bot the moved-input run messages
PACKET_TEXTS = ("Check the packet figures.", "Confirm the totals before you answer.")
LOWERED = [(at(1, 30), at(1, 42))]                                      # a cap is lowered from 10:30 to 10:42


def hit(runtime, text):
    """The next turn that runs on `runtime` meets this usage limit, as a turn on a spent login does."""
    def act(run, w, team):
        w.limit_next[runtime] = text
    return act


def cap(scope, hourly):
    """A team's (`company`) or a group's (`groups/<id>`) hourly limit, set as its owner sets it."""
    def act(run, w, team):
        w.set_pool(scope, hourly)
    return act


def pause(slugs):
    """The owner pauses these bots: they claim nothing until resumed, so the run leaves their work out of the count."""
    def act(run, w, team):
        for slug in slugs:
            w.pause(slug)
            run.paused.add(slug)
    return act


def at_times(events):
    """Each (when, action) runs once, on the first tick at or after `when`."""
    def during(run, w, team, i):
        fired = run.__dict__.setdefault("fired", set())
        for n, (when, action) in enumerate(events):
            if n not in fired and w.now() >= START + when:
                fired.add(n)
                action(run, w, team)
    return during


def lose_probe(mode):
    """The first turn on the login after its hold ends is the probe, and the probe is lost. `stale`: its computer goes off
    for 40 minutes, so its lease lapses and the job waits in the queue with its probe time old. `missing`: the job is
    cancelled before it finishes, so the hold names a probe that is no longer there (a fixture write made by the sim)."""
    def during(run, w, team, i):
        from backend import login_holds
        now = w.now()
        if getattr(run, "probe_lost_at", None) is not None or now < START + at(1, 30):
            return
        with w.store.read() as c:
            hold = login_holds.get(c, w.runner["runner_id"], "", "fake")
        job = (hold or {}).get("probe_job")
        row = w.sql("SELECT bot_id FROM jobs WHERE id=?", (job,)) if job else []
        if not row or not team.bot(row[0]["bot_id"]).running:
            return
        bot = team.bot(row[0]["bot_id"])
        run.probe_lost_at, run.probe_bot = now, bot.slug
        run.lost_probe = {"job": job, "bot_id": bot.slug, "attempt": bot.attempt["id"], "mode": mode, "at": now}
        if mode == "stale":
            bot.available = False
            run.back_at = now + at(0, 40)
        else:
            with w.store.transaction() as c:
                c.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (job,))
            bot.lose()
    return during


def back_from_loss(run, w, team, i):
    """The computer a lost probe ran on comes back on the tick its back time falls in. Its bot is available before that
    tick's clock moves, so the heartbeat the tick sends reports it ready and this tick's claims see it (sim.Run loop)."""
    back = getattr(run, "back_at", None)
    if back and w.now() + w.tick >= back:
        team.bot(run.probe_bot).available = True
        run.back_at = None
        run.back_check = {"slug": run.probe_bot, "at": w.now()}     # runner_round reads its readiness on this tick


def setup_with(tasks, each=0, providers=(), groups=()):
    """`tasks` tickets for the team, `each` routine tasks on every fleet bot (the backlog), the company's AI providers
    and the groups its limits name."""
    def setup(run, w, team):
        for gid in groups:
            w.add_group(gid, gid.replace("-", " ").title())
        if providers:
            w.enable_providers(providers)
        for bot in team.all:
            if bot.slug.startswith("fleet-"):
                for n in range(each):
                    w.post("tasks", {"owner": bot.slug, "title": f"Routine {n + 1}", "body": "Look at the queue."})
        tickets(run, w, tasks)
    return setup


def packet_task(slug):
    """One task the bot owns, which the person's messages to it are about: the run keeps its id."""
    def setup(run, w, team):
        made = w.post("tasks", {"owner": slug, "title": "Acme packet", "body": "Check the packet figures."})
        run.packet = made.get("task", made)["id"]
    return setup


def say(*texts):
    """A person's messages to the bot, on the task the run gave it: its queue for that task."""
    def act(run, w, team):
        for text in texts:
            w.post("messages", {"to": PACKET_BOT, "text": text, "refs": {"task": run.packet}})
    return act


def routine(slug, count):
    """`count` routine tasks the bot owns, filed now: work that waits in its queue while its login is held, so the board
    has queued work to resume with once the login is back."""
    def act(run, w, team):
        for n in range(count):
            w.post("tasks", {"owner": slug, "title": f"Routine {n + 1}", "body": "Look at the queue."})
    return act


def steer(slug):
    """The bot reads what is queued for its turn as the turn starts, and acks each input it read."""
    def act(run, w, team):
        w.steer_bots.add(slug)
    return act


# ---------------------------------------------------------------------------------------------- checks
FMT = "%Y-%m-%dT%H:%M:%S"


def clock_text(when):
    """A clock offset of the scenario, as a turn's start text reads it."""
    return (START + when).strftime(FMT)


def window_of(text):
    """The UTC half hour a turn's start text falls in, as [from, to) texts: the window the server counts a 30-minute cap
    in (backend/usage_limits.py)."""
    at = dt.datetime.strptime(text[:19], FMT)
    lo = at.replace(minute=30 if at.minute >= 30 else 0, second=0)
    return lo.strftime(FMT), (lo + dt.timedelta(minutes=30)).strftime(FMT)


def pool_turns(run, scope):
    """The turns the pool covers: every turn for the company, or the turns of the bots the server counts in the group."""
    from backend import usage_limits
    turns = [dict(r) for r in run.w.sql("SELECT bot, started_at, finished_at, est_cost_usd FROM turns ORDER BY started_at")]
    if scope == usage_limits.COMPANY:
        return turns
    with run.w.store.read() as c:
        members = usage_limits.members(c, scope)
    return [r for r in turns if r["bot"] in members]


def spend_under(cap_usd, since, until, scope="company"):
    """No turn of the pool starts in [since, until) in a UTC half hour whose spend, from the pool's turns that had
    finished before it started, has reached the window's cap. The server holds an hourly cap set through the API as the
    half hour that replaces it (usage.py: half of it, up to the next $0.10), so that is the figure checked. The cap must
    first be reached by `until` in the half hour it falls in: a cap nothing reached holds no turn, and the check would
    pass on nothing. `scope` is the pool: the company, or `group:<id>`."""
    from backend import usage_limits
    limit = usage_limits.half(cap_usd)

    def check(run):
        turns = pool_turns(run, scope)
        lo_t, hi_t = clock_text(since), clock_text(until)
        window_lo, window_hi = window_of(hi_t)
        reached = sum(r["est_cost_usd"] or 0 for r in turns
                      if window_lo <= r["started_at"][:19] < window_hi and r["finished_at"]
                      and r["finished_at"][:19] < hi_t)
        if reached < limit:
            return False, (f"the half hour spent ${reached:.2f} by {START + until:%H:%M}, short of the ${limit:g} "
                           f"window cap an ${cap_usd:g} hourly cap holds")
        late = []
        for r in turns:
            if not lo_t <= r["started_at"][:19] < hi_t:
                continue
            lo, hi = window_of(r["started_at"])
            spent = sum(x["est_cost_usd"] or 0 for x in turns if x is not r and lo <= x["started_at"][:19] < hi
                        and x["finished_at"] and x["finished_at"][:19] < r["started_at"][:19])
            if spent >= limit:
                late.append(f"{r['started_at'][11:16]} (${spent:.2f})")
        if late:
            return False, f"{len(late)} turns started past the ${limit:g} window cap: " + ", ".join(late[:4])
        return True, f"no turn started past the ${limit:g} window cap (an ${cap_usd:g} hourly cap)"
    return check


def hour_spent(run, when):
    """The company's spend in the clock hour of `when`, from the turns that had finished by then: the figure a cap is
    compared with, as usage_limits counts it."""
    at = (START + when).strftime("%Y-%m-%dT%H:%M:%S")
    rows = [dict(r) for r in run.w.sql("SELECT est_cost_usd FROM turns WHERE substr(started_at,1,13)=? AND finished_at < ?",
                                       (at[:13], at))]
    return sum(r["est_cost_usd"] or 0 for r in rows)


def resumed(run, when, minutes):
    """From the claim round at `when` (an offset on the scenario's clock) on: the first round with a free slot and queued
    work, and whether a turn starts within `minutes` of it. No such round fails, as nothing then shows the board resumed."""
    t0 = START + when
    after = [m for m in run.marks if m["at"] >= t0]
    if not after:
        return False, f"no claim round after {t0:%H:%M}"
    lift = after[0]
    state = f"at {lift['at']:%H:%M} {lift['free']} slots free, {lift['queued']} bots queued, {lift['held']} jobs held"
    ready = next((m for m in after if m["free"] and m["queued"]), None)
    if ready is None:
        return False, f"no free slot with queued work after {t0:%H:%M} ({state})"
    first = next((m for m in after if m["at"] >= ready["at"] and m["started_at"]), None)
    if first is None:
        return False, f"no turn started in the claim rounds after work waited at {ready['at']:%H:%M} ({state})"
    gap = (first["at"] - ready["at"]).total_seconds() / 60
    return gap <= minutes, (f"first turn {gap:.0f} min after work waited at {ready['at']:%H:%M} "
                            f"(limit {minutes} min); {state}")


def resumes_within(when, minutes):
    """Once the limit lifts at `when`, a turn starts within `minutes` of the first claim round after it with a free slot
    and queued work. The detail reports the hour's spend at the lift."""
    def check(run):
        ok, detail = resumed(run, when, minutes)
        return ok, f"{detail}; the hour spent ${hour_spent(run, when):.2f}"
    return check


def most_turns_on(runtime, since, share=0.5):
    """From `since` on, at least `share` of the turns ran on `runtime`, the fallback a held login sends them to."""
    def check(run):
        turns = run.turns(since=START + since)
        on = sum(1 for r in turns if r.get("runtime") == runtime)
        ratio = on / max(1, len(turns))
        return ratio >= share, f"{on} of {len(turns)} turns from {START + since:%H:%M} ran on {runtime} ({ratio:.0%})"
    return check


def probe_resumes(minutes):
    """After the probe is lost, a turn starts within `minutes` of the first claim round after it with a free slot and
    queued work: a stale or missing probe must not keep its login's queue waiting on it."""
    def check(run):
        lost = getattr(run, "probe_lost_at", None)
        if lost is None:
            return False, "no probe was in flight when the hold ended, so nothing was lost"
        ok, detail = resumed(run, lost - START, minutes)
        return ok, f"{detail}; the probe was lost at {lost:%H:%M}"
    return check


def turns_from(when):
    """Once the login is back at `when`, the bots finish turns again: a turn that ended without the limit."""
    def check(run):
        ran = [r for r in run.turns(since=START + when) if r["kind"] != "limited"]
        return bool(ran), f"{len(ran)} turns finished from {START + when:%H:%M} on"
    return check


def inputs_moved(bot, texts):
    """The moved-input mechanism, in order: the turn that met the limit had both messages, its own prompt (the claimed
    message) and the inputs its first poll read, it ended without answering them (its record has no action), and a later
    turn took them over. A later turn takes an input over with its attempt_inputs row; a prompt has no such row, so the
    later turn is the attempt that ran the message's job."""
    def check(run):
        w = run.w
        mids = [r["id"] for text in texts for r in w.sql(
            "SELECT id FROM messages WHERE body=? AND json_extract(refs,'$.task')=?", (text, run.packet))]
        if len(mids) != len(texts):
            return False, f"{len(mids)} of {len(texts)} messages were found on the task"
        limited = [r for r in run.t.bot(bot).turns if r["kind"] == "limited"]
        if not limited:
            return False, "no turn met the limit"
        turn = limited[0]
        read = {turn["prompt"]} | {m for _, _, aid, ids in w.input_reads if aid == turn["attempt"] for m in ids}
        missed = [m for m in mids if m not in read]
        if missed:
            return False, f"the limited turn read {len(mids) - len(missed)} of {len(mids)} messages"
        if turn["action"]:
            return False, f"the limited turn answered: {turn['action'][:80]}"
        owners = {r["message_id"]: r["attempt_id"] for r in w.sql(
            f"SELECT message_id, attempt_id FROM attempt_inputs WHERE message_id IN ({','.join('?' * len(mids))})",
            tuple(mids))}

        def taken_over(mid):
            if owners.get(mid) not in (None, turn["attempt"]):
                return True
            return bool(w.sql("SELECT 1 FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE j.message_id=? AND a.id<>?",
                              (mid, turn["attempt"])))
        still = [m for m in mids if not taken_over(m)]
        if still:
            return False, f"{len(still)} of {len(mids)} messages still belong to the limited turn"
        return True, (f"the limited turn {turn['attempt'][:8]} had both, left them unanswered, and a later turn took "
                      "them over")
    return check


def freeze_checks(since, *extra, waits=(), fallback=False):
    """The checks every freeze runs, the board's from `since` on. `waits` are windows (offsets on the scenario's clock)
    that a team-wide or group-wide cap holds every login; `fallback` is set where the freeze is about a fallback
    login."""
    return [*BASE, ("the board keeps moving from the event on", None,
                    board_keeps_moving(since, waits, fallback)), *extra]


SCENARIOS = [
    Scenario("freeze_company_cap",
             "The team's hourly limit is lowered to $3 at 10:30, below the hour's spend, and raised to $40 at 10:42, "
             "with a backlog on every fleet bot.",
             setup=setup_with(10, each=12), claim="runner", signed_in=("fake",), capacity=8, fleet=24,
             during=at_times([(at(1, 30), cap("company", 3)), (at(1, 42), cap("company", 40))]),
             checks=freeze_checks(at(1, 30),
                                  ("no turn starts past the cap while it is lowered", None,
                                   spend_under(3, at(1, 30), at(1, 42))),
                                  ("the board resumes within 15 minutes of the raise", None,
                                   resumes_within(at(1, 42), 15)),
                                  waits=LOWERED)),
    Scenario("freeze_group_cap",
             "The engineering group's hourly limit is lowered to $3 at 10:30 and raised to $40 at 10:42, with a backlog "
             "on every fleet bot: its bots resume.",
             setup=setup_with(10, each=12, groups=("engineering",)), claim="runner", signed_in=("fake",),
             capacity=8, fleet=24,
             during=at_times([(at(1, 30), cap("groups/engineering", 3)), (at(1, 42), cap("groups/engineering", 40))]),
             checks=freeze_checks(at(1, 30),
                                  ("no turn starts past the cap while it is lowered", None,
                                   spend_under(3, at(1, 30), at(1, 42), "group:engineering")),
                                  ("the board resumes within 15 minutes of the raise", None,
                                   resumes_within(at(1, 42), 15)),
                                  waits=LOWERED)),
    Scenario("freeze_weekly_hold",
             "The team's own login is held for a weekly limit with its reset days away; most bots run on the codex "
             "fallback that is ready, behind a backlog of hundreds of queued jobs.",
             setup=setup_with(10, each=12, providers=("openai",)), claim="runner", signed_in=("fake", "codex"),
             capacity=8, fleet=24, during=at_times([(at(0, 20), hit("fake", WEEKLY))]),
             checks=freeze_checks(at(0, 20),
                                  ("most turns after the hold run on codex", None, most_turns_on("codex", at(0, 20))),
                                  fallback=True)),
    Scenario("freeze_combined",
             "The weekly hold, a company hourly limit lowered and raised, codex held for two days so the second fallback "
             "takes the work, and two reviewers paused.",
             setup=setup_with(10, each=12, providers=("openai", "anthropic")), claim="runner",
             signed_in=("fake", "codex", "claude"), capacity=8, fleet=24,
             during=at_times([(at(0, 20), hit("fake", WEEKLY)),
                              (at(0, 45), pause(("reviewer-gpt", "qa-claude"))),
                              (at(1, 0), hit("codex", TWO_DAYS)),
                              (at(1, 30), cap("company", 3)),
                              (at(1, 42), cap("company", 40))]),
             checks=freeze_checks(at(0, 20),
                                  ("most turns after codex is held run on claude", None,
                                   most_turns_on("claude", at(1, 0))),
                                  ("no turn starts past the cap while it is lowered", None,
                                   spend_under(3, at(1, 30), at(1, 42))),
                                  waits=LOWERED, fallback=True)),
    Scenario("freeze_probe_stale",
             "The login's hold ends at 10:30 and its probe turn is lost mid-run: the probe's time goes stale and the queue "
             "must go on without it.",
             setup=setup_with(8), claim="runner", signed_in=("fake",),
             during=both(at_times([(at(0, 30), hit("fake", SESSION))]), lose_probe("stale")), arrive=back_from_loss,
             checks=freeze_checks(at(1, 30), ("the queue goes on once the lost probe is stale", None, probe_resumes(30)))),
    Scenario("freeze_probe_missing",
             "The login's hold ends at 10:30 and its probe job is gone before it finishes: the queue must take a new probe.",
             setup=setup_with(8), claim="runner", signed_in=("fake",),
             during=both(at_times([(at(0, 30), hit("fake", SESSION))]), lose_probe("missing")),
             checks=freeze_checks(at(1, 30), ("the queue takes a new probe once the probe is gone", None,
                                              probe_resumes(15)))),
    Scenario("freeze_moved_input",
             "A person's two messages reach a bot on its task while its turn runs: the turn reads them, then its login's "
             "limit stops it before it answers. A third message queues after the stop, and the claims that take the login "
             "back move the two unanswered ones to the run that takes the work.",
             setup=packet_task(PACKET_BOT), claim="runner", signed_in=("fake",), fleet=1,
             during=at_times([(at(0, 20), steer(PACKET_BOT)),
                              (at(0, 20), say(*PACKET_TEXTS)),
                              (at(0, 20), hit("fake", SESSION)),
                              (at(0, 45), say("Is the packet final?")),
                              (at(0, 50), routine(PACKET_BOT, 8))]),
             checks=freeze_checks(at(0, 20),
                                  ("the limited turn read both inputs, left them unanswered, and a later turn took them over",
                                   None, inputs_moved(PACKET_BOT, PACKET_TEXTS)),
                                  ("turns run again once the login is back", None, turns_from(at(1, 30))))),
]
