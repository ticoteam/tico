"""A person waives the last check before the merge (QA) for one exact PR head while QA can't run.

QA's first verdict is a fail; then its computers go off for five hours, and come back with a working environment
once ana has asked for a waiver at a head QA failed, seven hours at most. Builders push nothing after an approval (so
both reviews approve the head QA would judge), dispute a fail once by sending the same head back to QA, and ask Tico
to waive QA themselves with their own token. QA's pass on a disputed head clears nothing: the ticket goes back to QA
Fixes, and the builder pushes. While QA is off, ana waives each ticket that has waited half an hour at the QA step,
once per ticket, through the real API with the company's GitHub App answering from the sim's GitHub. The dispute has
to land while QA is off, since that is the only way a failed head comes up for ana; the builders' queues, which the
review routing by model company shifts, decide when it lands. One builder pushes once more on its first waived ticket
instead of merging: that waiver no longer covers the new head, so the ticket waits for QA's verdict on it. The other
builder merges on its waivers."""
from contextlib import contextmanager

from evals.factory.board import MERGE, dev_board
from evals.factory.scenarios import Scenario, standard, tickets
from evals.factory.sim import at
from evals.factory.world import Refusal

WAIVER = "QA waiver"   # the product rules this scenario guards (backend/qa_waivers.py, docs/tasks.md)
QA_DOWN_HOURS = 5
QA_DOWN_CAP_HOURS = 7  # below slow_qa_board's 8 h round timeout, so no QA round is skipped while QA is off
WAIT_MINUTES = 30      # how long ana lets a ticket sit at QA before she waives it
BUILDER = {"hold_approved": 1.0, "dispute_fail": 1.0, "try_waive": 1.0}


def last_check(board):
    """The check whose pass makes the merge next, found by its settings: the step a waiver is for."""
    return next(s for s in board.steps if s.check and s.check.passed == MERGE)


def slow_qa_board():
    """The Dev board with QA given 8 h before its round is skipped: the team waits for QA, or a person waives it."""
    board = dev_board()
    last_check(board).check.timeout_hours = 8
    return board


@contextmanager
def github_app(w):
    """The company's GitHub App, connected for the length of one request: its reads of a pull request and of a
    commit's check runs answer from the sim's GitHub. Without it a waiver is refused (webhooks alone can't tell a
    pass from a skip), and the rest of the run keeps reading the webhook-built links."""
    service = w.app.state.github_app

    class Answer:
        status_code = 200

        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def call(method, path, headers=None, params=None, **kw):
        parts = path.strip("/").split("/")
        if parts[-2] == "pulls":
            return Answer({"head": {"sha": w.prs[int(parts[-1])]["head"]}})
        sha, kind = parts[-2], parts[-1]
        if kind == "statuses":
            return Answer([])
        runs = []
        for number, pr in w.prs.items():
            done = pr.get("ci")
            if done and done[0] == sha:
                runs = [{"id": number, "name": "tests", "conclusion": "success" if done[1] else "failure"}]
            elif pr["head"] == sha and not runs:
                runs = [{"id": number, "name": "tests", "conclusion": None}]   # still running
        return Answer({"check_runs": runs})

    service.row = lambda c=None: {"id": "app"}
    service.mint = lambda repos, permissions, **kw: ("sim-installation-token", None)
    service._call = call
    try:
        yield
    finally:
        for name in ("row", "mint", "_call"):
            delattr(service, name)


def qa_children(w, ticket_id):
    step = last_check(w.board).name
    return [c for c in w.checks(ticket_id) if w.check_step(c) == step]


def failed_at(w, ticket_id, head):
    return any(c["verdict"] in ("fail", "changes") and str(c["verdict_head"] or "") == head
               for c in qa_children(w, ticket_id))


def waiver_events(w, ticket_id=None):
    import json
    rows = w.sql("SELECT task_id, actor, new, created_at FROM task_events WHERE field='qa_waiver'"
                 + (" AND task_id=?" if ticket_id else "") + " ORDER BY created_at, rowid", (ticket_id,) if ticket_id else ())
    return [{**r, "head": (json.loads(r["new"] or "{}") or {}).get("head")} for r in rows]


