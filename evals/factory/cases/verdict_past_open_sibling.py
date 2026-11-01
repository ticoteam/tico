"""A builder's ticket waits on a person while its review or QA is out: a person files her own check-titled subtask
under it ("Security: confirm the vendor contract"), or the builder's manager asks her a question on it. Either holds
the ticket Waiting on another owner's open subtask. The check's verdict must still reach the builder at once
(docs/tasks.md: a Waiting parent's other open subtasks never hold one subtask's result back), the person finishes her
check-titled subtask with no verdict, and once she has, the verdict that came in meanwhile moves the ticket on."""
import json

from ..board import MERGE
from ..scenarios import Scenario, standard, tickets, at
from ..world import START, Refusal

WATCH_FROM, WATCH_UNTIL = at(1), at(4)   # tickets whose check is out in this window get a person's subtask
ANSWER_AFTER = at(2)                     # the person finishes hers this long after filing it
CHILD = "Security: confirm the vendor contract"
ASK = "Is the vendor contract signed?"
DELIVERY = "subtask results past a Waiting parent's open subtasks"
RESUME = "verdict moves on a hold's return"


def _open_check(w, t):
    """The bot check out on ticket `t` at its check step, or None."""
    step = w.board.step(t["step"])
    if not step or not step.check or t["blocked_since"] or not str(t["owner_id"]).startswith("bot:"):
        return None
    return next((c for c in w.checks(t["id"]) if str(c["owner_id"]).startswith("bot:") and c["status"] not in
                 ("done", "closed", "declined") and w.check_step(c) == step.name), None)


def _file(run, w):
    """From hour 1, the first two bot tickets with a check out each get a person's open subtask: ana's own
    check-titled one, then an ask the builder's manager puts to her. Both hold the ticket Waiting on it."""
    if not WATCH_FROM <= w.now() - START <= WATCH_UNTIL or len(run.asked) >= 2:
        return
    for t in w.tickets():
        if len(run.asked) >= 2 or t["id"] in run.asked or not _open_check(w, t):
            continue
        if not run.asked:
            child = w.post("tasks", {"owner_id": "ana", "title": CHILD, "body": "Confirm the contract covers this.",
                                     "relations": [{"task": t["id"], "kind": "parent"}]})
            child = child.get("task", child)
        else:
            # Ana manages the builders: she may say whom their ticket waits on, which files the ask as her subtask.
            w.update(t["id"], status="waiting", waiting_on="ana", note=ASK)
            child = next((c for c in reversed(w.checks(t["id"])) if c["owner_id"] == "human:ana"), {})
        run.asked[t["id"]] = {"child": child.get("id"), "title": child.get("title"), "at": w.now()}


def _answer(run, w):
    """Ana finishes her subtask, with no verdict, ANSWER_AFTER after it was filed."""
    for ticket, ask in run.asked.items():
        if ask.get("done") or not ask["child"] or w.now() < ask["at"] + ANSWER_AFTER:
            continue
        ask["done"] = True
        try:
            w.update(ask["child"], status="done", note="Yes, it is signed." if ask["title"] == ASK else "Confirmed.")
        except Refusal as exc:
            run.answer_refused.append(f"{ask['title']}: {exc.body[:120]}")


def _watch_results(run, w):
    """A check's verdict on a watched ticket, queued for its builder for over a tick, that the claim passes over (the
    reason it gives, as it would answer the builder's claim now). A builder busy with another turn, or a computer
    with no free turn, only delays it; the claim holding it back is what the check counts."""
    from backend import execution as E
    with w.store.read() as c:
        runner = c.execute("SELECT * FROM runners WHERE id=?", (w.runner["runner_id"],)).fetchone()
        ready = E.readiness_document(runner["readiness"])
        rows = c.execute("SELECT j.*, m.refs FROM jobs j JOIN messages m ON m.id=j.message_id "
                         "WHERE j.state='queued' AND json_extract(m.refs,'$.subtask') IS NOT NULL "
                         "AND json_extract(m.refs,'$.verdict') IS NOT NULL").fetchall()
        for job in map(dict, rows):
            ticket = w.parent_of(json.loads(job.pop("refs"))["subtask"])
            if ticket not in run.asked or w.H.parse_ts(job["created_at"]) >= w.now() - w.tick:
                continue
            why = E.job_refusal(c, job, {}, ready, set(), E.lanes_on(runner), E.roots_on(runner))
            if why:
                run.violations["result_held"].append((w.now(), f"{w.title(ticket)} ({why})"))


def _watch_settled(run, w):
    """A ticket at a check step, not held, whose latest bot check there finished with a verdict at the head its pull
    request is at, for over a tick: the verdict is in and nothing moved it. A pass whose next move is the merge is the
    builder's (it merges once CI is green), so it does not count."""
    now = w.now()
    for t in w.tickets():
        step = w.board.step(t["step"])
        pr = w.pr_of(t["id"])
        if not step or not step.check or t["blocked_since"] or not pr or w.prs[pr]["merged"]:
            continue
        judged = [c for c in w.checks(t["id"]) if str(c["owner_id"]).startswith("bot:") and c["verdict"]
                  and c["status"] == "done" and w.check_step(c) == step.name]
        if not judged or judged[-1]["verdict_head"] != w.prs[pr]["head"]:
            continue
        if step.check.passed == MERGE and judged[-1]["verdict"] in ("approve", "pass"):
            continue
        since = w.sql("SELECT max(created_at) created_at FROM (SELECT max(created_at) created_at FROM task_events WHERE task_id=? AND field='step' "
                      "UNION ALL SELECT max(created_at) FROM task_events WHERE task_id=? AND field='status' AND new='done')",
                      (t["id"], judged[-1]["id"]))[0]["created_at"]
        if since and w.H.parse_ts(since) < now - w.tick:
            run.violations["verdict_sat"].append((now, f"{t['title']} @ {step.name}"))


