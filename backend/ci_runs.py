"""CI build time (Chris, Nov 7): how many VM-minutes of remote test runs a dev ticket's builder may start, and what the
repository's workflow runs cost the company, in Finance.

The heartbeat's sweep (`sweep`) records every recent workflow run of the repository in ci_runs. Once a run's jobs have
finished it is priced (job-minutes x rate x vCPUs / 4; hosted runners cost 0) and posted to Finance account 6150 "CI"
against Accrued expenses (2100), so the day's CI spend counts in the CI budget as it happens. Money is cents.

Only a builder's remote run is ever refused, and only by the permit scripts/remote.py asks before it dispatches: when
its ticket has used its minutes (`ci_minutes_per_ticket`, plus the one extension a manager grants at half of it), or
when today's share of the CI budget has no room for its estimate. A refusal of the ticket's minutes wakes the ticket's
manager (the nudge in backend/stranded_resume.py). Release, PR, queue and other runs are never refused. A remote run
dispatched without a permit is recorded as unpermitted and counted, not blocked.

Today's share is the CI budget's amount in the window of today (Finance's in_window); no amount means no daily gate.
Its spend is what posted to 6150 today plus the holds placed on it (source "ci") that have not posted yet.
"""

import math
import re
from datetime import timedelta
from typing import Any

from fastapi import Request
from pydantic import Field

from . import task_caps
from .finance import budgeting
from .finance.context import is_set_up, load
from .finance.core import budgets as B
from .finance.core import dates
from .finance.core.org import SYSTEM
from .finance.posting import post_entry
from .finance.store import budgets as SB
from .finance.store import ledger as SL
from .models import Contract
from .store import H, Problem

CPU_BASE = 4  # the vCPUs the rate is quoted for
CPUS = (4, 8, 16)  # the runner sizes remote.py offers
MINUTES_MAX = 60  # remote.py's --minutes limit
EXTENSION_PERCENT = 50  # one extension of this share of the ticket's minutes
RESERVE_HOURS = 12  # a permit's reserve frees itself when its run never reports back
SWEEP_HOURS = 6  # the runs created this far back are listed each sweep
PAGES = 5  # 100 runs a page
REMOTE_TITLE = re.compile(r"Remote run ([0-9a-f]{12})")
REMOTE_ID = re.compile(r"[0-9a-f]{12}")
CPU_LABEL = re.compile(r"(\d+)cpu")
WORKFLOW_KINDS = {
    "ci.yml": "ci",
    "pr-checks.yml": "pr",
    "remote-run.yml": "remote",
    "train.yml": "release",
    "release.yml": "release",
    "hotfix.yml": "release",
}
# The rate and cap the Rules page quotes: install() sets them from the running settings (backend/config.py).
ACTIVE = {"minutes": 120, "rate": 0.0016}
# The CHART rows' subtypes (backend/finance/setup.py): found by subtype, since an older book may have another account on
# the seeded code (a card liability on 2100).
CI_SUBTYPE, ACCRUED_SUBTYPE = "ci", "accrued"


class Permit(Contract):
    """A builder's remote run asking to start: its ticket, the run's id (remote.py's), runner size and time limit."""

    task_id: str = Field(min_length=1, max_length=200)
    remote_id: str = Field(min_length=12, max_length=12)
    cpus: int
    minutes: int


class Extension(Contract):
    task_id: str = Field(min_length=1, max_length=200)


# ---------------------------------------------------------------------------------------------------- pricing
def cents_for(minutes: float, cpus: int, rate: float) -> int:
    """Cents for `minutes` job-minutes on a runner of `cpus` vCPUs, at `rate` USD per job-minute on 4 vCPUs."""
    if minutes <= 0 or rate <= 0:
        return 0
    return round(minutes * rate * 100 * max(cpus, 1) / CPU_BASE)


