"""The seeded idea-to-done run (plan gap G18): one idea through Product and Engineering to Done, with fake bots and
people that act when the engine gives them a turn, and faults on.

Faults: webhooks lost, repeated or late; GitHub down for a while; checks failing; the merge queue dropping a PR; Jev
unsure, or its call failing; a bot's run crashing with nothing done. Bots act only on a wake the engine queued for them
(the real wakes and ladder), people only when the task waits on them, and the daily release hour publishes what merged.

What must hold, every step: the never-stuck check and the rig's checks (no open task waits on nothing). At the end: both
tasks Done within a week of simulated time, no task used more than 7 bot runs in a row without a change, and each task's
history holds every stage in order.

    python -m evals.never_stuck.run --software --seeds 1-200 [-j 2]"""

import random
from collections import Counter
from typing import Any

from .fake_github import REPO
from .fake_jev import FAIL, MET, NOT_MET
from .scenarios import ScenarioFailed
from .software import ENGINEERING_STAGES, PRODUCT_STAGES, NotBuiltYet, Page, SoftRig, soft_rig
from .world import ANA, BOTS, BUILDER, EM, PM, PPM, RELEASE, REVIEWER_1, REVIEWER_2, SOFTWARE_BOTS

WEEK_S = 7 * 86400
MOST_UNCHANGED_RUNS = 7


class SoftwareFailure(AssertionError):
    def __init__(self, seed: int, step: int, what: str) -> None:
        super().__init__(f"software seed {seed} step {step}: {what}")
        self.seed, self.step, self.what = seed, step, what


