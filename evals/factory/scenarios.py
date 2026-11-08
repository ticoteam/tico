"""Scenarios seen on a real team's Dev board, each with the checks the factory must pass. A check names the work that
fixes it when it is a known gap today, so a failing check reads as "not built yet", not as a broken simulation."""
from collections import Counter

from .board import design_board
from .sim import Scenario, at, tickets
from .world import START

QUEUE = "queue cleanup"   # stale notices dropped, the rest folded into one turn (Dev 601 spec)
TIMEOUT = "Dev 601 task timeout"
HOLD = "Dev 598"
SLOTS = "Dev 542"
HOE = "Dev 606"
ASSIGN = "assignment rule"
REPORT = "report"   # printed for a person to read, never failed: how long work takes is measured, not judged
LONG_TICKET_HOURS, LONG_STEP_HOURS = 24, 12


# ---------------------------------------------------------------------------------------------- checks
def all_through(run):
    left = [f"{t['title']} @ {t['step']}" for t in run.final if not run.shipped(t)]
    return not left, ", ".join(left[:6]) + (f" (+{len(left) - 6})" if len(left) > 6 else "")


def long_running(run):
    """Tickets that took over LONG_TICKET_HOURS to merge (or are still open past it), and stays at one step over
    LONG_STEP_HOURS: most likely a bug, but a hard change can take that long, so they are reported, not failed."""
    end = run.end.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    long = []
    for t in run.final:
        hours = ((run.merged_at.get(t["id"]) or run.end) - run.w.H.parse_ts(t["created_at"])).total_seconds() / 3600
        steps = [f"{name} {span:.0f}h" for name, span in run.w.step_spans(t["id"], until=end)
                 if span > LONG_STEP_HOURS and run.w.board.step(name) and not run.w.board.step(name).merged
                 and not run.w.board.finished(name)]
        if hours > LONG_TICKET_HOURS or steps:
            long.append(f"{t['title']} {hours:.0f}h" + (f" ({', '.join(steps)})" if steps else ""))
    return not long, ", ".join(long[:6]) + (f" (+{len(long) - 6})" if len(long) > 6 else "")


def never_blocked_after_merge(run):
    seen = run.violations["merged_blocked"]
    return not seen, ", ".join(sorted({t for _, t in seen})[:6])


def no_stale_turns(run):
    stale = [r for r in run.turns() if r["stale"]]
    kinds = Counter(r["news"] for r in stale)
    return not stale, f"{len(stale)} turns on stale notices: " + ", ".join(f"{k} {n}" for k, n in kinds.most_common())


def noise_under(share):
    def check(run):
        s = run.summary()
        ratio = s["noise"] / max(1, s["turns"])
        return ratio <= share, f"{s['noise']} of {s['turns']} turns did nothing ({ratio:.0%})"
    return check


def one_review_per_round(run):
    """Linear reviews: a review step that sends changes to fixes, which then go on to another step, gets one check per
    ticket; only a step whose fixes come back to it (QA) can have several."""
    board = run.w.board
    once = {s.name for s in board.checks()
            if s.name not in (board.step(s.check.other).after_green, board.step(s.check.other).next)}
    extra = []
    for t in run.final:
        kinds = Counter(run.w.check_step(c) for c in run.w.checks(t["id"]) if not c["title"].startswith("Plan:"))
        extra += [f"{t['title']} {k} x{n}" for k, n in kinds.items() if k in once and n > 1]
    return not extra, ", ".join(extra[:6])


