"""CI build time caps (backend/ci_runs.py): a ticket's remote-run minutes, the company's daily CI share in Finance, the
sweep that prices every workflow run into Finance account 6150, and the Rules sentences that show the caps."""

import pytest

from backend import ci_runs
from backend.finance import ci as FC
from backend.finance import setup as FS
from backend.finance.store import budgets as SB
from backend.finance.store import ledger as SL
from backend.store import H, Problem
from backend.tests.helpers.finance_fixtures import AT, OWNER, account, act, budget, books, finance_template, store  # noqa: F401

RUN = "a1b2c3d4e5f6"
CI_BOT = "bot:coder"


class Response:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


class FakeGitHub:
    """The two GitHub reads the sweep makes: the repository's runs and each run's jobs."""

    def __init__(self, runs, jobs):
        self.runs, self.jobs = runs, jobs

    def mint(self, repos, permissions):
        return "token", 0

    def _call(self, method, path, headers=None, params=None):
        if path.endswith("/actions/runs"):
            return Response(200, {"workflow_runs": self.runs})
        return Response(200, {"jobs": self.jobs[int(path.split("/")[-2])]})


def job(start, end, labels):
    return {"started_at": start, "completed_at": end, "labels": labels}


def ticket(c, title="Build the thing"):
    return H.task_create(c, OWNER, title, "Scenario task.", CI_BOT, deduplicate=False, lint=False)["id"]


def permit(c, task_id, remote_id=RUN, minutes=50, cpus=4, at=AT):
    body = ci_runs.Permit(task_id=task_id, remote_id=remote_id, cpus=cpus, minutes=minutes)
    return ci_runs.permit(c, CI_BOT, body, 0.0016, 120, at)


def set_ci_amount(c, cents):
    act(c, OWNER, "budget_update", {"budget_id": budget(c, "CI"), "amount": cents})


# ---------------------------------------------------------------------------------------------------- pricing
def test_a_job_minute_costs_the_rate_scaled_by_vcpus_and_hosted_runs_are_free():
    assert ci_runs.cents_for(50, 4, 0.0016) == 8
    assert ci_runs.cents_for(120, 4, 0.0016) == 19  # the ticket's cap, $0.19
    assert ci_runs.cents_for(50, 16, 0.0016) == 32
    assert ci_runs.cents_for(50, 4, 0) == 0
    jobs = [
        job("2026-11-07T10:00:00Z", "2026-11-07T10:50:30Z", ["runs-on=1/runner=4cpu-linux-x64"]),  # 51 minutes
        job("2026-11-07T10:00:00Z", "2026-11-07T10:10:00Z", ["ubuntu-24.04"]),  # hosted: minutes, no cost
        job("2026-11-07T10:00:00Z", "2026-11-07T10:05:00Z", ["macos-latest"]),  # hosted too
        job("2026-11-07T10:00:00Z", "2026-11-07T10:00:00Z", ["runs-on=1/runner=4cpu-linux-x64"]),  # zero length
        job(None, None, ["runs-on=1/runner=4cpu-linux-x64"]),  # skipped
    ]
    # 51 billed minutes at 4 vCPUs cost 8 cents; 15 hosted minutes count but cost nothing.
    assert ci_runs.price(jobs, 0.0016) == (66, 8)


def test_the_run_kind_comes_from_its_workflow_file_or_the_merge_queue():
    assert ci_runs.kind_of({"path": ".github/workflows/ci.yml", "event": "push"}) == "ci"
    assert ci_runs.kind_of({"path": ".github/workflows/pr-checks.yml", "event": "pull_request"}) == "pr"
    assert ci_runs.kind_of({"path": ".github/workflows/remote-run.yml", "event": "workflow_dispatch"}) == "remote"
    assert ci_runs.kind_of({"path": ".github/workflows/train.yml", "event": "push"}) == "release"
    assert ci_runs.kind_of({"path": ".github/workflows/ci.yml", "event": "merge_group"}) == "queue"
    assert ci_runs.kind_of({"path": ".github/workflows/app.yml", "event": "push"}) == "other"