def _runner(labels: list[str]) -> tuple[int, bool]:
    """(vCPUs, hosted) of a job from its runner labels: a RunsOn runner names its size (`4cpu-linux-x64`); a GitHub-hosted
    Ubuntu runner has no size in its label and costs Tico nothing. Anything else is taken as a 4-vCPU runner."""
    for label in labels:
        found = CPU_LABEL.search(str(label))
        if found:
            return int(found.group(1)), False
    if any(str(label).startswith("ubuntu-") for label in labels):
        return CPU_BASE, True
    return CPU_BASE, False


def price(jobs: list[dict[str, Any]], rate: float) -> tuple[int, int]:
    """(job-minutes, cents) of a finished run's jobs. Each job bills whole minutes, as GitHub does."""
    minutes, cents = 0, 0
    for job in jobs:
        if not job.get("started_at") or not job.get("completed_at"):
            continue
        start, end = H.parse_ts(job["started_at"]), H.parse_ts(job["completed_at"])
        if end <= start:
            continue
        spent = math.ceil((end - start).total_seconds() / 60)
        cpus, hosted = _runner(job.get("labels") or [])
        minutes += spent
        cents += 0 if hosted else cents_for(spent, cpus, rate)
    return minutes, cents


def kind_of(run: dict[str, Any]) -> str:
    """The run's kind: a merge-queue check, a workflow's kind by its file, or `other`."""
    if run.get("event") == "merge_group":
        return "queue"
    return WORKFLOW_KINDS.get(str(run.get("path") or "").rsplit("/", 1)[-1], "other")


# ---------------------------------------------------------------------------------------------------- the ticket cap
def extension_minutes(minutes_per_ticket: int) -> int:
    return math.ceil(minutes_per_ticket * EXTENSION_PERCENT / 100)


def ticket_cap(c, task_id: str, minutes_per_ticket: int) -> int:
    row = c.execute("SELECT minutes FROM ci_run_extensions WHERE task_id=?", (task_id,)).fetchone()
    return minutes_per_ticket + (int(row["minutes"]) if row else 0)


def ticket_used(c, task_id: str) -> int:
    """The VM-minutes a ticket's builder runs count: a run that has finished its real minutes, one still out its
    requested minutes. Refused runs count nothing."""
    row = c.execute(
        "SELECT COALESCE(SUM(CASE WHEN seen_at != '' THEN minutes ELSE requested_minutes END), 0) AS used "
        "FROM ci_runs WHERE task_id=? AND kind='builder' AND status='permitted'",
        (task_id,),
    ).fetchone()
    return int(row["used"])


# ---------------------------------------------------------------------------------------------------- the daily share
def _budget(c, book):
    account = next((a.id for a in SL.accounts(c, book.id).values() if a.subtype == CI_SUBTYPE), None)
    if account is None:
        return None
    return next(
        (b for b in SB.budgets(c, book.id).values() if b.account_id == account and not b.parent_id and not b.catch_all),
        None,
    )


def day_share(c, at: str):
    """(budget, today's share in cents or None). None when Finance is not set up, the CI budget is missing or off, or no
    amount is set: then no daily gate applies."""
    if not is_set_up(c):
        return None, None
    book = SL.book(c)
    budget = _budget(c, book) if book is not None else None
    if budget is None or not budget.active:
        return budget, None
    versions = SB.versions(c, [budget.id]).get(budget.id, [])
    day = dates.day(at[:10])
    return budget, B.in_window(budget, versions, (day, day), book.fiscal_year_start)


def day_spent(c, book, budget, at: str) -> int:
    """Today's CI spend in cents: posted to the account today, and holds on it that have not posted yet."""
    day = at[:10]
    return SL.rollup_sum(c, book.id, day, day, [budget.account_id]) + SB.held(c, [budget.id], day, day)


def digest_line(c, at: str | None = None) -> str | None:
    """The one line of CI build time in a manager's update request (updates.request_text), for today: what CI has cost
    or reserved against today's share, and the builder runs refused today. None when there is nothing to say."""
    at = at or H.now()
    book = SL.book(c) if is_set_up(c) else None
    budget = _budget(c, book) if book is not None else None
    refused = c.execute(
        "SELECT COUNT(*) AS n FROM ci_runs WHERE status = 'refused' AND created_at LIKE ?", (at[:10] + "%",)
    ).fetchone()["n"]
    spent = day_spent(c, book, budget, at) if budget is not None else 0
    if not spent and not refused:
        return None
    _, share = day_share(c, at)
    line = f"CI today: ${spent / 100:,.2f}"
    if share is not None:
        line += f" of a ${share / 100:,.2f} share"
    if refused:
        line += f"; {refused} builder run{'' if refused == 1 else 's'} refused"
    return line + "."