# ---------------------------------------------------------------------------------------------- events
def qa_down_then_waive(run, w, team, i):
    """QA's first verdict (a fail) is its last for a while: its computers go off, and come back with a working
    environment no sooner than QA_DOWN_HOURS later, and once ana has asked for a waiver at a head QA failed. They are
    back by QA_DOWN_CAP_HOURS at the latest, so no QA round times out while they are off. The failed head is the one
    the builder's dispute sends back to QA, so the dispute has to land while QA is off. While it is off, ana waives
    each ticket that has waited WAIT_MINUTES at the QA step, once per ticket, at the PR's current head."""
    now = w.now()
    if not hasattr(run, "qa_down"):
        run.qa_down, run.qa_started, run.ana = None, None, []
    qa = [b.slug for b in team.qa]
    if run.qa_down is None and w.sql(f"SELECT 1 FROM tasks WHERE verdict IS NOT NULL AND owner_id IN "
                                     f"({','.join('?' * len(qa))}) LIMIT 1", tuple("bot:" + s for s in qa)):
        run.qa_started = now
        run.qa_down = now + at(QA_DOWN_HOURS)
        for b in team.qa:
            b.available, b.style = False, {}
    if run.qa_down is None:
        return
    failed_asked = any(a["qa_failed"] for a in run.ana)
    if now >= run.qa_down and (failed_asked or now >= run.qa_started + at(QA_DOWN_CAP_HOURS)):
        for b in team.qa:
            b.available = True
        return
    step = last_check(w.board).name
    tried = {a["ticket"] for a in run.ana}
    for t in w.tickets():
        pr = w.pr_of(t["id"])
        if t["step"] != step or t["id"] in tried or not pr or w.prs[pr]["merged"]:
            continue
        entered = w.step_entered_at(t["id"])
        if not entered or (now - w.H.parse_ts(entered)).total_seconds() < WAIT_MINUTES * 60:
            continue
        head = w.prs[pr]["head"]
        attempt = {"ticket": t["id"], "title": t["title"], "head": head, "at": now,
                   "qa_failed": failed_at(w, t["id"], head)}
        try:
            with github_app(w):
                w.post(f"tasks/{t['id']}/qa-waiver", {"head": head, "reason": "QA's test computers are down."})
            attempt["outcome"] = "accepted"
        except Refusal as exc:
            attempt["outcome"] = f"refused ({exc.status}): {exc.body[:200]}"
        run.ana.append(attempt)


# ---------------------------------------------------------------------------------------------- checks
GOOD = ("approve", "pass", "waiver")   # what the backend takes as good (backend/verdict_moves.py), and a waiver


def judgments_at(w, check, head):
    """(time, verdict) of a QA check's judgments of `head`: each verdict recorded for it, and its completion as
    "comments" when it finished with no verdict (`_verdict` in backend/verdict_moves.py). A check with no verdict judged
    the head it was filed for (its review_head, as `_judged_head` reads it)."""
    out = []
    for e in w.sql("SELECT created_at, lower(new) new FROM task_events WHERE task_id=? AND field='verdict'", (check["id"],)):
        verdict, _, judged = str(e["new"] or "").partition(" ")
        if judged and head.startswith(judged):
            out.append((w.H.parse_ts(e["created_at"]), verdict))
    if not check.get("verdict") and check["status"] == "done":
        judged = w.judged_head(check)
        done = w.sql("SELECT max(created_at) created_at FROM task_events WHERE task_id=? AND field='status' AND new='done'",
                     (check["id"],))
        if judged and head.startswith(judged) and done and done[0]["created_at"]:
            out.append((w.H.parse_ts(done[0]["created_at"]), "comments"))
    return out


def standing_at(w, ticket_id, head, when):
    """What stood for `head` when the pull request merged at `when`: QA's judgments of it (`judgments_at`) and a
    person's waiver ("waiver"), recorded at or before `when`; None when there was none. The first judgment there other
    than GOOD stands whatever came after it, a later pass included, since only a new head clears it: it is returned
    first. Otherwise the newest of them is what stood. A fail after a waiver is the first, so it stands: the waiver did
    not see it."""
    head = str(head).lower()
    found = []
    for c in qa_children(w, ticket_id):
        found += judgments_at(w, c, head)
    found += [(w.H.parse_ts(e["created_at"]), "waiver") for e in waiver_events(w, ticket_id)
              if e["head"] == head and e["actor"].startswith("human:")]
    found = sorted(f for f in found if f[0] <= w.H.parse_ts(when))
    failed = [f for f in found if f[1] not in GOOD]
    if failed:
        return failed[0][1]
    return found[-1][1] if found else None


def merged_on_pass_or_waiver(run):
    """Every merged ticket's head had a QA pass or a person's waiver for it by the time its pull request merged, with
    no QA fail, changes or comments at it recorded by then, and at least one ticket merged on a waiver: nothing merges
    on a stale waiver or a failed head. A verdict QA records after the merge does not change what the merge stood on."""
    w, bad, on_waiver = run.w, [], 0
    for t in run.final:
        if t["id"] not in run.merged_at:
            continue
        pr = w.pr_of(t["id"])
        head = w.prs[pr]["head"] if pr else None
        merged = (w.prs[pr] if pr else {}).get("merged_at") or w.H.now()
        stood = standing_at(w, t["id"], head, merged)
        on_waiver += stood == "waiver"
        if stood not in GOOD:
            bad.append(f"{t['title']} merged at {str(head)[:7]} on " + (f"QA's {stood}" if stood else
                                                                         "no QA pass or waiver there"))
    if not on_waiver:
        bad.append("no ticket merged on a waiver (ana: " + "; ".join(
            f"{a['title']} {a['outcome'][:200]}" for a in getattr(run, "ana", [])) + ")")
    return not bad, ", ".join(bad[:6]) or f"{on_waiver} merged on a waiver"