class SoftwareSim:
    def __init__(self, r: SoftRig, rng: random.Random) -> None:
        self.r, self.rng = r, rng
        self.pages: dict[tuple[str, str], Page] = {}
        self.unchanged: Counter[str] = Counter()
        self.most_unchanged = 0
        self.runs = 0
        self.used: Counter[str] = Counter()
        self.tag = 0
        self.fired: str | None = None  # the last day the release hour fired
        self.crash = rng.choice((0.0, 0.05, 0.15))
        self.slow_people = rng.choice((0.2, 0.5, 0.8))
        self.older_first = rng.random() < 0.5  # production runs an older version when the PM first checks
        r.fake_jev.unsure_rate = rng.choice((0.0, 0.1, 0.25))
        r.fake_jev.fail_rate = rng.choice((0.0, 0.05, 0.15))

    # ------------------------------------------------------------------ what a bot does on its turn
    def _page(self, task: str, author: str, purpose: str, verdict: str = MET) -> None:
        key = (task, purpose)
        page = self.r.page(author, f"{purpose} for {task}", verdict=verdict, page=self.pages.get(key))
        self.pages[key] = page
        self._act(author, f"attach {purpose}", lambda c: _attach(self.r, c, task, page, author))

    def _act(self, who: str, what: str, fn: Any) -> None:
        out = self.r.act(who, fn)
        self.used[f"{what}{' (refused)' if out.__class__.__name__ == 'Refusal' else ''}"] += 1

    def _red(self, task: str) -> list[int]:
        """The task's open PRs whose newest commit's checks failed, as the bot sees them on GitHub."""
        gh = self.r.gh.gh
        out: list[int] = []
        for n in self._numbers(task):
            pr = gh.prs[(REPO, n)]
            states = {c["state"] for c in gh.checks.get((REPO, pr["head_sha"]), []) if not c.get("tico")}
            if pr["state"] == "open" and "failure" in states:
                out.append(n)
        return out

    def _numbers(self, task: str) -> list[int]:
        from backend.item_sources import github as TF

        return [int(p["number"]) for p in self.r.w.read(lambda c: TF.prs_of(c, task))]

    def turn(self, task: str, bot: str) -> None:
        r = self.r
        stage = r.stage_of(task)
        lines = " / ".join(r.lines(task))
        if bot == PM and stage == "Spec":
            if "Jev is checking" not in lines and "person" not in {s for s, _ in r.waits(task)}:
                self._page(task, PM, "Spec")
        elif bot == PM and stage == "Validate":
            if "Jev is checking" in lines:
                return
            older = self.older_first and (task, "Validate") not in self.pages
            self._page(task, PM, "Validate", verdict=NOT_MET if older else MET)
        elif bot == BUILDER and stage == "Build":
            if not self._numbers(task):
                for _ in range(self.rng.choice((1, 1, 2))):
                    n = max((n for (_, n) in r.gh.gh.prs), default=0) + 1
                    self.used["open a PR"] += 1
                    r.gh.open_pr(n)
                    url = f"https://github.com/{REPO}/pull/{n}"
                    self._act(BUILDER, "link a PR", lambda c, url=url: _link(c, task, url))
            for n in self._red(task):
                self._push(n)
        elif bot == BUILDER and stage in ("Fixes 1", "Fixes 2"):
            red = self._red(task)
            for n in red:
                self._push(n)
            if not red:
                self._act(BUILDER, "say done", lambda c: _done(r, c, task, BUILDER))
        elif bot == BUILDER and stage == "QA":
            for n in self._red(task):
                self._push(n)
            if "Jev is checking" not in lines:
                self._page(task, BUILDER, "QA")
        elif bot in (REVIEWER_1, REVIEWER_2) and stage in ("Review 1", "Review 2"):
            if "Jev is checking" not in lines:
                self._page(task, bot, stage)
        elif bot in (EM, PPM) and "Decide if it meets the check" in lines:
            # Jev was unsure twice on a bot's stage: the pipeline's manager decides, with why (two sign-offs rule).
            self._act(bot, "the manager decides the page", lambda c: _decide(r, c, task, bot))
        elif bot == RELEASE and stage == "Merge":
            if "Dropped from the merge queue" in lines or "GitHub refused the merge" in lines:
                red = self._red(task)
                for n in red:
                    self._push(n)
                if not red:
                    self._act(RELEASE, "queue again", lambda c: _requeue(c, task, int(r.row(task)["version"])))
        elif bot == RELEASE and stage == "Release":
            if r.gh.unreleased():
                self.tag += 1
                self.used["publish a release"] += 1
                r.gh.publish(f"v1.{self.tag}.0")

    def release_hour(self) -> bool:
        """The daily release hour (D13, a routine on the Release Owner): once a day from 10:00, the Release Owner
        publishes a release of what merged since the last one. While GitHub is down it cannot; the next step tries."""
        gh, now = self.r.gh, self.r.w.now()
        day = now[:10]
        if self.fired == day or now[11:13] < "10" or gh.is_down():
            return False
        self.fired = day
        if not gh.unreleased():
            return False
        self.tag += 1
        self.used["the release hour publishes"] += 1
        gh.publish(f"v1.{self.tag}.0")
        return True

    def _push(self, number: int) -> None:
        self.used["push a fix"] += 1
        self.r.gh.push(number)

    # ------------------------------------------------------------------ the loop
    def open_tasks(self, p: str) -> list[str]:
        r = self.r
        return [t for t in [p, *r.filed(p)] if r.row(t)["status"] == "open"]

    def step(self, p: str) -> bool:
        """One round: every bot with a queued wake runs once, people answer what waits on them. True if anyone did."""
        r = self.r
        did = self.release_hour()
        for task in self.open_tasks(p):
            for bot in (*BOTS, *SOFTWARE_BOTS):
                if not r.queued(task, bot):
                    continue
                before = int(r.row(task)["version"])
                crashed = self.rng.random() < self.crash
                self.used["bot crashed" if crashed else "bot ran"] += 1
                r.ran(
                    task,
                    bot,
                    state="failed" if crashed else "completed",
                    during=None if crashed else (lambda t=task, b=bot: self.turn(t, b)),
                )
                self.runs += 1
                if r.row(task)["status"] != "open" or int(r.row(task)["version"]) != before:
                    self.unchanged[task] = 0
                else:
                    self.unchanged[task] += 1
                    self.most_unchanged = max(self.most_unchanged, self.unchanged[task])
                did = True
            if r.row(task)["status"] != "open":
                continue
            for state, who in r.waits(task):
                if state == "by_hand":
                    r.by_hand(task)
                    self.used["person moves it by hand"] += 1
                    did = True
                elif state == "person" and who and self.rng.random() > self.slow_people:
                    self.person(task, str(who))
                    did = True
        return did

    def person(self, task: str, who: str) -> None:
        from backend import task_engine as E

        r = self.r
        stage = r.row(task)["stage_id"]
        has_page = r.w.read(
            lambda c: c.execute(
                "SELECT 1 FROM requirements WHERE stage_id=? AND kind='page' AND withdrawn_at IS NULL", (stage,)
            ).fetchone()
        )
        if (r.visit(task).get("level") or "owner") == "person":
            self._act(who, "person says try again", lambda c: E.try_again(c, task, who))
        elif has_page:
            self._act(who, "person OKs the page", lambda c: _ok(r, c, task, who))

    def finish(self, seed: int, p: str, step: int) -> None:
        r = self.r
        filed = r.filed(p)
        if r.row(p)["status"] != "done":
            raise SoftwareFailure(seed, step, f"the idea is {r.row(p)['status']} in {r.stage_of(p)}: {r.lines(p)}")
        if self.most_unchanged > MOST_UNCHANGED_RUNS:
            raise SoftwareFailure(seed, step, f"{self.most_unchanged} bot runs in a row changed nothing")
        seen = r.visited(p)
        if [s for s in seen if s in PRODUCT_STAGES][:3] != list(PRODUCT_STAGES[:3]) or seen[-2:] != [
            "Validate",
            "Done",
        ]:
            raise SoftwareFailure(seed, step, f"the Product task's stages: {seen}")
        for e in filed:
            if r.row(e)["status"] != "done":
                raise SoftwareFailure(seed, step, f"Engineering task {e} is {r.row(e)['status']}")
            order = [s for s in r.visited(e) if s in ENGINEERING_STAGES]
            first = [s for i, s in enumerate(order) if s not in order[:i]]
            if first != list(ENGINEERING_STAGES):
                raise SoftwareFailure(seed, step, f"Engineering task {e} went {order}")


