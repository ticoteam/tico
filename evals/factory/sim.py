"""The simulation loop: every tick moves the clock, runs the real scheduler pass, lets each available bot claim and run
its queued turns, and records what the board and the queues look like."""
import datetime as dt
import linecache
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import bots as B
from .board import dev_board
from .world import PRIMARY, Refusal, World

PRICE = {"claude-opus-5-5": (4.0, 0.20, 20.0)}   # $ per million: uncached input, cached input, output

CLAIM_CALLS = []    # per claim request: (step, last line the step ran, what it returned), in order


def record_claim_steps(execution):
    """Diagnostic only: wraps the claim's read path, its candidate selections and its write path so a trace can name
    the line that answered each step and what each selection returned. Every answer is passed back unchanged."""
    def last_line(original):
        code = original.__code__

        def traced(self, *args, **kw):
            lines = []

            def local(frame, event, arg):
                if event == "line":
                    lines.append(frame.f_lineno)
                return local

            def wanted(frame, event, arg):
                return local if frame.f_code is code else None
            sys.settrace(wanted)
            try:
                answer = original(self, *args, **kw)
            finally:
                sys.settrace(None)
            CLAIM_CALLS.append((original.__name__, lines[-1] if lines else None, answer))
            return answer
        traced.__wrapped__ = original
        return traced

    def selection(original):
        def traced(self, *args, **kw):
            row = original(self, *args, **kw)
            CLAIM_CALLS.append(("candidate " + ("job" if kw.get("job_id") else "select"), None, row))
            return row
        traced.__wrapped__ = original
        return traced
    execution.Execution._idle_claim = last_line(execution.Execution._idle_claim)
    execution.Execution.claim = last_line(execution.Execution.claim)
    execution.Execution.candidate = selection(execution.Execution.candidate)


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
    "builder-claude": ("builder", "claude"), "builder-openai": ("builder", "gpt"),
    "reviewer-gpt": ("reviewer", "gpt"), "reviewer-xai": ("reviewer", "grok"), "reviewer-muse": ("reviewer", "muse"),
    "qa-claude": ("qa", "claude"), "qa-xai": ("qa", "grok"),
    "release-manager": ("other", "claude"), "engineering-lead": ("lead", "claude"),
}
ARCHITECT = {"architect": ("architect", "claude")}


