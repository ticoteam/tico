"""Golden standings: what Tico does today with GitHub, pinned before the generic "sources" refactor (cut 1 of
reports/2026-11-09-pipelines-v3/sources-build-plan.md) so the refactor can show it changed no behaviour.

Each situation below drives the real code with the in-memory GitHub (backend/tests/helpers/fake_github.py): webhooks
the way the server takes them, the 5-minute re-read, the Merge stage's effects step, queue again. Then it records, in a
stable form (no ids, no times, no random values):

- the task's stage, status and ladder level;
- the saved fact of each linked PR, the saved releases, and the merge-queue effects (`Seam.rows`);
- every requirement of the open visit with (state, whose turn, line, can it be pushed past), and the same for each of
  the pipeline's four GitHub requirements read against the open visit, wherever they sit;
- `waiting_on` (state, who, line), and the bot whose turn it is;
- what Tico did about it: GitHub calls that change a PR (ready / enqueue / dequeue), wakes, `pr_*` history events.

The expected answers are in backend/tests/fixtures/source_golden.json. A difference fails with a readable diff.
`TICO_GOLDEN_WRITE=1` rewrites the situations it runs (only do that when a change in behaviour is wanted, and list the
changed situations in the PR).

CUT 1: `Seam` below is the ONE helper to repoint. Every call into task_facts / merge_queue and every read of their
tables (pr_facts, releases, release_reads, release_commits, effects, requirements.kind, task_links) goes through it. When
those modules move to backend/sources/github.py, backend/item_facts.py and backend/source_actions.py, change `Seam`
only; the situations and the golden file stay as they are. `Seam.WAIT_STATE` maps renamed waiting states (`github` ->
`source`) back to today's names, and `Seam.rows` must give the same normalised shapes from the new tables.
"""

import difflib
import fcntl
import json
import os
import re
import tempfile
from pathlib import Path

import pytest

from backend.item_sources import github as MQ
from backend import task_engine as E
from backend.item_sources import github as F
from backend import task_requirements as R
from backend import waiting_on as WO
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.fake_github import FakeGitHub, GitHubError

GOLDEN = Path(__file__).parent / "fixtures" / "source_golden.json"
WRITE = os.environ.get("TICO_GOLDEN_WRITE") == "1"
REPO, OTHER = "acme/app", "acme/server"
B, RM, EM = "bot:builder", "bot:release", "bot:eng-manager"
GITHUB_KINDS = ("pr_open", "checks_pass", "pr_merged", "in_release")