def max_at_check(slack=0.5):
    """No check keeps a ticket waiting longer than its timeout (plus `slack` hours for the sweep to act): from the
    ticket reaching the step to its verdict, its skipped round or its merge wake."""
    def check(run):
        long = []
        for t in run.final:
            spans = run.w.check_waits(t["id"], until=run.end.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
            for name, span in spans:
                step = run.w.board.step(name)
                if step and step.check and span > step.check.timeout_hours + slack:
                    long.append(f"{t['title']} {name} {span:.1f}h")
        return not long, ", ".join(long[:6])
    return check


def burst_after(when, bound, bots=None):
    """In the hour after `when`, no bot runs more than `bound` turns: a backlog arrives as one folded turn."""
    def check(run):
        since = START + when
        per = Counter(r["bot"] for r in run.turns(since=since, until=since + at(1), bots=bots))
        over = {b: n for b, n in per.items() if n > bound}
        return not over, "turns in the hour after: " + ", ".join(f"{b} {n}" for b, n in per.most_common(5))
    return check


def peak_doing(cap):
    def check(run):
        over = {b: n for b, n in run.peak_doing.items() if n > cap}
        return not over, ", ".join(f"{b} {n}" for b, n in run.peak_doing.items())
    return check


def no_uncertain_runs(run):
    """No run is left 'uncertain' (its computer stopped answering partway): such a job waits for a person, and its
    ticket waits with it."""
    rows = run.w.sql("SELECT bot_id, count(*) n FROM jobs WHERE state='uncertain' GROUP BY bot_id")
    return not rows, ", ".join(f"{r['bot_id']} {r['n']}" for r in rows)


def no_server_error(run):
    """No call the sim makes gets a server error: a route that raises or answers 500 fails the run's checks, with the
    first error and its traceback when the app raised. Each claim's status is counted in the detail."""
    errors = run.w.server_errors
    counts = ", ".join(f"{status} x{n}" for status, n in sorted(run.claims.items())) or "no claims"
    if not errors:
        return True, f"no call got a server error; claims: {counts}"
    when, route, status, body = errors[0]
    text = body if len(body) <= 500 else body[:200] + " ... " + body[-300:]
    return False, (f"{len(errors)} calls got a server error; first {status} on {route} at {when:%H:%M}: {text}; "
                   f"claims: {counts}")


def board_keeps_moving(since, waits=(), fallback=False):
    """From `since` on, each claim round that has a free slot and a ready bot starts as many turns as the smaller of its
    free slots and its ready bots. A bot is ready when the records say it can run then (sim.Run.readiness): its computer
    is awake, and a login it can run on is free, its own (no hold, no probe out, not cooling off) or a fallback that is
    signed in and not held. The claim's own answer is not the judge: a round the server passes over while work is ready
    is a stall whatever its reason, and that reason is printed with the round (sim.Run.reasons). `waits` are (from, to)
    windows of the scenario's clock in which a team-wide or group-wide cap holds every login: a round inside one is not
    judged. `fallback` also requires a judged round in which a bot started on a fallback login, for a scenario whose
    point is that fallback. A run with no judged round fails, since nothing then shows the board moved."""
    def check(run):
        if not run.marks:
            return False, "no runner claim rounds: this check needs runner-style claims"
        start = START + since
        windows = [(START + lo, START + hi) for lo, hi in waits]
        judged, short, waived, on_fallback = 0, [], 0, 0
        for m in run.marks:
            if m["at"] < start or not m["free"] or not m["ready"]:
                continue
            if any(lo <= m["at"] < hi for lo, hi in windows):
                waived += 1
                continue
            judged += 1
            on_fallback += bool(m["fallback"])
            if m["started_at"] < min(m["free"], len(m["ready"])):
                short.append(m)
        if not judged:
            return False, ("no claim round had a free slot and a ready bot outside the waits: nothing shows the board "
                           "moved")
        if short:
            return False, (f"{len(short)} of {judged} claim rounds started fewer turns than the smaller of free slots "
                           "and ready bots: " + " || ".join(_round_line(m) for m in short[:5]))
        if fallback and not on_fallback:
            return False, (f"{judged} claim rounds judged, and no bot started on a fallback login: the fallback this "
                           "scenario is about was never tested")
        note = f"; {waived} rounds inside declared waits were not judged" if waived else ""
        return True, (f"all {judged} claim rounds with a free slot and a ready bot started the smaller of the two; "
                      f"{on_fallback} of them started a bot on a fallback login{note}")
    return check


def _round_line(m):
    """One short round for a failing check: its time, its ready bots, its turns started, and the server's reasons."""
    ready = ", ".join(m["ready"][:6]) + (f" (+{len(m['ready']) - 6})" if len(m["ready"]) > 6 else "")
    text = f"{m['at']:%H:%M} free {m['free']}, ready {ready}, started {m['started_at']}"
    if m.get("why"):
        text += " [" + m["why"][:700] + "]"
    return text


def lead_heard_of(word):
    """Head of Engineering was told (a note or a wake) of something matching `word`."""
    def check(run):
        rows = run.w.sql("SELECT body FROM messages WHERE recipient_id='bot:engineering-lead' AND body LIKE ?",
                         (f"%{word}%",))
        return bool(rows), f"{len(rows)} messages mention {word!r}"
    return check


def late_told(run):
    """Every ticket late at a step was named to Head of Engineering: the step's late hours (the product's default for
    a working step, backend/step_age.LATE_HOURS) and one scheduler pass more. Sooner, a quiet ticket is its owner's
    to hear of (a status check), and a check that runs long is its timeout's."""
    from backend.step_age import LATE_HOURS
    rows = run.w.sql("SELECT body, refs FROM messages WHERE recipient_id='bot:engineering-lead'")
    told = " ".join((r["body"] or "") + (r["refs"] or "") for r in rows)
    named = {tid for _, tid in run.w.stuck_told}
    slack = 2 * run.scenario.tick_minutes / 60
    late = []
    for t in run.final:
        spans = run.w.step_spans(t["id"], until=run.end.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
        for name, span in spans:
            step = run.w.board.step(name)
            hours = step and not step.merged and (getattr(step, "late_hours", None)
                                                  or (LATE_HOURS if step.status == "doing" else None))
            if hours and span >= hours + slack and t["id"] not in told and t["title"] not in told \
                    and t["id"] not in named:
                late.append(t["title"])
                break
    return not late, ", ".join(late[:6])


def lead_only_scheduled(run):
    """Head of Engineering runs on its schedule (and when a person asks), never on routine notices."""
    other = [r for r in run.t.bot("engineering-lead").turns if r["action"] is None and r["news"] != "update"]
    kinds = Counter(r["news"] for r in other)
    return not other, f"{len(other)} turns on routine notices: " + ", ".join(f"{k} {n}" for k, n in kinds.most_common(4))


def no_fallbacks(kinds):
    """The board did these hand-offs itself: no bot or person had to (findings list each one)."""
    def check(run):
        found = [f for f in run.w.fallbacks if any(k in f[3] for k in kinds)]
        return not found, ", ".join(f"{f[2]}: {f[3]}" for f in found[:4]) + (f" (+{len(found) - 4})" if len(found) > 4 else "")
    return check


def every_major_planned(run):
    """Each major ticket got one plan from the architect before its PR, and nobody else woke the architect."""
    bad = []
    for t in run.final:
        plans = [c for c in run.w.checks(t["id"]) if c["title"].startswith("Plan:")]
        if "major" in t["labels"] and len(plans) != 1:
            bad.append(f"{t['title']} plans {len(plans)}")
    idle = [r for r in run.t.bot("architect").turns if r["action"] is None]
    if idle:
        bad.append(f"architect woken {len(idle)}x for nothing")
    return not bad, ", ".join(bad[:6])


def window_spend_under(cap):
    """The cap stops new turns, never running ones: no turn starts in a UTC half hour once the runs that started in that
    half hour and have finished cost the cap (what usage_limits counts). The window's total may pass the cap by what was
    already running; that is reported, not judged."""
    def window(stamp):
        return stamp[:14] + ("00" if stamp[14:16] < "30" else "30")

    def check(run):
        runs = [dict(r) for r in run.w.sql("SELECT started_at, finished_at, est_cost_usd FROM turns ORDER BY started_at")]
        per, late = Counter(), []
        for r in runs:
            here = window(r["started_at"])
            per[here[11:16]] += r["est_cost_usd"] or 0
            # Finished strictly before: a run ending in the same tick as this claim may end after it.
            spent = sum(x["est_cost_usd"] or 0 for x in runs if window(x["started_at"]) == here and x["finished_at"]
                        and x["finished_at"] < r["started_at"] and x is not r)
            if spent >= cap:
                late.append(f"{r['started_at'][11:16]} (${spent:.2f} spent)")
        return not late, (f"peak half hour ${max(per.values() or [0]):.2f}; "
                          + (f"{len(late)} turns started past the cap: {', '.join(late[:4])}" if late
                             else "no turn started past the cap"))
    return check


def company_cap(per_30m):
    def setup(run, w, team):
        from backend import usage_limits
        with w.store.transaction() as c:
            usage_limits.save_pool(c, usage_limits.COMPANY, {"per_30m_usd": per_30m, "hourly_usd": None,
                                                              "daily_usd": None, "weekly_usd": None,
                                                              "monthly_usd": None}, "human:ana")
        tickets(run, w, 10)
    return setup


BASE = [("tickets not merged by the end", REPORT, all_through),
        (f"tickets over {LONG_TICKET_HOURS} h, or a step over {LONG_STEP_HOURS} h", REPORT, long_running),
        ("merged tickets never Blocked", HOLD, never_blocked_after_merge),
        ("no call got a server error", None, no_server_error)]


# ---------------------------------------------------------------------------------------------- events
def outage(start, hours, slugs=None, kind="quota"):
    """Bots out from `start` for `hours`: on a usage limit (runs refused, jobs held until the reset) or with their
    computer off (nothing claimed)."""
    def during(run, w, team, i):
        now = w.now()
        for b in team.all:
            if slugs and b.slug not in slugs:
                continue
            if kind == "quota":
                b.limited_until = START + start + at(hours) if START + start <= now else None
            else:
                b.available = not (START + start <= now < START + start + at(hours))
    return during


def close_many(when, count):
    """A person triages the board: closes `count` On Deck tickets in one go."""
    def during(run, w, team, i):
        if w.now() != START + when:
            return
        closed = next(s.name for s in w.board.steps if s.status == "closed")
        for t in [t for t in w.tickets() if t["step"] == w.board.first().name][:count]:
            w.update(t["id"], step=closed)
    return during


def both(*events):
    def during(run, w, team, i):
        for event in events:
            event(run, w, team, i)
    return during


def stale_blocker(run, w, team, i):
    """A ticket's leftover blocker (an old lead task nobody closes) is linked to it just after it merges: a merged
    ticket is never put back on hold (its step's can_hold), it is reverted or fixed forward."""
    first = next(t for t in w.tickets() if t["title"] == "Ticket 1")
    if first["id"] not in run.merged_at or getattr(run, "blocker_added", False):
        return
    run.blocker_added = True
    old = w.post("tasks", {"owner": "engineering-lead", "title": "Restore an old check", "body": "Old."})
    old = old.get("task", old)
    w.post(f"tasks/{first['id']}/relations", {"task": old["id"], "kind": "blocked_by"})


def merge_outside(when):
    """A person merges a ticket's PR by hand while it is still being reviewed or fixed."""
    def during(run, w, team, i):
        if w.now() != START + when:
            return
        for t in w.tickets():
            step = w.board.step(t["step"])
            pr = w.pr_of(t["id"])
            if pr and step and (step.check or step.work) and not w.prs[pr]["merged"]:
                w.merge(pr)
                return
    return during


QUEUE_CHECKS = [("no turns on stale notices", QUEUE, no_stale_turns)]
BOARD_CHECKS = [("tickets assigned by the board", ASSIGN, no_fallbacks(["assigned"])),
                ("the board moves tickets itself", "verdict and merge rules", no_fallbacks(["moved to", "merged by hand"]))]
REVIEW_TIMEOUT = [("no ticket waits past a check's timeout", TIMEOUT, max_at_check())]
HOE_CHECKS = [("every late ticket named to Head of Engineering", HOE, late_told),
              ("Head of Engineering runs on its schedule only", HOE, lead_only_scheduled)]


def standard(*extra):
    return [*BASE, *QUEUE_CHECKS, *BOARD_CHECKS, *extra]


SCENARIOS = [
    Scenario("happy_path", "Six tickets, every bot answers well: assigned, R1, R2, QA, merge, release.",
             setup=lambda run, w, team: tickets(run, w, 6),
             checks=standard(("at most a fifth of turns are noise", QUEUE, noise_under(0.2)),
                             ("one review per round, no re-review", None, one_review_per_round), *HOE_CHECKS)),
    Scenario("review_changes", "Reviewers ask for changes most of the time: the builder fixes and moves on.",
             setup=lambda run, w, team: tickets(run, w, 6), styles={"reviewer": {"changes": 0.7}},
             checks=standard(("one review per round, no re-review", None, one_review_per_round))),
    Scenario("garbled_reviews", "Reviewers' replies are often garbled: no verdict line. It counts as comments.",
             setup=lambda run, w, team: tickets(run, w, 6), styles={"reviewer": {"garbled": 0.4, "changes": 0.3}},
             checks=standard(*REVIEW_TIMEOUT)),
    Scenario("silent_reviewer", "One reviewer never reports back: its reviews time out and the round is skipped.",
             setup=lambda run, w, team: tickets(run, w, 6), styles={"reviewer-gpt": {"silent": 1.0}},
             checks=standard(*REVIEW_TIMEOUT)),
    Scenario("fallback_model_stop", "Checks run on a fallback model stop with a note and leave the check open.",
             setup=lambda run, w, team: tickets(run, w, 6),
             styles={"reviewer": {"fallback_stop": 0.3}, "qa": {"fallback_stop": 0.3}},
             checks=standard(*REVIEW_TIMEOUT)),
    Scenario("quota_outage", "Every bot hits its usage limit for 3h while tickets are mid-flight and a person triages.",
             setup=lambda run, w, team: tickets(run, w, 14),
             during=both(outage(at(0, 30), 3), close_many(at(1), 4)), hours=16,
             checks=standard(("at most 3 turns per bot in the hour after", QUEUE, burst_after(at(3, 30), 3)))),
    Scenario("computer_off", "The builders' computer is off for 3h while reviews and QA finish.",
             setup=lambda run, w, team: tickets(run, w, 10),
             during=outage(at(0, 20), 3, slugs={"builder-claude", "builder-openai"}, kind="off"),
             checks=standard(("no run left waiting for a person", "uncertain runs", no_uncertain_runs),
                             ("at most 3 builder turns in the hour after", QUEUE,
                              burst_after(at(3, 20), 3, bots={"builder-claude", "builder-openai"})))),
    Scenario("bulk_triage", "A person closes 12 of 20 queued tickets at once.",
             setup=lambda run, w, team: tickets(run, w, 20), during=close_many(at(0, 5), 12), slots=2,
             checks=standard()),
    Scenario("merged_stale_blocker", "A ticket keeps an old open blocker; it merges and must still ship.",
             setup=lambda run, w, team: tickets(run, w, 3), during=stale_blocker, checks=standard()),
    Scenario("merged_outside", "A person merges a PR by hand mid-review: the ticket still goes on to release.",
             setup=lambda run, w, team: tickets(run, w, 4), during=merge_outside(at(2)), checks=standard()),
    Scenario("stale_head", "Reviewers sometimes judge an older head of the PR.",
             setup=lambda run, w, team: tickets(run, w, 6), styles={"reviewer": {"stale_head": 0.3, "changes": 0.4}},
             checks=standard(*REVIEW_TIMEOUT)),
    Scenario("flaky_ci", "CI fails a quarter of the time: the builder fixes it in its own PR, nothing is rerun.",
             setup=lambda run, w, team: tickets(run, w, 6), ci_fail=0.25, checks=standard()),
    Scenario("backlog_30", "Thirty tickets land on one builder.",
             setup=lambda run, w, team: tickets(run, w, 30, owner="builder-claude"), hours=16,
             checks=[("at most 4 tickets in work per builder", SLOTS, peak_doing(4)),
                     ("Head of Engineering hears of the backlog", HOE, lead_heard_of("backlog"))]),
    Scenario("qa_mixed", "QA fails a third of changes and can't test another third.",
             setup=lambda run, w, team: tickets(run, w, 8), styles={"qa": {"fail": 0.33, "cant_test": 0.33}},
             checks=standard()),
    Scenario("skip_qa", "QA fails half the changes; builders often tag the ticket to skip QA after a fail.",
             setup=lambda run, w, team: tickets(run, w, 8), styles={"qa": {"fail": 0.5},
                                                                     "builder": {"skip_check": 0.5}},
             checks=standard()),
    Scenario("off_script", "Reviewers sometimes try to move the ticket themselves (refused).",
             setup=lambda run, w, team: tickets(run, w, 6), styles={"reviewer": {"off_script": 0.3}},
             checks=standard(*REVIEW_TIMEOUT)),
    Scenario("engineering_budget", "A $5 half-hour budget for the team (below what it would spend): work slows to it, and still ships.",
             setup=company_cap(5), hours=24,
             checks=standard(("the $5 half-hour cap holds", None, window_spend_under(5)))),
    Scenario("forgetful_builder", "Builders often forget to hand a ticket on after fixes: the hourly sweep unsticks it.",
             setup=lambda run, w, team: tickets(run, w, 6), styles={"builder": {"forget_move": 0.4}},
             checks=standard(*HOE_CHECKS)),
    Scenario("stuck_ticket", "A builder's runs on one ticket keep crashing: Head of Engineering must notice.",
             setup=lambda run, w, team: tickets(run, w, 4),
             styles={"builder-claude": {"crash_titles": ["Ticket 1"]}}, checks=HOE_CHECKS),
    Scenario("architect", "A senior architect on a big model plans major tickets when builders ask, and only then.",
             setup=lambda run, w, team: tickets(run, w, 8), architect=True, major=0.4, hours=16,
             checks=standard(("major tickets planned once, architect never idle", None, every_major_planned))),
    Scenario("design_board", "Another team's board: a design step, one peer check, no QA, other step names.",
             setup=lambda run, w, team: tickets(run, w, 6), board=design_board,
             styles={"reviewer": {"changes": 0.4}}, checks=standard(*REVIEW_TIMEOUT)),
]



from .cases import SCENARIOS as _CASES      # evals/factory/cases/: one file per scenario

SCENARIOS += _CASES
