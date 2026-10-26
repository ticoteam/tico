"""The verdict moves the Dev ticket (docs/software-factory.md, "The verdict moves the ticket").

A Review 1, Review 2 or QA check's verdict, recorded on the head the ticket's pull request is at now while the ticket
is still at that check's step, moves the ticket as Tico, in the same write (`after_verdict`):
- Review 1 approve -> Review 2; on a ticket tagged `review-1-only` it is the last review, as Review 2 below;
- Review 2 approve -> In QA when the ticket has the `qa` tag; otherwise it stays and its builder is woken to merge;
- QA pass -> Pending Release when the pull request is merged; otherwise its builder is woken to merge;
- changes -> that review's Fixes step, fail -> QA Fixes; the builder is woken once, with the verdict and the head.
Tico never merges (Chris's open question 1, default no): the builder does, woken once per head when the last check
approved it and CI is green (`merge_wake`). The merge then moves a ticket waiting for it to Pending Release
(`merged`), and checks passing on a new head at a Fixes step move it on to the next check (`checks_done`, AFTER_FIXES).

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
from . import task_relations as TR
from .check_independence import assess as assess_independence

log = logging.getLogger(__name__)

CHECK_STEP = {"review 1": "Review 1", "review 2": "Review 2", "qa": "In QA"}
FIXES = {"review 1": "Review 1 Fixes", "review 2": "Review 2 Fixes", "qa": "QA Fixes"}
GOOD = ("approve", "pass")
REVIEW_1_ONLY = "review-1-only"
PENDING_RELEASE = "Pending Release"
HANDOFF = "handoff"
HANDOFFS = ("verdict", "failed_check", "merge_conflict", "merge")
# The claim's order key: a keeper hand-off before the bot's other queued jobs (backend/execution.py).
HANDOFF_FIRST_SQL = (f"CASE WHEN m.from_actor='{H.KEEPER}' AND json_extract(m.refs_json,'$.{HANDOFF}') "
                     "IS NOT NULL THEN 0 ELSE 1 END")


def kind_of(row):
    """'review 1', 'review 2' or 'qa' for a check subtask, by its title (review_gate.ONE_OPEN), else None."""
    found = review_gate.ONE_OPEN.match(str((row or {}).get("title") or "").strip())
    return " ".join(found[1].lower().split()) if found else None


def _ticket(c, row):
    """The Dev ticket a bot builds, or None: an open engineering ticket owned by a bot."""
    return row if (row and review_gate.engineering(c, row) and H.is_bot(row.get("owner"))
                   and row.get("status") in H.ACTIVE_STATUSES) else None


def step_of(c, row):
    found = c.execute("SELECT name FROM task_steps WHERE id=?", (row.get("step_id") or "",)).fetchone()
    return found[0] if found else None


def _has_step(c, row, name):
    return bool(c.execute("SELECT 1 FROM task_steps WHERE type_id=? AND name=?", (row.get("type_id"), name)).fetchone())


def _prs(c, row):
    return [p for p in H.task_links(c, row["id"]) if p["kind"] == "pr" and p.get("state") != "closed"]


def _head(pr):
    return str((H._json(pr.get("detail_json"), {}) or {}).get("head_sha") or "").lower()


def at_head(c, row, head):
    """Whether `head` (7 to 40 characters) is the head a pull request on this ticket is at now."""
    head = str(head or "").lower()
    return bool(head) and any(_head(pr).startswith(head) for pr in _prs(c, row) if _head(pr))


def is_merged(c, row):
    prs = _prs(c, row)
    return bool(prs) and all(p.get("state") in ("merged", "shipped") for p in prs)


def last_review(row):
    """The review after which only QA (on a `qa` ticket) and the merge are left."""
    return "review 1" if REVIEW_1_ONLY in H.task_labels(row) else "review 2"


def _approved(row, kind):
    """Where an approval of a `kind` check sends the ticket: a step name, or None when the merge is next."""
    if kind == "review 1" and kind != last_review(row):
        return "Review 2"
    if kind != "qa" and "qa" in H.task_labels(row):
        return "In QA"
    return None


# A green new head at a Fixes step goes on to the next check: Review 1 Fixes to Review 2, as the factory runs one
# round of each review (docs/software-factory.md); QA Fixes back to QA. None: the merge is next.
AFTER_FIXES = {
    "Review 1 Fixes": lambda row: _approved(row, "review 1"),
    "Review 2 Fixes": lambda row: _approved(row, "review 2"),
    "QA Fixes": lambda row: "In QA",
}


def _checks(c, row, kind):
    """The ticket's check subtasks of one kind, newest last."""
    out = [H.task(c, cid) for cid in TR.child_ids(c, row["id"])]
    return sorted((t for t in out if t and kind_of(t) == kind), key=lambda t: (t.get("created") or "", t["id"]))