# ============================================================================= the one seam cut 1 repoints
class Seam:
    """Everything the situations do to, and read from, the GitHub fact and merge-queue code. Cut 1 changes this class
    and nothing else in this file."""

    # Waiting states as `waiting_on` names them -> as the golden file names them.
    WAIT_STATE: dict[str, str] = {"source": "github"}
    # Source action kinds (backend/source_actions.py) -> the merge-queue effect kinds the golden file names.
    EFFECT_KINDS = {"act": "queue_pr", "undo": "dequeue_pr"}
    # A Merge stage's on-enter action: queue the task's PRs.
    QUEUE_ON_ENTER = {"act": "github.queue"}

    @staticmethod
    def github(condition, settings=None):
        """A requirement on the task's GitHub PRs, as a pipeline spec names it (`{"kind": "pr_open"}` in the golden
        file's terms)."""
        out = {"kind": "item", "source": "github", "condition": condition}
        return {**out, "settings": settings} if settings is not None else out

    @staticmethod
    def kind(requirement):
        """A requirement's kind as the golden file names it: a GitHub condition by its name."""
        return requirement.get("condition") or requirement["kind"]

    @staticmethod
    def wait(w):
        """A wait as the golden file records it: [state, who, line]. A wait on a source named no one."""
        state = Seam.WAIT_STATE.get(w.state, w.state)
        return [state, None if w.state == "source" else w.who, w.line]

    # ------------------------------------------------------------------ driving
    @staticmethod
    def webhook(c, event, payload, delivery=None, app_slug=None):
        """A webhook as the server's route takes it (backend/github.py `install_github.handle`): a repeated delivery
        is dropped, then the engine's facts, then the older PR pass. Returns what the facts step returned, or
        "repeat"."""
        from backend import github as G

        if F.seen_delivery(c, delivery, event):
            return "repeat"
        found = F.webhook(c, event, payload, app_slug)
        if event == "pull_request":
            G.pull_request(c, payload)
        elif event in ("check_run", "status"):
            G.pr_signal(c, event, payload)
        elif event == "release":
            G.release(c, payload)
        return found

    @staticmethod
    def read(c, gh, number, repo=REPO):
        """GitHub read now and saved the way the re-read saves it."""
        return F.save_and_settle(c, repo, number, F.read_pr(gh, repo, number), F.version_now())

    @staticmethod
    def refresh(store, gh, now):
        """The 5-minute re-read (PRs and releases)."""
        return F.refresh(store, gh, now)

    @staticmethod
    def run_queue(store, gh, now=None):
        """The Heartbeat's effects step."""
        return MQ.run(store, gh, now)

    @staticmethod
    def requeue(c, task_id, by):
        """Queue again (`tico task queue`): the actions owed, each with the head it is pinned to as `head_sha`."""
        owed = MQ.requeue(c, task_id, by, seen_version=E.task(c, task_id)["version"])
        return [{**e, "head_sha": e["pinned"]} for e in owed]

    @staticmethod
    def pull(c, task_id, by):
        return MQ.pull(c, E.task(c, task_id), by)

    @staticmethod
    def fail_checks(c, number, repo=REPO):
        """A failing check saved on the PR's fact (as a check_run webhook would), at a fresh version."""
        F.save_pr(c, repo, number, {"checks": [{"name": "unit", "state": "failure"}]}, F.version_now())

    # ------------------------------------------------------------------ time passing (rows are backdated)
    @staticmethod
    def age_fact(c, number, field, hours=0, minutes=0, repo=REPO):
        """Move one time of a PR's fact back: read_at (last word from GitHub), head_seen_at, checks_started_at,
        updated_at."""
        assert field in ("read_at", "head_seen_at", "checks_started_at", "updated_at")
        at = H.shift(H.now(), hours=-hours, minutes=-minutes)
        where = ("github", "pr", F.pr_key(repo, number))
        if field in ("read_at", "updated_at"):
            c.execute(f"UPDATE item_metadata SET {field}=? WHERE source=? AND item_type=? AND item_key=?", (at, *where))
        else:
            c.execute(
                "UPDATE item_metadata SET fields=json_set(fields, ?, ?) WHERE source=? AND item_type=? AND item_key=?",
                (f"$.{field}", at, *where),
            )

    @staticmethod
    def age_links(c, task_id, hours):
        c.execute("UPDATE task_links SET created_at=? WHERE task_id=?", (H.shift(H.now(), hours=-hours), task_id))

    @staticmethod
    def age_release_reads(c, hours):
        c.execute("UPDATE github_release_reads SET read_at=?", (H.shift(H.now(), hours=-hours),))

    # ------------------------------------------------------------------ reading
    @staticmethod
    def github_requirements(c, row):
        """The pipeline's GitHub requirements on any stage, in stage then position order: [(name, requirement)]."""
        found = c.execute(
            "SELECT r.id, r.condition FROM requirements r JOIN stages s ON s.id = r.stage_id WHERE s.pipeline_id=? "
            "AND r.withdrawn_at IS NULL AND r.kind = 'item' AND r.source = 'github' "
            "ORDER BY s.position, r.position, r.created_at",
            (row["pipeline_id"],),
        ).fetchall()
        return [(kind, R.get(c, rid)) for rid, kind in found]

    @staticmethod
    def rows(c, task_id):
        """The task's GitHub rows, normalised: {prs, releases, effects, due}."""
        prs = []
        for link in F.prs_of(c, task_id):
            fact = link["metadata"]
            prs.append(
                {
                    "ref": F.pr_ref(link["repo"], link["number"]),
                    "fact": None
                    if fact is None
                    else {
                        "state": fact["state"],
                        "draft": bool(fact["draft"]),
                        "head": fact["head_sha"],
                        "checks_state": fact["checks_state"],
                        "checks": [[ch.get("name"), ch.get("state"), bool(ch.get("tico"))] for ch in fact["checks"]],
                        "merged_sha": fact["merged_sha"],
                        "merged_at": bool(fact["merged_at"]),
                        "queue_state": fact["queue_state"],
                        "queued_at": bool(fact["queued_at"]),
                        "removed_at": bool(fact["removed_at"]),
                        "removed_reason": fact["removed_reason"],
                        "failing_check": fact["failing_check"],
                        "failing_check_url": fact["failing_check_url"],
                        "head_seen_at": bool(fact["head_seen_at"]),
                        "checks_started_at": bool(fact["checks_started_at"]),
                        "read_again": fact["due_at"] is not None,  # a full read asked for on the next pass
                    },
                }
            )
        repos = sorted({str(link["repo"]) for link in F.prs_of(c, task_id)} | {OTHER})
        releases = {}
        for repo in repos:
            saved = F.releases(c, repo, prereleases=True)
            if saved or F.releases_read_at(c, repo):
                releases[repo] = {
                    "read": bool(F.releases_read_at(c, repo)),
                    "publishes": F.publishes_releases(c, repo),
                    "releases": [
                        {
                            "tag": r["tag"],
                            "commit": r["commit_sha"],
                            "prerelease": bool(r["prerelease"]),
                            "holds": sorted(
                                [sha, bool(inc)]
                                for sha, inc in c.execute(
                                    "SELECT commit_sha, included FROM github_release_commits WHERE release_id=?",
                                    (r["id"],),
                                ).fetchall()
                            ),
                        }
                        for r in saved
                    ],
                }
        effects = [
            {
                "kind": Seam.EFFECT_KINDS[e["kind"]],
                "state": e["state"],
                "tries": e["tries"],
                "ref": F.pr_ref(*F.split_key(e["item_key"])),
                "head": e["pinned"],
                "error": e["last_error"],
            }
            for e in (
                dict(zip([d[0] for d in cur.description], r, strict=True))
                for cur in [
                    c.execute(
                        "SELECT e.* FROM effects e JOIN visits v ON v.id = e.visit_id WHERE v.task_id=? "
                        "AND e.source=? AND e.action=? ORDER BY v.entered_at, e.created_at, e.rowid",
                        (task_id, MQ.SOURCE, MQ.QUEUE),
                    )
                ]
                for r in cur.fetchall()
            )
        ]
        due = sorted(F.pr_ref(repo, n) for repo, n in F.due_reads(c, H.shift(H.now(), minutes=10)))
        return {"prs": prs, "releases": releases, "effects": effects, "due": due}

    @staticmethod
    def drops_in_visit(c, task_id):
        return MQ.drops_in_visit(c, task_id, E.task(c, task_id)["visit"]["id"])