# ---------------------------------------------------------------------------------------------------- the ticket cap
def test_a_ticket_refuses_its_run_once_its_minutes_are_used_and_the_extension_adds_half(books):
    with books.transaction() as c:
        task_id = ticket(c)
        assert permit(c, task_id)["allowed"] is True  # 50
        assert permit(c, task_id, remote_id="111111111111")["allowed"] is True  # 100
        assert permit(c, task_id, remote_id="222222222222")["allowed"] is True  # 150 counted from here
        refused = permit(c, task_id, remote_id="333333333333")
        assert refused["allowed"] is False
        assert refused["message"].startswith("Remote run refused: ticket ") and "120 VM-minutes" in refused["message"]
        assert "(" not in refused["message"]  # one line, no detail
        # The same run asked again gets the answer it had.
        assert permit(c, task_id, remote_id="333333333333")["allowed"] is False
        assert ci_runs.extend(c, "human:cara", task_id, 120, AT)["cap"] == 180
        assert permit(c, task_id, remote_id="444444444444")["allowed"] is True  # 150 is under 180


def test_only_a_manager_above_the_builder_grants_the_extension_and_once(books):
    with books.transaction() as c:
        task_id = ticket(c)
        permit(c, task_id)
        with pytest.raises(Problem) as stranger:
            ci_runs.extend(c, "human:dan", task_id, 120, AT)
        assert stranger.value.status == 403
        ci_runs.extend(c, "human:cara", task_id, 120, AT)
        with pytest.raises(Problem) as again:
            ci_runs.extend(c, "human:cara", task_id, 120, AT)
        assert again.value.status == 409


def test_a_permit_for_a_ticket_that_does_not_exist_is_not_found(books):
    with books.transaction() as c:
        with pytest.raises(Problem) as missing:
            permit(c, "no-such-task")
        assert missing.value.status == 404


def test_a_permit_with_a_bad_run_id_size_or_time_limit_is_refused_as_invalid(books):
    with books.transaction() as c:
        task_id = ticket(c)
        with pytest.raises(Problem):
            permit(c, task_id, remote_id="NOT-HEX")
        with pytest.raises(Problem):
            permit(c, task_id, cpus=2)
        with pytest.raises(Problem):
            permit(c, task_id, minutes=61)


# ---------------------------------------------------------------------------------------------------- the daily share
def test_the_daily_share_refuses_a_run_that_no_longer_fits_and_no_amount_means_no_gate(books):
    with books.transaction() as c:
        task = ticket(c)
        assert FC.day_share(c, AT)[1] is None  # the CI budget starts with no amount
        assert permit(c, task, remote_id="555555555551")["allowed"] is True  # no gate, no hold
        set_ci_amount(c, 3_100)  # $31 in October: $1.00 a day
        share = FC.day_share(c, AT)[1]
        assert share == 100
        # 50 minutes on 16 vCPUs is 32 cents: three fit in $1.00, the fourth does not.
        for n in range(3):
            assert permit(c, ticket(c, f"Share run {n}"), remote_id=f"66666666666{n}", cpus=16)["allowed"] is True
        full = permit(c, ticket(c, "Share run full"), remote_id="777777777777", cpus=16)
        assert full["allowed"] is False and "today's CI share" in full["message"]
        held = c.execute("SELECT count(*), sum(amount) FROM fin_holds WHERE source='ci'").fetchone()
        assert held[0] == 3 and held[1] == 96


