"""The verdict moves the ticket (docs/software-factory.md, "The verdict moves the ticket"), by its steps' flow.

What a check step does with a verdict is set on the step (backend/step_flow.py), never read from its name: a check
filed while its ticket sat at a check step, finished on the head the ticket's pull request is at now while the ticket
is still at that step, moves the ticket as Tico (`after_verdict` marks it, `after_capture` moves it once the judging
attempt is recorded):
- approve or pass -> the step's `on_pass`; when that is the merge, the builder is woken to merge once CI is green;
- changes, fail, or a check finished with no verdict (its comments are the feedback) -> the step's `on_fail`, and the
  builder is woken once, with the verdict and the head. A pass at a head that one of those already stands on at the
  step goes there too: a later round at the same head clears nothing (`_verdict_at`).
Tico never merges: the builder does, woken once per head (`merge_wake`). The merge moves the ticket to the type's
`on_merge` step from any earlier step (`merged`), and green CI on a new head at a step with `after_green` moves it on
to its next check (`checks_done`). A ticket entering a step whose `skip_tag` it carries goes on to the step's
`on_pass` (`skip`), and a check open past its step's `timeout_hours` is closed and its round skipped (`timeouts`).

One move per step and head: Tico moves only a ticket still at the check's own step, on the judged head, so the
builder's move and Tico's are the same move, made once. A move the review gate refuses (CI failing, no receipt at the
head) stays where it is and the builder is told what clears it, once per head and kind (review_gate.notify).

Builder hand-offs (a verdict to act on, a failed check, a merge conflict, a merge to do) carry `refs.handoff`. Only
the keeper's count: they always wake (wake_rules.always_wakes), are never held for a batch, and are the first of the
bot's queued jobs a claim hands out (HANDOFF_FIRST_SQL). Each wakes the turn of the ticket's own session (its lane).
"""
import json
import logging

from . import hubdb as H
from . import review_gate
from . import step_flow as F
from . import task_relations as TR
from .check_independence import assess as assess_independence

log = logging.getLogger(__name__)

GOOD = ("approve", "pass")
NO_VERDICT = "comments"     # a check finished with no verdict: what it said is feedback, like changes
HANDOFF = "handoff"
HANDOFFS = ("verdict", "failed_check", "merge_conflict", "merge")
# The claim's order key: a keeper hand-off before the bot's other queued jobs (backend/execution.py).
HANDOFF_FIRST_SQL = (f"CASE WHEN m.from_actor='{H.KEEPER}' AND json_extract(m.refs,'$.{HANDOFF}') "
                     "IS NOT NULL THEN 0 ELSE 1 END")


def at_step(c, task_id, refs):
    """`refs` with the step the ticket is at now: the news is about that step, and a claim drops it once the ticket
    has left it (execution.obsolete_notices_sql)."""
    row = H.task(c, task_id)
    return {**refs, "at_step": row.get("step_id")} if row and row.get("step_id") else refs


def _ticket(c, row):
    """The ticket a bot builds, or None: an open ticket owned by a bot, on a type whose steps have a flow, and not
    held: a hold keeps the ticket at its check step, where a verdict must wait for it to clear (`resumed`)."""
    return row if (row and H.is_bot(row.get("owner")) and row.get("status") in H.ACTIVE_STATUSES
                   and not row.get("blocked_since") and F.has_flow(c, row.get("type_id"))) else None


def step_of(c, row):
    found = c.execute("SELECT name FROM task_steps WHERE id=?", (row.get("step_id") or "",)).fetchone()
    return found[0] if found else None


def _prs(c, row):
    return [p for p in H.task_links(c, row["id"]) if p["kind"] == "pr" and p.get("state") != "closed"]


def _head(pr):
    return str((H._json(pr.get("detail"), {}) or {}).get("head_sha") or "").lower()


