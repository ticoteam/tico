"""The simulation loop: every tick moves the clock, runs the real scheduler pass, lets each available bot claim and run
its queued turns, and records what the board and the queues look like."""

import datetime as dt
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import bots as B
from .board import dev_board
from .world import PRIMARY, Refusal, World
from backend.step_age import (
    LATE_HOURS as STUCK_HOURS,
)  # the product's late hours for a working step (world imports first)

PRICE = {"claude-opus-5-5": (4.0, 0.20, 20.0)}  # $ per million: uncached input, cached input, output
SHORT_ROUNDS = 40  # a run reads the reasons of at most this many short claim rounds (see Run.reasons)


@dataclass
class Team:
    builders: list = field(default_factory=list)
    reviewers: list = field(default_factory=list)
    qa: list = field(default_factory=list)
    architects: list = field(default_factory=list)
    others: list = field(default_factory=list)

    @property
    def all(self):
        return [*self.builders, *self.reviewers, *self.qa, *self.architects, *self.others]

    def bot(self, slug):
        return next(b for b in self.all if b.slug == slug)


TEAM = {  # slug: (role, family)
    "builder-claude": ("builder", "claude"),
    "builder-openai": ("builder", "gpt"),
    "reviewer-gpt": ("reviewer", "gpt"),
    "reviewer-xai": ("reviewer", "grok"),
    "reviewer-muse": ("reviewer", "muse"),
    "qa-claude": ("qa", "claude"),
    "qa-xai": ("qa", "grok"),
    "release-manager": ("other", "claude"),
    "engineering-lead": ("lead", "claude"),
}
ARCHITECT = {"architect": ("architect", "claude")}


def login_ready(held, cooling, fallback):
    """Whether a bot may start by its logins, as the server's claim decides (execution._claim_refusal): while a hold
    keeps its own login (`held`), a free fallback takes the turn; otherwise its own cooldown decides, whatever fallback
    is free."""
    return fallback if held else not cooling


