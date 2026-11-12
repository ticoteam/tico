"""GitHub for the fault rig: `item_sources.github.GitHub` over the shared fake (backend/tests/helpers/fake_github.py, track C's),
with webhooks, CI, a merge queue that lands or drops PRs, and the faults.

- Every change (a PR opened, a push, checks finishing, a close, a merge, a queue removal, a release) sends a webhook:
  a `Delivery` with its own delivery id and the fact as GitHub had it then, at a version taken when it was sent.
  `deliver` takes the ones that are due the way the server's webhook does (contract section 7): drop a delivery id seen
  before (`item_sources.github.seen_delivery`), then `item_sources.github.save_and_settle` (a PR) or `save_release` (a release).
- With faults on, a webhook may be lost, sent twice, or late (so they arrive out of order), and GitHub may be down for a
  while: every call raises, and webhooks wait until it is back. Only the 5-minute re-read (`item_sources.github.refresh`) finds
  what a lost webhook said.
- CI: checks start pending on a new commit and finish later (`ci`). The merge queue (`land`) merges a queued PR whose
  checks pass after a few minutes, or, as a fault, takes it out with the check that failed there.

Nothing here reads the wall clock: times are the world's frozen clock (`hubdb.now`), ids are counted."""

import random
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from backend.tests.helpers.fake_github import FakeGitHub, GitHubError

REPO = "acme/app"
DOWN = "GitHub is unavailable"


def _h() -> Any:
    from backend import hubdb

    return hubdb


@dataclass
class Delivery:
    id: str
    event: str  # pull_request, check_run or release
    repo: str
    version: int
    due: str
    seq: int
    number: int | None = None
    fact: dict[str, Any] | None = None
    release: dict[str, Any] | None = None


@dataclass
class Faults:
    """How often each GitHub fault strikes, per webhook or per step. All zero: GitHub behaves."""

    lose: float = 0.0  # a webhook never arrives
    repeat: float = 0.0  # a webhook arrives twice
    late: float = 0.0  # a webhook arrives up to 15 minutes late, so out of order
    down: float = 0.0  # per step: GitHub goes down for a while
    drop: float = 0.0  # per queued PR: the merge queue takes it out instead of merging it
    red: float = 0.0  # per commit: its checks fail

    @classmethod
    def on(cls, rng: random.Random) -> "Faults":
        return cls(
            lose=rng.choice((0.1, 0.2, 0.3)),
            repeat=rng.choice((0.05, 0.15)),
            late=rng.choice((0.1, 0.3)),
            down=rng.choice((0.002, 0.005)),
            drop=rng.choice((0.1, 0.25)),
            red=rng.choice((0.05, 0.15)),
        )


@dataclass
class _Pending:
    at: str
    outcome: str