# ============================================================================= recording
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SHA = re.compile(r"\b[0-9a-f]{40}\b")
TS = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)?")


def _normal(value, names):
    """Ids -> names (`task`, `visit`, ...), fake shas -> `sha<n>`, timestamps -> `<ts>`."""
    if isinstance(value, dict):
        return {k: _normal(v, names) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normal(v, names) for v in value]
    if not isinstance(value, str):
        return value
    for raw, name in names.items():
        value = value.replace(raw, f"<{name}>")
    value = UUID.sub("<id>", value)
    value = SHA.sub(lambda m: f"sha{int(m.group(0), 16)}", value)
    return TS.sub("<ts>", value)


def record(c, task_id, gh, extra=None):
    from backend.repositories import metadata

    row = E.task(c, task_id)
    names = {task_id: "task"}
    visits = c.execute("SELECT id FROM visits WHERE task_id=? ORDER BY entered_at, rowid", (task_id,)).fetchall()
    for n, (vid,) in enumerate(visits, 1):
        names[vid] = f"visit{n}"
    for (rid,) in c.execute("SELECT id FROM requirements").fetchall():
        names[rid] = "requirement"

    def standing(s):
        return {
            "state": s.state,
            "whose": s.on,
            "line": s.line,
            "can_push_past": R.can_push_past(s.requirement),
        }

    status = c.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
    out = {"stage": row["stage"]["name"], "status": status, "level": (row.get("visit") or {}).get("level")}
    out["requirements"] = [{"kind": Seam.kind(s.requirement), **standing(s)} for s in R.standings(c, row)]
    out["github_kinds"] = (
        [{"kind": kind, **standing(R.standing(c, row, req))} for kind, req in Seam.github_requirements(c, row)]
        if row.get("visit")
        else []
    )
    out["waiting"] = [Seam.wait(w) for w in WO.of(c, row)]
    out["turn"] = WO.turn(c, row) if row.get("visit") else None
    out.update(Seam.rows(c, task_id))
    out["github_calls"] = [list(call) for call in gh.calls if call[0] in ("ready", "enqueue", "dequeue")]
    out["wakes"] = [[key, who] for key, who in X.wakes(c, task_id)]
    out["older_pr_pass_items"] = metadata(c, "github-task-wake:" + task_id).get("items", [])
    out["events"] = [kind for kind in X.events(c, task_id) if kind.startswith("pr_")]
    if extra:
        out["extra"] = extra
    found = _normal(json.loads(json.dumps(out, default=str)), names)
    found["wakes"].sort()  # by what they say, not when: two wakes in one write have no order
    return found


# ============================================================================= building blocks
class World:
    def __init__(self, store):
        self.store = store
        self.gh = FakeGitHub()

    def tx(self):
        return self.store.transaction()


def later(minutes=10):
    return H.shift(H.now(), minutes=minutes)


def engineering(c, release=None):
    """Build (PR open, Checks pass) -> Merge (queues PRs; PR merged) -> Release (In a release), managed by EM: the
    pipeline test_github_facts_merge_queue.py drives, in a company whose GitHub App is installed."""
    for slug in ("builder", "release", "eng-manager"):
        X.bot(c, slug)
    X.github_installed(c)
    return X.pipeline(
        c,
        "eng",
        "Engineering",
        stages=(
            {"name": "Build", "owner": B, "requirements": [Seam.github("pr_open"), Seam.github("checks_pass")]},
            {"name": "Merge", "owner": RM, "on_enter": Seam.QUEUE_ON_ENTER, "requirements": [Seam.github("pr_merged")]},
            {"name": "Release", "owner": RM, "requirements": [Seam.github("in_release", release or {})]},
        ),
        manager=EM,
    )


