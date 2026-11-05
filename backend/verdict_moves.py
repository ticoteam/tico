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
import re

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
HANDOFF_FIRST_SQL = (f"CASE WHEN m.sender_id='{H.KEEPER}' AND json_extract(m.refs,'$.{HANDOFF}') "
                     "IS NOT NULL THEN 0 ELSE 1 END")


def at_step(c, task_id, refs):
    """`refs` with the step the ticket is at now: the news is about that step, and a claim drops it once the ticket
    has left it (execution.obsolete_notices_sql)."""
    row = H.task(c, task_id)
    return {**refs, "at_step": row.get("step_id")} if row and row.get("step_id") else refs


def forward(c, ticket, target, text, refs=None, *, sources, source=None, **wake):
    """Copy task text with every source's audience, in addition to the destination's audience.

    A later check can deliver an earlier check's findings. The trigger alone cannot describe those readers;
    source task refs also survive saved outcomes and later result delivery through another child's thread.
    """
    refs = {**(refs or {})}
    sources = list(dict.fromkeys([*sources, *refs.get("source_tasks", [])]))
    refs["source_tasks"] = sources
    return H._wake(c, ticket, target, text, refs, source=source or sources[0], **wake)


def _ticket(c, row, *, evidence=None):
    """The ticket a bot builds, or None: an open ticket owned by a bot, on a type whose steps have a flow, and not
    held: a hold keeps the ticket at its check step, where a verdict must wait for it to clear (`resumed`).
    Health passes the same task fields with holds and its set-read board as evidence."""
    from . import task_holds
    row = task_holds.with_holds(c, row)     # its holds, when the row came from plain `tasks`
    return row if (row and (H.is_bot(row.get("owner_id")) or evidence is None and F.pr_checks(c, row))
                   and row.get("status") in H.ACTIVE_STATUSES
                   and not row.get("blocked_since") and (F.has_flow(c, row.get("type_id"), evidence=evidence)
                                                         or evidence is None and F.pr_checks(c, row))) else None


def step_of(c, row):
    found = c.execute("SELECT name FROM task_steps WHERE id=?", (row.get("step_id") or "",)).fetchone()
    return found[0] if found else None


def _prs(c, row, *, evidence=None):
    return [p for p in (H.task_links(c, row["id"]) if evidence is None else evidence["prs"])
            if p["kind"] == "pr" and p.get("state") != "closed"]


def _head(pr):
    return str((H._json(pr.get("detail"), {}) or {}).get("head_sha") or "").lower()


def at_head(c, row, head, check=None):
    """Whether `head` (7 to 40 characters) is the head a pull request on this ticket is at now."""
    head = str(head or "").lower()
    return bool(head) and any(_head(pr).startswith(head) for pr in _prs(c, row)
                              if _head(pr) and (check is None or for_pr(check, pr)))


def _full_head(c, row, head, *, evidence=None):
    """The full head of the pull request on the ticket that `head` (7 to 40 characters, as a verdict may write it)
    starts, lowered; `head` itself when no pull request is at it. Comparisons start from that full SHA, so a verdict's
    short head and full head of one commit match whichever form the checker wrote."""
    head = str(head or "").lower()
    return next((_head(pr) for pr in _prs(c, row, evidence=evidence) if head and _head(pr).startswith(head)), head)


def is_merged(c, row):
    prs = _prs(c, row)
    return bool(prs) and all(p.get("state") in ("merged", "shipped") for p in prs)


def review_checks(c, row, *, evidence=None):
    """Bot check candidates with their filed step and judged head, from rows or Health's raw set reads."""
    from .check_pools import is_security
    children = evidence["children"] if evidence is not None else [H.task(c, cid) for cid in TR.child_ids(c, row["id"])]
    out = []
    for child in children:
        if not child or not H.is_bot(child.get("owner_id")):
            continue
        step = F.check_step(c, child, row, evidence=evidence)
        board = evidence["boards"].get(row["type_id"], []) if evidence is not None else None
        filed = (child["id"] in evidence["filed"] if evidence is not None else
                 c.execute("SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? "
                           "AND kind='check_filed' AND new=? LIMIT 1", (row["id"], child["id"])).fetchone())
        if step or filed or child.get("verdict") or is_security(child.get("title")) or F.titled(c, row["type_id"], child.get("title"), board=board):
            out.append({**child, "check_step": step, "judged_head": _judged_head(c, child, evidence=evidence)})
    return sorted(out, key=lambda t: (t.get("created_at") or "", t["id"]))