def at_head(c, row, head):
    """Whether `head` (7 to 40 characters) is the head a pull request on this ticket is at now."""
    head = str(head or "").lower()
    return bool(head) and any(_head(pr).startswith(head) for pr in _prs(c, row) if _head(pr))


def _full_head(c, row, head):
    """The full head of the pull request on the ticket that `head` (7 to 40 characters, as a verdict may write it)
    starts, lowered; `head` itself when no pull request is at it. Comparisons start from that full SHA, so a verdict's
    short head and full head of one commit match whichever form the checker wrote."""
    head = str(head or "").lower()
    return next((_head(pr) for pr in _prs(c, row) if head and _head(pr).startswith(head)), head)


def is_merged(c, row):
    prs = _prs(c, row)
    return bool(prs) and all(p.get("state") in ("merged", "shipped") for p in prs)


def _checks(c, row, step):
    """The ticket's checks filed at check step `step`, newest last. A person's own subtask filed there is ordinary
    work, never a check, whatever its title: finished with no verdict, it is not a check's comments."""
    out = [H.task(c, cid) for cid in TR.child_ids(c, row["id"])]
    out = [t for t in out if t and H.is_bot(t.get("owner")) and (F.check_step(c, t, row) or {}).get("id") == step["id"]]
    return sorted(out, key=lambda t: (t.get("created_at") or "", t["id"]))


def _verdict(check):
    """A finished check's verdict, lowered; a check done with none counts as comments."""
    value = str(check.get("verdict") or "").lower()
    return value or (NO_VERDICT if check.get("status") == "done" else "")


def move(c, row, target, why):
    """Move the ticket to step `target` (a step row or id) as Tico. True when it moved. A refusal (the review gate)
    undoes only this move, tells the builder what clears it, and is remembered so Tico retries it then
    (backend/move_retry.py), as the notice promises."""
    target = target if isinstance(target, dict) else F.step(c, target)
    if not target or target["type_id"] != row.get("type_id") or row.get("step_id") == target["id"]:
        return False
    c.execute("SAVEPOINT verdict_move")
    try:
        # The owner hears of the move on the hand-off that caused it, not again from a step rule.
        H.task_update(c, H.KEEPER, row["id"], step=target["id"], skip_step_wake=(row["owner"],))
        H._task_event(c, row["id"], H.KEEPER, "verdict_move", None, target["name"], why)
    except H.Refused as exc:
        c.execute("ROLLBACK TO verdict_move")
        c.execute("RELEASE verdict_move")
        notice = getattr(exc, "review_gate", None)
        if notice:
            review_gate.notify(c, H.KEEPER, notice)
            from . import move_retry
            move_retry.remember(c, H.KEEPER, notice)
        return False
    except Exception:  # noqa: BLE001 - a verdict or a merge is never lost to the move it causes
        c.execute("ROLLBACK TO verdict_move")
        c.execute("RELEASE verdict_move")
        log.exception("verdict move of %s to %s failed", row["id"], target["id"])
        return False
    c.execute("RELEASE verdict_move")
    return True