# ---------------------------------------------------------------------------------------------------- the sweep
def test_the_sweep_prices_a_finished_run_and_posts_it_to_ci_against_accrued_expenses(books):
    runs = [
        {"id": 1001, "status": "completed", "path": ".github/workflows/ci.yml", "event": "push", "display_title": "CI"},
        {
            "id": 1002,
            "status": "in_progress",
            "path": ".github/workflows/ci.yml",
            "event": "push",
            "display_title": "CI",
        },
    ]
    jobs = {1001: [job("2026-10-15T11:00:00Z", "2026-10-15T11:50:00Z", ["runs-on=1/runner=4cpu-linux-x64"])]}
    github = FakeGitHub(runs, jobs)
    words, quiet = ci_runs.sweep(books, github, "ticoteam/tico", 0.0016, AT)
    assert (words, quiet) == ("1 recorded, 1 posted", False)
    with books.read() as c:
        row = c.execute("SELECT * FROM ci_runs WHERE github_run_id='1001'").fetchone()
        assert (row["kind"], row["status"], row["minutes"], row["cost_cents"]) == ("ci", "recorded", 50, 8)
        assert c.execute("SELECT 1 FROM ci_runs WHERE github_run_id='1002'").fetchone() is None
        book = SL.book(c)
        assert SL.rollup_sum(c, book.id, "2026-10-15", "2026-10-15", [account(c, "6150")]) == 8
        assert SL.rollup_sum(c, book.id, "2026-10-15", "2026-10-15", [account(c, "2100")]) == -8
    again = ci_runs.sweep(books, github, "ticoteam/tico", 0.0016, AT)
    assert again[1] is True  # nothing new: the run is priced once and posts once


def test_a_builder_run_absorbs_its_permit_hold_when_it_posts(books):
    with books.transaction() as c:
        set_ci_amount(c, 3_100)
        task_id = ticket(c)
        assert permit(c, task_id, remote_id=RUN, cpus=16)["allowed"] is True
    runs = [
        {
            "id": 2001,
            "status": "completed",
            "path": ".github/workflows/remote-run.yml",
            "event": "workflow_dispatch",
            "display_title": f"Remote run {RUN}",
        }
    ]
    jobs = {2001: [job("2026-10-15T11:00:00Z", "2026-10-15T11:50:00Z", ["runs-on=1/runner=16cpu-linux-x64"])]}
    ci_runs.sweep(books, FakeGitHub(runs, jobs), "ticoteam/tico", 0.0016, AT)
    with books.read() as c:
        row = c.execute("SELECT * FROM ci_runs WHERE remote_id=?", (RUN,)).fetchone()
        assert (row["kind"], row["status"], row["cost_cents"]) == ("builder", "permitted", 32)
        hold = c.execute("SELECT status, entry_id FROM fin_holds WHERE source='ci'").fetchone()
        assert hold["status"] == "settled" and hold["entry_id"] is not None  # the hold no longer reserves anything
        assert FC.day_spent(c, SL.book(c), _budget_of(c), AT) == 32  # posted, and nothing held


def _budget_of(c):
    book = SL.book(c)
    return next(b for b in SB.budgets(c, book.id).values() if b.name == "CI")


def test_an_unpermitted_remote_run_is_recorded_and_counted_not_blocked(books):
    runs = [
        {
            "id": 3001,
            "status": "completed",
            "path": ".github/workflows/remote-run.yml",
            "event": "workflow_dispatch",
            "display_title": "Remote run 999999999999",
        }
    ]
    jobs = {3001: [job("2026-10-15T11:00:00Z", "2026-10-15T11:10:00Z", ["runs-on=1/runner=4cpu-linux-x64"])]}
    ci_runs.sweep(books, FakeGitHub(runs, jobs), "ticoteam/tico", 0.0016, AT)
    with books.read() as c:
        row = c.execute("SELECT kind, status, cost_cents FROM ci_runs WHERE github_run_id='3001'").fetchone()
        assert (row["kind"], row["status"], row["cost_cents"]) == ("remote", "unpermitted", 2)


# ---------------------------------------------------------------------------------------------------- Rules
def test_the_manager_update_carries_one_line_of_ci_today_only_when_there_is_some(books):
    with books.transaction() as c:
        assert ci_runs.digest_line(c, AT) is None  # no CI budget amount, nothing spent or refused
        set_ci_amount(c, 3_100)
        task_id = ticket(c)
        for n in range(3):  # 32 cents each on 16 vCPUs: the fourth does not fit today's $1.00
            assert permit(c, task_id, remote_id=f"88888888888{n}", cpus=16)["allowed"] is True
        assert permit(c, task_id, remote_id="888888888889", cpus=16)["allowed"] is False
        assert ci_runs.digest_line(c, AT) == "CI today: $0.96 of a $1.00 share; 1 builder run refused."