# ---------------------------------------------------------------------------------------------------- the permit
def _refusal(c, who: str, task, remote_id: str, cpus: int, minutes: int, reserve: int, why: str, at: str, cap: int):
    # The manager hears once per cap: the first ticket refusal since the ticket's last extension (or ever).
    ticket = why.startswith("ticket ")
    first = (
        ticket
        and not c.execute(
            "SELECT 1 FROM ci_runs WHERE task_id=? AND status='refused' AND reason LIKE 'ticket %' AND created_at >= ? LIMIT 1",
            (task["id"], _granted_at(c, task["id"])),
        ).fetchone()
    )
    c.execute(
        "INSERT INTO ci_runs(kind, status, remote_id, task_id, bot, cpus, requested_minutes, reserve_cents, reason, "
        "created_at, seen_at) VALUES('builder','refused',?,?,?,?,?,?,?,?,?)",
        (remote_id, task["id"], who, cpus, minutes, reserve, why, at, at),
    )
    if first:
        _nudge(c, task, who, cap, at)
    return {"allowed": False, "message": f"Remote run refused: {why}.", "cap": cap}


def _granted_at(c, task_id: str) -> str:
    row = c.execute("SELECT created_at FROM ci_run_extensions WHERE task_id=?", (task_id,)).fetchone()
    return row["created_at"] if row else ""


def _nudge(c, task, who: str, cap: int, at: str) -> None:
    """The ticket's manager gets one ticket to act on: the builder used its minutes, and one extension is its to grant.
    As the stalled-task nudge does (backend/stranded_resume.py), only a bot manager is woken; a person reads it in the
    ticket."""
    managers = task_caps.above(c, who)
    if not managers or not H.is_bot(managers[0]):
        return
    told = managers[0]
    slug = H.actor_id(who)
    label = f"Ticket {task['number']}" if task["number"] else task["title"]
    body = (
        f"{task['title']}\n\n{slug} has used its {cap} VM-minutes of remote test runs on this ticket, so its next run is "
        f"refused. You may grant one extension of {extension_minutes(ACTIVE['minutes'])} more (POST /api/v2/ci/extensions "
        "with the task id), or close or reassign the ticket."
    )
    made = H.task_create(
        c,
        H.KEEPER,
        f"{label}: remote test runs reached {cap} VM-minutes"[:150],
        body,
        told,
        deduplicate=False,
        lint=False,
    )
    refs = {"wake": "nudge", "task": task["id"], "needs_action": True}
    H._wake(c, H.task(c, made["id"]), told, body, refs, trigger="stall_check")