def after_verdict(c, check):
    """A verdict was just recorded on `check` (or it finished with none): mark its ticket to move once the judging
    attempt is captured, and keep what the builder is to hear on its delivery (`delivery`). Returns the outcome, or
    None when the check moves nothing."""
    check = H.task(c, check["id"])
    verdict = _verdict(check)
    ticket = _ticket(c, H.task(c, TR.parent_of(c, check["id"]) or ""))
    step = ticket and F.check_step(c, check, ticket)
    head = str(check.get("verdict_head") or _judged_head(c, check) or "").lower()
    if not verdict or not step or ticket.get("step_id") != step["id"] or not at_head(c, ticket, head):
        return None
    name = step["name"]
    # Verdicts are commonly written inside the checker turn's final tool call. The executed
    # model segments are committed by turn_finish afterwards, so this callback records an
    # immutable pending marker and leaves routing to the post-capture callback.
    attempt = c.execute("SELECT id FROM turns WHERE task_id=? ORDER BY started_at DESC,id DESC LIMIT 1",
                        (check["id"],)).fetchone()
    attempt_id = attempt["id"] if attempt else None
    pending = {"check_id": check["id"], "attempt_id": attempt_id, "step": name, "head": head,
               "verdict": verdict}
    existing = c.execute("SELECT new FROM task_events WHERE task_id=? AND field='verdict_wait' "
                         "ORDER BY rowid DESC LIMIT 1", (ticket["id"],)).fetchone()
    if not existing or H._json(existing["new"], {}).get("check_id") != check["id"] or \
            H._json(existing["new"], {}).get("attempt_id") != attempt_id or \
            H._json(existing["new"], {}).get("head") != head:
        H._task_event(c, ticket["id"], H.KEEPER, "verdict_wait", None,
                      json.dumps(pending, sort_keys=True), "")
    return {"moved": None, "handoff": None, "quiet": True, "pending": True,
            "say": f"{name} recorded {verdict} at head {head}; waiting for this check attempt's execution record."}


def _judged_head(c, check):
    """The head a check was filed for (its review_head), for a check finished with no verdict line."""
    found = c.execute("SELECT new FROM task_events WHERE task_id=? AND field='review_head' "
                      "ORDER BY ts DESC,rowid DESC LIMIT 1", (check["id"],)).fetchone()
    return str(found[0] if found else "").split(",")[0]


def after_capture(c, check_id, head, attempt_id):
    """Consume a verdict only after its exact judging attempt has captured usage and settled."""
    check = H.task(c, check_id)
    verdict = _verdict(check or {})
    if not verdict or str(check.get("verdict_head") or _judged_head(c, check) or "").lower() != str(head or "").lower():
        return None
    attempt = c.execute("SELECT task_id,bot,finished_at FROM turns WHERE id=?", (attempt_id,)).fetchone()
    latest = c.execute("SELECT id FROM turns WHERE task_id=? ORDER BY started_at DESC,id DESC LIMIT 1", (check_id,)).fetchone()
    if (not attempt or attempt["task_id"] != check_id or not attempt["finished_at"] or
            not latest or latest["id"] != attempt_id or check.get("status") != "done" or
            H.actor_id(attempt["bot"]) != H.actor_id(check.get("owner"))):
        return None
    ticket = _ticket(c, H.task(c, TR.parent_of(c, check_id) or ""))
    step = ticket and F.check_step(c, check, ticket)
    if not step:
        return None
    marker = c.execute("SELECT rowid,new FROM task_events WHERE task_id=? AND field='verdict_wait' "
                       "ORDER BY rowid DESC LIMIT 1", (ticket["id"],)).fetchone()
    pending = H._json(marker["new"], {}) if marker else {}
    if (pending.get("check_id"), pending.get("attempt_id"), pending.get("head"), pending.get("verdict")) != \
            (check_id, attempt_id, str(head or "").lower(), verdict):
        return None
    if ticket.get("step_id") != step["id"] or not at_head(c, ticket, head):
        return None
    prior = delivery(c, check)
    if prior and prior.get("consumed"):
        return None
    name = step["name"]
    eligible = assess_independence(c, ticket, H.actor_id(check.get("owner") or ""), head=head,
                                   check_id=check["id"], attempt_id=attempt_id)
    # Model company is a routing preference, never a verdict gate. Keep actual builder/checker families
    # alongside the verdict for audit, including fallback runs and unknown provenance.
    if verdict not in GOOD:
        out = _failed(c, ticket, step, verdict, head)
    elif (standing := _verdict_at(c, ticket, step, head)) not in GOOD:
        out = _failed(c, ticket, step, standing, head, passed=verdict)
    else:
        out = _passed(c, ticket, step, f"{name} gave {verdict} at head {head}")
    out["eligibility"] = eligible  # Historical field name; this is provenance, not a gate.
    out["consumed"] = True
    out["attempt_id"] = attempt_id
    H._task_event(c, check["id"], H.KEEPER, "verdict_move", None,
                  json.dumps({"verdict": verdict, "head": head, **out}, sort_keys=True), "")
    if out.get("handoff"):
        refs = {"verdict": verdict, "verdict_head": head, HANDOFF: out["handoff"]}
        if out["handoff"] == "merge":
            refs["pr_head"] = head
        H._wake(c, ticket, ticket["owner"], out["say"], at_step(c, ticket["id"], refs), source=check_id)
    return out


