"""Board freezes like the one on a live install on v0.4.16: a usage limit that holds every claim, and a board that has to keep
moving once the limit lifts or a fallback is ready. The runs claim the way a real runner does (no bot named, its busy
lists), so each claim the server answers is one a computer sends. Limits, holds, pauses and groups are set through the
product's own routes and sign-in state. The one thing the sim does itself is lose a probe turn (its computer goes off, or
its job is cancelled before it finishes), and that is marked where it happens."""
from evals.factory.scenarios import BASE, Scenario, at, board_keeps_moving, both, no_claim_5xx, tickets
from evals.factory.world import START

WEEKLY = "You've hit your weekly limit · resets Oct 27, 9am (UTC)"      # a reset days away: the login is held for it
SESSION = "You've hit your session limit · resets 10:30am (UTC)"        # a reset within the hour
TWO_DAYS = "You've hit your session limit · resets Oct 22, 9am (UTC)"   # a second login, held two days


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
    """The owner pauses these bots: they claim nothing until resumed."""
    def act(run, w, team):
        for slug in slugs:
            w.pause(slug)
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
        if getattr(run, "back_at", None) and now >= run.back_at:
            team.bot(run.probe_bot).available = True
            run.back_at = None
        if getattr(run, "probe_lost_at", None) is not None or now < START + at(1, 30):
            return
        with w.store.read() as c:
            hold = login_holds.get(c, w.runner["runner_id"], "", "fake")
        job = (hold or {}).get("probe_job")
        row = w.sql("SELECT bot FROM jobs WHERE id=?", (job,)) if job else []
        if not row or not team.bot(row[0]["bot"]).running:
            return
        bot = team.bot(row[0]["bot"])
        run.probe_lost_at, run.probe_bot = now, bot.slug
        if mode == "stale":
            bot.available = False
            run.back_at = now + at(0, 40)
        else:
            with w.store.transaction() as c:
                c.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (job,))
            bot.lose()
    return during


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


# ---------------------------------------------------------------------------------------------- checks
def spend_under(cap_usd, since, until):
    """No turn starts in [since, until) in a clock hour whose spend, from the runs that finished before it started, has
    already reached the cap: usage_limits counts it that way."""
    lo, hi = ((START + t).strftime("%Y-%m-%dT%H:%M:%S") for t in (since, until))

    def check(run):
        runs = [dict(r) for r in run.w.sql("SELECT started, finished, est_cost_usd FROM turns ORDER BY started")]
        late = []
        for r in runs:
            if not lo <= r["started"][:19] < hi:
                continue
            hour = r["started"][:13]
            spent = sum(x["est_cost_usd"] or 0 for x in runs if x is not r and x["started"][:13] == hour
                        and x["finished"] and x["finished"] < r["started"])
            if spent >= cap_usd:
                late.append(f"{r['started'][11:16]} (${spent:.2f})")
        if late:
            return False, f"{len(late)} turns started past the ${cap_usd:g} cap: " + ", ".join(late[:4])
        return True, f"no turn started past the ${cap_usd:g} cap"
    return check


def hour_spent(run, when):
    """The company's spend in the clock hour of `when`, from the turns that had finished by then: the figure a cap is
    compared with, as usage_limits counts it."""
    at = (START + when).strftime("%Y-%m-%dT%H:%M:%S")
    rows = [dict(r) for r in run.w.sql("SELECT est_cost_usd FROM turns WHERE substr(started,1,13)=? AND finished < ?",
                                       (at[:13], at))]
    return sum(r["est_cost_usd"] or 0 for r in rows)