def new(c, *numbers, repo=REPO):
    task = X.new_task(c, X.ANA, "Ship it", "eng")
    for number in numbers:
        H.task_link(c, X.ANA, task["id"], f"https://github.com/{repo}/pull/{number}")
    return task["id"]


def stage(c, task_id):
    return E.task(c, task_id)["stage"]["name"]


def pr_payload(gh, action, number=1, updated_at=None, repo=REPO, **over):
    """GitHub's pull_request webhook built from the fake's PR as it is now."""
    pr = gh.prs[(repo, number)]
    snapshot = {
        "number": number,
        "html_url": f"https://github.com/{repo}/pull/{number}",
        "state": "closed" if pr["state"] != "open" else "open",
        "merged": pr["state"] == "merged",
        "merge_commit_sha": pr["merged_sha"],
        "merged_at": pr["merged_at"],
        "draft": pr["draft"],
        "head": {"sha": pr["head_sha"], "ref": "feature"},
    }
    if updated_at:
        snapshot["updated_at"] = updated_at
    return {"action": action, "pull_request": snapshot, "repository": {"full_name": repo}, **over}


def check_payload(sha, name="unit", status="completed", conclusion="success", slug="ci", numbers=(), repo=REPO):
    run = {"head_sha": sha, "name": name, "status": status, "conclusion": conclusion, "app": {"slug": slug}}
    if numbers:
        run["pull_requests"] = [{"number": n} for n in numbers]
    return {"check_run": run, "repository": {"full_name": repo}}


def in_build(w, *numbers, checks=None, draft=False):
    """A task in Build with its PRs open and read; `checks` the states of the checks on each head."""
    with w.tx() as c:
        engineering(c)
        task_id = new(c, *numbers)
        for number in numbers:
            head = w.gh.open_pr(REPO, number, draft=draft)
            if checks:
                w.gh.set_checks(REPO, head, *checks)
            Seam.read(c, w.gh, number)
    return task_id


def in_merge(w, *numbers, draft=False, release=None):
    """A task whose PRs are open with green checks: it moved to Merge, which owes their queue."""
    with w.tx() as c:
        engineering(c, release)
        task_id = new(c, *numbers)
        for number in numbers:
            w.gh.set_checks(REPO, w.gh.open_pr(REPO, number, draft=draft), "success")
            Seam.read(c, w.gh, number)
        assert stage(c, task_id) == "Merge"
    return task_id


def queued(w, *numbers, release=None):
    task_id = in_merge(w, *(numbers or (1,)), release=release)
    Seam.run_queue(w.store, w.gh)
    return task_id


def dropped(w, times):
    """A PR dropped from the queue `times` times in one visit, the owner (then the manager) queuing it again between."""
    task_id = queued(w)
    for n in range(1, times + 1):
        w.gh.drop(REPO, 1, "The merge group's checks failed", check="e2e", url="https://ci.example/run/9")
        Seam.refresh(w.store, w.gh, later(10 * n))
        if n < times:
            with w.tx() as c:
                Seam.requeue(c, task_id, EM if n >= 2 else RM)
            Seam.run_queue(w.store, w.gh)
    return task_id


def merged(w, release=None, published=()):
    """A queued PR GitHub merged; the re-read saw it. `published` releases are made before the merge."""
    for tag, at in published:
        w.gh.history.setdefault(REPO, []).append(w.gh.sha())
        w.gh.publish(REPO, tag, at=at)
    task_id = queued(w, release=release)
    w.gh.merge(REPO, 1)
    return task_id


SITUATIONS = {}


def situation(name):
    def add(fn):
        assert name not in SITUATIONS, name
        SITUATIONS[name] = fn
        return fn

    return add


# ============================================================================= PR open
@situation("unlinked task")
def _(w):
    with w.tx() as c:
        engineering(c)
        return new(c)


@situation("linked, never read")
def _(w):
    with w.tx() as c:
        engineering(c)
        return new(c, 1)


@situation("webhook for a PR no task links")
def _(w):
    with w.tx() as c:
        engineering(c)
        task_id = new(c, 1)
        w.gh.open_pr(REPO, 9)
        said = Seam.webhook(c, "pull_request", pr_payload(w.gh, "opened", 9))
    return task_id, {"webhook": said}


@situation("PR opened by webhook, no checks yet")
def _(w):
    with w.tx() as c:
        engineering(c)
        task_id = new(c, 1)
        w.gh.open_pr(REPO, 1)
        said = Seam.webhook(c, "pull_request", pr_payload(w.gh, "opened"))
    return task_id, {"webhook": said}


@situation("draft PR open, checks pass")
def _(w):
    return in_build(w, 1, checks=("success",), draft=True)


@situation("PR closed without merging")
def _(w):
    task_id = in_build(w, 1, checks=("pending",))
    w.gh.close(REPO, 1)
    with w.tx() as c:
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "closed"))
    return task_id