def resumed(c, task_id):
    """The ticket is back at a check step from a hold (task_blocked.reconcile): a verdict its check there gave while it
    was held, at the head its pull request is at now, moves it as it would have then. A verdict moves only a ticket at
    the check's own step, so one given during the hold moved nothing, and the ticket would sit on a settled check."""
    ticket = _ticket(c, H.task(c, task_id))
    step = ticket and F.step(c, ticket.get("step_id"))
    if not step or not F.is_check(step):
        return None
    # A replay must keep the same audience as the child's normal result delivery.
    last = next((t for t in reversed(_checks(c, ticket, step))
                 if _verdict(t) and H._subtask_result_parent(c, t)), None)
    if not last or (delivery(c, last) or {}).get("consumed") or not after_verdict(c, last):
        return None
    attempt = c.execute("SELECT id FROM turns WHERE task_id=? ORDER BY started_at DESC,id DESC LIMIT 1",
                        (last["id"],)).fetchone()
    head = str(last.get("verdict_head") or _judged_head(c, last) or "").lower()
    return after_capture(c, last["id"], head, attempt["id"]) if attempt else None


def _failed(c, ticket, step, verdict, head, passed=None):
    """A check at `step` gave `verdict`, not good, at `head`: the ticket moves to the step's `on_fail`, and the builder
    hears of it on that hand-off. `passed` is a pass at the same head that the failure still stands against."""
    name = step["name"]
    gave = "no verdict (its comments are the feedback)" if verdict == NO_VERDICT else verdict
    target = F.step(c, F.of(step).get("on_fail"))
    moved = bool(target) and move(c, ticket, target, f"{name} gave {verdict} at {head[:12]}")
    said = (f"{name} gave {passed} at head {head}, but {name} already gave {gave} at that head, and only a new head "
            "clears it" if passed else f"{name} gave {gave} at head {head}")
    return {"moved": target["name"] if moved else None, "handoff": "verdict", "quiet": False,
            "say": (f"Tico moved this ticket to {target['name']}: {said}." if moved else
                    said + (f"; Tico could not move this ticket to {target['name']}." if target else "."))}


def _passed(c, ticket, step, why):
    """The ticket's check at `step` passed (or its round was skipped): on to the step's `on_pass`, or, when the merge
    is next, the merged step if it already merged, else the builder is woken to merge once CI is green."""
    target = F.of(step).get("on_pass")
    if target == F.MERGE:
        target = F.merge_step(c, ticket["type_id"]) if is_merged(c, ticket) else None
        if not target:
            ready = merge_ready(c, ticket)
            if ready:
                _merge_woken(c, ticket, ready[1])
            return {"moved": None, "handoff": "merge" if ready else None, "quiet": not ready,
                    "say": merge_text(c, ticket, *ready) if ready else
                    f"{why}. Tico wakes you to merge once CI is green on it."}
    target = F.step(c, target) if target and not isinstance(target, dict) else target
    if not target:
        return {"moved": None, "handoff": None, "quiet": True, "say": f"{why}."}
    moved = move(c, ticket, target, why)
    # A move to the next check needs nothing from the builder; a move back to the builder's own step is its cue.
    cue = moved and target["status"] in ("open", "doing")
    return {"moved": target["name"] if moved else None, "handoff": "verdict" if cue else None, "quiet": moved and not cue,
            "say": (f"Tico moved this ticket to {target['name']}: {why}." if moved else
                    f"{why}; Tico could not move this ticket to {target['name']}.")}