@dataclass
class RigGitHub:
    """The GitHub the rig's server reads and the fake bots push to."""

    rng: random.Random = field(default_factory=lambda: random.Random(0))
    faults: Faults = field(default_factory=Faults)
    gh: FakeGitHub = field(default_factory=FakeGitHub)
    outbox: list[Delivery] = field(default_factory=list)
    taken: list[Delivery] = field(default_factory=list)
    stats: Counter[str] = field(default_factory=Counter)
    down_until: str | None = None
    land_after_s: int = 600  # a queued PR with passing checks merges this long after it was queued
    ci_s: int = 300  # checks on a new commit finish this long after it was pushed
    _ci: dict[tuple[str, str], _Pending] = field(default_factory=dict)
    _queued: dict[tuple[str, int], str] = field(default_factory=dict)  # when GitHub queued each PR
    _n: int = 0
    _version: int = 0
    log: Callable[[str], None] = lambda _text: None

    # ------------------------------------------------------------------ item_sources.github.GitHub
    def _up(self, call: str) -> None:
        self.stats[f"call {call}"] += 1
        if self.is_down():
            self.stats["call while down"] += 1
            raise GitHubError(DOWN)

    def is_down(self) -> bool:
        return self.down_until is not None and _h().now() < self.down_until

    def read_pr(self, repo: str, number: int) -> dict[str, Any] | None:
        self._up("read_pr")
        return self.gh.read_pr(repo, number)

    def read_checks(self, repo: str, sha: str) -> list[dict[str, Any]]:
        self._up("read_checks")
        return self.gh.read_checks(repo, sha)

    def read_releases(self, repo: str) -> list[dict[str, Any]]:
        self._up("read_releases")
        return self.gh.read_releases(repo)

    def contains(self, repo: str, release_sha: str, commit_sha: str) -> bool:
        self._up("contains")
        return self.gh.contains(repo, release_sha, commit_sha)

    def pr_diff(self, repo: str, number: int, sha: str) -> str | None:
        self._up("pr_diff")
        return self.gh.pr_diff(repo, number, sha)

    def ready(self, repo: str, number: int) -> None:
        self._up("ready")
        self.gh.ready(repo, number)

    def enqueue(self, repo: str, number: int, head_sha: str) -> dict[str, Any]:
        self._up("enqueue")
        out = self.gh.enqueue(repo, number, head_sha)
        if out.get("ok"):
            self._queued[(repo, number)] = _h().now()
            self._pr_hook(repo, number)
        return out

    def post_check(self, repo: str, sha: str, ok: bool | None, summary: str) -> bool:
        self._up("post_check")
        return self.gh.post_check(repo, sha, ok, summary)

    def merge_group_prs(self, repo: str, base_ref: str, head_sha: str) -> list[dict[str, Any]]:
        self._up("merge_group_prs")
        return self.gh.merge_group_prs(repo, base_ref, head_sha)

    def cut_release(self, repo: str, kind: str, name: str, sha: str) -> dict[str, Any]:
        self._up("cut_release")
        out: dict[str, Any] = self.gh.cut_release(repo, kind, name, sha)
        return out

    def dequeue(self, repo: str, number: int) -> dict[str, Any]:
        self._up("dequeue")
        out = self.gh.dequeue(repo, number)
        if out.get("how") == "dequeued":
            self._queued.pop((repo, number), None)
            self._pr_hook(repo, number)
        return out

    # ------------------------------------------------------------------ what bots, people and GitHub do
    def open_pr(self, number: int, repo: str = REPO, checks: str | None = "success", draft: bool = True) -> str:
        """A PR is opened, a draft as Tico opens it at a branch's first push (backend/github.py `_open_draft`). Its
        checks start running and end in `checks` (None: the repo runs no checks)."""
        head = self.gh.open_pr(repo, number, draft=draft)
        if checks:
            self.ci(repo, head, checks)
        self._pr_hook(repo, number)
        return head

    def push(self, number: int, repo: str = REPO, checks: str | None = "success") -> str:
        """A new commit on the PR: its checks start again. A queued PR leaves the queue, as on GitHub."""
        pr = self.gh.prs[(repo, number)]
        was = pr["queue_state"]
        head = self.gh.push(repo, number)
        if was in ("queued", "auto_merge"):
            self._queued.pop((repo, number), None)
        if checks:
            self.ci(repo, head, checks)
        self._pr_hook(repo, number)
        return head

    def ci(self, repo: str, sha: str, outcome: str = "success", after: int | None = None) -> None:
        """The checks on a commit run now and end in `outcome` after `after` seconds. With faults on they may fail."""
        if outcome == "success" and self.faults.red and self.rng.random() < self.faults.red:
            outcome = "failure"
            self.stats["checks fail"] += 1
        self.gh.set_checks(repo, sha, "pending", "pending")
        self._ci[(repo, sha)] = _Pending(_h().shift(_h().now(), seconds=self.ci_s if after is None else after), outcome)

    def finish_checks(self, number: int, outcome: str = "success", repo: str = REPO) -> None:
        """The checks on the PR's newest commit end now."""
        head = self.gh.prs[(repo, number)]["head_sha"]
        self._ci.pop((repo, head), None)
        self.gh.set_checks(repo, head, outcome, outcome)
        self._hook("check_run", repo, number)

    def close(self, number: int, repo: str = REPO) -> None:
        self.gh.close(repo, number)
        self._queued.pop((repo, number), None)
        self._pr_hook(repo, number)

    def merge(self, number: int, repo: str = REPO) -> str:
        sha = self.gh.merge(repo, number, at=_h().now())
        self._queued.pop((repo, number), None)
        self._pr_hook(repo, number)
        return sha

    def drop(
        self,
        number: int,
        reason: str = "Checks failed in the merge queue",
        check: str | None = "check-1",
        url: str | None = "https://github.com/acme/app/actions/runs/1",
        repo: str = REPO,
    ) -> None:
        """GitHub takes the PR out of the merge queue without merging it."""
        self.gh.drop(repo, number, reason, check, url)
        self._queued.pop((repo, number), None)
        self._pr_hook(repo, number)

    def vanish(self, number: int, repo: str = REPO) -> None:
        """The PR leaves the merge queue unmerged with no webhook and no removal event: only a read shows it is out."""
        self.gh.vanish(repo, number)
        self._queued.pop((repo, number), None)

    def publish(self, tag: str, prerelease: bool = False, repo: str = REPO) -> dict[str, Any]:
        """A release of main's newest commit."""
        release = self.gh.publish(repo, tag, prerelease=prerelease, at=_h().now())
        self._send("release", repo, release=dict(release))
        return release

    def unreleased(self, repo: str = REPO) -> bool:
        """Main has a merge no release holds yet."""
        line = self.gh.history.get(repo) or []
        released = {r["commit_sha"] for r in self.gh.published.get(repo, [])}
        return bool(line) and line[-1] not in released

    def head(self, number: int, repo: str = REPO) -> str:
        return str(self.gh.prs[(repo, number)]["head_sha"])

    def state(self, number: int, repo: str = REPO) -> dict[str, Any]:
        return dict(self.gh.prs[(repo, number)])

    # ------------------------------------------------------------------ time passing
    def tick(self) -> None:
        """What GitHub does on its own by now: checks finish, the queue merges or drops, GitHub comes back up."""
        H = _h()
        now = H.now()
        if self.down_until is not None and now >= self.down_until:
            self.down_until = None
            self.log("fault over: GitHub is back")
        if self.down_until is None and self.faults.down and self.rng.random() < self.faults.down:
            minutes = self.rng.choice((2, 10, 30, 90))
            self.down_until = H.shift(now, seconds=minutes * 60)
            self.stats["GitHub down"] += 1
            self.log(f"fault: GitHub is down for {minutes} minutes")
        for (repo, sha), pending in sorted(self._ci.items(), key=lambda kv: (kv[1].at, kv[0])):
            if pending.at > now:
                continue
            del self._ci[(repo, sha)]
            self.gh.set_checks(repo, sha, pending.outcome, pending.outcome)
            for (r, number), pr in sorted(self.gh.prs.items()):
                if r == repo and pr["head_sha"] == sha:
                    self._hook("check_run", repo, number)
        self.land()

    def land(self) -> None:
        """The merge queue: a queued PR whose checks passed merges `land_after_s` after it was queued; failing checks
        take it out. A drop fault takes it out with a failed queue check instead."""
        now = _h().now()
        for (repo, number), since in sorted(self._queued.items()):
            pr = self.gh.prs.get((repo, number))
            if not pr or pr["queue_state"] not in ("queued", "auto_merge"):
                self._queued.pop((repo, number), None)
                continue
            if _h().shift(since, seconds=self.land_after_s) > now:
                continue
            states = {c["state"] for c in self.gh.checks.get((repo, pr["head_sha"]), []) if not c.get("tico")}
            if "failure" in states:
                self.drop(number, "Checks failed in the merge queue", "check-1", repo=repo)
            elif "pending" in states:
                continue
            elif self.faults.drop and self.rng.random() < self.faults.drop:
                self.stats["queue drop"] += 1
                self.log(f"fault: the merge queue drops {repo}#{number}")
                self.drop(number, "The merge group's checks failed", "e2e", repo=repo)
            else:
                self.merge(number, repo)
                self.log(f"GitHub merges {repo}#{number}")

    # ------------------------------------------------------------------ webhooks
    def _pr_hook(self, repo: str, number: int) -> None:
        self._hook("pull_request", repo, number)

    def _hook(self, event: str, repo: str, number: int) -> None:
        pr = self.gh.prs[(repo, number)]
        fact = {k: v for k, v in pr.items()}
        fact["checks"] = [dict(c) for c in self.gh.checks.get((repo, pr["head_sha"]), [])]
        self._send(event, repo, number=number, fact=fact)

    def _send(self, event: str, repo: str, **what: Any) -> None:
        from backend.item_sources import github as TF

        H = _h()
        self._n += 1
        # When GitHub sent it: a pull_request webhook's `updated_at`. Each is later than the one before, as GitHub's
        # clock moves between two events that the rig's frozen clock puts in the same millisecond.
        self._version = max(TF.version_now(), self._version + 1)
        now = H.now()
        delivery = Delivery(
            id=f"delivery-{self._n}", event=event, repo=repo, version=self._version, due=now, seq=self._n
        )
        for key, value in what.items():
            setattr(delivery, key, value)
        f = self.faults
        if f.lose and self.rng.random() < f.lose:
            self.stats["webhook lost"] += 1
            return
        if f.late and self.rng.random() < f.late:
            self.stats["webhook late"] += 1
            delivery.due = H.shift(now, seconds=self.rng.choice((15, 60, 240, 900)))
        self.outbox.append(delivery)
        if f.repeat and self.rng.random() < f.repeat:
            self.stats["webhook repeated"] += 1
            again = Delivery(**{**delivery.__dict__, "seq": delivery.seq})
            again.due = H.shift(delivery.due, seconds=self.rng.choice((1, 30, 300)))
            self.outbox.append(again)

    def deliver(self, world: Any) -> int:
        """The server takes every webhook due by now (none while GitHub is down). Returns how many it took."""
        if self.is_down():
            return 0
        now = _h().now()
        due = sorted((d for d in self.outbox if d.due <= now), key=lambda d: (d.due, d.seq))
        from backend.item_sources import github as TF

        for d in due:
            self.outbox.remove(d)
            # As on the server (item_sources.github.webhook): a pull_request webhook's version is when GitHub sent it, any
            # other's is when it arrives, so a late check run or release is newer than what came before it.
            self._version = max(TF.version_now(), self._version + 1)
            if d.event != "pull_request":
                d.version = self._version
            with world.tx() as c:
                take(c, d)
            self.taken.append(d)
        return len(due)

    def lose_all(self) -> int:
        """Every webhook on its way is lost."""
        n = len(self.outbox)
        self.outbox.clear()
        self.stats["webhook lost"] += n
        return n