def push_voids_waiver(run):
    """A push after a waiver: the task view shows the waiver as no longer current, and the ticket went back to the
    check and merged only on QA's pass at its new head."""
    w, bad, pushed = run.w, [], 0
    for t in run.final:
        pr = w.pr_of(t["id"])
        for e in waiver_events(w, t["id"]):
            if not pr or e["head"] == w.prs[pr]["head"]:
                continue
            pushed += 1
            view = w.get("tasks/" + t["id"])["task"].get("qa_waivers") or []
            if any(x["head"] == e["head"] and x["current"] for x in view):
                bad.append(f"{t['title']} waiver at {e['head'][:7]} still current after a push")
            head = w.prs[pr]["head"]
            if t["id"] in run.merged_at and not any(c["verdict"] == "pass" and c["verdict_head"] == head
                                                    for c in qa_children(w, t["id"])):
                bad.append(f"{t['title']} merged at {head[:7]} without QA judging it")
    if not pushed:
        bad.append("no ticket was pushed after its waiver")
    return not bad, ", ".join(bad[:6]) or f"{pushed} waivers voided by a push"


def bot_waiver_refused(run):
    """A bot asking to waive QA, even the ticket's owner with its own token, is refused."""
    tries = [x for b in run.t.builders for x in b.waiver_tries]
    by_bots = [e for e in waiver_events(run.w) if not e["actor"].startswith("human:")]
    bad = [f"{run.w.title(tid)} {outcome}" for _, tid, _, outcome in tries if not outcome.startswith("refused")]
    bad += [f"waiver by {e['actor']}" for e in by_bots]
    if not tries:
        bad.append("no bot asked to waive")
    return not bad, ", ".join(bad[:6]) or f"{len(tries)} bot requests refused"


def qa_fail_refuses_waiver(run):
    """A waiver stands in for a missing QA result, never a failed one: ana's waiver at a head QA failed is refused,
    and no waiver is recorded at such a head."""
    w = run.w
    attempts = [a for a in run.ana if a["qa_failed"]]
    bad = [f"{a['title']} waived at failed {a['head'][:7]}" for a in attempts if a["outcome"] == "accepted"]
    bad += [f"{w.title(e['task_id'])} waiver at failed {e['head'][:7]}" for e in waiver_events(w)
            if failed_at(w, e["task_id"], e["head"])]
    if not attempts:
        bad.append("no waiver was asked at a failed head")
    return not bad, ", ".join(bad[:6]) or "; ".join(f"{a['title']}: {a['outcome'][:240]}" for a in attempts)


def waiver_sets_no_verdict(run):
    """A waiver is never a QA verdict: nobody but a QA bot sets a verdict on a QA subtask, and ana's waivers touch
    no QA subtask."""
    w, bad = run.w, []
    qa = {"bot:" + b.slug for b in run.t.qa}
    waived = {e["task_id"] for e in waiver_events(w)}
    for tid in waived:
        for c in qa_children(w, tid):
            for e in w.sql("SELECT actor, field FROM task_events WHERE task_id=? AND (actor LIKE 'human:%' OR "
                           "(field IN ('verdict','verdict_head') AND new IS NOT NULL AND new<>''))", (c["id"],)):
                if e["actor"].startswith("human:") or e["actor"] not in qa:
                    bad.append(f"{w.title(tid)} QA {e['field']} by {e['actor']}")
    if not waived:
        bad.append("nothing was waived")
    return not bad, ", ".join(bad[:6]) or f"{len(waived)} waived tickets, QA subtasks untouched"


SCENARIOS = [
    Scenario("qa_waiver_head", "QA is down for 5 h, and until a waiver is asked at a failed head (7 h at most): a "
                               "person waives it per head, a push voids the waiver, bots can't waive, a failed head "
                               "can't be waived.",
             setup=lambda run, w, team: tickets(run, w, 6), board=slow_qa_board, during=qa_down_then_waive,
             styles={"qa": {"fail": 1.0}, "builder-claude": {**BUILDER, "push_after_waiver": 1}, "builder": BUILDER},
             checks=standard(("merged heads have a QA pass or a person's waiver", WAIVER, merged_on_pass_or_waiver),
                             ("a push voids the waiver; the ticket goes back to QA", WAIVER, push_voids_waiver),
                             ("a bot's waiver is refused", WAIVER, bot_waiver_refused),
                             ("a QA fail at the head refuses the waiver", WAIVER, qa_fail_refuses_waiver),
                             ("a waiver sets no QA verdict", WAIVER, waiver_sets_no_verdict))),
]