def delivery(c, check):
    """What the verdict's delivery to the builder carries (hubdb._deliver_subtask_result): the outcome
    `after_verdict` kept for the check's current verdict and head, or None."""
    row = c.execute("SELECT new FROM task_events WHERE task_id=? AND field='verdict_move' ORDER BY ts DESC, rowid DESC "
                    "LIMIT 1", (check["id"],)).fetchone()
    out = H._json(row[0], {}) if row else None
    marker = c.execute("SELECT new FROM task_events WHERE task_id=? AND field='verdict_wait' "
                       "ORDER BY rowid DESC LIMIT 1", (TR.parent_of(c, check["id"]) or "",)).fetchone()
    pending = H._json(marker["new"], {}) if marker else {}
    turn = c.execute("SELECT id FROM turns WHERE task_id=? ORDER BY started_at DESC,id DESC LIMIT 1",
                     (check["id"],)).fetchone()
    waiting = turn and pending.get("check_id") == check["id"] and pending.get("attempt_id") == turn["id"]
    verdict = _verdict(check)
    head = str(check.get("verdict_head") or _judged_head(c, check) or "").lower()
    # A rerouted check can get the same verdict at the same head from its next checker: that attempt's own outcome is
    # still to come, so the earlier attempt's is not it.
    if out and (out.get("verdict"), out.get("head")) == (verdict, head) \
            and not (waiting and out.get("attempt_id") not in (None, turn["id"])):
        return out
    if waiting:
        return {"verdict": verdict, "head": head, "pending": True,
                "quiet": True, "say": "This check verdict is waiting for its executed attempt to be captured."}
    return None


def _verdict_at(c, row, step, head):
    """The verdict the checks at `step` hold against `head` (lowered), or None when none judged that head. A fail,
    changes or comments wins over any pass on the same head, newer or not: a failed head is cleared by a fix at a new
    head, not by a later round at the same one, as a failed CI attempt or test receipt is (docs/tasks.md)."""
    head = _full_head(c, row, head)
    given = [_verdict(check) for check in _checks(c, row, step) if _verdict(check)
             and head.startswith(str(check.get("verdict_head") or _judged_head(c, check) or "-").lower())]
    return next((v for v in given if v not in GOOD), given[-1] if given else None)


def _skipped(c, row, step, head):
    """Whether the ticket passes `step` without a check at `head`: it carries the step's skip tag, or the step's round
    was skipped (timed out) at that head and no check there failed the head."""
    if F.skip_target(c, row, step):
        return True
    if not c.execute("SELECT 1 FROM task_events WHERE task_id=? AND field='round_skipped' AND new=? LIMIT 1",
                     (row["id"], f"{step['id']}:{head}")).fetchone():
        return False
    verdict = _verdict_at(c, row, step, head)
    return not verdict or verdict in GOOD


def merge_ready(c, row):
    """(pull request, head) when the merge is next from the ticket's step (its `on_pass`, or `after_green` on a new
    head) and CI is green on the head it is at now. On `on_pass`, the step's checks must have approved that head or its
    round was skipped there, and none may have failed it. None otherwise."""
    step = F.step(c, row.get("step_id"))
    flow = F.of(step)
    if not step or F.MERGE not in (flow.get("on_pass"), flow.get("after_green")):
        return None
    prs = [p for p in _prs(c, row) if p.get("state") in ("open", "draft")]
    if len(prs) != 1 or prs[0].get("state") != "open" or prs[0].get("checks") != "passing" \
            or prs[0].get("mergeable") == "conflict" or not _head(prs[0]):
        return None
    pr, head = prs[0], _head(prs[0])
    if flow.get("on_pass") == F.MERGE:
        # The same verdict the timeout and the skipped round read: a pass after a fail at this head clears nothing.
        return (pr, head) if _skipped(c, row, step, head) or _verdict_at(c, row, step, head) in GOOD else None
    judged = _judged_by(c, row, step)
    return (pr, head) if judged and not head.startswith(judged) else None


