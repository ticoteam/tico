"""CI build time caps (backend/ci_runs.py): a builder's remote runs stop at its ticket's VM-minutes and at today's CI share
of the Finance budget CI, and every workflow run of the repository is recorded and priced into account 6150 (CI).

The runs go through the real permit route and the heartbeat's sweep. The sim dispatches no Actions runs, so the fake
GitHub's runs and jobs (world.py connect_github) stand in for them, each finished when it is asked for. Four cases:

- ci_ticket_cap: a ticket's builder asks for 60-minute runs. Two fit its 120 VM-minutes; the third is refused in one line,
  and asking again gets the same answer. The manager's one extension (120 to 180) lets one more run start; a second
  extension is refused.
- ci_daily_share: the CI budget is $31 a month, $1.00 a day in October. Runs of 50 minutes on 16 vCPUs cost 32 cents:
  three fit today's share, and the next two are refused for it.
- ci_pr_check_exhausted: on the exhausted share a PR check and a release run still finish. Both are recorded and posted to
  CI, neither is refused, and their cost counts toward the day; the next builder run is refused.
- ci_direct_dispatch: a remote run dispatched without a permit is recorded as unpermitted and counted, not blocked, and a
  job on a hosted runner costs nothing.
"""

from backend.finance import actions as FA
from backend.finance import ci as FC
from backend.finance.store import ledger as SL
from evals.factory.scenarios import Scenario, at, standard, tickets
from evals.factory.world import REPO, START

MINUTES = 60  # the ask of ci_ticket_cap: two fit a ticket's 120 VM-minutes
CI_AMOUNT = 3_100  # cents a month: $1.00 a day in October (31 days)
SMALL = ["runs-on=1/runner=4cpu-linux-x64"]
BIG = ["runs-on=1/runner=16cpu-linux-x64"]
HOSTED = ["ubuntu-24.04"]
CALLS = ("ask", "retry", "extend")  # the steps whose answer is allowed (or granted) or not
OWNER = "ana@acme.example"  # the world's owner (world.py hub-access.yaml)


# ---------------------------------------------------------------------------------------------------- the driver
def token_of(team):
    """A running builder's token: a builder's own attempt asks for its remote runs."""
    return next((b.token for b in team.builders if b.running and b.token), None)


def answer(step, status, reply, ok):
    message = reply.get("message", "") if isinstance(reply, dict) else ""
    return {"step": step, "status": status, "ok": ok, "message": message}


def next_id(run):
    run.ci["seq"] += 1
    return f"{0xC1A000000000 + run.ci['seq']:012x}"  # twelve lowercase hex, as remote.py makes them


def github(run, w, *, path, event, title, labels, minutes):
    """A completed Actions run of the repository with one job of `minutes` on `labels`: the sweep prices it next pass."""
    now = w.now()
    run.ci["gh"] += 1
    gh = run.ci["gh"]
    w.workflow_runs.append({"id": gh, "status": "completed", "path": path, "event": event, "display_title": title})
    w.workflow_jobs[gh] = [{"started_at": stamp(now - at(0, minutes)), "completed_at": stamp(now), "labels": labels}]