def permit(c, who: str, body: Permit, rate: float, minutes_per_ticket: int, at: str) -> dict[str, Any]:
    """Whether a builder's remote run may start. `allowed` with the ticket's cap, or a refusal with one line of why. The
    run is recorded either way; an allowed run holds its estimate on the CI budget until its jobs post."""
    if not REMOTE_ID.fullmatch(body.remote_id):
        raise Problem("validation", "remote_id is 12 lowercase hex characters", 422)
    if body.cpus not in CPUS:
        raise Problem("validation", f"cpus is one of {', '.join(map(str, CPUS))}", 422)
    if not 1 <= body.minutes <= MINUTES_MAX:
        raise Problem("validation", f"minutes is 1 to {MINUTES_MAX}", 422)
    task = H.task(c, body.task_id)
    if task is None:
        raise Problem("not_found", "No such ticket for this run", 404)
    seen = c.execute("SELECT status, reason FROM ci_runs WHERE remote_id=?", (body.remote_id,)).fetchone()
    if seen is not None:  # a retried permit gets the answer it had
        ok = seen["status"] == "permitted"
        return {"allowed": ok, "message": "" if ok else f"Remote run refused: {seen['reason']}."}
    reserve = cents_for(body.minutes, body.cpus, rate)
    cap = ticket_cap(c, body.task_id, minutes_per_ticket)
    used = ticket_used(c, body.task_id)
    if used >= cap:
        why = f"ticket {task['number'] or task['id']} has used its {cap} VM-minutes"
        return _refusal(c, who, task, body.remote_id, body.cpus, body.minutes, reserve, why, at, cap)
    budget, share = day_share(c, at)
    book = SL.book(c)
    if share is not None and budget is not None and book is not None:
        if day_spent(c, book, budget, at) + reserve > share:
            why = f"today's CI share (${share / 100:,.2f}) has no room for this run"
            return _refusal(c, who, task, body.remote_id, body.cpus, body.minutes, reserve, why, at, cap)
    hold_id = ""
    if share is not None and budget is not None and book is not None and reserve > 0:
        decision = budgeting.hold_place(
            c,
            budget_scope={"account_id": budget.account_id},
            amount=reserve,
            currency=book.base_currency,
            source="ci",
            source_id="run:" + body.remote_id,
            expires_at=H.shift(at, hours=RESERVE_HOURS),
            at=at,
            past_reserves=True,
        )
        if not decision.allowed:
            why = "the CI budget will not hold this run"
            return _refusal(c, who, task, body.remote_id, body.cpus, body.minutes, reserve, why, at, cap)
        hold_id = decision.hold_id or ""
    c.execute(
        "INSERT INTO ci_runs(kind, status, remote_id, task_id, bot, cpus, requested_minutes, reserve_cents, hold_id, "
        "created_at) VALUES('builder','permitted',?,?,?,?,?,?,?,?)",
        (body.remote_id, body.task_id, who, body.cpus, body.minutes, reserve, hold_id, at),
    )
    return {"allowed": True, "message": "", "cap": cap, "used": used + body.minutes}


def extend(c, who: str, task_id: str, minutes_per_ticket: int, at: str) -> dict[str, Any]:
    """One extension of the ticket's minutes, granted by a manager above the builder that ran the ticket."""
    task = H.task(c, task_id)
    if task is None:
        raise Problem("not_found", "No such ticket", 404)
    last = c.execute(
        "SELECT bot FROM ci_runs WHERE task_id=? AND kind='builder' ORDER BY id DESC LIMIT 1", (task_id,)
    ).fetchone()
    builder = last["bot"] if last else "bot:" + H.actor_id(task["owner_id"])
    if who not in task_caps.above(c, builder):
        raise Problem("forbidden", "Only a manager above the ticket's builder can grant its extension", 403)
    if c.execute("SELECT 1 FROM ci_run_extensions WHERE task_id=?", (task_id,)).fetchone():
        raise Problem("conflict", "This ticket already has its extension", 409)
    minutes = extension_minutes(minutes_per_ticket)
    c.execute(
        "INSERT INTO ci_run_extensions(task_id, minutes, granted_by, created_at) VALUES(?,?,?,?)",
        (task_id, minutes, who, at),
    )
    return {"task_id": task_id, "extension": minutes, "cap": minutes_per_ticket + minutes}