def _judged_by(c, row, step):
    """The head the check that sent the ticket to work step `step` judged, lowered, or None."""
    check = F.fixes_for(c, step)
    if not check:
        return None
    found = next((t for t in reversed(_checks(c, row, check)) if _verdict(t)), None)
    return str(found.get("verdict_head") or _judged_head(c, found) or "").lower() if found else None


def merge_text(c, row, pr, head):
    name = step_of(c, row) or "The last check"
    number = pr.get("number") or "?"
    landing = F.merge_step(c, row["type_id"])
    return (f"Ready to merge: {name} cleared PR #{number} at head {head} and CI is green. Merge it under the merge "
            f"rules (gh pr merge {number} --squash --match-head-commit {head}) and post the MERGED line"
            + (f"; Tico then moves this ticket to {landing['name']}." if landing else "."))


def _merge_woken(c, row, head):
    """Record the merge wake for this head; False when one was already sent for it."""
    if c.execute("SELECT 1 FROM task_events WHERE task_id=? AND field='merge_wake' AND new=? LIMIT 1",
                 (row["id"], head)).fetchone():
        return False
    H._task_event(c, row["id"], H.KEEPER, "merge_wake", None, head, "")
    return True


def merge_wake(c, row):
    """Wake the builder to merge when the ticket is ready (merge_ready), once per head."""
    row = _ticket(c, row)
    ready = row and merge_ready(c, row)
    if ready and _merge_woken(c, row, ready[1]):
        H._wake(c, row, row["owner"], merge_text(c, row, *ready), {HANDOFF: "merge", "pr_head": ready[1]},
                trigger="pr_checks")
        return True
    return False


def checks_done(c, row, head, outcome):
    """Every check on `head` finished (github.pr_signal). Green on a head newer than the one its check judged, at a
    step with `after_green`, moves the ticket on to its next check; green with the merge next wakes the builder to
    merge."""
    row = _ticket(c, H.task(c, row["id"]))
    if not row or outcome != "pass" or not at_head(c, row, head):
        return None
    step = F.step(c, row.get("step_id"))
    target = F.of(step).get("after_green")
    if target and target != F.MERGE:
        judged = _judged_by(c, row, step)
        if not (judged and str(head).lower().startswith(judged)):
            if move(c, row, target, f"checks passed on the new head {str(head)[:12]} at {step['name']}"):
                return F.name(c, target)
        return None
    merge_wake(c, row)
    return None


def merged(c, row):
    """A pull request merged: the ticket goes to its type's merged step (`on_merge`) from any earlier step."""
    row = _ticket(c, H.task(c, row["id"]))
    if not row or not is_merged(c, row):
        return False
    target = F.merge_step(c, row["type_id"])
    if not target or F.at_or_past(c, row, target):
        return False
    return move(c, row, target, "its pull request merged")


def new_head(c, row):
    """The ticket's pull request has a new head while it sits at a check step: when no check of that step is still
    open, file one for the new head (the step's pool); an open one judges whatever head is current when it runs."""
    row = _ticket(c, H.task(c, row["id"]))
    step = row and F.step(c, row.get("step_id"))
    if not step or not F.is_check(step) or F.skip_target(c, row, step):
        return None
    if any(t["status"] in H.ACTIVE_STATUSES for t in _checks(c, row, step)):
        return None
    from . import check_pools
    return check_pools.entered(c, H.KEEPER, row)