class Run:
    """One scenario run: the world, the team and everything measured."""

    def __init__(self, scenario, seed):
        self.scenario, self.seed = scenario, seed
        self.rng = random.Random(seed)
        self.samples = []  # per tick: {bot: queued}
        self.violations = defaultdict(list)
        self.merged_at = {}  # ticket id -> when it first reached a merged step
        self.peak_doing = Counter()
        self.claims = Counter()  # claim replies by HTTP status: an exception the app raised counts as a 500
        self.paused = set()  # bots the scenario paused: the work they hold waits on their owner, not the board
        self.marks = []  # per runner claim round: free slots, the bots it could hand out, turns started
        self.lost_probe = None
        self.probe_seen = set()  # probe jobs a login's hold named after a round's claims, read from the hold records
        self.back_check = None  # set by the scenario when a computer comes back: its readiness is read on that tick
        self.back_lines = []  # what the server held for the returning computer's bot on its first round back
        self.short_rounds = 0  # short rounds whose reasons were read (at most SHORT_ROUNDS)
        self.stale_busy = None  # a bot a scenario keeps naming busy for its first rounds (see stale_round)
        self.stale_named = None  # the bot stale_round names busy in the current claim round, if any
        self.stuck = {}  # task id -> the stuck stretch found on it: title, step, owner, status, idle hours, since
        self.idle = (
            Counter()
        )  # task id -> hours it has waited with its owner free and nothing on it since its last event
        self.acted = {}  # task id -> when a turn last did something on it (a turn that did nothing is no event)
        self.resume_trace = {}  # task id -> [(when, resume_why answer)] at each change of the answer (diagnostic only)
        self.resume_last = {}  # task id -> the last answer put in resume_trace
        self.seen_turns = Counter()  # bot slug -> turns stuck_tick has read
        self.tick_at = None  # when the last tick was observed
        self.plate_peak = 0  # the most open items on the owner's plate at any tick (the questions and tasks she owns)

    def team(self, w):
        styles = self.scenario.styles
        team = Team()
        made = {}
        for slug, (role, family) in self.members().items():
            rng = random.Random(f"{self.seed}:{slug}")
            style = styles.get(slug, styles.get(role, {}))
            kind = {
                "builder": B.Builder,
                "reviewer": B.Reviewer,
                "qa": B.QA,
                "lead": B.Lead,
                "architect": B.Architect,
            }.get(role, B.Quiet)
            made[slug] = kind(
                w,
                slug,
                role,
                rng,
                style=style,
                family=family,
                **({"slots": self.scenario.slots} if role == "builder" else {}),
            )
        team.reviewers = [b for b in made.values() if b.role == "reviewer"]
        team.qa = [b for b in made.values() if b.role == "qa"]
        team.builders = [b for b in made.values() if b.role == "builder"]
        team.architects = [b for b in made.values() if b.role == "architect"]
        team.others = [b for b in made.values() if b.role in ("other", "lead")]
        for b in team.builders:
            b.architects = team.architects
        return team

    def members(self):
        fleet = {f"fleet-{n + 1:02d}": ("other", "claude") for n in range(self.scenario.fleet)}
        return {**TEAM, **(ARCHITECT if self.scenario.architect else {}), **fleet}

    def go(self):
        s = self.scenario
        board = s.board()
        with World(
            board,
            self.members(),
            tick_minutes=s.tick_minutes,
            rng=random.Random(f"{self.seed}:world"),
            ci_fail=s.ci_fail,
            signed_in=s.signed_in,
            capacity=s.capacity,
            profiles=s.profiles,
        ) as w:
            self.w = w
            team = self.team(w)
            self.t = team
            s.setup(self, w, team)
            ticks = int(s.hours * 60 / s.tick_minutes)
            drain = int(s.drain_hours * 60 / s.tick_minutes)
            for i in range(ticks + drain):
                # Past its hours the run only drains: it stops once every ticket has merged. "Every ticket ships"
                # asks whether a ticket got stuck, not whether the team finished in the scenario's hours.
                if i >= ticks and all(t["id"] in self.merged_at or self.shipped(t) for t in w.tickets()):
                    break
                s.arrive(self, w, team, i)
                w.advance(
                    ready=[b.slug for b in team.all if b.available],
                    beat=lambda: [b.renew() for b in team.all if b.available],
                )
                ready_before = {b.slug for b in team.all if b.available}
                s.during(self, w, team, i)
                if w.down:  # the server is stopped: the runner's calls wait for it, so this tick claims nothing
                    continue
                ready_now = [b.slug for b in team.all if b.available]
                if set(ready_now) != ready_before:
                    # A computer that went off or came back is reported before this tick's claims: the readiness
                    # the server holds is the one advance() sent, from before the change.
                    w.heartbeat(ready_now)
                self.release(w, i)
                self.assign(w, team)
                w.scheduler_pass()
                # A parked turn asks for its computer place again before this tick's claims, the earliest parked first:
                # the server grants places in claim order (start_places.reserve).
                for bot in sorted((b for b in team.all if b.parked and b.available), key=lambda b: b.parked_since):
                    bot.retry_start()
                bots = list(team.all)
                self.rng.shuffle(bots)
                for bot in bots:
                    if bot.running and not bot.available:
                        bot.lose()  # its computer went off under the run: the lease lapses
                    if bot.available:
                        bot.recover()
                    if s.claim == "bot":
                        self.bot_turns(w, bot)
                    elif bot.due(w.now()):
                        bot.end()
                if s.claim == "runner":
                    self.sweep(w)
                    self.runner_round(w, team)
                self.observe(w, team)
            self.final = w.tickets()
            self.end = w.now()
            return self

    def bot_turns(self, w, bot):
        """One bot's turns under per-bot claims: finish the running one, then claim the next while the bot is free."""
        for _ in range(8):
            if bot.due(w.now()):
                bot.end()
            if bot.running or not bot.available:
                break
            _, reply = self.claim_once(w, {"bot": bot.slug, "start_places": True})
            attempt = reply.get("attempt") if isinstance(reply, dict) else None
            if not attempt:
                break
            bot.start(attempt)

    def sweep(self, w):
        """The history sweep (read_deps.sweep), run once per tick. The server's loop runs it every 10 seconds; here a
        tick is the step the claim rounds run on, so each round meets the index the sweep kept on that tick."""
        from backend import read_deps

        with w.store.read() as c:
            read_deps.sweep(c, gate=w.store.writer)

    def runner_round(self, w, team):
        """One claim round as a real runner makes it: while it has a free slot it asks for work, naming the bots it is
        running, and each claim's body is built fresh (runner.rs claim_next). The round is judged on the records after
        its claims, never on a claim's answer: the bots it could hand out are the queued ones that are available, not
        running and not paused, less the jobs the claims took off the queue and the obsolete keeper notices the server's
        own rule names. A bot is ready when the records say a login it can run on is free (readiness). A round that
        starts fewer turns than its free slots and ready bots is short, and its reasons are read from the server
        (reasons)."""
        self.stale_round(w.now())
        body = self.runner_body(team)
        free = w.capacity - self.places_used(team)
        if self.back_check:
            # The computer's readiness as the heartbeat before this round's claims told the server it (the sim sends
            # that heartbeat once the scenario changes who is available).
            self.back_lines.append(self.back_read(w, self.back_check["slug"]))
            self.back_check = None
        pool = self.pool(team, body)
        before, held = self.queued_before(w, pool)
        started, started_bots, started_jobs, answers, fallback = 0, [], [], [], 0
        claims = 0
        # The claims go on while the computer holds fewer places than its capacity. A parked turn holds no place, so a
        # round may claim past its free places, up to the server's bound on leased work (runner-rs runner.rs claim_next).
        while claims < 2 * w.capacity and self.places_used(team) < w.capacity:
            claims += 1
            body = self.runner_body(team)
            _, reply = self.claim_once(w, body)
            answers.append(reply)
            attempt = reply.get("attempt") if isinstance(reply, dict) else None
            if not attempt:
                break
            bot = team.bot(attempt["bot"])
            if bot.start(attempt):
                started += 1
            started_bots.append(bot.slug)
            started_jobs.append(attempt.get("job_id"))
            fallback += bot.runtime != PRIMARY
        queued = self.queued_now(w, pool)
        candidates = sorted(set(queued.values()) | set(started_bots))
        ready, blocked = self.readiness(w, candidates, started_bots)
        mark = {
            "at": w.now(),
            "free": free,
            "held": held,
            "started_at": started,
            "fallback": fallback,
            "queued": len(candidates),
            "slugs": candidates,
            "ready": ready,
            "blocked": blocked,
            "started_bots": list(started_bots),
            "cap_held": {},
            "cap_seen": [],
        }
        if free and ready and started < min(free, len(ready)):
            mark["cap_held"], mark["cap_seen"] = self.cap_holds(w, [b for b in ready if b not in started_bots])
        if free and ready and started < min(free, len(ready)) and self.short_rounds < SHORT_ROUNDS:
            self.short_rounds += 1
            gone = [job for job in before if job not in queued and job not in started_jobs]
            lines = self.reasons(w, [b for b in candidates if b not in started_bots], blocked)
            lines.append(
                f"{len(gone)} job(s) left the queue during the claims "
                f"({', '.join(sorted({before[job] for job in gone})) or 'none'}); the last claim answered "
                f"{answers[-1] if answers else None}"
            )
            lines.extend(self.turn_states(w, team, [b for b in ready if b not in started_bots]))
            mark["why"] = " | ".join(lines)
        self.marks.append(mark)

    def turn_states(self, w, team, slugs):
        """Diagnostic only, read on a short round: for each ready bot the round did not start, the sim's own turn for it
        (running, parked, since when) and the server's newest attempt for it (id, state, lease, stop, and the start
        place's wait reason). A bot the sim names busy in the claim body is refused by the claim, whatever readiness says."""
        from backend import bot_settings as BS
        from backend import lifecycle
        from backend.claim_holds import CHECK
        from backend.execution import (
            CAPACITY_CONTINUE,
            RUNNER_SQL,
            SOURCE_CONTINUE,
            capability_set,
            continuation_thread,
            scoped_sql,
            unfenced_sql,
        )

        lines = []
        execution = w.app.state.execution
        with w.store.read() as c:
            runner = c.execute(RUNNER_SQL, (w.runner["runner_id"],)).fetchone()
            caps = capability_set(runner["capabilities"]) if runner else frozenset()
            for slug in slugs:
                bot = team.bot(slug)
                row = c.execute(
                    "SELECT a.id,a.state,a.lease_until,a.stop_requested_at,p.wait_reason,p.start_until,a.started_at,"
                    "a.thread_id,json_extract(a.result,'$.lease_continue'),json_extract(a.result,'$.quota_continue') "
                    "FROM attempts a LEFT JOIN start_places p ON p.attempt_id=a.id "
                    "WHERE a.bot_id=? ORDER BY a.created_at DESC,a.id DESC LIMIT 1",
                    (slug,),
                ).fetchone()
                health = c.execute("SELECT last_error FROM service_health WHERE service=?", (CHECK + slug,)).fetchone()
                queued = c.execute(
                    "SELECT id,held,redirect FROM jobs WHERE bot_id=? AND state='queued' "
                    "ORDER BY created_at,id LIMIT 1",
                    (slug,),
                ).fetchone()
                thread = continuation_thread(c, queued[0]) if queued else None
                predicates = ""
                if queued:
                    # The claim's own predicates for this bot's oldest queued job, in the order candidate() applies them.
                    job, rid = queued[0], w.runner["runner_id"]
                    checks = [
                        (
                            "assigned to this runner and bot active",
                            c.execute(
                                "SELECT EXISTS(SELECT 1 FROM assignments a JOIN bots b ON b.id=a.bot "
                                f"JOIN bot_config bc ON bc.bot=a.bot {BS.JOIN} "
                                "WHERE a.bot=? AND a.runner_id=? AND b.state='active')",
                                (slug, rid),
                            ).fetchone()[0],
                        ),
                        (
                            "claim message state",
                            c.execute(
                                "SELECT EXISTS(SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id "
                                "JOIN conversations cv ON cv.id=m.conversation_id "
                                f"WHERE j.id=? AND {lifecycle.claim_message_sql()})",
                                (job,),
                            ).fetchone()[0],
                        ),
                        (
                            "interrupted-work fence and scope",
                            c.execute(
                                "SELECT EXISTS(SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id "
                                "JOIN conversations cv ON cv.id=m.conversation_id "
                                f"WHERE j.id=? AND {unfenced_sql()} AND {scoped_sql()})",
                                (job,),
                            ).fetchone()[0],
                        ),
                        ("not named busy in the claim body", not bot.running),
                    ]
                    failed = [name for name, ok in checks if not ok]
                    predicates = (
                        f"; oldest job {job[:8]} predicates "
                        + ", ".join(f"{name} {bool(ok)}" for name, ok in checks)
                        + f"; dropped at: {failed[0] if failed else 'none, every predicate passes'}"
                    )
                lines.append(
                    f"{slug} turn: sim running {bot.running}, parked {bot.parked}, available {bot.available}; "
                    f"server newest attempt {tuple(row) if row else None}; claim check backoff "
                    f"{execution.check_backoff.get(slug)} waiting {execution.check_waiting(slug)} "
                    f"health {health[0] if health else None}; oldest queued job {tuple(queued) if queued else None} "
                    f"continues thread {thread} (runner continues capacity {CAPACITY_CONTINUE in caps}, "
                    f"source {SOURCE_CONTINUE in caps}){predicates}"
                )
        return lines

    def cap_holds(self, w, bots):
        """The bots a usage cap holds that the records show reached: ({bot: {scope, period, limit, spent}}, seen). The
        server holds a bot on a limit (usage_limits.over) only when its window's spend has reached the limit. This reads
        that spend again from the usage records the caps are counted from (usage_records.RUNS, the runs the window's
        spend is summed over, with the company's subscription setting), in its own query, and keeps a bot only when the
        spend is at or over the limit. A hold the records do not show is not kept, so its round stays judged. `seen`
        says, for each bot the caps name, what the hold reads and what the records sum to, for a failing round."""
        from backend import usage_limits
        from backend.usage_records import RUNS

        held, seen = {}, []
        if not bots:
            return held, seen
        with w.store.read() as c:
            subscription = bool(usage_limits.company(c)["count_subscription"])
            for bot in bots:
                found = usage_limits.over(c, bot)
                if not found or found.get("scope") == "budget" or not found.get("limit"):
                    seen.append(f"{bot} no usage hold")
                    continue
                scope, period = found["scope"], found["period"]
                _, start, end = usage_limits.bounds(None)[period]
                sql = (
                    f"SELECT coalesce(sum(est_cost_usd), 0) FROM {RUNS} WHERE started_at >= ? AND started_at < ? "
                    "AND (? OR coalesce(billing, 'api') != 'subscription')"
                )
                params = [start, end, 1 if subscription else 0]
                if scope == "bot":
                    sql += " AND bot = ?"
                    params.append(bot)
                elif scope != usage_limits.COMPANY:
                    members = sorted(usage_limits.members(c, scope))
                    if not members:
                        seen.append(f"{bot} {scope} has no members")
                        continue
                    sql += " AND bot IN (" + ",".join("?" * len(members)) + ")"
                    params += members
                amount = float(c.execute(sql, params).fetchone()[0] or 0.0)
                seen.append(f"{bot} {scope} {period} limit ${found['limit']:g}, records ${amount:.2f}")
                if amount + 1e-9 >= found["limit"]:
                    held[bot] = {"scope": scope, "period": period, "limit": found["limit"], "spent": round(amount, 2)}
        return held, seen

    def places_used(self, team):
        """The computer's places in use: its running turns, less the parked ones, which hold no place."""
        return sum(1 for b in team.all if b.running and not b.parked)

    def pool(self, team, body):
        """The bots a round may hand work to: available on this computer, not running a turn (its lane is busy), not
        named busy by the body, and not paused by their owner."""
        return sorted(
            b.slug
            for b in team.all
            if b.available and not b.running and b.slug not in body["busy_bots"] and b.slug not in self.paused
        )

    def queued_before(self, w, bots):
        """Before a round's claims: the queued jobs of `bots` that are not held, as {job: bot}, and how many are
        held."""
        if not bots:
            return {}, 0
        marks = ",".join("?" * len(bots))
        with w.store.read() as c:
            rows = c.execute(
                f"SELECT id,bot_id,held FROM jobs WHERE state='queued' AND bot_id IN ({marks})", bots
            ).fetchall()
        return {r["id"]: r["bot_id"] for r in rows if not r["held"]}, sum(1 for r in rows if r["held"])

    def queued_now(self, w, bots):
        """After a round's claims: the queued jobs of `bots` that are not held and not an obsolete keeper notice. The
        server's own rule (execution.obsolete_notices_sql) names those for this computer; a claim cancels only a few of
        them per call, so one still queued is not work the round could hand out."""
        from backend import execution as X
        from backend.store import H

        if not bots:
            return {}
        marks = ",".join("?" * len(bots))
        with w.store.read() as c:
            obsolete = {
                r[0]
                for r in c.execute(
                    "SELECT j.id " + X.obsolete_notices_sql(c), (w.runner["runner_id"], H.KEEPER, H.now())
                )
            }
            rows = c.execute(
                f"SELECT id,bot_id FROM jobs WHERE state='queued' AND held=0 AND bot_id IN ({marks})", bots
            ).fetchall()
        return {r["id"]: r["bot_id"] for r in rows if r["id"] not in obsolete}

    def readiness(self, w, bots, started):
        """Each candidate bot's readiness now, from the records and never from a claim's answer. A bot is ready when its
        computer is awake (execution.Execution.awake), and a login it can run on is free: its own, when no hold keeps it
        (login_holds.holding: a hold before its end, or a probe out after it) and it is not cooling off
        (execution.cooling_off), or a fallback login the computer reports signed in that no hold keeps. A bot a usage
        limit holds is not ready either: the product's own check (usage_limits.blocked, which the claim refuses with
        "limit") says it cannot start, so a round it passes over is no stall. A bot that started this round was ready.
        Returns (ready, blocked): blocked maps each other bot to what the records keep it from. Each probe a login's hold
        names is recorded in probe_seen, read after the round's claims, so a probe the round started is seen too."""
        from backend import execution as X, login_holds, usage_limits
        from backend.store import H

        rid = w.runner["runner_id"]
        ready, blocked = [], {}
        with w.store.read() as c:
            now = H.now()
            pools = usage_limits.company(c)  # one Default for every bot's check, so the pool reads are shared
            runner = c.execute(X.RUNNER_SQL, (rid,)).fetchone()
            awake = bool(runner) and X.Execution.awake(X.Execution.waking(runner, now), now)
            login = login_holds.canonical(c, rid, "", PRIMARY)
            own = login_holds.holding(c, rid, login, PRIMARY, now=now)
            probe = login_holds.get(c, rid, login, PRIMARY).get("probe_job")
            prober = c.execute("SELECT bot_id FROM jobs WHERE id=?", (probe,)).fetchone() if probe else None
            fallbacks = []
            for name in w.runtime_fallbacks():
                if name != PRIMARY and name in w.signed_in:
                    login = login_holds.canonical(c, rid, "", name)
                    if login_holds.holding(c, rid, login, name, now=now) is None:
                        fallbacks.append(name)
            for hold in login_holds.on_runner(c, rid):
                if hold.get("probe_job"):
                    self.probe_seen.add(hold["probe_job"])
            for bot in bots:
                if bot in started:
                    ready.append(bot)
                elif not awake:
                    blocked[bot] = "computer waking"
                elif capped := usage_limits.blocked(c, bot, default=pools):
                    # The same check the claim makes before it looks at a bot's work (execution._claim_refusal "limit").
                    blocked[bot] = f"{capped} usage limit"
                else:
                    # The probe's own bot is let through its login's probe (execution.login_wait).
                    held = None if own == "probing" and prober and prober["bot_id"] == bot else own
                    cooling = X.cooling_off(c, bot)
                    if login_ready(held, cooling, bool(fallbacks)):
                        ready.append(bot)
                    else:
                        blocked[bot] = ", ".join(
                            ([f"own login {held}"] if held else [])
                            + (["cooling off"] if cooling else [])
                            + ([] if fallbacks else ["no fallback free"])
                        )
        return sorted(ready), blocked

    def reasons(self, w, bots, blocked=None):
        """Diagnostic only, read on a short round after its claims: the computer's gates (turns held against its
        capacity, places, starts waiting, overload, pause, company waits), then for each bot the round did not start the
        server's own reason for its oldest queued job (execution.queued_reasons: the bot's refusal, then the job's, then
        its login's wait) and the readiness the records gave it. No check judges these lines."""
        from backend import execution as X, performance, runner_versions, start_places

        rid = w.runner["runner_id"]
        blocked = blocked or {}
        lines = []
        with w.store.read() as c:
            try:
                cap = c.execute("SELECT capacity FROM runners_with_state WHERE id=?", (rid,)).fetchone()
                lines.append(
                    f"computer: turns held {sum(X.turns_here(c, rid).values())} "
                    f"of capacity {cap['capacity']}, "
                    f"places held {sum(start_places.held(c, rid).values())}, starts waiting "
                    f"{start_places.ready_waiting(c, rid)}, overloaded "
                    f"{performance.load_status(c, rid)['overloaded']}, paused "
                    f"{runner_versions.paused(c, rid)}, company waits "
                    f"{c.execute('SELECT count(*) FROM org_turn_waits').fetchone()[0]}"
                )
            except Exception as error:  # a diagnostic never stops the run
                lines.append(f"computer: diagnostic error {error!r}")
            for bot in bots:
                try:
                    first = next(iter(X.queued_reasons(c, w.store.settings, rid, bot, oldest=True)), None)
                    text = (
                        f"{bot} oldest job {first[0]['id'][:6]} {first[1] or 'hands out'}"
                        if first
                        else f"{bot} has no queued job"
                    )
                    lines.append(text + (f"; readiness {blocked[bot]}" if bot in blocked else ""))
                except Exception as error:  # a diagnostic never stops the run
                    lines.append(f"{bot}: diagnostic error {error!r}")
        return lines

    def back_read(self, w, slug):
        """Diagnostic only: the readiness the server holds for one bot of this computer, as its heartbeat last reported
        it, with the reasons its queued work gets on this round."""
        from backend.execution import readiness_document

        with w.store.read() as c:
            row = c.execute("SELECT readiness FROM runners_with_state WHERE id=?", (w.runner["runner_id"],)).fetchone()
        doc = readiness_document(row["readiness"]) if row else {}
        ready = ((doc.get("bots") or {}).get(slug) or {}).get("ready")
        return {"at": w.now(), "bot_id": slug, "server_ready": ready, "reasons": self.reasons(w, [slug])}

    def stale_round(self, now):
        """Once per claim round, before its claims: a scenario's stale bot (`stale_busy`) stays named busy for its
        first rounds, as a runner that has not rebuilt its list after a restart still names it. The first round after
        those is when the list is right again, and `now` is that round's time."""
        stale = self.stale_busy
        self.stale_named = None
        if not stale:
            return
        if stale["rounds"] > 0:
            stale["rounds"] -= 1
            stale["sent"] += 1
            self.stale_named = stale["bot_id"]
        elif stale["sent"] and stale["corrected_at"] is None:
            stale["corrected_at"] = now

    def runner_body(self, team):
        """The body runner-rs claims with (runner.rs claim_next): no bot named; `busy_bots` the bots it is still running
        (its list on a server without lanes), and `busy_lanes` the lanes with a live worker, each running turn's bot on
        its one lane. A computer that is off names nothing: the server holds its bots by readiness. The sim advertises
        no lanes. The stale bot of stale_round is named too while it is set."""
        busy = sorted({b.slug for b in team.all if b.running} | ({self.stale_named} if self.stale_named else set()))
        return {
            "next_run": True,
            "busy_bots": busy,
            "busy_lanes": [f"{slug}|main" for slug in busy],
            "start_places": True,
        }

    def claim_once(self, w, body):
        """One claim, its status counted. A 5xx is recorded by the world (with its body, or the traceback when the app
        raised) and the claim answers nothing; any other refusal is the sim's own fault and stops the run."""
        status, reply = w.request("post", "jobs/claim", body, token=w.runner["token"])
        self.claims[status] += 1
        if status >= 500:
            return status, None
        if status >= 400:
            raise Refusal(status, str(reply))
        return status, reply

    def release(self, w, i):
        """The Release Manager bot ships every few hours: each ticket at a release step moves to the next step. Its
        moves cost no turn here: the run measures the engineering queue, not the release."""
        if i % int(self.scenario.release_every_hours * 60 / self.scenario.tick_minutes) != 0 or i == 0:
            return
        board = w.board
        for t in w.tickets():
            step = board.step(t["step"])
            if step and step.release:
                try:
                    w.update(t["id"], token="release-test", step=board.steps[board.steps.index(step) + 1].name)
                except Refusal as exc:
                    # An open question its builder put on the ticket: only the builder or its manager may close it, and
                    # the person answers it. The ticket waits at the release step, as it would for the person.
                    if exc.status != 403 or "open subtask" not in exc.body:
                        raise

    def assign(self, w, team):
        """An unowned ticket at an assigning step waits for the board to give it to a bot of that role. After 30
        minutes a person does it instead, and the run records the fallback."""
        for t in w.tickets():
            step = w.board.step(t["step"])
            if not step or not step.assign or str(t["owner_id"]).startswith("bot:") or t["status"] in B.FINISHED:
                continue
            if (w.now() - w.H.parse_ts(t["created_at"])).total_seconds() < 30 * 60:
                continue
            pool = [b for b in team.all if b.role == step.assign and w.slots(b.slug) < b.slots]
            if not pool:
                continue
            owned = Counter(x["owner_id"] for x in w.tickets() if not self.shipped(x))
            pick = min(pool, key=lambda b: (w.slots(b.slug), owned["bot:" + b.slug], b.slug))
            w.update(t["id"], owner="bot:" + pick.slug)
            w.fallback("person", t["id"], f"assigned to {pick.slug} by hand")

    def observe(self, w, team):
        now = w.now()
        self.samples.append((now, w.queued()))
        merged = w.board.merged_steps()
        for t in w.tickets():
            if t["step"] in merged and t["id"] not in self.merged_at:
                self.merged_at[t["id"]] = now
            if t["id"] in self.merged_at and (t["step"] == w.board.hold_step or t["blocked_since"]):
                self.violations["merged_blocked"].append((now, t["title"]))
        for b in team.builders:
            self.peak_doing[b.slug] = max(self.peak_doing[b.slug], w.slots(b.slug))
        self.stuck_tick(w, team)
        self.trace_resume(w)
        plate = w.sql(
            "SELECT count(*) n FROM tasks WHERE owner_id IN ('human:ana','ana') AND status IN ('open','doing','review')"
        )
        self.plate_peak = max(self.plate_peak, plate[0]["n"])

    def stuck_tick(self, w, team):
        """The stuck-work invariant, once per tick, over every open or doing task with a bot as its owner (a ticket, a
        review or QA subtask, a lead's task). Such a task counts the time it waits while its owner is free: no turn
        running, no job queued for the owner, its computer on, not paused, not on a usage limit. Any event on the task
        (a history event, a change to it, a turn that did something on it) since the last tick restarts the count, so
        only a stretch with no event at all is stuck. A task waiting on a hold (Blocked, snoozed), on its subtasks, or on
        its owner's full slots is not counted. Reaching STUCK_HOURS (or the scenario's allowance for it) records the task
        once in `stuck`."""
        H, now, hours = w.H, w.now(), self.scenario.tick_minutes / 60
        for b in team.all:
            for r in b.turns[self.seen_turns[b.slug] :]:
                if r.get("task") and r.get("action"):
                    self.acted[r["task"]] = r["at"]
            self.seen_turns[b.slug] = len(b.turns)
        events = {
            r["subject_id"]: H.parse_ts(r["at"])
            for r in w.sql(
                "SELECT subject_id, max(created_at) at FROM history_events WHERE subject_type='task' GROUP BY subject_id"
            )
        }
        held = {
            r["task_id"]
            for r in w.sql(
                "SELECT task_id FROM task_holds WHERE released_at IS NULL AND (kind='blocked' OR (kind='snoozed' AND until_at>?))",
                (H.now(),),
            )
        }
        parents = {
            r["to_task"]
            for r in w.sql(
                "SELECT r.to_task FROM task_relations r JOIN tasks c ON c.id=r.from_task "
                "WHERE r.kind='parent' AND c.status IN ('open','doing','review')"
            )
        }
        # A held job will not run, so it does not make its bot busy: the bot's other tasks still count as waiting.
        queued = {
            r["bot_id"] for r in w.sql("SELECT DISTINCT bot_id FROM jobs WHERE state='queued' AND coalesce(held,0)=0")
        }
        bots = {b.slug: b for b in team.all}
        live = set()
        for row in w.sql(
            "SELECT id,title,owner_id,status,step_id,updated_at FROM tasks WHERE status IN ('open','doing') AND owner_id LIKE 'bot:%'"
        ):
            bot = bots.get(row["owner_id"][4:])
            if bot is None:
                continue
            tid = row["id"]
            live.add(tid)
            last = max(x for x in (events.get(tid), H.parse_ts(row["updated_at"]), self.acted.get(tid)) if x)
            if self.tick_at is not None and last > self.tick_at:
                self.idle[tid] = 0.0  # something happened on it since the last tick
            elif self.waiting(w, row, bot, now, held, parents, queued) and not self.day_capped(w, tid):
                self.idle[tid] += hours
            limit = self.limit_for(row["title"], bot.slug)
            if limit is not None and self.idle[tid] >= limit - 1e-6 and tid not in self.stuck:
                self.stuck[tid] = {
                    "title": row["title"],
                    "step": w.step_of(row),
                    "owner": bot.slug,
                    "status": row["status"],
                    "idle": self.idle[tid],
                    "since": now,
                    "why": self.resume_why(w, tid),
                }
        for tid in list(self.idle):
            if tid not in live:
                del self.idle[tid]
        self.tick_at = now

    def trace_resume(self, w):
        """Diagnostic only: each tick, the resume's answer (resume_why) for every bot task in progress, kept whenever the
        answer changes, so a late pickup shows what held the task through the hours before it."""
        import re

        now = w.now()
        for r in w.sql("SELECT tasks.id FROM tasks WHERE tasks.status='doing' AND tasks.owner_id LIKE 'bot:%'"):
            why = self.resume_why(w, r["id"])
            key = re.sub(r"[\d.]+", "#", why)  # minutes and amounts move every tick; the answer is what changes
            if self.resume_last.get(r["id"]) != key:
                self.resume_last[r["id"]] = key
                self.resume_trace.setdefault(r["id"], []).append((now, why))

    def resume_why(self, w, tid):
        """Diagnostic only: the first condition of the stranded resume (backend/stranded_resume.py) that holds this task
        back now, read in a read of the store with the product's own helpers, so the answer is the sweep's."""
        from backend import auto_tasks, check_pools, task_holds
        from backend import hubdb as H
        from backend import stranded_resume as SR

        with w.store.read() as c:
            row = H.task(c, tid)
            if row is None:
                return "task gone"
            slug = H.actor_id(row["owner_id"])
            if not SR._bot_ready(c, slug):
                jobs = c.execute(
                    f"SELECT count(*) FROM jobs j WHERE j.bot_id=? AND j.state IN ('queued','leased','running') "
                    f"AND {H.unheld_sql()}",
                    (slug,),
                ).fetchone()[0]
                drain = c.execute("SELECT 1 FROM bot_control WHERE bot=? AND draining=1", (slug,)).fetchone()
                return (
                    f"owner not ready: online={check_pools.online(c, slug)}, draining={drain is not None}, "
                    f"unheld jobs={jobs}"
                )
            at = H.now()
            if not SR.stranded(c, row, at):
                reasons = []
                if task_holds.with_holds(c, row) is None:
                    reasons.append("hold")
                if row.get("blocked_since") or (row.get("snoozed_until") and row["snoozed_until"] > at):
                    reasons.append("blocked or snoozed")
                if row.get("next_run") or row.get("resume") or row.get("carried_by"):
                    reasons.append("carried or next run or resume recorded")
                if row.get("check_back_at") and row["check_back_at"] > at:
                    reasons.append("check-back ahead")
                if not H.bot_reads_task(c, row["owner_id"], row):
                    reasons.append("owner does not read it")
                if SR._live(c, row):
                    reasons.append("a job or run is live")
                if c.execute(
                    "SELECT 1 FROM quarantine_holds WHERE bot_id=? AND task_id=? AND released_at IS NULL LIMIT 1",
                    (slug, tid),
                ).fetchone():
                    reasons.append("quarantined")
                if auto_tasks.waiting(c, row):
                    reasons.append("auto-task waiting")
                if H.recorded_wait(c, {**row, "due_at": None}, at) is not None:
                    reasons.append("recorded wait")
                return "not stranded: " + (", ".join(reasons) or "no single condition (see stranded_resume.stranded)")
            found = SR.state(c, row, at)
            if found is None:
                return "stranded, not resumed: " + self.quiet_parts(c, row, at, slug)
            return f"stranded and ready: resumes in a row with no progress={found['since']}, stop={found['stop']}"

    def quiet_parts(self, c, row, at, slug):
        """Diagnostic only: the parts of the stranded resume's quiet time (backend/stranded_resume.py `state`), each as
        minutes before `at`, and the newest of them, which blocks the resume while it is under QUIET_MINUTES."""
        import sqlite3

        from backend import hubdb as H
        from backend import stranded_resume as SR

        try:
            tid = row["id"]
            progress = SR._progress(c, row)
            resumes = [
                r[0]
                for r in c.execute(
                    "SELECT created_at FROM history_events WHERE subject_type='task' AND subject_id=? AND kind=? "
                    "ORDER BY created_at",
                    (tid, SR.RESUMED),
                )
            ]
            last_run = c.execute(
                "SELECT max(coalesce(settled_at, finished_at, created_at)) FROM attempts WHERE bot_id=? AND task_id=?",
                (slug, tid),
            ).fetchone()[0]
            others = c.execute(
                "SELECT max(created_at) FROM messages WHERE conversation_id=? AND deleted_at IS NULL "
                "AND sender_id IS NOT NULL AND sender_id NOT IN (?,?)",
                (row["conversation_id"], H.KEEPER, row["owner_id"]),
            ).fetchone()[0]
            parts = {
                "task row": row["updated_at"],
                "progress": progress or None,
                "last run": last_run,
                "others' messages": others,
                "last resume": resumes[-1] if resumes else None,
            }
            now = H.parse_ts(at)
            ages = {k: round((now - H.parse_ts(v)).total_seconds() / 60, 1) for k, v in parts.items() if v}
            day = sum(1 for r in resumes if r >= H.shift(at, hours=-24))
            if not ages:
                return f"no quiet-time part; resumes in 24 h {day}"
            newest = min(ages, key=lambda k: ages[k])
            return f"newest part {newest} {ages[newest]} min before; parts (min before): {ages}; resumes in 24 h {day}"
        except (
            sqlite3.Error,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:  # a diagnostic reports its failure, it does not end the run
            return f"quiet parts unreadable: {type(exc).__name__}: {exc}"

    def waiting(self, w, row, bot, now, held, parents, queued):
        """Whether the task waits for its owner to act: the owner is free to take it, and nothing holds the task back."""
        if row["id"] in held or row["id"] in parents:
            return False
        if not bot.available or bot.slug in self.paused or bot.running or bot.slug in queued:
            return False
        if bot.limited_until and now < bot.limited_until:
            return False
        if row["status"] == "open" and bot.role == "builder" and w.slots(bot.slug) >= w.board.slots:
            return False  # its slots are full: the task waits in line, which is no stall
        return True

    def day_capped(self, w, tid):
        """Whether the daily resume limit holds this task now: it is stranded (no progress since its last resume) and
        DAY_LIMIT resumes fell in the 24 hours up to now (backend/stranded_resume.py state). That wait is the product's
        documented rule, so the stuck check does not count it; the late hours and the explain name it."""
        from backend import hubdb as H
        from backend import stranded_resume as SR

        now = w.now()
        recent = sum(
            1
            for r in w.sql(
                "SELECT created_at FROM history_events WHERE subject_type='task' AND subject_id=? AND kind=?",
                (tid, SR.RESUMED),
            )
            if now - dt.timedelta(hours=24) <= H.parse_ts(r["created_at"]) <= now
        )
        if recent < SR.DAY_LIMIT:
            return False
        with w.store.read() as c:
            row = H.task(c, tid)
            return row is not None and bool(SR.stranded(c, row, H.now()))

    def limit_for(self, title, slug):
        """The hours a task may wait with its owner free: the scenario's first allowance that names its owner and title
        gives it (None: not counted), else STUCK_HOURS."""
        for a in self.scenario.stuck:
            if (not a.owner or slug.startswith(a.owner)) and (not a.match or a.match in title):
                return a.hours
        return STUCK_HOURS

    def shipped(self, t):
        return t["step"] in self.w.board.merged_steps() or self.w.board.finished(t["step"])

    # ------------------------------------------------------------------ measures
    def turns(self, since=None, until=None, bots=None):
        out = []
        for b in self.t.all:
            if bots and b.slug not in bots:
                continue
            out += [r for r in b.turns if (since is None or r["at"] >= since) and (until is None or r["at"] < until)]
        return out

    def cost(self, turns=None):
        total = 0.0
        for r in turns if turns is not None else self.turns():
            kind = r["kind"] if r["kind"] in B.TOKENS else None
            if kind:
                cached, fresh, out = B.TOKENS[kind]
                price = PRICE["claude-opus-5-5"]
                total += (fresh * price[0] + cached * price[1] + out * price[2]) / 1e6
        return total

    def explain(self):
        """Where each unfinished ticket stands: its step, its checks and the owners' jobs."""
        out = []
        # Diagnostic: a merged ticket a check names (blocked after its merge, or moved by hand) is shown with the rest.
        named = {title for _, title in self.violations["merged_blocked"]}
        named |= {f[2] for f in self.w.fallbacks if "merged" in f[3] or "moved to" in f[3]}
        shown = [t for t in self.final if not self.shipped(t) or t["title"] in named]
        # Diagnostic: a manager's ticket about a stop (no step) is shown too, with its turns and jobs.
        shown += [
            dict(r, step=None, blocked_since=None)
            for r in self.w.sql("SELECT id,title,owner_id,status FROM tasks WHERE title LIKE '%made no progress in%'")
        ]
        for t in shown:
            out.append(
                f"{t['title']} @ {t['step']} ({t['status']}, {t['owner_id']})"
                + (f" blocked: {t['blocked_since']}" if t["blocked_since"] else "")
            )
            for c in self.w.checks(t["id"]):
                out.append(
                    f"  {c['title']}: {c['owner_id']} {c['status']} verdict={c['verdict']} head={str(c['verdict_head'])[:7]}"
                )
            for e in self.w.sql(
                "SELECT e.created_at, e.actor_id AS actor, e.kind AS field, coalesce(s.name, e.new) new, e.note FROM history_events e "
                "LEFT JOIN task_steps s ON s.id=e.new WHERE e.subject_type='task' AND e.subject_id=? AND e.kind IN ('step','verdict_move',"
                "'round_skipped','step_skipped','allocated','merge_wake','verdict_wait','resumed','resume_stopped') "
                "ORDER BY e.created_at, e.rowid",
                (t["id"],),
            ):
                out.append(
                    f"  {e['created_at'][11:16]} {e['actor'][:14]:14} {e['field']:13} {str(e['new'])[:60]} {str(e['note'] or '')[:60]}"
                )
            for a in self.w.sql(
                "SELECT created_at, bot_id, state, settled_at FROM attempts WHERE task_id=? ORDER BY created_at",
                (t["id"],),
            ):
                out.append(
                    f"  turn {a['created_at'][11:16]} {a['bot_id']} {a['state']} settled {str(a['settled_at'])[11:16]}"
                )
            if t["step"] is None:  # diagnostic: what each turn on a step-less task (a manager's ticket) did
                for r in self.turns():
                    if r.get("task") == t["id"]:
                        out.append(f"  sim turn {str(r['at'])[11:16]} {r.get('news')} -> {r.get('action')}")
            pr = self.w.pr_of(t["id"])
            if pr:
                out.append(
                    f"  PR #{pr} head={self.w.prs[pr]['head'][:7]} ci={self.w.ci_state(pr)} merged={self.w.prs[pr]['merged']}"
                )
            for j in self.w.sql(
                "SELECT j.bot_id, j.state, j.held, j.wake_class, m.refs FROM jobs j "
                "JOIN messages m ON m.id=j.message_id WHERE json_extract(m.refs,'$.task') IN "
                "(SELECT ? UNION SELECT from_task FROM task_relations WHERE to_task=? AND kind='parent') "
                "AND j.state NOT IN ('done','canceled')",
                (t["id"], t["id"]),
            ):
                out.append(f"  job {j['bot_id']} {j['state']} held={j['held']} {j['wake_class'] or ''}")
        out += self.diagnostics()
        return out

    def diagnostics(self):
        """Diagnostic only: the lost probe's posts, the readiness read after each returning bot's heartbeat, each
        limited bot's wait as the server holds it, and the probes that held a login."""
        out, w = [], self.w
        # Diagnostic only: each nudge notice the lead was sent about a stop, with its job, and each lead turn that read one.
        for m in w.sql(
            "SELECT m.created_at, json_extract(m.refs,'$.task') AS task, (SELECT group_concat(j.state) FROM jobs j "
            "WHERE j.message_id=m.id) AS states FROM messages m WHERE m.recipient_id='bot:engineering-lead' "
            "AND json_extract(m.refs,'$.wake')='nudge'"
        ):
            out.append(
                f"nudge notice {m['created_at'][11:16]} about {str(m['task'])[:8]}: jobs {m['states'] or 'none'}"
            )
        for r in self.turns():
            if r.get("wake") == "nudge":
                out.append(f"nudge turn {str(r['at'])[11:16]} task {str(r.get('task'))[:8]} -> {r.get('action')}")
        if self.lost_probe:
            lp = self.lost_probe
            posts = [p for p in w.attempt_posts if p[1].startswith(f"attempts/{lp['attempt']}/")]
            off = [p for p in posts if not p[4]]
            out.append(
                f"lost probe {lp['job'][:6]} bot {lp['bot_id']} attempt {lp['attempt'][:6]} ({lp['mode']} at "
                f"{lp['at']:%H:%M}): {len(posts)} sim posts to it, {len(off)} sent while its bot was off"
            )
        for b in self.back_lines:
            out.append(
                f"back {b['bot_id']} at {b['at']:%H:%M}: server_ready {b['server_ready']}; " + " | ".join(b["reasons"])
            )
        for win in w.limit_windows:
            out.append(
                f"limit {win['bot_id']} since {win['since']} until {win['until']} ({win['source']}, "
                f"streak {win['streak']})"
            )
        for job in sorted(self.probe_seen):
            for row in w.sql(
                "SELECT id,state,created_at,started_at,finished_at,lease_until FROM attempts "
                "WHERE job_id=? ORDER BY created_at",
                (job,),
            ):
                out.append(
                    f"probe {job[:6]} attempt {row['id'][:6]} {row['state']} created {row['created_at']} "
                    f"started {row['started_at']} finished {row['finished_at']} "
                    f"lease {row['lease_until']}"
                )
        return out

    def summary(self):
        turns = self.turns()
        kinds = Counter(r["kind"] for r in turns)
        steps = Counter(t["step"] for t in self.final)
        peak_queue = Counter()
        for _, queued in self.samples:
            for bot, (n, _) in queued.items():
                peak_queue[bot] = max(peak_queue[bot], n)
        return {
            "turns": len(turns),
            "work": kinds["work"] + kinds["review"],
            "noise": kinds["noise"],
            "stale": sum(1 for r in turns if r["stale"]),
            "parked": sum(1 for r in turns if r.get("parked")),
            "silent": kinds["silent"],
            "crash": kinds["crash"],
            "restarted": kinds["restart"],
            "limited": kinds["limited"],
            "cost": round(self.cost(), 2),
            "steps": dict(steps),
            "peak_queue": dict(peak_queue.most_common(4)),
            "peak_doing": dict(self.peak_doing),
            "refused": sum(1 for r in turns if str(r["action"]).startswith("refused")),
            "fallbacks": dict(Counter(f[3].split(":")[0] for f in self.w.fallbacks)),
            "lead_time_h": self.lead_time(),
            "claims": {str(k): v for k, v in sorted(self.claims.items())},
        }

    def lead_time(self):
        """Median hours from a ticket's creation to its merge."""
        hours = sorted(
            (when - self.w.H.parse_ts(t["created_at"])).total_seconds() / 3600
            for t in self.final
            for tid, when in self.merged_at.items()
            if tid == t["id"]
        )
        return round(hours[len(hours) // 2], 1) if hours else None


@dataclass
class Allowance:
    """A task that may wait longer than STUCK_HOURS with its owner free, and why. It names the tasks by owner (a prefix
    of the bot's slug, empty: any bot) and by title (a substring, empty: any task); both must match. `hours` None means
    the task is not counted at all, which only fleet stand-ins (inert bots) may use."""

    reason: str
    hours: float | None = None
    match: str = ""
    owner: str = ""


@dataclass
class Scenario:
    name: str
    about: str
    setup: object
    during: object = lambda run, w, team, i: None
    arrive: object = lambda run, w, team, i: None  # before the tick's clock moves and its heartbeat
    checks: list = field(default_factory=list)  # (name, ticket, fn(run) -> (ok, detail))
    styles: dict = field(default_factory=dict)
    hours: float = 12
    drain_hours: float = 6  # then up to this long more, until every ticket has merged
    tick_minutes: int = 5
    slots: int = 4
    release_every_hours: float = 4
    ci_fail: float = 0.0  # chance CI fails on a new head
    board: object = dev_board
    architect: bool = False  # a senior architect builders ask to plan major tickets
    major: float = 0.0  # share of tickets tagged major
    claim: str = "bot"  # "runner": one claim round as a runner makes it (no bot named) instead of one claim per bot
    signed_in: tuple = ()  # runtimes the computer reports signed in (empty: the plain per-bot readiness map)
    capacity: int = 4  # the computer's slots, as its heartbeat says
    fleet: int = 0  # extra bots beyond the team, for a backlog (their turns do nothing)
    profiles: dict = field(default_factory=dict)  # slug -> profile name, instead of the template's (World.chart)
    stuck: tuple = ()  # Allowances: tasks that may wait longer than STUCK_HOURS here, each with its reason


def tickets(run, w, n, owner=None, step=None, title="Ticket"):
    """`n` tickets at the board's first step: unowned when that step assigns them, else spread over the builders."""
    first = w.board.first()
    made = []
    for i in range(n):
        if owner is None and not first.assign:
            owner = run.t.builders[i % len(run.t.builders)].slug
        tags = ["major"] if run.rng.random() < run.scenario.major else []
        # A task always has an owner: an unassigned ticket is the person's until the board hands it to a bot.
        made.append(
            w.post(
                "tasks",
                {
                    "owner": owner or "ana",
                    "title": f"{title} {i + 1}",
                    "body": "Build it.",
                    "type": w.dev["id"],
                    "step": step or first.name,
                    "labels": tags,
                },
            )
        )
    return [m.get("task", m) for m in made]


def at(hours, minutes=0):
    return dt.timedelta(hours=hours, minutes=minutes)