class Run:
    """One scenario run: the world, the team and everything measured."""

    def __init__(self, scenario, seed):
        self.scenario, self.seed = scenario, seed
        self.rng = random.Random(seed)
        self.samples = []          # per tick: {bot: queued}
        self.violations = defaultdict(list)
        self.merged_at = {}        # ticket id -> when it first reached a merged step
        self.peak_doing = Counter()
        self.claims = Counter()    # claim replies by HTTP status: an exception the app raised counts as a 500
        self.paused = set()        # bots the scenario paused: the work they hold waits on their owner, not the board
        self.marks = []            # per runner claim round: free slots, queued bots, held jobs and turns started
        self.lost_probe = None
        self.probe_seen = set()    # probe jobs a round met on a login the server held for them (windows read at check time)
        self.back_check = None     # set by the scenario when a computer comes back: its readiness is read on that tick
        self.back_lines = []       # what the server held for the returning computer's bot on its first round back
        self.traces = []           # claim traces of short rounds: the server's candidate gates for one queued bot
        self.trace_rounds = 0      # short rounds traced so far (the first few are read)
        self.claim_path = ""       # where the last claim answered: the read path, or the write path's last line
        from backend import execution
        if not hasattr(execution.Execution.claim, "__wrapped__"):
            record_claim_steps(execution)

    def team(self, w):
        styles = self.scenario.styles
        team = Team()
        made = {}
        for slug, (role, family) in self.members().items():
            rng = random.Random(f"{self.seed}:{slug}")
            style = styles.get(slug, styles.get(role, {}))
            kind = {"builder": B.Builder, "reviewer": B.Reviewer, "qa": B.QA, "lead": B.Lead,
                    "architect": B.Architect}.get(role, B.Quiet)
            made[slug] = kind(w, slug, role, rng, style=style, family=family,
                              **({"slots": self.scenario.slots} if role == "builder" else {}))
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
        with World(board, self.members(), tick_minutes=s.tick_minutes, rng=random.Random(f"{self.seed}:world"),
                   ci_fail=s.ci_fail, signed_in=s.signed_in, capacity=s.capacity) as w:
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
                w.advance(ready=[b.slug for b in team.all if b.available],
                          beat=lambda: [b.renew() for b in team.all if b.available])
                ready_before = {b.slug for b in team.all if b.available}
                s.during(self, w, team, i)
                ready_now = [b.slug for b in team.all if b.available]
                if set(ready_now) != ready_before:
                    # A computer that went off or came back is reported before this tick's claims: the readiness
                    # the server holds is the one advance() sent, from before the change.
                    w.heartbeat(ready_now)
                self.release(w, i)
                self.assign(w, team)
                w.scheduler_pass()
                bots = list(team.all)
                self.rng.shuffle(bots)
                for bot in bots:
                    if bot.running and not bot.available:
                        bot.lose()   # its computer went off under the run: the lease lapses
                    if bot.available:
                        bot.recover()
                    if s.claim == "bot":
                        self.bot_turns(w, bot)
                    elif bot.running and w.now() >= bot.ends:
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
            if bot.running and w.now() >= bot.ends:
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
        """One claim round as a real runner runs it: while it has a free slot it asks for work, naming the bots it is
        already running. The work the board could hand out is counted from the queue before the claims (`queued`), not
        from the claim's own pick, so a claim that passes over work it could take shows in `marks`. The bots the
        scenario paused are left out of that count: their work waits on their owner."""
        body = self.runner_body(team)
        free = w.capacity - sum(1 for b in team.all if b.running)
        idle = {b.slug for b in team.all} - set(body["busy_bots"]) - self.paused
        if self.back_check:
            # The computer's readiness as the heartbeat before this round's claims told the server it (the sim sends
            # that heartbeat once the scenario changes who is available).
            self.back_lines.append(self.back_read(w, self.back_check["slug"]))
            self.back_check = None
        queued, held, queued_slugs = w.queued_bots(idle)
        before = self.refusals(w, idle) if free and queued else []
        started, reply = 0, None
        for _ in range(max(0, free)):
            _, reply = self.claim_once(w, body)
            attempt = reply.get("attempt") if isinstance(reply, dict) else None
            if not attempt:
                break
            team.bot(attempt["bot"]).start(attempt)
            started += 1
        mark = {"at": w.now(), "free": free, "queued": queued, "slugs": queued_slugs, "held": held,
                "started": started}
        if free and queued and started < min(free, queued):
            # Diagnostic: the server's view of each queued bot before the claims, and the last claim's answer.
            mark["why"] = (f"{' | '.join(before)}; the last claim answered {reply} [{self.claim_path}]; the claim body was {body} "
                           f"to runner {w.runner['runner_id'][:8]}; capacity {w.capacity}, {w.capacity - free} running, "
                           f"paused {sorted(self.paused)}")
            if self.trace_rounds < 60:
                self.trace_rounds += 1
                self.traces.extend(self.trace(w, body, idle))
        self.marks.append(mark)

    def back_read(self, w, slug):
        """Diagnostic only: the readiness the server holds for one bot of this computer, as its heartbeat last reported
        it, with the refusals its queued work gets on this round."""
        from backend.execution import readiness_document
        with w.store.read() as c:
            row = c.execute("SELECT readiness_json FROM runners WHERE id=?", (w.runner["runner_id"],)).fetchone()
        doc = readiness_document(row["readiness_json"]) if row else {}
        ready = ((doc.get("bots") or {}).get(slug) or {}).get("ready")
        return {"at": w.now(), "bot": slug, "server_ready": ready, "refusals": self.refusals(w, [slug])}

    def trace(self, w, body, slugs, limit=2):
        """Diagnostic only, read before a short round's claims: for up to `limit` queued bots that Health calls handed
        out, the server's candidate for the whole computer and for the bot's oldest job, and each gate the claim applies to
        that job (execution.candidate), read on a read connection. No check judges these lines."""
        from types import SimpleNamespace
        from backend import execution as X, subscriptions, usage_limits
        from backend.auth import Identity
        from backend.shared_bots import follow
        rid = w.runner["runner_id"]
        who = Identity(actor="runner", role="runner", runner_id=rid)
        ns = SimpleNamespace(bot=None, busy_bots=list(body["busy_bots"]), busy_lanes=list(body["busy_lanes"]),
                             start_places=True, next_run=True)
        ex, out = w.app.state.execution, []
        stamp = f"{w.now():%H:%M}"
        with w.store.read() as c:
            try:
                runner = ex.runner(c, who)
                lanes = X.lanes_on(runner)
                live = set(X.live_lanes(c))
                held_bots = set(body["busy_bots"]) | ({b for b, _ in live} if not lanes else set())
                busy = live | {tuple(e.split("|", 1)) for e in body["busy_lanes"] if "|" in e}
                ready = X.readiness_document(runner["readiness_json"])
                ctx, default, roots, live_tasks = subscriptions.context(c), usage_limits.company(c), X.roots_on(runner), X.live_tasks(c)
                any_job = ex.candidate(c, who, ns, runner)
                out.append(f"{stamp} candidate for the computer: {any_job['id'][:6] if any_job else None}"
                           + (f" for bot {any_job['bot']}" if any_job else ""))
                # The computer's gates the claim applies before any job: the waking period from its last contact,
                # its turns held against its capacity.
                now = X.H.now()
                computer = c.execute("SELECT last_seen,awake_since,capacity FROM runners WHERE id=?", (rid,)).fetchone()
                waking = ex.waking(runner, now)
                out.append(f"{stamp} computer: last_seen {computer['last_seen']}, awake_since {computer['awake_since']}, "
                           f"waking {waking}, awake {ex.awake(waking, now)}, turns held "
                           f"{sum(X.turns_here(c, rid).values())} of capacity {computer['capacity']}")
                # The candidate's bot first: it is the one the claim would start.
                order = [any_job["bot"]] if any_job and any_job["bot"] in slugs else []
                order += [slug for slug in sorted(slugs) if slug not in order]
                # Keeper notices the claim's write path cancels before it chooses (its `obsolete` step): a candidate
                # among them is suppressed under the lock, while the read path and Health still see it queued.
                obsolete = {row[0] for row in c.execute("SELECT j.id " + X.obsolete_notices_sql() + " LIMIT 50",
                                                         (rid, X.H.KEEPER, now)).fetchall()}
                traced = 0
                for slug in order:
                    if traced >= limit:
                        break
                    reasons = list(X.queued_reasons(c, w.store.settings, rid, slug, oldest=True))
                    # The candidate's own job is traced, not the bot's oldest: it is the one the claim would hand out.
                    chosen = any_job is not None and slug == any_job["bot"]
                    if not chosen and (not reasons or reasons[0][1] is not None):
                        continue
                    traced += 1
                    job = any_job if chosen else reasons[0][0]
                    assigned = c.execute("SELECT 1 FROM assignments a JOIN bots b ON b.slug=a.bot "
                                         "WHERE a.runner_id=? AND a.bot=? AND b.state='active'",
                                         (rid, slug)).fetchone() is not None
                    sql_ok = c.execute("SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id "
                                       "JOIN conversations cv ON cv.id=m.conversation_id WHERE j.id=? AND "
                                       f"{X.unfenced_sql()} AND {X.scoped_sql()}", (job["id"],)).fetchone() \
                        is not None
                    cfg = c.execute(X.CLAIM_CONFIG_SQL, (slug,)).fetchone()
                    why, config = X.claim_refusal(c, w.store.settings, runner, slug, cfg["config_json"], cfg["draining"],
                                                  ready, held_bots, ctx, default)
                    gates = [f"assigned {assigned}, unfenced and scoped {sql_ok}", f"bot-level {why or 'passes'}"]
                    if config is not None:
                        followed = follow(c, slug, config)
                        gates.append(f"job {X.job_refusal(c, job, followed, ready, busy, lanes, roots, live_tasks)}")
                        gates.append(f"login with ctx {X.login_refusal(c, w.store.settings, rid, slug, followed, job['id'], ctx)}")
                        gates.append(f"login without ctx {X.login_refusal(c, w.store.settings, rid, slug, followed, job['id'])}")
                    gates.append(f"source continuation {bool(X.source_continuation(c, job['id']))}")
                    gates.append(f"thread {X.continuation_thread(c, job['id']) or '-'}, continues "
                                 f"{X.capacity_continue_on(runner)}, source_continue "
                                 f"{X.SOURCE_CONTINUE in X._capability_set(runner['capabilities_json'])}")
                    mine = ex.candidate(c, who, ns, runner, job_id=job["id"])
                    gates.append(f"candidate for this job {mine['id'][:6] if mine else None}")
                    # The write path's own checks (turn_budgets.enforce records a first hit and passes the job over; the
                    # claim's read path sees only a hold already recorded), read here without recording anything.
                    try:
                        from backend import turn_budgets, run_backoff, task_privacy
                        from backend.store import H as store_h
                        tb = turn_budgets.get(c, slug)
                        seen = turn_budgets.counts(c, slug, tb.get("released_at") or "", store_h.now())
                        cap = turn_budgets.limits(c, turn_budgets._declared(cfg["config_json"] if cfg else None))
                        over = [label for window, key, _, label in turn_budgets.WINDOWS
                                if cap[key] and seen[window] >= cap[key]]
                        gates.append(f"budget held {bool(tb.get('held_at'))}, over cap {over or 'no'}")
                        rb = run_backoff.get(c, slug)
                        gates.append(f"run backoff held {bool(rb.get('held_at'))}, probe due "
                                     f"{run_backoff.probe_due(c, slug, rb) if rb.get('held_at') else '-'}")
                        msg = store_h.message(c, job["message_id"])
                        gates.append(f"message readable {task_privacy.message_readable(c, 'bot:' + slug, msg)}")
                        gates.append(f"obsolete notice {job['id'] in obsolete}, live meeting ref "
                                     f"{bool((msg.get('refs') or {}).get('live_meeting'))}")
                        kind = c.execute("SELECT m.kind AS kind, m.from_actor AS src FROM jobs j JOIN messages m "
                                         "ON m.id=j.message_id WHERE j.id=?", (job["id"],)).fetchone()
                        gates.append(f"message {kind['kind']} from {kind['src']}")
                    except Exception as error:      # a diagnostic never stops the run
                        gates.append(f"budget gate error {error!r}")
                    out.append(f"{stamp} {slug} oldest {job['id'][:6]}: " + "; ".join(gates))
            except Exception as error:      # a diagnostic never stops the run
                out.append(f"{stamp} trace error {error!r}")
        return out

    def refusals(self, w, slugs):
        """Diagnostic only, read before a round's claims: for each bot among `slugs` with queued work, the claim's own
        refusal for its oldest queued job (queued_reasons, the rule the claim applies), and each login it could run on,
        free or held. A hold names its end, and a probe out on it names the probe's job and attempt with the attempt's
        lease. A short round reports these in its mark; no check judges them."""
        from backend import login_holds
        from backend.execution import queued_reasons
        from backend.store import H
        rid = w.runner["runner_id"]
        stamp = lambda value: (value or "")[5:16].replace("T", " ")
        out = []
        with w.store.read() as c:
            try:
                # The claim's own gates on this computer, read before its per-bot answers: places held against capacity,
                # starts waiting, overload, pause, and the awake check.
                from backend import performance, runner_versions, start_places
                cap =c.execute("SELECT capacity,awake_since FROM runners WHERE id=?", (rid,)).fetchone()
                waits = c.execute("SELECT count(*) FROM org_turn_waits").fetchone()[0]
                out.append(f"computer: capacity {cap['capacity']}, places held "
                           f"{sum(start_places.held(c, rid).values())}, starts waiting "
                           f"{start_places.ready_waiting(c, rid)}, overloaded "
                           f"{performance.load_status(c, rid)['overloaded']}, paused "
                           f"{runner_versions.paused(c, rid)}, awake since {cap['awake_since']}, now {H.now()}, "
                           f"company waits {waits}")
            except Exception as error:      # a diagnostic never stops the run
                out.append(f"computer: diagnostic error {error!r}")
            for slug in sorted(slugs):
                try:
                    reasons = list(queued_reasons(c, w.store.settings, rid, slug, oldest=True))
                    if not reasons:
                        continue
                    job, why = reasons[0]
                    parts = [f"claim {why or 'hands out'} {job['id'][:6]}"]
                    status = H.status(c, slug) or {}
                    if status.get("state") == "limited" and status.get("since"):
                        from backend.execution import limit_until
                        parts[0] += (f" (limited since {stamp(status['since'])}, until "
                                     f"{stamp(limit_until(c, slug, status['since']))})")
                    for name in (PRIMARY, *w.runtime_fallbacks()):
                        if name != PRIMARY and name not in w.signed_in:
                            continue
                        hold = login_holds.get(c, rid, "", name)
                        text = f"{name} {login_holds.holding(c, rid, '', name, job['id']) or 'free'}"
                        if hold:
                            text += f" (hold ends {stamp(hold.get('until'))})"
                            probe = hold.get("probe_job")
                            if probe:
                                self.probe_seen.add(probe)
                            row = c.execute("SELECT j.state AS job, a.id AS aid, a.state AS attempt, a.lease_until "
                                            "FROM jobs j LEFT JOIN attempts a ON a.job_id=j.id WHERE j.id=? "
                                            "ORDER BY a.rowid DESC LIMIT 1", (probe,)).fetchone() if probe else None
                            if row:
                                # Who sent the probe attempt's renew and event posts (the sim's own calls), and the
                                # server's lease events for it: a lease the server extends shows here with no post.
                                posts = [(when, caller, slug, on, running) for when, path, caller, slug, on, running
                                         in w.attempt_posts if row["aid"] and path.startswith(f"attempts/{row['aid']}/")]
                                leases = c.execute("SELECT ts, action FROM events WHERE target=? AND "
                                                   "action LIKE 'attempt.lease%' ORDER BY ts", (row["aid"],)).fetchall()
                                text += (f", probe {probe[:6]} job {row['job']} attempt {row['attempt']} "
                                         f"lease {stamp(row['lease_until'])}; {len(posts)} sim posts"
                                         + "".join(f" [{when:%H:%M} {slug} on={on} running={running} {caller}]"
                                                   for when, caller, slug, on, running in posts[-3:])
                                         + "; lease events " + (", ".join(f"{stamp(e['ts'])} {e['action']}"
                                                                         for e in leases) or "none"))
                        parts.append(text)
                    out.append(f"{slug}: " + "; ".join(parts))
                except Exception as error:      # a diagnostic never stops the run
                    out.append(f"{slug}: diagnostic error {error!r}")
        return out

    def runner_body(self, team):
        """What a runner claims with: no bot named, and the bots it is running (or whose computer is off) as busy. The
        sim advertises no lanes, so a busy bot is held on its one lane."""
        busy = [b.slug for b in team.all if b.running or not b.available]
        return {"next_run": True, "busy_bots": busy, "busy_lanes": [f"{slug}|main" for slug in busy],
                "start_places": True}

    @staticmethod
    def where_claim_answered():
        """Each step the last claim ran, in order: the read path or a selection, and the write path's last line."""
        from backend import execution
        steps = []
        for step, line, answer in CLAIM_CALLS:
            if step.startswith("candidate"):
                steps.append(f"{step} returned {answer['id'][:6] if answer is not None else None}")
                continue
            source = linecache.getline(execution.__file__, line).strip() if line else ""
            outcome = answer.get("attempt") if isinstance(answer, dict) else None
            shown = f"attempt {outcome['job_id'][:6]}" if isinstance(outcome, dict) else f"{answer}"[:120]
            steps.append(f"{step} at execution.py line {line} ({source}) answered {shown}")
        return "; ".join(steps) or "no step ran"

    def claim_once(self, w, body):
        """One claim, its status counted. A 5xx is recorded by the world (with its body, or the traceback when the app
        raised) and the claim answers nothing; any other refusal is the sim's own fault and stops the run."""
        del CLAIM_CALLS[:]
        status, reply = w.request("post", "jobs/claim", body, token=w.runner["token"])
        self.claims[status] += 1
        self.claim_path = self.where_claim_answered()
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
                w.update(t["id"], token="release-test", step=board.steps[board.steps.index(step) + 1].name)

    def assign(self, w, team):
        """An unowned ticket at an assigning step waits for the board to give it to a bot of that role. After 30
        minutes a person does it instead, and the run records the fallback."""
        for t in w.tickets():
            step = w.board.step(t["step"])
            if not step or not step.assign or str(t["owner"]).startswith("bot:") or t["status"] in B.FINISHED:
                continue
            if (w.now() - w.H.parse_ts(t["created"])).total_seconds() < 30 * 60:
                continue
            pool = [b for b in team.all if b.role == step.assign and w.slots(b.slug) < b.slots]
            if not pool:
                continue
            owned = Counter(x["owner"] for x in w.tickets() if not self.shipped(x))
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
        for t in self.final:
            if self.shipped(t):
                continue
            out.append(f"{t['title']} @ {t['step']} ({t['status']}, {t['owner']})"
                       + (f" blocked: {t['blocked_since']}" if t["blocked_since"] else ""))
            for c in self.w.checks(t["id"]):
                out.append(f"  {c['title']}: {c['owner']} {c['status']} verdict={c['verdict']} head={str(c['verdict_head'])[:7]}")
            for e in self.w.sql("SELECT e.ts, e.actor, e.field, coalesce(s.name, e.new) new, e.note FROM task_events e "
                                "LEFT JOIN task_steps s ON s.id=e.new WHERE e.task_id=? AND e.field IN ('step','verdict_move',"
                                "'round_skipped','step_skipped','allocated','merge_wake','verdict_wait') ORDER BY e.ts, e.rowid",
                                (t["id"],)):
                out.append(f"  {e['ts'][11:16]} {e['actor'][:14]:14} {e['field']:13} {str(e['new'])[:60]} {str(e['note'] or '')[:60]}")
            pr = self.w.pr_of(t["id"])
            if pr:
                out.append(f"  PR #{pr} head={self.w.prs[pr]['head'][:7]} ci={self.w.ci_state(pr)} merged={self.w.prs[pr]['merged']}")
            for j in self.w.sql("SELECT j.bot, j.state, j.held, j.wake_class, m.refs_json FROM jobs j "
                                "JOIN messages m ON m.id=j.message_id WHERE json_extract(m.refs_json,'$.task') IN "
                                "(SELECT ? UNION SELECT from_task FROM task_relations WHERE to_task=? AND kind='parent') "
                                "AND j.state NOT IN ('done','cancelled')", (t["id"], t["id"])):
                out.append(f"  job {j['bot']} {j['state']} held={j['held']} {j['wake_class'] or ''}")
        out += self.diagnostics()
        return out

    def diagnostics(self):
        """Diagnostic only: the lost probe's posts, the readiness read after each returning bot's heartbeat, each
        limited bot's wait as the server holds it, the probes that held a login, and the short rounds' traces."""
        out, w = [], self.w
        if self.lost_probe:
            lp = self.lost_probe
            posts = [p for p in w.attempt_posts if p[1].startswith(f"attempts/{lp['attempt']}/")]
            off = [p for p in posts if not p[4]]
            out.append(f"lost probe {lp['job'][:6]} bot {lp['bot']} attempt {lp['attempt'][:6]} ({lp['mode']} at "
                       f"{lp['at']:%H:%M}): {len(posts)} sim posts to it, {len(off)} sent while its bot was off")
        for b in self.back_lines:
            out.append(f"back {b['bot']} at {b['at']:%H:%M}: server_ready {b['server_ready']}; "
                       + " | ".join(b["refusals"]))
        for win in w.limit_windows:
            out.append(f"limit {win['bot']} since {win['since']} until {win['until']} ({win['source']}, "
                       f"streak {win['streak']})")
        for job in sorted(self.probe_seen):
            for row in w.sql("SELECT id,state,created,started,finished,lease_until FROM attempts "
                             "WHERE job_id=? ORDER BY created", (job,)):
                out.append(f"probe {job[:6]} attempt {row['id'][:6]} {row['state']} created {row['created']} "
                           f"started {row['started']} finished {row['finished']} "
                           f"lease {row['lease_until']}")
        out += self.traces
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
            "turns": len(turns), "work": kinds["work"] + kinds["review"], "noise": kinds["noise"],
            "stale": sum(1 for r in turns if r["stale"]), "silent": kinds["silent"], "crash": kinds["crash"],
            "limited": kinds["limited"], "cost": round(self.cost(), 2), "steps": dict(steps),
            "peak_queue": dict(peak_queue.most_common(4)), "peak_doing": dict(self.peak_doing),
            "refused": sum(1 for r in turns if str(r["action"]).startswith("refused")),
            "fallbacks": dict(Counter(f[3].split(":")[0] for f in self.w.fallbacks)),
            "lead_time_h": self.lead_time(),
            "claims": {str(k): v for k, v in sorted(self.claims.items())},
        }

    def lead_time(self):
        """Median hours from a ticket's creation to its merge."""
        hours = sorted((when - self.w.H.parse_ts(t["created"])).total_seconds() / 3600
                       for t in self.final for tid, when in self.merged_at.items() if tid == t["id"])
        return round(hours[len(hours) // 2], 1) if hours else None


@dataclass
class Scenario:
    name: str
    about: str
    setup: object
    during: object = lambda run, w, team, i: None
    arrive: object = lambda run, w, team, i: None   # before the tick's clock moves and its heartbeat
    checks: list = field(default_factory=list)   # (name, ticket, fn(run) -> (ok, detail))
    styles: dict = field(default_factory=dict)
    hours: float = 12
    drain_hours: float = 6   # then up to this long more, until every ticket has merged
    tick_minutes: int = 5
    slots: int = 4
    release_every_hours: float = 4
    ci_fail: float = 0.0     # chance CI fails on a new head
    board: object = dev_board
    architect: bool = False  # a senior architect builders ask to plan major tickets
    major: float = 0.0       # share of tickets tagged major
    claim: str = "bot"       # "runner": one claim round as a runner makes it (no bot named) instead of one claim per bot
    signed_in: tuple = ()    # runtimes the computer reports signed in (empty: the plain per-bot readiness map)
    capacity: int = 4        # the computer's slots, as its heartbeat says
    fleet: int = 0           # extra bots beyond the team, for a backlog (their turns do nothing)


def tickets(run, w, n, owner=None, step=None, title="Ticket"):
    """`n` tickets at the board's first step: unowned when that step assigns them, else spread over the builders."""
    first = w.board.first()
    made = []
    for i in range(n):
        if owner is None and not first.assign:
            owner = run.t.builders[i % len(run.t.builders)].slug
        tags = ["major"] if run.rng.random() < run.scenario.major else []
        # A task always has an owner: an unassigned ticket is the person's until the board hands it to a bot.
        made.append(w.post("tasks", {"owner": owner or "ana", "title": f"{title} {i + 1}",
                                     "body": "Build it.", "type": w.dev["id"], "step": step or first.name,
                                     "labels": tags}))
    return [m.get("task", m) for m in made]


def at(hours, minutes=0):
    return dt.timedelta(hours=hours, minutes=minutes)