def skip(c, row):
    """The ticket is at a step whose skip tag it carries: on to the step's `on_pass`, as if its check passed. The
    skip is recorded on the ticket."""
    row = _ticket(c, H.task(c, row["id"]))
    step = row and F.step(c, row.get("step_id"))
    if not step or not F.skip_target(c, row, step):
        return None
    tag = F.of(step)["skip_tag"]
    H._task_event(c, row["id"], H.KEEPER, "step_skipped", None, step["name"], f"tagged {tag}")
    out = _passed(c, row, step, f"{step['name']} skipped: the ticket is tagged {tag}")
    if out.get("handoff"):
        H._wake(c, row, row["owner"], out["say"], at_step(c, row["id"], {HANDOFF: out["handoff"]}))
    return out


def timeouts(c, at=None):
    """Skip the round of each ticket at a check step past the step's `timeout_hours` with no verdict Tico acted on
    there: its open checks are closed and the ticket goes on as if the check passed with no comments. The clock starts
    when the ticket entered the step, so a check left open, one judged on an old head and one closed unanswered all end
    the same way. Returns the tickets moved on."""
    at = H.parse_ts(at or H.now())
    skipped = []
    timed = [dict(r) for r in c.execute("SELECT * FROM task_steps WHERE flow LIKE '%timeout_hours%'")]
    for step in timed:
        hours = F.of(step).get("timeout_hours")
        if not hours or not F.is_check(step):
            continue
        for (ticket_id,) in c.execute(f"SELECT id FROM tasks WHERE step_id=? AND status IN "
                                      f"({','.join('?' * len(H.ACTIVE_STATUSES))})",
                                      (step["id"], *H.ACTIVE_STATUSES)).fetchall():
            ticket = _ticket(c, H.task(c, ticket_id))
            entered = c.execute("SELECT ts FROM task_events WHERE task_id=? AND field='step' AND new=? "
                                "ORDER BY ts DESC,rowid DESC LIMIT 1", (ticket_id, step["id"])).fetchone()
            if not ticket or not entered or (at - H.parse_ts(entered[0])).total_seconds() < hours * 3600:
                continue
            if c.execute("SELECT 1 FROM task_events WHERE task_id=? AND field IN ('round_skipped','step_skipped') "
                         "AND ts>=? LIMIT 1", (ticket_id, entered[0])).fetchone():
                continue        # skipped already: the merge, its builder's to do, is what it waits for
            head = next((_head(p) for p in _prs(c, ticket) if _head(p)), "")
            # A verdict at the head is usable whichever way it went. A pass ends the round on it, waiting only for what
            # comes after the check (green CI and the merge), and says so rather than that no verdict came (Dev 646).
            # A fail is not skipped over: the ticket goes to the step's on_fail, as when the check gave it.
            verdict = _verdict_at(c, ticket, step, head)
            for check in _checks(c, ticket, step):
                if check["status"] in H.ACTIVE_STATUSES:
                    H.task_close(c, H.KEEPER, check["id"],
                                 note=f"Unanswered for {hours:g} h; Tico closed it and skipped this round.")
            if verdict:
                note = f"{step['name']}: {verdict} at head {head[:12]}; round closed after {hours:g} h"
            else:
                note = f"{step['name']}: no usable verdict in {hours:g} h"
            H._task_event(c, ticket_id, H.KEEPER, "round_skipped", None, f"{step['id']}:{head}", note)
            if verdict and verdict not in GOOD:
                out = _failed(c, H.task(c, ticket_id), step, verdict, head)
                refs = {"verdict": verdict, "verdict_head": head, HANDOFF: out["handoff"]}
            else:
                why = (f"{step['name']} gave {verdict} at head {head}" if verdict else
                       f"{step['name']} had no usable verdict for {hours:g} h; Tico skipped the round")
                out = _passed(c, H.task(c, ticket_id), step, why)
                refs = {HANDOFF: out["handoff"]}
            if out.get("handoff"):
                H._wake(c, ticket, ticket["owner"], out["say"], at_step(c, ticket_id, refs))
            skipped.append(ticket_id)
    return skipped