@situation("PR closed then reopened")
def _(w):
    task_id = in_build(w, 1, checks=("pending",))
    w.gh.close(REPO, 1)
    with w.tx() as c:
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "closed"))
    w.gh.prs[(REPO, 1)]["state"] = "open"
    with w.tx() as c:
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "reopened"))
    return task_id


@situation("two PRs, the first closed, the second open")
def _(w):
    task_id = in_build(w, 1, 2, checks=("pending",))
    w.gh.close(REPO, 1)
    Seam.refresh(w.store, w.gh, later())
    return task_id


@situation("two PRs, both closed")
def _(w):
    task_id = in_build(w, 1, 2, checks=("pending",))
    w.gh.close(REPO, 1)
    w.gh.close(REPO, 2)
    Seam.refresh(w.store, w.gh, later())
    return task_id


@situation("PR merged by hand while in Build")
def _(w):
    with w.tx() as c:
        engineering(c)
        task_id = new(c, 1)
        w.gh.open_pr(REPO, 1)
        w.gh.merge(REPO, 1)
        Seam.read(c, w.gh, 1)
    return task_id


# ============================================================================= Checks pass
@situation("checks running (check_run webhook)")
def _(w):
    with w.tx() as c:
        engineering(c)
        task_id = new(c, 1)
        head = w.gh.open_pr(REPO, 1)
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "opened"))
        Seam.webhook(c, "check_run", check_payload(head, status="in_progress", conclusion=None))
    return task_id


@situation("checks pass by webhook, moves to Merge")
def _(w):
    with w.tx() as c:
        engineering(c)
        task_id = new(c, 1)
        head = w.gh.open_pr(REPO, 1)
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "opened"))
        Seam.webhook(c, "check_run", check_payload(head))
    return task_id


@situation("checks failing")
def _(w):
    return in_build(w, 1, checks=("failure", "success"))


@situation("checks failing and running")
def _(w):
    return in_build(w, 1, checks=("pending", "failure"))


@situation("no checks, head seen under 10 minutes")
def _(w):
    return in_build(w, 1)


@situation("no checks configured (C13, 10 minutes)")
def _(w):
    task_id = in_build(w, 1)
    with w.tx() as c:
        Seam.age_fact(c, 1, "head_seen_at", minutes=11)
        E.advance(c, task_id)
    return task_id


@situation("checks running over 2 hours")
def _(w):
    task_id = in_build(w, 1, checks=("pending",))
    with w.tx() as c:
        Seam.age_fact(c, 1, "checks_started_at", hours=3)
    return task_id


@situation("only Tico's own check failed")
def _(w):
    with w.tx() as c:
        engineering(c)
        task_id = new(c, 1)
        head = w.gh.open_pr(REPO, 1)
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "opened"))
        Seam.webhook(
            c, "check_run", check_payload(head, name="Tico", conclusion="failure", slug="tico-app"), None, "tico-app"
        )
        Seam.webhook(
            c, "status", {"sha": head, "context": "ci/lint", "state": "pending", "repository": {"full_name": REPO}}
        )
    return task_id


@situation("commit status success")
def _(w):
    with w.tx() as c:
        engineering(c)
        task_id = new(c, 1)
        head = w.gh.open_pr(REPO, 1)
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "opened"))
        Seam.webhook(
            c, "status", {"sha": head, "context": "ci/lint", "state": "success", "repository": {"full_name": REPO}}
        )
    return task_id


@situation("check run before its synchronize")
def _(w):
    task_id = in_build(w, 1, checks=("pending",))
    head = w.gh.push(REPO, 1)
    with w.tx() as c:
        Seam.webhook(c, "check_run", check_payload(head, numbers=(1,)))
    return task_id


@situation("new commit pushed in Build")
def _(w):
    task_id = in_build(w, 1, checks=("failure",))
    w.gh.push(REPO, 1)
    with w.tx() as c:
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "synchronize"))
    return task_id


@situation("two PRs, the second failing")
def _(w):
    with w.tx() as c:
        engineering(c)
        task_id = new(c, 1, 2)
        w.gh.set_checks(REPO, w.gh.open_pr(REPO, 1), "success")
        w.gh.set_checks(REPO, w.gh.open_pr(REPO, 2), "failure")
        Seam.read(c, w.gh, 1)
        Seam.read(c, w.gh, 2)
    return task_id


@situation("two PRs in two repos, one not read yet")
def _(w):
    with w.tx() as c:
        engineering(c)
        task_id = new(c, 1)
        H.task_link(c, X.ANA, task_id, f"https://github.com/{OTHER}/pull/4")
        w.gh.set_checks(REPO, w.gh.open_pr(REPO, 1), "success")
        Seam.read(c, w.gh, 1)
    return task_id


# ============================================================================= the merge queue
@situation("entered Merge, queue owed")
def _(w):
    return in_merge(w, 1)