def take(c: Any, d: Delivery) -> bool:
    """One webhook, as the server's webhook takes it: False for a delivery seen before."""
    from backend import release_schedule_routes as RS
    from backend import task_engine as E
    from backend import item_metadata as IM
    from backend.item_sources import github as TF

    if TF.seen_delivery(c, d.id, d.event):
        return False
    if d.event == "release":
        assert d.release is not None
        TF.save_release(c, d.repo, d.release, d.version)
        tasks = list(RS.pending(c))
    else:
        assert d.fact is not None and d.number is not None
        # What the server's webhook does with a PR's fact (item_sources.github.webhook, then save_and_settle): save it, the one
        # wake when it left the queue, the Merge stage's reactions (a late fact queued, "Changed after OK"), and a
        # nudge of the tasks that link it. A pull_request webhook's fact is the whole PR, as the re-read sees it; one
        # older than the saved fact is not saved, and an open PR is read in full on the next pass. A check run only
        # changes the checks of a PR whose saved head it is on; on a head not saved yet it has the PR read in full.
        old = TF.pr(c, d.repo, d.number)
        if d.event == "check_run":
            if old and old["state"] == "open" and old["head_sha"] == d.fact["head_sha"]:
                TF.save_and_settle(c, d.repo, d.number, {"checks": d.fact["checks"]}, d.version)
            elif old:
                IM.mark_due(c, TF.SOURCE, TF.PR, TF.pr_key(d.repo, d.number))
            return True
        if old and int(old["metadata_version"]) > d.version:
            IM.mark_due(c, TF.SOURCE, TF.PR, TF.pr_key(d.repo, d.number))
            return True
        TF.save_and_settle(c, d.repo, d.number, d.fact, d.version)
        return True
    for task in tasks:
        E.advance(c, task)
    return True


def linked(c: Any, repo: str, number: int) -> list[str]:
    """The open tasks on the engine that link a PR."""
    return [
        str(r[0])
        for r in c.execute(
            "SELECT DISTINCT l.task_id FROM task_links l JOIN tasks t ON t.id = l.task_id WHERE l.source='github' "
            "AND l.item_type='pr' AND l.item_key=? AND t.status='open' AND t.pipeline_id IS NOT NULL "
            "ORDER BY l.task_id",
            (f"{repo}#{int(number)}",),
        ).fetchall()
    ]