def stamp(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def ask(run, w, token, which, minutes=MINUTES, cpus=4):
    body = {"task_id": run.ci["tickets"][which], "remote_id": next_id(run), "cpus": cpus, "minutes": minutes}
    status, reply = w.request("post", "ci/permits", body, token=token)
    ok = status == 200 and isinstance(reply, dict) and reply.get("allowed") is True
    run.ci["runs"].append(body)
    if ok:  # the builder's run went out: it finishes as it was asked for
        github(
            run,
            w,
            path=".github/workflows/remote-run.yml",
            event="workflow_dispatch",
            title=f"Remote run {body['remote_id']}",
            labels=BIG if cpus == 16 else SMALL,
            minutes=minutes,
        )
    return answer("ask", status, reply, ok)


def retry(run, w, token, index):
    status, reply = w.request("post", "ci/permits", run.ci["runs"][index], token=token)
    ok = status == 200 and isinstance(reply, dict) and reply.get("allowed") is True
    return answer("retry", status, reply, ok)


def extend(run, w, token, which=0):
    """The manager's extension of the ticket (ana owns the bots and is above them on the org chart)."""
    status, reply = w.request("post", "ci/extensions", {"task_id": run.ci["tickets"][which]}, token="ana-test")
    return answer("extend", status, reply, status == 200)


def pr_check(run, w, token):
    github(
        run,
        w,
        path=".github/workflows/pr-checks.yml",
        event="pull_request",
        title="PR checks",
        labels=SMALL,
        minutes=50,
    )
    return answer("pr_check", 200, {}, True)


def release(run, w, token):
    github(run, w, path=".github/workflows/train.yml", event="push", title="Release", labels=SMALL, minutes=50)
    return answer("release", 200, {}, True)


def direct(run, w, token):
    """A remote run somebody dispatched by hand: no permit asked."""
    title = f"Remote run {next_id(run)}"
    github(
        run,
        w,
        path=".github/workflows/remote-run.yml",
        event="workflow_dispatch",
        title=title,
        labels=SMALL,
        minutes=30,
    )
    return answer("direct", 200, {}, True)


def hosted(run, w, token):
    github(run, w, path=".github/workflows/ci.yml", event="push", title="CI", labels=HOSTED, minutes=20)
    return answer("hosted", 200, {}, True)


STEPS = {
    "ask": ask,
    "retry": retry,
    "extend": extend,
    "pr_check": pr_check,
    "release": release,
    "direct": direct,
    "hosted": hosted,
}


def during(run, w, team, i):
    """One step of the case's script per tick, in order. A builder's step waits for a builder to be running."""
    script = run.ci["script"]
    if run.ci["next"] >= len(script):
        return
    kind, *args = script[run.ci["next"]]
    token = token_of(team)
    if kind in ("ask", "retry") and token is None:
        return
    run.ci["next"] += 1
    run.ci["answers"].append(STEPS[kind](run, w, token, *args))


def setup_for(script, share=False):
    def setup(run, w, team):
        w.connect_github()
        w.store.settings.release_repo = REPO  # the repository the sweep lists (settings.release_repo)
        # The world builds its scheduler without the App (world.py); a hub passes it (backend/app.py), and the sweep reads
        # the repository's runs through it. Without it the sweep records nothing.
        w.scheduler.github_app = w.app.state.github_app
        # A hub sets Finance up on its scheduler's first pass (backend/scheduler.py), after this setup. The budget lookup
        # and the posts need it now, so set it up as app startup does (backend/app.py), once the owner exists.
        with w.store.transaction() as c:
            FA.ensure_setup(c, OWNER)
        run.ci = {"script": script, "next": 0, "answers": [], "runs": [], "tickets": [], "seq": 0, "gh": 7_000_000}
        run.ci["tickets"] = [t["id"] for t in tickets(run, w, 5)]
        if share:
            budget = w.sql("SELECT id FROM fin_budgets WHERE name='CI'")[0]["id"]
            w.call(
                "post",
                "finance/actions",
                {"kind": "budget_update", "payload": {"budget_id": budget, "amount": CI_AMOUNT}},
            )

    return setup


# ---------------------------------------------------------------------------------------------------- checks
def answers_are(expected):
    def check(run):
        got = [a["ok"] for a in run.ci["answers"] if a["step"] in CALLS]
        return got == expected, f"allowed {got}, expected {expected}"

    return check


def one_line_refusal(run):
    refused = next((a for a in run.ci["answers"] if a["step"] == "ask" and not a["ok"]), None)
    message = refused["message"] if refused else ""
    ok = (
        message.startswith("Remote run refused: ticket ")
        and message.endswith("has used its 120 VM-minutes.")
        and "(" not in message
    )
    return ok, message


def retry_same_answer(run):
    answers = run.ci["answers"]
    ok = answers[3]["step"] == "retry" and not answers[3]["ok"] and answers[3]["message"] == answers[2]["message"]
    return ok, answers[3]["message"]


def one_extension(run):
    answers = run.ci["answers"]
    return answers[4]["ok"] and answers[7][
        "status"
    ] == 409, f"extend {answers[4]['status']}, again {answers[7]['status']}"


def share_refusals(run):
    refused = [a["message"] for a in run.ci["answers"] if a["step"] == "ask" and not a["ok"]]
    ok = bool(refused) and all("today's CI share" in m for m in refused)
    return ok, refused[0] if refused else "no refusal"


def day_posted(run):
    """Today's posted CI cost in cents, and the share it is measured against (None without a daily gate)."""
    day = stamp(START)
    with run.w.store.read() as c:
        book = SL.book(c)
        account = c.execute("SELECT id FROM fin_accounts WHERE code='6150'").fetchone()
        posted = SL.rollup_sum(c, book.id, day[:10], day[:10], [str(account["id"])])
        share = FC.day_share(c, day)[1]
    return posted, share


def within_share(run):
    posted, share = day_posted(run)
    return share is not None and posted <= share, f"posted {posted}c against a {share}c share"


def pr_and_release_recorded(run):
    rows = run.w.sql("SELECT kind, status, cost_cents, entry_id FROM ci_runs WHERE kind IN ('pr', 'release')")
    kinds = sorted(r["kind"] for r in rows if r["status"] == "recorded" and r["cost_cents"] == 8 and r["entry_id"])
    return kinds == ["pr", "release"], f"recorded and posted: {kinds}"


def exhausted_day_counts_them(run):
    posted, share = day_posted(run)
    return (
        posted == 112,
        f"posted {posted}c today (three builder runs at 32, a PR check and a release at 8 each), share {share}c",
    )


def only_builders_refused(run):
    rows = run.w.sql("SELECT kind FROM ci_runs WHERE status = 'refused' AND kind != 'builder'")
    return not rows, f"{len(rows)} non-builder runs refused"


def direct_counted(run):
    rows = run.w.sql("SELECT status, minutes, cost_cents, entry_id FROM ci_runs WHERE kind = 'remote'")
    ok = (
        len(rows) == 1
        and rows[0]["status"] == "unpermitted"
        and rows[0]["minutes"] == 30
        and rows[0]["cost_cents"] == 5
        and rows[0]["entry_id"] != ""
    )
    return ok, str(rows)


def hosted_is_free(run):
    rows = run.w.sql("SELECT status, minutes, cost_cents, entry_id FROM ci_runs WHERE kind = 'ci'")
    ok = len(rows) == 1 and rows[0]["minutes"] == 20 and rows[0]["cost_cents"] == 0 and rows[0]["entry_id"] == ""
    return ok, str(rows)


def every_run_priced(run):
    left = run.w.sql("SELECT count(*) n FROM ci_runs WHERE seen_at = '' OR (cost_cents > 0 AND entry_id = '')")[0]["n"]
    return left == 0, f"{left} runs not priced and posted"


def posted_is_the_ledger(run):
    with run.w.store.read() as c:
        book = SL.book(c)
        account = c.execute("SELECT id FROM fin_accounts WHERE code='6150'").fetchone()
        ledger = SL.rollup_sum(c, book.id, "2000-01-01", "2100-01-01", [str(account["id"])])
    posted = run.w.sql("SELECT COALESCE(SUM(cost_cents), 0) n FROM ci_runs WHERE entry_id != ''")[0]["n"]
    return ledger == posted, f"6150 holds {ledger}c, the runs {posted}c"


PRICED = [
    ("every run priced once and posted to CI", None, every_run_priced),
    ("CI account is the runs' cost", None, posted_is_the_ledger),
]

# ---------------------------------------------------------------------------------------------------- scenarios
SCENARIOS = [
    Scenario(
        "ci_ticket_cap",
        "A ticket's builder asks for 60-minute remote runs: two fit its 120 VM-minutes, the third is refused in one line "
        "and asking again gets the same answer, one extension of 60 lets one more run start, and a second extension is refused.",
        setup=setup_for(
            [("ask", 0), ("ask", 0), ("ask", 0), ("retry", 2), ("extend",), ("ask", 0), ("ask", 0), ("extend",)]
        ),
        during=during,
        checks=standard(
            (
                "the ticket's runs get their answers",
                None,
                answers_are([True, True, False, False, True, True, False, False]),
            ),
            ("a refusal is one line that names the ticket's minutes", None, one_line_refusal),
            ("the same run asked again gets its refusal", None, retry_same_answer),
            ("one extension, once", None, one_extension),
            *PRICED,
        ),
    ),
    Scenario(
        "ci_daily_share",
        "The CI budget is $1.00 a day: 50-minute runs on 16 vCPUs cost 32 cents, three fit today's share and the next two "
        "are refused for it, and the day's posted CI cost stays within the share.",
        setup=setup_for(
            [("ask", 0, 50, 16), ("ask", 1, 50, 16), ("ask", 2, 50, 16), ("ask", 3, 50, 16), ("ask", 4, 50, 16)],
            share=True,
        ),
        during=during,
        checks=standard(
            ("runs are allowed until today's share is used", None, answers_are([True, True, True, False, False])),
            ("the refusals name today's CI share", None, share_refusals),
            ("the day's posted CI cost stays within the share", None, within_share),
            *PRICED,
        ),
    ),
    Scenario(
        "ci_pr_check_exhausted",
        "Today's CI share is spent, then a PR check and a release run finish: both are recorded and posted to CI and never "
        "refused, their cost counts toward the day, and the next builder run is refused.",
        setup=setup_for(
            [
                ("ask", 0, 50, 16),
                ("ask", 1, 50, 16),
                ("ask", 2, 50, 16),
                ("ask", 3, 50, 16),
                ("pr_check",),
                ("release",),
                ("ask", 4, 50, 16),
            ],
            share=True,
        ),
        during=during,
        checks=standard(
            ("runs are allowed until today's share is used", None, answers_are([True, True, True, False, False])),
            ("the refusals name today's CI share", None, share_refusals),
            ("a PR check and a release run are recorded and posted, not refused", None, pr_and_release_recorded),
            ("only builder runs are ever refused", None, only_builders_refused),
            ("PR and release cost count toward the day", None, exhausted_day_counts_them),
            *PRICED,
        ),
    ),
    Scenario(
        "ci_direct_dispatch",
        "A remote run dispatched without a permit is recorded as unpermitted and counted, not blocked; a job on a hosted "
        "runner is recorded and costs nothing.",
        setup=setup_for([("direct",), ("hosted",)]),
        during=during,
        checks=standard(
            ("an unpermitted remote run is counted, not blocked", None, direct_counted),
            ("a hosted runner costs nothing", None, hosted_is_free),
            *PRICED,
        ),
    ),
]