@situation("queued")
def _(w):
    return queued(w)


@situation("two PRs queued")
def _(w):
    return queued(w, 1, 2)


@situation("draft reaches Merge: marked ready and queued")
def _(w):
    task_id = in_merge(w, 1, draft=True)
    Seam.run_queue(w.store, w.gh)
    return task_id


@situation("repo without a merge queue: auto-merge")
def _(w):
    w.gh = FakeGitHub(queue=False)
    return queued(w)


@situation("enqueue failed once, retry owed")
def _(w):
    task_id = in_merge(w, 1)
    w.gh.fail_next("enqueue")
    Seam.run_queue(w.store, w.gh)
    return task_id


@situation("enqueue failed three times")
def _(w):
    task_id = in_merge(w, 1)
    now = H.now()
    for seconds in (0, 61, 400):
        w.gh.fail_next("enqueue")
        Seam.run_queue(w.store, w.gh, H.shift(now, seconds=seconds))
    return task_id


@situation("enqueue refused: head moved")
def _(w):
    task_id = in_merge(w, 1)
    w.gh.push(REPO, 1)
    Seam.run_queue(w.store, w.gh)
    return task_id


@situation("enqueue refused by a branch rule")
def _(w):
    task_id = in_merge(w, 1)
    w.gh.rule(REPO, 1)
    now = H.now()
    Seam.run_queue(w.store, w.gh, now)
    Seam.run_queue(w.store, w.gh, H.shift(now, minutes=30))
    return task_id


@situation("dropped from the queue, 1st")
def _(w):
    return dropped(w, 1)


@situation("dropped from the queue, 2nd")
def _(w):
    return dropped(w, 2)


@situation("dropped from the queue, 3rd: the manager")
def _(w):
    return dropped(w, 3)


@situation("dropped from the queue, 4th: a person")
def _(w):
    return dropped(w, 4)


@situation("queue entry vanished")
def _(w):
    task_id = queued(w)
    w.gh.vanish(REPO, 1)
    Seam.refresh(w.store, w.gh, later())
    return task_id


@situation("dequeued webhook, before the re-read")
def _(w):
    task_id = queued(w)
    w.gh.drop(REPO, 1, "failed_checks")
    with w.tx() as c:
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "dequeued", reason="failed_checks"))
    return task_id


@situation("dequeued webhook, then the re-read")
def _(w):
    task_id = queued(w)
    w.gh.drop(REPO, 1, "Checks failed in the merge queue", check="e2e")
    with w.tx() as c:
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "dequeued", reason="failed_checks"))
    Seam.refresh(w.store, w.gh, H.now())
    return task_id


@situation("queue again after a drop, checks green")
def _(w):
    task_id = dropped(w, 1)
    with w.tx() as c:
        owed = Seam.requeue(c, task_id, RM)
    Seam.run_queue(w.store, w.gh)
    return task_id, {"owed": [[e["state"], e["head_sha"]] for e in owed]}


@situation("queue again refused while checks run, and before any drop")
def _(w):
    task_id = in_merge(w, 1)
    said = {}
    with w.tx() as c:
        try:
            Seam.requeue(c, task_id, RM)
        except H.Refused as refused:
            said["before_drop"] = [refused.rule, refused.detail]
    Seam.run_queue(w.store, w.gh)
    w.gh.drop(REPO, 1, "It conflicts with its base branch")
    w.gh.set_checks(REPO, w.gh.push(REPO, 1), "pending")
    Seam.refresh(w.store, w.gh, later())
    with w.tx() as c:
        for who in (RM, B):
            try:
                Seam.requeue(c, task_id, who)
            except H.Refused as refused:
                said[who] = [refused.rule, refused.detail]
    return task_id, said


@situation("commit pushed in Merge after a drop")
def _(w):
    task_id = dropped(w, 1)
    w.gh.push(REPO, 1)
    Seam.refresh(w.store, w.gh, later(30))
    return task_id


@situation("moved back from Merge while queued")
def _(w):
    task_id = queued(w)
    with w.tx() as c:
        Seam.fail_checks(c, 1)
        E.move(c, task_id, "Build", X.ANA, "Needs another look")
    Seam.run_queue(w.store, w.gh)
    return task_id


@situation("closed before GitHub queued it")
def _(w):
    task_id = in_merge(w, 1)
    with w.tx() as c:
        E.move(c, task_id, "closed", X.ANA, "Not needed")
    return task_id


@situation("closed after GitHub merged it anyway")
def _(w):
    task_id = queued(w)
    w.gh.merge(REPO, 1)
    with w.tx() as c:
        E.move(c, task_id, "closed", X.ANA, "Not needed")
    Seam.run_queue(w.store, w.gh)
    return task_id


@situation("PR closed by hand in Merge")
def _(w):
    task_id = queued(w)
    w.gh.close(REPO, 1)
    Seam.refresh(w.store, w.gh, later())
    return task_id