def for_pr(check, pr):
    """Keep separate PRs separate even when they point at the same commit."""
    return pr["url"] in (check.get("body") or "") or bool(re.search(r"#" + str(pr.get("number")) + r"\b", check.get("title") or ""))


def _checks(c, row, step, *, evidence=None, pr=None):
    """The ticket's checks filed at check step `step`, newest last. A person's own subtask filed there is ordinary
    work, never a check, whatever its title: finished with no verdict, it is not a check's comments."""
    return [t for t in review_checks(c, row, evidence=evidence) if (t["check_step"] or {}).get("id") == step["id"]
            and (pr is None or for_pr(t, pr))]


def _verdict(check):
    """A finished check's verdict, lowered; a check done with none counts as comments."""
    value = str(check.get("verdict") or "").lower()
    return value or (NO_VERDICT if check.get("status") == "done" else "")


def move(c, row, target, why, *, sources=()):
    """Move the ticket to step `target` (a step row or id) as Tico. True when it moved. A refusal (the review gate)
    undoes only this move, tells the builder what clears it, and is remembered so Tico retries it then
    (backend/move_retry.py), as the notice promises."""
    target = target if isinstance(target, dict) else F.step(c, target)
    if not target or target["type_id"] != row.get("type_id") or row.get("step_id") == target["id"]:
        return False
    c.execute("SAVEPOINT verdict_move")
    try:
        # The owner hears of the move on the hand-off that caused it, not again from a step rule.
        H.task_update(c, H.KEEPER, row["id"], step=target["id"], skip_step_wake=(row["owner_id"],), strict=False)
        H._task_event(c, row["id"], H.KEEPER, "verdict_move",
                      json.dumps({"source_tasks": list(sources)}) if sources else None, target["name"], why)
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
    if (not verdict or not step or (not step.get("detached") and ticket.get("step_id") != step["id"])
            or not at_head(c, ticket, head, check if step.get("detached") else None)):
        return None
    name = step["name"]
    # Verdicts are commonly written inside the checker turn's final tool call. The executed
    # model segments are committed by turn_finish afterwards, so this callback records an
    # immutable pending marker and leaves routing to the post-capture callback.
    attempt = c.execute("SELECT id FROM attempts WHERE task_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                        (check["id"],)).fetchone()
    attempt_id = attempt["id"] if attempt else None
    pending = {"check_id": check["id"], "attempt_id": attempt_id, "step": name, "head": head,
               "verdict": verdict}
    existing = c.execute("SELECT new FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='verdict_wait' "
                         "ORDER BY rowid DESC LIMIT 1", (ticket["id"],)).fetchone()
    if not existing or H._json(existing["new"], {}).get("check_id") != check["id"] or \
            H._json(existing["new"], {}).get("attempt_id") != attempt_id or \
            H._json(existing["new"], {}).get("head") != head:
        H._task_event(c, ticket["id"], H.KEEPER, "verdict_wait", None,
                      json.dumps(pending, sort_keys=True), "")
    return {"moved": None, "handoff": None, "quiet": True, "pending": True,
            "say": f"{name} recorded {verdict} at head {head}; waiting for this check attempt's execution record."}


def _judged_head(c, check, *, evidence=None):
    """The head a check was filed for (its review_head), for a check finished with no verdict line."""
    if evidence is not None:
        found = next((e for e in reversed(evidence["child_events"].get(check["id"], [])) if e["kind"] == "review_head"), None)
    else:
        found = c.execute("SELECT new FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='review_head' "
                          "ORDER BY created_at DESC,rowid DESC LIMIT 1", (check["id"],)).fetchone()
    return str(found["new"] if found else "").split(",")[0]


def after_capture(c, check_id, head, attempt_id):
    """Consume a verdict only after its exact judging attempt has captured usage and settled."""
    check = H.task(c, check_id)
    verdict = _verdict(check or {})
    if not verdict or str(check.get("verdict_head") or _judged_head(c, check) or "").lower() != str(head or "").lower():
        return None
    attempt = c.execute("SELECT task_id,bot_id,settled_at FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    latest = c.execute("SELECT id FROM attempts WHERE task_id=? ORDER BY created_at DESC,id DESC LIMIT 1", (check_id,)).fetchone()
    if (not attempt or attempt["task_id"] != check_id or not attempt["settled_at"] or
            not latest or latest["id"] != attempt_id or check.get("status") != "done" or
            H.actor_id(attempt["bot_id"]) != H.actor_id(check.get("owner_id"))):
        return None
    ticket = _ticket(c, H.task(c, TR.parent_of(c, check_id) or ""))
    step = ticket and F.check_step(c, check, ticket)
    if not step:
        return None
    marker = c.execute("SELECT rowid,new FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='verdict_wait' "
                       "AND json_extract(new,'$.check_id')=? ORDER BY rowid DESC LIMIT 1", (ticket["id"], check_id)).fetchone()
    pending = H._json(marker["new"], {}) if marker else {}
    if (pending.get("check_id"), pending.get("attempt_id"), pending.get("head"), pending.get("verdict")) != \
            (check_id, attempt_id, str(head or "").lower(), verdict):
        return None
    if ((not step.get("detached") and ticket.get("step_id") != step["id"])
            or not at_head(c, ticket, head, check if step.get("detached") else None)):
        return None
    prior = delivery(c, check)
    if prior and prior.get("consumed"):
        return None
    name = step["name"]
    eligible = assess_independence(c, ticket, H.actor_id(check.get("owner_id") or ""), head=head,
                                   check_id=check["id"], attempt_id=attempt_id)
    # Model company is a routing preference, never a verdict gate. Keep actual builder/checker families
    # alongside the verdict for audit, including fallback runs and unknown provenance.
    pr = next((p for p in _prs(c, ticket) if for_pr(check, p) and _head(p).startswith(head)), None) if step.get("detached") else None
    route = verdict_route(c, ticket, head, step=step, pr=pr)
    standing = verdict if verdict not in GOOD else route["verdict"]
    if standing not in GOOD:
        out = _failed(c, ticket, step, standing, head, passed=verdict if verdict in GOOD else None, pr=pr)
    else:
        out = _passed(c, ticket, step, f"{name} gave {verdict} at head {head}", sources=[check_id])
    out["eligibility"] = eligible  # Historical field name; this is provenance, not a gate.
    out["consumed"] = True
    out["attempt_id"] = attempt_id
    out["source_tasks"] = list(dict.fromkeys([*out.get("source_tasks", []), check_id]))
    H._task_event(c, check["id"], H.KEEPER, "verdict_move", None,
                  json.dumps({"verdict": verdict, "head": head, **out}, sort_keys=True), "")
    if step.get("detached"):
        from . import check_pools
        check_pools.entered(c, H.KEEPER, ticket)
    if out.get("handoff"):
        refs = {"verdict": verdict, "verdict_head": head, HANDOFF: out["handoff"]}
        if out["handoff"] == "merge":
            refs["pr_head"] = head
        forward(c, ticket, ticket["owner_id"], out["say"], at_step(c, ticket["id"], refs),
                sources=out["source_tasks"])
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
    attempt = c.execute("SELECT id FROM attempts WHERE task_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                        (last["id"],)).fetchone()
    head = str(last.get("verdict_head") or _judged_head(c, last) or "").lower()
    return after_capture(c, last["id"], head, attempt["id"]) if attempt else None


def _failed(c, ticket, step, verdict, head, passed=None, *, pr=None):
    """A check at `step` gave `verdict`, not good, at `head`: the ticket moves to the step's `on_fail`, and the builder
    hears of it on that hand-off. `passed` is a pass at the same head that the failure still stands against."""
    name = step["name"]
    gave = "no verdict (its comments are the feedback)" if verdict == NO_VERDICT else verdict
    target = F.step(c, F.of(step).get("on_fail"))
    said = (f"{name} gave {passed} at head {head}, but {name} already gave {gave} at that head, and only a new head "
            "clears it" if passed else f"{name} gave {gave} at head {head}")
    head = _full_head(c, ticket, head)
    failed = [t for t in _checks(c, ticket, step, pr=pr) if verdict_at([t], head) not in (None, *GOOD)]
    sources = [t["id"] for t in failed if _verdict(t) == verdict][:1]
    if step.get("detached"):
        findings = next((t for t in reversed(failed) if t.get("note")), None)
        if findings:
            said += f". {findings['note'][:4000]}"
            sources = list(dict.fromkeys([findings["id"], *sources]))
    moved = bool(target) and move(c, ticket, target, f"{name} gave {verdict} at {head[:12]}", sources=sources)
    return {"moved": target["name"] if moved else None, "handoff": "verdict", "quiet": False,
            "source_tasks": sources,
            "say": (f"Tico moved this ticket to {target['name']}: {said}." if moved else
                    said + (f"; Tico could not move this ticket to {target['name']}." if target else "."))}


def _passed(c, ticket, step, why, *, sources=()):
    """The ticket's check at `step` passed (or its round was skipped): on to the step's `on_pass`, or, when the merge
    is next, the merged step if it already merged, else the builder is woken to merge once CI is green."""
    if step.get("detached"):
        return {"moved": None, "handoff": None, "quiet": True, "say": f"{why}."}
    target = F.of(step).get("on_pass")
    if target == F.MERGE:
        target = F.merge_step(c, ticket["type_id"]) if is_merged(c, ticket) else None
        if not target:
            ready = merge_ready(c, ticket)
            if ready:
                _merge_woken(c, ticket, ready[1])
            return {"moved": None, "handoff": "merge" if ready else None, "quiet": not ready,
                    "source_tasks": merge_sources(c, ticket, ready[1]) if ready else list(sources),
                    "say": merge_text(c, ticket, *ready) if ready else
                    f"{why}. Tico wakes you to merge once CI is green on it."}
    target = F.step(c, target) if target and not isinstance(target, dict) else target
    if not target:
        return {"moved": None, "handoff": None, "quiet": True, "say": f"{why}."}
    moved = move(c, ticket, target, why, sources=sources)
    # A move to the next check needs nothing from the builder; a move back to the builder's own step is its cue.
    cue = moved and target["status"] in ("open", "doing")
    return {"moved": target["name"] if moved else None, "handoff": "verdict" if cue else None, "quiet": moved and not cue,
            "say": (f"Tico moved this ticket to {target['name']}: {why}." if moved else
                    f"{why}; Tico could not move this ticket to {target['name']}.")}


def delivery(c, check):
    """What the verdict's delivery to the builder carries (hubdb._deliver_subtask_result): the outcome
    `after_verdict` kept for the check's current verdict and head, or None."""
    row = c.execute("SELECT new FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='verdict_move' "
                    "ORDER BY created_at DESC, rowid DESC "
                    "LIMIT 1", (check["id"],)).fetchone()
    out = H._json(row[0], {}) if row else None
    marker = c.execute("SELECT new FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='verdict_wait' "
                       "AND json_extract(new,'$.check_id')=? ORDER BY rowid DESC LIMIT 1",
                       (TR.parent_of(c, check["id"]) or "", check["id"])).fetchone()
    pending = H._json(marker["new"], {}) if marker else {}
    turn = c.execute("SELECT id FROM attempts WHERE task_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
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


def _verdict_at(c, row, step, head, *, evidence=None, pr=None):
    """The verdict the checks at `step` hold against `head` (lowered), or None when none judged that head. A fail,
    changes or comments wins over any pass on the same head, newer or not: a failed head is cleared by a fix at a new
    head, not by a later round at the same one, as a failed CI attempt or test receipt is (docs/tasks.md)."""
    head = _full_head(c, row, head, evidence=evidence)
    return verdict_at(_checks(c, row, step, evidence=evidence, pr=pr), head)


def verdict_route(c, row, head, *, step=None, evidence=None, pr=None):
    """The standing verdict and its flow target, at the check or the work step it sent the ticket to."""
    if step is None:
        step = evidence["step"] if evidence is not None else F.step(c, row.get("step_id"))
        if step and not F.is_check(step):
            step = F.fixes_for(c, step, row, evidence=evidence)
    verdict = _verdict_at(c, row, step, head, evidence=evidence, pr=pr) if step else None
    if not verdict:
        return None
    return {"verdict": verdict, "target": F.of(step).get("on_pass" if verdict in GOOD else "on_fail")}


def verdict_at(checks, head):
    """The same-head verdict from already read checks; a failed head needs a new commit."""
    given = [_verdict(check) for check in checks if _verdict(check)
             and head.startswith(str(check.get("verdict_head") or check.get("judged_head") or "-").split(",")[0].lower())]
    return next((v for v in given if v not in GOOD), given[-1] if given else None)


def _skipped(c, row, step, head, *, evidence=None):
    """Whether the ticket passes `step` without a check at `head`: it carries the step's skip tag, or the step's round
    was skipped (timed out) at that head and no check there failed the head."""
    if F.skip_target(c, {**row, "tags": evidence["tags"]} if evidence is not None else row, step):
        return True
    skipped = (any(e["kind"] == "round_skipped" and e["new"] == f"{step['id']}:{head}" for e in evidence["events"])
               if evidence is not None else c.execute(
                   "SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='round_skipped' "
                   "AND new=? LIMIT 1", (row["id"], f"{step['id']}:{head}")).fetchone())
    if not skipped:
        return False
    verdict = _verdict_at(c, row, step, head, evidence=evidence)
    return not verdict or verdict in GOOD


def merge_ready(c, row, *, evidence=None, pr=None):
    """(pull request, head) when the merge is next from the ticket's step (its `on_pass`, or `after_green` on a new
    head) and CI is green on the head it is at now. On `on_pass`, the step's checks must have approved that head or its
    round was skipped there, and none may have failed it. None otherwise. Health supplies raw set-read `evidence`;
    check selection, standing failures, skipped rounds and fixes heads all use the ticket flow's functions."""
    step = evidence["step"] if evidence is not None else F.step(c, row.get("step_id"))
    detached = F.pr_checks(c, row) if evidence is None else []
    if detached:
        step = detached[-1]
    flow = F.of(step)
    if not step or F.MERGE not in (flow.get("on_pass"), flow.get("after_green")):
        return None
    prs = [p for p in ([pr] if pr else _prs(c, row, evidence=evidence)) if p.get("state") in ("open", "draft")]
    if len(prs) != 1 or prs[0].get("state") != "open" or prs[0].get("checks") != "passing" \
            or prs[0].get("mergeable") == "conflict" or not _head(prs[0]):
        return None
    pr, head = prs[0], _head(prs[0])
    if detached:
        from .check_pools import bot_pr
        detail = H._json(pr.get("detail"), {}) or {}
        if (not bot_pr(c, row, pr) or detail.get("error") or detail.get("mergeable_state") == "behind"
                or any(v not in ("passing", "skipped") for v in (detail.get("checks") or {}).values())
                or ("qa" in H.task_labels(row) and not any(F.check_kind(s) == "qa" for s in detached))):
            return None
        for required in detached:
            checks = _checks(c, row, required, pr=pr)
            consumed = [t for t in checks if (delivery(c, t) or {}).get("consumed")]
            if verdict_at(checks, head) not in GOOD or verdict_at(consumed, head) not in GOOD:
                return None
        return pr, head
    if flow.get("on_pass") == F.MERGE:
        # The same verdict the timeout and the skipped round read: a pass after a fail at this head clears nothing.
        verdict = _verdict_at(c, row, step, head, evidence=evidence)
        skipped = _skipped(c, row, step, head, evidence=evidence)
        return (pr, head) if skipped or verdict in GOOD else None
    judged = _judged_by(c, row, step, evidence=evidence)
    return (pr, head) if judged and not head.startswith(judged) else None


def _judged_by(c, row, step, *, evidence=None):
    """The head the check that sent the ticket to work step `step` judged, lowered, or None."""
    check = F.fixes_for(c, step, row, evidence=evidence)
    if not check:
        return None
    checks = _checks(c, row, check, evidence=evidence)
    heads = [_head(pr) for pr in _prs(c, row, evidence=evidence) if _head(pr)]
    failed = [t for t in checks if _verdict(t) and _verdict(t) not in GOOD
              and any(verdict_at([t], head) for head in heads)]
    found = next((t for t in reversed(failed or checks) if _verdict(t)), None)
    return str(found.get("verdict_head") or found["judged_head"] or "").lower() if found else None


def merge_sources(c, row, head, pr=None):
    """The checks whose approvals a merge notice summarizes, including when CI turned green later."""
    return [t["id"] for t in review_checks(c, row) if (pr is None or for_pr(t, pr))
            and verdict_at([t], head) in GOOD]


def merge_text(c, row, pr, head, *, announce=True):
    if F.pr_checks(c, row):
        prefix = f"Approved at {head}, checks green: " if announce else f"At {head}: "
        return (prefix + f"merge PR #{pr['number']} under the repo's merge rules. "
                f"Use --match-head-commit {head}. {pr['url']}")
    name = step_of(c, row) or "The last check"
    number = pr.get("number") or "?"
    landing = F.merge_step(c, row["type_id"])
    return (f"Ready to merge: {name} cleared PR #{number} at head {head} and CI is green. Merge it under the merge "
            f"rules (gh pr merge {number} --squash --match-head-commit {head}) and post the MERGED line"
            + (f"; Tico then moves this ticket to {landing['name']}." if landing else "."))


def _merge_woken(c, row, head, pr=None):
    """Record the merge wake for this head; False when one was already sent for it."""
    key = f"{pr['url']}@{head}" if pr else head
    if c.execute("SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='merge_wake' "
                 "AND new=? LIMIT 1",
                 (row["id"], key)).fetchone():
        return False
    H._task_event(c, row["id"], H.KEEPER, "merge_wake", None, key, "")
    return True


def merge_wake(c, row, snapshots=None):
    """Wake the builder to merge when the ticket is ready (merge_ready), once per head."""
    row = _ticket(c, row)
    if row and F.pr_checks(c, row):
        prs = _prs(c, row)
        if snapshots is not None:
            verified = []
            for pr in prs:
                fresh = snapshots.get(pr["id"]) or {}
                checks = fresh.get("checks") or {}
                if (fresh.get("error") or fresh.get("head_sha") != _head(pr) or fresh.get("state") == "closed"
                        or fresh.get("draft") or fresh.get("mergeable") is False):
                    continue
                green = bool(checks) and "passing" in checks.values() and all(v in ("passing", "skipped") for v in checks.values())
                detail = {**(H._json(pr.get("detail"), {}) or {}), **fresh}
                verified.append({**pr, "checks": "passing" if green else "pending", "detail": json.dumps(detail)})
            prs = verified
        ready = next((ready for pr in prs if (ready := merge_ready(c, row, pr=pr))
                      and not c.execute("SELECT 1 FROM history_events WHERE subject_id=? AND kind='merge_wake' AND new=?",
                                        (row["id"], f"{pr['url']}@{ready[1]}")).fetchone()), None)
    else:
        ready = row and merge_ready(c, row)
    sources = (merge_sources(c, row, ready[1], pr=ready[0]) or [row["id"]]) if ready else []
    if ready and F.pr_checks(c, row):
        recipient = merge_recipient(c, row, ready[0])
        if not recipient:
            return False
        if recipient != row["owner_id"]:
            actor = row["owner_id"] if H.task_private(c, row) else H.KEEPER
            # The task's body is work to do; the review summary keeps its sources on a separate notice.
            merge_task = H.task_create(c, actor, f"Merge PR #{ready[0]['number']} at {ready[1][:12]}",
                                       merge_text(c, row, *ready, announce=False), recipient, parent_id=row["id"],
                                       quiet_notice=True)
            forward(c, merge_task, recipient, merge_text(c, row, *ready),
                    {HANDOFF: "merge", "pr_head": ready[1]}, sources=sources, trigger="pr_checks")
            _merge_woken(c, row, ready[1], ready[0])
            return True
    if ready and _merge_woken(c, row, ready[1], ready[0] if F.pr_checks(c, row) else None):
        forward(c, row, row["owner_id"], merge_text(c, row, *ready), {HANDOFF: "merge", "pr_head": ready[1]},
                sources=sources, trigger="pr_checks")
        return True
    return False


def merge_recipient(c, row, pr):
    """Keep the owner's merge authority; otherwise use a repo writer already responsible for that repo."""
    if H.is_human(row["owner_id"]):
        return row["owner_id"]
    from . import repositories, release_flow
    repo = str(pr.get("repo") or "")
    if "/" not in repo:
        return None
    org = repo.split("/")[0]

    def writable(actor):
        if not actor or not H.task_private_readable(c, actor, row):
            return False
        if actor != row["owner_id"] and not H.task_private(c, row) and H.private_tasks_default(c, actor):
            return False
        bot = H.bot(c, H.actor_id(actor))
        if not bot or bot.get("state") != "active":
            return False
        return any(r["full_name"].lower() == repo.lower() and r["access"] == "write"
                   for r in repositories.access(c, H.actor_id(actor), org)["effective"])

    if writable(row["owner_id"]):
        return row["owner_id"]
    board = F.pr_checks(c, row)
    manager = H.type_manager(c, {"type_id": board[0]["type_id"]}) if board else None
    candidates = [manager, release_flow.release_manager(c)]
    candidates += [r["bot"] for r in c.execute("SELECT bot FROM bot_config WHERE repo=? COLLATE NOCASE ORDER BY bot", (repo,))]
    return next(("bot:" + slug for slug in dict.fromkeys(candidates) if slug and writable("bot:" + slug)), None)


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
    if row and F.pr_checks(c, row):
        from . import check_pools
        return check_pools.entered(c, H.KEEPER, row)
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
        forward(c, row, row["owner_id"], out["say"], at_step(c, row["id"], {HANDOFF: out["handoff"]}),
                sources=out.get("source_tasks") or [row["id"]])
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
            entered = c.execute("SELECT created_at FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='step' "
                                "AND new=? "
                                "ORDER BY created_at DESC,rowid DESC LIMIT 1", (ticket_id, step["id"])).fetchone()
            if not ticket or not entered or (at - H.parse_ts(entered[0])).total_seconds() < hours * 3600:
                continue
            if c.execute("SELECT 1 FROM history_events WHERE subject_type='task' AND subject_id=? "
                         "AND kind IN ('round_skipped','step_skipped') "
                         "AND created_at>=? LIMIT 1", (ticket_id, entered[0])).fetchone():
                continue        # skipped already: the merge, its builder's to do, is what it waits for
            head = next((_head(p) for p in _prs(c, ticket) if _head(p)), "")
            # A verdict at the head is usable whichever way it went. A pass ends the round on it, waiting only for what
            # comes after the check (green CI and the merge), and says so rather than that no verdict came (Dev 646).
            # A fail is not skipped over: the ticket goes to the step's on_fail, as when the check gave it.
            verdict = _verdict_at(c, ticket, step, head)
            sources = [t["id"] for t in _checks(c, ticket, step) if verdict_at([t], head) == verdict] if verdict else []
            for check in _checks(c, ticket, step):
                if check["status"] in H.ACTIVE_STATUSES:
                    H.task_close(c, H.KEEPER, check["id"],
                                 note=f"Unanswered for {hours:g} h; Tico closed it and skipped this round.", strict=False)
            if verdict:
                note = f"{step['name']}: {verdict} at head {head[:12]}; round closed after {hours:g} h"
            else:
                note = f"{step['name']}: no usable verdict in {hours:g} h"
            H._task_event(c, ticket_id, H.KEEPER, "round_skipped",
                          json.dumps({"source_tasks": sources}) if sources else None, f"{step['id']}:{head}", note)
            if verdict and verdict not in GOOD:
                out = _failed(c, H.task(c, ticket_id), step, verdict, head)
                refs = {"verdict": verdict, "verdict_head": head, HANDOFF: out["handoff"]}
            else:
                why = (f"{step['name']} gave {verdict} at head {head}" if verdict else
                       f"{step['name']} had no usable verdict for {hours:g} h; Tico skipped the round")
                out = _passed(c, H.task(c, ticket_id), step, why, sources=sources)
                refs = {HANDOFF: out["handoff"]}
            if out.get("handoff"):
                forward(c, ticket, ticket["owner_id"], out["say"], at_step(c, ticket_id, refs),
                        sources=out.get("source_tasks") or sources or [ticket_id])
            skipped.append(ticket_id)
    return skipped