def resumes_within(when, minutes):
    """Once the limit lifts at `when`, a turn starts within `minutes` while work is waiting. The detail says what the
    board had to work with at the lift: the free slots, the work waiting and the hour's spend."""
    def check(run):
        t0 = START + when
        after = [m for m in run.marks if m["at"] >= t0]
        if not any(m["claimable"] for m in after):
            return True, "no work was waiting once it lifted"
        first = next((m["at"] for m in after if m["started"]), None)
        waiting = "yes" if after[0]["claimable"] else "no"
        state = (f"at the lift {after[0]['free']} slots free, work waiting {waiting}, the hour spent "
                 f"${hour_spent(run, when):.2f}")
        if first is None:
            return False, f"no turn started in the {len(after)} claim rounds after it lifted; {state}"
        gap = (first - t0).total_seconds() / 60
        return gap <= minutes, f"first turn {gap:.0f} min after it lifted (limit {minutes} min) at {first:%H:%M}; {state}"
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
    """After the probe is lost, another turn starts within `minutes` while work is waiting: a stale or missing probe
    must not keep the login's queue waiting on it."""
    def check(run):
        lost = getattr(run, "probe_lost_at", None)
        if lost is None:
            return False, "no probe was in flight when the hold ended, so nothing was lost"
        after = [m for m in run.marks if m["at"] > lost]
        if not any(m["claimable"] for m in after):
            return True, "no work was waiting once the probe was lost"
        first = next((m["at"] for m in after if m["started"]), None)
        if first is None:
            return False, f"no turn started in the {len(after)} claim rounds after the probe was lost"
        gap = (first - lost).total_seconds() / 60
        return gap <= minutes, f"first turn {gap:.0f} min after the probe was lost (limit {minutes} min)"
    return check


def freeze_checks(since, *extra):
    return [*BASE, ("no claim got a server error", None, no_claim_5xx),
            ("the board keeps moving from the event on", None, board_keeps_moving(since)), *extra]


SCENARIOS = [
    Scenario("freeze_company_cap",
             "The team's hourly limit is lowered to $3 at 10:30, below the hour's spend, and raised to $40 at 10:42.",
             setup=setup_with(12), claim="runner", signed_in=("fake",),
             during=at_times([(at(1, 30), cap("company", 3)), (at(1, 42), cap("company", 40))]),
             checks=freeze_checks(at(1, 30),
                                  ("no turn starts past the cap while it is lowered", None,
                                   spend_under(3, at(1, 30), at(1, 42))),
                                  ("the board resumes within 15 minutes of the raise", None,
                                   resumes_within(at(1, 42), 15)))),
    Scenario("freeze_group_cap",
             "The engineering group's hourly limit is lowered to $3 at 10:30 and raised to $40 at 10:42: its bots resume.",
             setup=setup_with(12, groups=("engineering",)), claim="runner", signed_in=("fake",),
             during=at_times([(at(1, 30), cap("groups/engineering", 3)), (at(1, 42), cap("groups/engineering", 40))]),
             checks=freeze_checks(at(1, 30),
                                  ("no turn starts past the cap while it is lowered", None,
                                   spend_under(3, at(1, 30), at(1, 42))),
                                  ("the board resumes within 15 minutes of the raise", None,
                                   resumes_within(at(1, 42), 15)))),
    Scenario("freeze_weekly_hold",
             "The team's own login is held for a weekly limit with its reset days away; most bots run on the codex "
             "fallback that is ready, behind a backlog of hundreds of queued jobs.",
             setup=setup_with(10, each=12, providers=("openai",)), claim="runner", signed_in=("fake", "codex"),
             capacity=8, fleet=24, during=at_times([(at(0, 20), hit("fake", WEEKLY))]),
             checks=freeze_checks(at(0, 20),
                                  ("most turns after the hold run on codex", None, most_turns_on("codex", at(0, 20))))),
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
                                   spend_under(3, at(1, 30), at(1, 42))))),
    Scenario("freeze_probe_stale",
             "The login's hold ends at 10:30 and its probe turn is lost mid-run: the probe's time goes stale and the queue "
             "must go on without it.",
             setup=setup_with(8), claim="runner", signed_in=("fake",),
             during=both(at_times([(at(0, 30), hit("fake", SESSION))]), lose_probe("stale")),
             checks=freeze_checks(at(1, 30), ("the queue goes on once the lost probe is stale", None, probe_resumes(30)))),
    Scenario("freeze_probe_missing",
             "The login's hold ends at 10:30 and its probe job is gone before it finishes: the queue must take a new probe.",
             setup=setup_with(8), claim="runner", signed_in=("fake",),
             during=both(at_times([(at(0, 30), hit("fake", SESSION))]), lose_probe("missing")),
             checks=freeze_checks(at(1, 30), ("the queue takes a new probe once the probe is gone", None,
                                              probe_resumes(15)))),
]