def _attach(r: SoftRig, c: Any, task: str, page: Page, by: str) -> Any:
    from backend import task_pages as TP

    stage = r.row(task)["stage_id"]
    rid = c.execute(
        "SELECT id FROM requirements WHERE stage_id=? AND kind='page' AND withdrawn_at IS NULL ORDER BY position",
        (stage,),
    ).fetchone()
    return TP.attach(c, task, str(rid[0]), page.ref, by, seen_version=int(r.row(task)["version"]))


def _link(c: Any, task: str, url: str) -> Any:
    from backend.store import H

    return H.task_link(c, BUILDER, task, url)


def _done(r: SoftRig, c: Any, task: str, by: str) -> Any:
    from backend import answers as A

    return A.answer(c, task, r.requirement(task, "owner_done"), by, "done", seen_version=int(r.row(task)["version"]))


def _ok(r: SoftRig, c: Any, task: str, who: str) -> Any:
    from backend import answers as A

    return A.answer(c, task, r.requirement(task, "page"), who, "ok")


def _decide(r: SoftRig, c: Any, task: str, who: str) -> Any:
    from backend import answers as A

    version = int(r.row(task)["version"])
    why = "Read it against the check: it meets it"
    return A.answer(c, task, r.requirement(task, "page"), who, "ok", why, seen_version=version)


def _requeue(c: Any, task: str, version: int) -> Any:
    from backend.item_sources import github as MQ

    return MQ.requeue(c, task, RELEASE, seen_version=version)


def run_software_seed(seed: int) -> dict[str, Any]:
    """One idea from Ideas to Done with faults on. Raises SoftwareFailure, or NotBuiltYet on the shared base."""
    from backend import hubdb as H

    rng = random.Random(seed * 7919 + 13)
    with soft_rig(seed, faults=True) as r:
        sim = SoftwareSim(r, rng)
        start = r.w.now()
        p = r.idea(rng.choice(("Export to CSV", "Saved filters", "Dark mode", "Audit trail")), by=ANA)
        step = 0
        try:
            while r.row(p)["status"] == "open":
                step += 1
                if (H.parse_ts(r.w.now()) - H.parse_ts(start)).total_seconds() > WEEK_S:
                    break
                did = sim.step(p)
                r.settle(10 if did else rng.choice((60, 300)))
        except ScenarioFailed as failure:
            if isinstance(failure, NotBuiltYet):
                raise
            raise SoftwareFailure(seed, step, str(failure)) from failure
        sim.finish(seed, p, step)
        return {
            "seed": seed,
            "steps": step,
            "runs": sim.runs,
            "most_unchanged_runs": sim.most_unchanged,
            "days": round((H.parse_ts(r.w.now()) - H.parse_ts(start)).total_seconds() / 86400, 2),
            "behaviours": dict(sim.used),
            "faults": {**dict(r.gh.stats), **{f"jev {k}": n for k, n in r.fake_jev.counts.items() if k != MET}},
            "jev_failed": r.fake_jev.counts[FAIL],
        }
