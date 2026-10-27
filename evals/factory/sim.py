"""The simulation loop: every tick moves the clock, runs the real scheduler pass, lets each available bot claim and run
its queued turns, and records what the board and the queues look like."""
import datetime as dt
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import bots as B
from .board import dev_board
from .world import World

PRICE = {"claude-opus-5-5": (4.0, 0.20, 20.0)}   # $ per million: uncached input, cached input, output


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
        return {**TEAM, **(ARCHITECT if self.scenario.architect else {})}

    def go(self):
        s = self.scenario
        board = s.board()
        with World(board, self.members(), tick_minutes=s.tick_minutes, rng=random.Random(f"{self.seed}:world"),
                   ci_fail=s.ci_fail) as w:
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
                w.advance(ready=[b.slug for b in team.all if b.available],
                          beat=lambda: [b.renew() for b in team.all if b.available])
                s.during(self, w, team, i)
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
                    for _ in range(8):
                        if bot.running and w.now() >= bot.ends:
                            bot.end()
                        if bot.running or not bot.available:
                            break
                        attempt = w.call("post", "jobs/claim", {"bot": bot.slug, "start_places": True},
                                         token=w.runner["token"])
                        attempt = attempt and attempt.get("attempt")
                        if not attempt:
                            break
                        bot.start(attempt)
                self.observe(w, team)
            self.final = w.tickets()
            self.end = w.now()
            return self

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