@situation("queued by someone else (enqueued webhook)")
def _(w):
    task_id = in_merge(w, 1)
    w.gh.prs[(REPO, 1)]["queue_state"] = "queued"
    with w.tx() as c:
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "enqueued"))
    return task_id


@situation("auto-merge turned on by someone else (webhook)")
def _(w):
    task_id = in_merge(w, 1)
    w.gh.prs[(REPO, 1)]["queue_state"] = "auto_merge"
    with w.tx() as c:
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "auto_merge_enabled"))
    return task_id


@situation("sent back to Build after a drop, then into Merge again")
def _(w):
    task_id = dropped(w, 1)
    with w.tx() as c:
        moved = E.move(c, task_id, "Build", X.ANA, "Needs the Builder")
        E.move(c, task_id, "Merge", X.ANA, "Again", seen_version=moved["version"])
    Seam.run_queue(w.store, w.gh)
    with w.tx() as c:
        drops = Seam.drops_in_visit(c, task_id)
    return task_id, {"drops_in_visit": drops}


@situation("sent back while GitHub queues it")
def _(w):
    task_id = in_merge(w, 1)
    enqueue = w.gh.enqueue

    def slow(repo, number, head):
        with w.tx() as c:  # the task moves back while the call is out
            Seam.fail_checks(c, 1)
            E.move(c, task_id, "Build", X.ANA, "Needs another look")
        return enqueue(repo, number, head)

    w.gh.enqueue = slow
    Seam.run_queue(w.store, w.gh)
    w.gh.enqueue = enqueue
    Seam.run_queue(w.store, w.gh)
    return task_id


@situation("merged from the queue (webhook closed+merged)")
def _(w):
    task_id = queued(w)
    w.gh.merge(REPO, 1)
    with w.tx() as c:
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "closed"))
    return task_id


# ============================================================================= In a release
@situation("merged, releases never read")
def _(w):
    task_id = merged(w)
    with w.tx() as c:
        Seam.read(c, w.gh, 1)
    return task_id


@situation("merged, the repo publishes no releases (C31)")
def _(w):
    task_id = merged(w)
    Seam.refresh(w.store, w.gh, later())
    return task_id


@situation("merged, only an older release (not asked)")
def _(w):
    task_id = merged(w, published=(("v0.9.0", "2026-01-01T00:00:00.000000Z"),))
    Seam.refresh(w.store, w.gh, later())
    return task_id


@situation("merged, a newer release that does not hold it")
def _(w):
    task_id = merged(w, published=(("v0.9.0", "2099-01-01T00:00:00.000000Z"),))
    Seam.refresh(w.store, w.gh, later())
    return task_id


@situation("merged, a release holds it: Done")
def _(w):
    task_id = merged(w, published=(("v0.9.0", "2026-01-01T00:00:00.000000Z"),))
    Seam.refresh(w.store, w.gh, later())
    w.gh.publish(REPO, "v1.0.0", at="2099-01-01T00:00:00.000000Z")
    Seam.refresh(w.store, w.gh, later(20))
    return task_id


@situation("merged, only a pre-release holds it")
def _(w):
    task_id = merged(w, published=(("v0.9.0", "2026-01-01T00:00:00.000000Z"),))
    Seam.refresh(w.store, w.gh, later())
    w.gh.publish(REPO, "v1.0.0-rc.1", at="2099-01-01T00:00:00.000000Z")
    Seam.refresh(w.store, w.gh, later(20))
    return task_id


@situation("merged, release repo is another repo")
def _(w):
    task_id = merged(w, release={"release_repo": OTHER})
    with w.tx() as c:
        Seam.read(c, w.gh, 1)
    return task_id


@situation("release published webhook")
def _(w):
    task_id = merged(w)
    with w.tx() as c:
        Seam.read(c, w.gh, 1)
        payload = {
            "action": "published",
            "release": {"tag_name": "v1.2.0", "published_at": "2099-01-01T00:00:00Z"},
            "repository": {"full_name": REPO},
        }
        Seam.webhook(c, "release", payload)
    return task_id


# ============================================================================= GitHub not answering
class Refusing(FakeGitHub):
    """Every PR read fails with `reason`."""

    def __init__(self, reason):
        super().__init__()
        self.reason = reason

    def read_pr(self, repo, number):
        self._call("read_pr", repo, number)
        raise GitHubError(self.reason)


@situation("repo not connected")
def _(w):
    w.gh = Refusing("The GitHub app is not installed on acme/app")
    with w.tx() as c:
        engineering(c)
        task_id = new(c, 1)
    Seam.refresh(w.store, w.gh, later())
    return task_id


@situation("access refused, 2 hours")
def _(w):
    w.gh = Refusing("Resource not accessible by integration")
    with w.tx() as c:
        engineering(c)
        task_id = new(c, 1)
        Seam.age_links(c, task_id, 3)
    Seam.refresh(w.store, w.gh, later())
    return task_id