def during(run, w, team, i):
    if not hasattr(run, "asked"):
        run.asked, run.answer_refused = {}, []
    _watch_results(run, w)
    _watch_settled(run, w)
    _file(run, w)
    _answer(run, w)


# ---------------------------------------------------------------------------------------------- checks
def held_with_verdicts(run):
    """The situation happened: tickets were held Waiting on a person's subtask, and a check's verdict came in on at
    least one while it waited."""
    during_wait = []
    for ticket in run.asked:
        holds = run.w.sql("SELECT created_at, new FROM task_events WHERE task_id=? AND field='blocked_since' ORDER BY created_at, rowid",
                          (ticket,))
        for c in run.w.checks(ticket):
            if not (str(c["owner_id"]).startswith("bot:") and c["verdict"]):
                continue
            given = run.w.sql("SELECT created_at FROM task_events WHERE task_id=? AND field='verdict' ORDER BY created_at LIMIT 1",
                              (c["id"],))
            if given and _held_at(holds, given[0]["created_at"]):
                during_wait.append(run.w.title(ticket))
    ok = len(run.asked) == 2 and bool(during_wait)
    return ok, f"{len(run.asked)} tickets held; verdicts while waiting on {', '.join(sorted(set(during_wait))) or 'none'}"


def _held_at(holds, ts):
    """Whether a ticket's hold history (blocked_since events: set, then cleared) has it held at `ts`."""
    held = False
    for h in holds:
        if h["ts"] > ts:
            break
        held = bool(h["new"])
    return held


def verdicts_reach_builder(run):
    """Every verdict on a watched ticket reached its builder: a notice naming the check is in the builder's history
    (a quiet one when the verdict's move needs nothing of it), any turn it asks for was claimed, and the claim never
    passed one over for more than a tick."""
    missing = []
    for ticket in run.asked:
        owner = run.w.sql("SELECT owner_id FROM tasks WHERE id=?", (ticket,))[0]["owner_id"]
        for c in run.w.checks(ticket):
            if not (str(c["owner_id"]).startswith("bot:") and c["verdict"]):
                continue
            notices = run.w.sql("SELECT j.state, j.attempt_id, EXISTS (SELECT 1 FROM attempt_inputs a WHERE "
                                "a.message_id=m.id) folded FROM messages m LEFT JOIN jobs j ON j.message_id=m.id "
                                "WHERE m.recipient_id=? AND json_extract(m.refs,'$.subtask')=?", (owner, c["id"]))
            if not notices or any(n["state"] and not n["attempt_id"] and not n["folded"] for n in notices):
                missing.append(f"{run.w.title(ticket)}: {c['title']} "
                               f"({', '.join(str(n['state'] or 'quiet') for n in notices) or 'no notice'})")
    held = run.violations["result_held"]
    detail = [f"never delivered: {', '.join(missing[:4])}"] if missing else []
    if held:
        detail.append(f"claim held it {len(held)} ticks: " + ", ".join(sorted({f'{t} {when:%H:%M}' for when, t in held})[:4]))
    return not missing and not held, "; ".join(detail)


def no_settled_check_waits(run):
    seen = run.violations["verdict_sat"]
    return not seen, ", ".join(sorted({f"{t} {when:%H:%M}" for when, t in seen})[:6])


def person_child_finishes(run):
    """The person's subtasks finished with no verdict and no refusal."""
    bad = list(run.answer_refused)
    for ticket, ask in run.asked.items():
        row = run.w.sql("SELECT status, verdict FROM tasks WHERE id=?", (ask["child"] or "",))
        if not row or row[0]["status"] != "done" or row[0]["verdict"]:
            bad.append(f"{ask['title']}: {row[0] if row else 'not filed'}")
    return bool(run.asked) and not bad, ", ".join(bad[:4])


def watched_merge(run):
    left = [run.w.title(t) for t in run.asked if t not in run.merged_at]
    return not left, ", ".join(left)


SCENARIOS = [
    Scenario("verdict_past_open_sibling",
             "Tickets wait on a person's own subtask while their review is out: the verdict still reaches the "
             "builder, she finishes her Security subtask without a verdict, and the ticket moves on and merges.",
             setup=lambda run, w, team: tickets(run, w, 6), during=during,
             checks=standard(("tickets held on a person's subtask with a verdict in meanwhile", None, held_with_verdicts),
                             ("every verdict reaches the builder, never held past a tick", DELIVERY, verdicts_reach_builder),
                             ("no ticket sits at a check step with its verdict in", RESUME, no_settled_check_waits),
                             ("a person's check-titled subtask finishes without a verdict", None, person_child_finishes),
                             ("the held tickets merge", None, watched_merge))),
]