def move(c, row, target, why):
    """Move the ticket to `target` as Tico, when its type has that step. True when it moved. A refusal (the review
    gate) undoes only this move and tells the builder what clears it."""
    if not _has_step(c, row, target) or step_of(c, row) == target:
        return False
    c.execute("SAVEPOINT verdict_move")
    try:
        # The owner hears of the move on the hand-off that caused it, not again from a step rule.
        H.task_update(c, H.KEEPER, row["id"], step=target, skip_step_wake=(row["owner"],))
        H._task_event(c, row["id"], H.KEEPER, "verdict_move", None, target, why)
    except H.Refused as exc:
        c.execute("ROLLBACK TO verdict_move")
        c.execute("RELEASE verdict_move")
        notice = getattr(exc, "review_gate", None)
        if notice:
            review_gate.notify(c, H.KEEPER, notice)
        return False
    except Exception:  # noqa: BLE001 - a verdict or a merge is never lost to the move it causes
        c.execute("ROLLBACK TO verdict_move")
        c.execute("RELEASE verdict_move")
        log.exception("verdict move of %s to %s failed", row["id"], target)
        return False
    c.execute("RELEASE verdict_move")
    return True


def after_verdict(c, check):
    """A verdict was just recorded on `check`: move its Dev ticket, and keep what the builder is to hear on its
    delivery (`delivery`). Returns the outcome, or None when the verdict moves nothing."""
    check = H.task(c, check["id"])
    kind, verdict = kind_of(check), str(check.get("verdict") or "").lower()
    ticket = _ticket(c, H.task(c, TR.parent_of(c, check["id"]) or ""))
    head = str(check.get("verdict_head") or "").lower()
    if not kind or not verdict or not ticket or step_of(c, ticket) != CHECK_STEP[kind] or not at_head(c, ticket, head):
        return None
    name = CHECK_STEP[kind]
    # Verdicts are commonly written inside the checker turn's final tool call. The executed
    # model segments are committed by turn_finish afterwards, so this callback records an
    # immutable pending marker and leaves routing to the post-capture callback.
    attempt = c.execute("SELECT id FROM turns WHERE task_id=? ORDER BY started DESC,id DESC LIMIT 1",
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
    else:
        pending = H._json(existing["new"], {})
    pending.update({"check_id": check["id"], "attempt_id": attempt_id, "step": name,
                    "head": head, "verdict": verdict})
    return {"moved": None, "handoff": None, "quiet": True, "pending": True,
            "say": f"{name} recorded {verdict} at head {head}; waiting for this check attempt's execution record."}


def after_capture(c, check_id, head, attempt_id):
    """Consume a verdict only after its exact judging attempt has captured usage and settled."""
    check = H.task(c, check_id)
    if not check or not check.get("verdict") or str(check.get("verdict_head") or "").lower() != str(head or "").lower():
        return None
    attempt = c.execute("SELECT task_id,bot,finished FROM turns WHERE id=?", (attempt_id,)).fetchone()
    latest = c.execute("SELECT id FROM turns WHERE task_id=? ORDER BY started DESC,id DESC LIMIT 1", (check_id,)).fetchone()
    if (not attempt or attempt["task_id"] != check_id or not attempt["finished"] or
            not latest or latest["id"] != attempt_id or check.get("status") != "done" or
            H.actor_id(attempt["bot"]) != H.actor_id(check.get("owner"))):
        return None
    ticket = _ticket(c, H.task(c, TR.parent_of(c, check_id) or ""))
    kind = kind_of(check)
    if not kind or not ticket:
        return None
    verdict = str(check.get("verdict") or "").lower()
    marker = c.execute("SELECT rowid,new FROM task_events WHERE task_id=? AND field='verdict_wait' "
                       "ORDER BY rowid DESC LIMIT 1", (ticket["id"],)).fetchone()
    pending = H._json(marker["new"], {}) if marker else {}
    if (pending.get("check_id"), pending.get("attempt_id"), pending.get("head"), pending.get("verdict")) != \
            (check_id, attempt_id, str(head or "").lower(), str(check["verdict"]).lower()):
        return None
    if step_of(c, ticket) != CHECK_STEP[kind] or not at_head(c, ticket, head):
        return None
    prior = delivery(c, check)
    if prior and prior.get("consumed"):
        return None
    name = CHECK_STEP[kind]
    eligible = assess_independence(c, ticket, H.actor_id(check.get("owner") or ""), head=head,
                                   check_id=check["id"], attempt_id=attempt_id)
    if not eligible["eligible"]:
        target = CHECK_STEP[kind]
        out = {"moved": None, "handoff": None, "quiet": True, "eligibility": eligible,
               "say": (f"{name} recorded {verdict} at head {head}, but Tico is holding movement because "
                       f"{eligible['reason']}. The verdict remains on the check for audit; the check pool must "
                       "reroute it or leave it waiting for an independent checker.")}
        # Part A consumes the returned final eligibility after task status settles and reroutes this same check.
    elif verdict not in GOOD:
        target = FIXES[kind]
        moved = move(c, ticket, target, f"{name} gave {verdict} at {head[:12]}")
        out = {"moved": target if moved else None, "handoff": "verdict", "quiet": False,
               "say": (f"Tico moved this ticket to {target}: {name} gave {verdict} at head {head}." if moved else
                       f"{name} gave {verdict} at head {head}; Tico could not move this ticket to {target}.")}
    else:
        target = _approved(ticket, kind) or (PENDING_RELEASE if is_merged(c, ticket) else None)
        if target:
            moved = move(c, ticket, target, f"{name} gave {verdict} at {head[:12]}")
            out = {"moved": target if moved else None, "handoff": None, "quiet": moved,
                   "say": (f"Tico moved this ticket to {target}: {name} gave {verdict} at head {head}." if moved else
                           f"{name} gave {verdict} at head {head}; Tico could not move this ticket to {target}.")}
        else:
            ready = merge_ready(c, ticket)
            out = {"moved": None, "handoff": "merge" if ready else None, "quiet": not ready,
                   "say": merge_text(c, ticket, *ready) if ready else
                   f"{name} gave {verdict} at head {head}. Tico wakes you to merge once CI is green on it."}
            if ready:
                _merge_woken(c, ticket, ready[1])
    out["consumed"] = True
    out["attempt_id"] = attempt_id
    H._task_event(c, check["id"], H.KEEPER, "verdict_move", None,
                  json.dumps({"verdict": verdict, "head": head, **out}, sort_keys=True), "")
    if out.get("handoff"):
        refs = {"verdict": verdict, "verdict_head": head, HANDOFF: out["handoff"]}
        if out["handoff"] == "merge":
            refs["pr_head"] = head
        H._wake(c, ticket, ticket["owner"], out["say"], refs,
                source=check_id)
    return out


def delivery(c, check):
    """What the verdict's delivery to the builder carries (hubdb._deliver_subtask_result): the outcome
    `after_verdict` kept for the check's current verdict and head, or None."""
    row = c.execute("SELECT new FROM task_events WHERE task_id=? AND field='verdict_move' ORDER BY ts DESC, rowid DESC "
                    "LIMIT 1", (check["id"],)).fetchone()
    out = H._json(row[0], {}) if row else None
    marker = c.execute("SELECT new FROM task_events WHERE task_id=? AND field='verdict_wait' "
                       "ORDER BY rowid DESC LIMIT 1", (TR.parent_of(c, check["id"]) or "",)).fetchone()
    pending = H._json(marker["new"], {}) if marker else {}
    turn = c.execute("SELECT id FROM turns WHERE task_id=? ORDER BY started DESC,id DESC LIMIT 1",
                     (check["id"],)).fetchone()
    waiting = turn and pending.get("check_id") == check["id"] and pending.get("attempt_id") == turn["id"]
    # A rerouted check can get the same verdict at the same head from its next checker: that attempt's own outcome is
    # still to come, so the earlier attempt's is not it.
    if out and (out.get("verdict"), out.get("head")) == (str(check.get("verdict") or "").lower(),
                                                         str(check.get("verdict_head") or "").lower()) \
            and not (waiting and out.get("attempt_id") not in (None, turn["id"])):
        return out
    if waiting:
        return {"verdict": str(check.get("verdict") or "").lower(),
                "head": str(check.get("verdict_head") or "").lower(), "pending": True,
                "quiet": True, "say": "This check verdict is waiting for its executed attempt to be captured."}
    return None


def merge_ready(c, row):
    """(pull request, head) when the ticket's last check approved the head it is at now and CI is green on it: the
    builder merges next. None otherwise."""
    kind = "qa" if "qa" in H.task_labels(row) else last_review(row)
    if step_of(c, row) != CHECK_STEP[kind]:
        return None
    prs = [p for p in _prs(c, row) if p.get("state") in ("open", "draft")]
    if len(prs) != 1 or prs[0].get("state") != "open" or prs[0].get("checks") != "passing" \
            or prs[0].get("mergeable") == "conflict" or not _head(prs[0]):
        return None
    pr, head = prs[0], _head(prs[0])
    last = next((t for t in reversed(_checks(c, row, kind)) if t.get("verdict")), None)
    if not last or str(last["verdict"]).lower() not in GOOD or not head.startswith(str(last.get("verdict_head") or "-").lower()):
        return None
    return pr, head


def merge_text(c, row, pr, head):
    name = CHECK_STEP["qa" if "qa" in H.task_labels(row) else last_review(row)]
    number = pr.get("number") or "?"
    return (f"Ready to merge: {name} approved PR #{number} at head {head} and CI is green. Merge it under the merge "
            f"rules (gh pr merge {number} --squash --match-head-commit {head}) and post the MERGED line; Tico then "
            f"moves this ticket to {PENDING_RELEASE}.")


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
    """Every check on `head` finished (github.pr_signal). Green on a new head at a Fixes step moves the ticket on to
    its next check (AFTER_FIXES); green on the head its last check approved wakes the builder to merge."""
    row = _ticket(c, H.task(c, row["id"]))
    if not row or outcome != "pass" or not at_head(c, row, head):
        return None
    step = step_of(c, row)
    if step in AFTER_FIXES:
        kind = {"Review 1 Fixes": "review 1", "Review 2 Fixes": "review 2", "QA Fixes": "qa"}[step]
        judged = next((t.get("verdict_head") for t in reversed(_checks(c, row, kind)) if t.get("verdict")), None)
        target = AFTER_FIXES[step](row)
        if target and not (judged and str(head).lower().startswith(str(judged).lower())):
            if move(c, row, target, f"checks passed on the new head {str(head)[:12]} at {step}"):
                return target
        return None
    merge_wake(c, row)
    return None


def merged(c, row):
    """A pull request merged: a ticket whose last check passed (or that was at Review 2 Fixes, merged after its
    fixes) goes to Pending Release. Other tickets keep their step for their builder."""
    row = _ticket(c, H.task(c, row["id"]))
    if not row or not is_merged(c, row):
        return False
    step, qa, last = step_of(c, row), "qa" in H.task_labels(row), last_review(row)
    if qa:
        checks = _checks(c, row, "qa")
        ok = step == "In QA" and checks and str(checks[-1].get("verdict") or "").lower() in GOOD
    else:
        ok = step in (CHECK_STEP[last], FIXES[last])
    return bool(ok) and move(c, row, PENDING_RELEASE, "its pull request merged")