@situation("PR gone, never read")
def _(w):
    with w.tx() as c:
        engineering(c)
        task_id = new(c, 1)
    Seam.refresh(w.store, w.gh, later())
    return task_id


@situation("PR gone after it was read, 2 hours")
def _(w):
    task_id = in_build(w, 1, checks=("pending",))
    del w.gh.prs[(REPO, 1)]
    Seam.refresh(w.store, w.gh, later())
    with w.tx() as c:
        Seam.age_fact(c, 1, "read_at", hours=3)
    return task_id


@situation("GitHub down once, then answers")
def _(w):
    with w.tx() as c:
        engineering(c)
        task_id = new(c, 1)
    w.gh.open_pr(REPO, 1)
    w.gh.fail_next("read_pr")
    Seam.refresh(w.store, w.gh, H.now())
    Seam.refresh(w.store, w.gh, H.now())
    return task_id


@situation("2 hours of silence on a PR never read")
def _(w):
    with w.tx() as c:
        engineering(c)
        task_id = new(c, 1)
        Seam.age_links(c, task_id, 3)
    return task_id


@situation("2 hours of silence in the merge queue")
def _(w):
    task_id = queued(w)
    with w.tx() as c:
        Seam.age_fact(c, 1, "read_at", hours=3)
    return task_id


@situation("2 hours of silence waiting for releases")
def _(w):
    task_id = merged(w)
    with w.tx() as c:
        Seam.read(c, w.gh, 1)
        Seam.age_fact(c, 1, "updated_at", hours=3)
    return task_id


@situation("2 hours since releases were last read")
def _(w):
    task_id = merged(w, published=(("v0.9.0", "2026-01-01T00:00:00.000000Z"),))
    Seam.refresh(w.store, w.gh, later())
    with w.tx() as c:
        Seam.age_release_reads(c, 3)
    return task_id


# ============================================================================= webhooks out of order, repeated
@situation("webhook with an older updated_at after a newer one")
def _(w):
    with w.tx() as c:
        engineering(c)
        task_id = new(c, 1)
        w.gh.open_pr(REPO, 1, head="a" * 40)
        old = pr_payload(w.gh, "opened", updated_at="2026-11-10T10:00:00Z")
        w.gh.push(REPO, 1, head="b" * 40)
        pushed = pr_payload(w.gh, "synchronize", updated_at="2026-11-10T10:00:05Z")
        said = [Seam.webhook(c, "pull_request", pushed), Seam.webhook(c, "pull_request", old)]
    return task_id, {"webhooks": said}


@situation("late dequeued after the merge never unmerges")
def _(w):
    task_id = queued(w)
    open_snapshot = pr_payload(w.gh, "dequeued", reason="merged")
    w.gh.merge(REPO, 1)
    with w.tx() as c:
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "closed"))
        Seam.webhook(c, "pull_request", open_snapshot)
        Seam.webhook(c, "pull_request", {**open_snapshot, "action": "synchronize"})
    return task_id


@situation("a webhook delivered twice")
def _(w):
    with w.tx() as c:
        engineering(c)
        task_id = new(c, 1)
        head = w.gh.open_pr(REPO, 1)
        Seam.webhook(c, "pull_request", pr_payload(w.gh, "opened"), delivery="d-1")
        payload = check_payload(head, status="in_progress", conclusion=None)
        said = [Seam.webhook(c, "check_run", payload, delivery="d-2")]
        said.append(Seam.webhook(c, "check_run", check_payload(head), delivery="d-2"))  # the repeat says done: dropped
    return task_id, {"webhooks": said}


# ============================================================================= the test
def _golden():
    return json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}


def _write(name, value):
    """Rewrite one situation in the golden file; xdist workers take turns on a lock."""
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    with open(Path(tempfile.gettempdir()) / "tico_source_golden.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = _golden()
        data[name] = value
        data = {k: data[k] for k in sorted(data) if k in SITUATIONS}
        GOLDEN.write_text(json.dumps(data, indent=1, sort_keys=False) + "\n")


@pytest.mark.parametrize("name", list(SITUATIONS))
def test_situation_matches_the_golden_standings(company, name):
    w = World(company)
    got = SITUATIONS[name](w)
    task_id, extra = got if isinstance(got, tuple) else (got, None)
    with company.transaction() as c:
        found = record(c, task_id, w.gh, extra)
    if WRITE:
        _write(name, found)
        return
    golden = _golden()
    assert name in golden, f"{name!r} is not in {GOLDEN.name}: run with TICO_GOLDEN_WRITE=1"
    if found != golden[name]:
        want = json.dumps(golden[name], indent=1).splitlines()
        have = json.dumps(found, indent=1).splitlines()
        diff = "\n".join(difflib.unified_diff(want, have, "golden", "now", lineterm="", n=2))
        pytest.fail(f"{name}: today's GitHub behaviour changed\n{diff}", pytrace=False)