# ---------------------------------------------------------------------------------------------------- the sweep
def _list_runs(github_app, repo: str, since: str, headers: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in range(1, PAGES + 1):
        r = github_app._call(
            "GET",
            f"/repos/{repo}/actions/runs",
            headers=headers,
            params={"created": ">=" + since, "per_page": 100, "page": page},
        )
        if r.status_code != 200:
            raise RuntimeError(f"GitHub would not list the workflow runs ({r.status_code})")
        got = r.json().get("workflow_runs") or []
        rows.extend(got)
        if len(got) < 100:
            break
    return rows


def _jobs(github_app, repo: str, run_id: int, headers: dict[str, str]) -> list[dict[str, Any]]:
    r = github_app._call("GET", f"/repos/{repo}/actions/runs/{run_id}/jobs", headers=headers, params={"per_page": 100})
    if r.status_code != 200:
        raise RuntimeError(f"GitHub would not list the jobs of run {run_id} ({r.status_code})")
    return r.json().get("jobs") or []


def _record(c, run: dict[str, Any], jobs: list[dict[str, Any]], rate: float, at: str) -> None:
    gh_id = str(run["id"])
    kind = kind_of(run)
    found = REMOTE_TITLE.search(str(run.get("display_title") or ""))
    remote_id = found.group(1) if kind == "remote" and found else ""
    row = None
    if remote_id:
        row = c.execute("SELECT * FROM ci_runs WHERE remote_id=?", (remote_id,)).fetchone()
    if row is None:
        row = c.execute("SELECT * FROM ci_runs WHERE github_run_id=?", (gh_id,)).fetchone()
    if row is None:
        c.execute(
            "INSERT INTO ci_runs(kind, status, remote_id, github_run_id, created_at) VALUES(?,?,?,?,?)",
            (kind, "unpermitted" if kind == "remote" else "recorded", remote_id, gh_id, at),
        )
        row = c.execute("SELECT * FROM ci_runs WHERE github_run_id=?", (gh_id,)).fetchone()
    if row["seen_at"] or row["status"] == "refused":
        return
    minutes, cents = price(jobs, rate)
    c.execute(
        "UPDATE ci_runs SET github_run_id=?, minutes=?, cost_cents=?, seen_at=? WHERE id=?",
        (gh_id, minutes, cents, at, row["id"]),
    )
    hold = SB.hold(c, row["hold_id"]) if row["hold_id"] else None
    if hold is not None and hold["status"] == "held" and not cents and SL.book(c) is not None:
        budgeting.release(load(c, at), hold["id"])  # a run that cost nothing frees its estimate now


def _post(c, row, book, at: str) -> str:
    """Post one run's cost to CI (6150) against Accrued expenses (2100), absorbing its permit's hold. Returns the entry id
    or ""."""
    accounts = {a.subtype: a.id for a in SL.accounts(c, book.id).values() if a.subtype in (CI_SUBTYPE, ACCRUED_SUBTYPE)}
    if CI_SUBTYPE not in accounts or ACCRUED_SUBTYPE not in accounts or not row["cost_cents"]:
        return ""
    ctx = load(c, at)
    hold_ids: list[str] = []
    if row["hold_id"]:
        hold = SB.hold(c, row["hold_id"])
        if hold is not None and hold["status"] == "held":
            budgeting.settle(ctx, hold["id"], int(row["cost_cents"]), None, SYSTEM)
        hold = SB.hold(c, row["hold_id"])
        if hold is not None and hold["status"] == "settled" and hold["entry_id"] is None:
            hold_ids.append(hold["id"])
    label = f"CI run {row['github_run_id']}"
    entry = {
        "date": at[:10],
        "memo": f"CI build time, {row['kind']}",
        "source": "usage",
        "source_id": "ci:" + row["github_run_id"],
        "lines": [
            {"account_id": accounts[CI_SUBTYPE], "amount": int(row["cost_cents"]), "description": label},
            {"account_id": accounts[ACCRUED_SUBTYPE], "amount": -int(row["cost_cents"]), "description": label},
        ],
    }
    made = post_entry(c, entry, by=SYSTEM, hold_ids=hold_ids, at=at, touch=True)
    entry_id = str(made["entry_id"])
    c.execute("UPDATE ci_runs SET entry_id=? WHERE id=?", (entry_id, row["id"]))
    return entry_id


def post_unposted(c, at: str) -> int:
    """Post every priced run that has no entry yet (a run priced before Finance was set up posts once it is)."""
    book = SL.book(c) if is_set_up(c) else None
    if book is None:
        return 0
    rows = c.execute(
        "SELECT * FROM ci_runs WHERE seen_at != '' AND entry_id = '' AND cost_cents > 0 AND github_run_id != '' "
        "ORDER BY id LIMIT 500"
    ).fetchall()
    posted = 0
    for row in rows:
        if _post(c, row, book, at):
            posted += 1
    return posted


def sweep_fetch(github_app, repo: str, at: str, known: set[str]) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """The finished runs of the repository in the window that are not priced yet, with their jobs. Outside any write."""
    if not repo or github_app is None:
        return []
    since = (H.parse_ts(at) - timedelta(hours=SWEEP_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    token, _ = github_app.mint([repo], {"actions": "read"})
    headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"}
    runs = _list_runs(github_app, repo, since, headers)
    todo = [run for run in runs if run.get("status") == "completed" and str(run.get("id")) not in known]
    return [(run, _jobs(github_app, repo, run["id"], headers)) for run in todo]


def sweep_known(c) -> set[str]:
    return {str(r["github_run_id"]) for r in c.execute("SELECT github_run_id FROM ci_runs WHERE seen_at != ''")}


def sweep_record(c, fetched, rate: float, at: str) -> dict[str, int]:
    """Record the fetched runs, then post what is priced and unposted. Runs in the caller's transaction."""
    for run, jobs in fetched:
        _record(c, run, jobs, rate, at)
    posted = post_unposted(c, at)
    return {"recorded": len(fetched), "posted": posted}


def sweep(store, github_app, repo: str, rate: float, at: str) -> tuple[str, bool]:
    """One heartbeat pass: list and price the repository's recent runs (outside the write lock), then record and post in
    one short transaction. Returns (words, quiet) for the heartbeat."""
    if not repo or github_app is None:
        return "No repository to sweep", True
    with store.read() as c:
        known = sweep_known(c)
    fetched = sweep_fetch(github_app, repo, at, known)
    if not fetched:
        with store.transaction() as c:
            posted = post_unposted(c, at)
        return ("Nothing new" if not posted else f"{posted} posted"), not posted
    with store.transaction() as c:
        done = sweep_record(c, fetched, rate, at)
    return f"{done['recorded']} recorded, {done['posted']} posted", False


# ---------------------------------------------------------------------------------------------------- Rules
def sentences(c) -> list[dict[str, Any]]:
    """The CI caps as Rules sentences (org_rules.settings_for): the ticket's minutes with their dollars, and today's share
    of the CI budget, or that there is no daily limit."""
    minutes, rate = ACTIVE["minutes"], ACTIVE["rate"]
    cap_cents = cents_for(minutes, CPU_BASE, rate)
    out = [
        {
            "text": f"Remote test runs: up to {minutes} VM-minutes (${cap_cents / 100:,.2f}) per ticket, "
            f"plus one extension of {extension_minutes(minutes)} a manager may grant",
            "source": "ci_runs",
            "edit": None,
        }
    ]
    _, share = day_share(c, H.now())
    if share is None:
        text = "No daily CI limit set"
    else:
        text = f"CI spend up to ${share / 100:,.2f} a day (Finance budget CI)"
    out.append({"text": text, "source": "ci_runs", "edit": {"page": "finance", "anchor": "budgets"}})
    return out


# ---------------------------------------------------------------------------------------------------- the routes
def install(app, store, auth, mutate):
    ACTIVE["minutes"] = store.settings.ci_minutes_per_ticket
    ACTIVE["rate"] = store.settings.ci_rate_per_job_minute

    @app.post("/api/v2/ci/permits")
    def ci_permit(request: Request, body: Permit):
        """A builder's remote run asks to start (scripts/remote.py). Refused with one line when its ticket has used its
        minutes or today's CI share has no room; the answer is `allowed` either way."""
        who = request.state.identity
        auth.domain(who)
        if who.role != "bot":
            raise Problem("forbidden", "Only a builder's own run asks for a permit", 403)

        settings = store.settings

        def work(c):
            return permit(c, who.actor, body, settings.ci_rate_per_job_minute, settings.ci_minutes_per_ticket, H.now())

        return mutate(request, body, work)

    @app.post("/api/v2/ci/extensions")
    def ci_extension(request: Request, body: Extension):
        """A manager above a ticket's builder grants the ticket one extension of its CI minutes."""
        who = request.state.identity
        auth.domain(who)

        def work(c):
            return extend(c, who.actor, body.task_id, store.settings.ci_minutes_per_ticket, H.now())

        return mutate(request, body, work)
