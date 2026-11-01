"""A person's recorded waiver of a Dev ticket's QA step for one exact pull request head (Dev 563, task 558).

A waiver is not a QA verdict. It never sets a verdict on the QA subtask or anywhere else; it is a `qa_waiver` task
event on the Dev ticket, so task history shows who waived QA, why, at which head and step, and when. Only a QA step
(a step whose flow's `check` is `qa`: In QA on the Dev board) can be waived. CI, the reviews and release approval
stay mandatory: a waiver needs every hosted check green on that head, read from GitHub at the time, and the
ticket's reviews finished, with each of its board's review steps approving that exact head. A waiver does not override QA: a QA `fail` at that head refuses it. A push gives a
new head, and a waiver for the old head does not cover it (`current`).
"""
import json
import re

FIELD = "qa_waiver"
SHA = re.compile(r"^[0-9a-f]{40}$")
# Steps that look waivable but never are in this scope (Chris's open question on reviews, task 558).
MANDATORY = re.compile(r"\b(review|ci|checks?|approval)\b", re.I)


def _refuse(c, actor, row, text, rule="qa_waiver"):
    from . import hubdb as H
    H.refuse(c, actor, rule, text, task=row["id"] if row else None)


def _reviews(c, row, head):
    """What stops the ticket's reviews from covering `head`, or None. Every review step of the ticket's board (Review 1
    and Review 2 on the Dev board, docs/software-factory.md) has a slot, and each slot's latest review must approve
    this exact head: a `changes` verdict is cleared only by that reviewer's approve at the fixed head."""
    from . import hubdb as H
    from . import step_flow
    from . import task_relations as TR
    slots = step_flow.checks(c, row.get("type_id"), "review")
    latest = {}
    for child_id in TR.child_ids(c, row["id"]):
        child = H.task(c, child_id)
        found = step_flow.child_check(c, child, row) if child else None
        if not found or step_flow.check_kind(found) != "review":
            continue
        name = found["name"]
        if child["status"] in H.ACTIVE_STATUSES:
            return f"{name} is still open (task {child['id']}); a waiver comes after the reviews"
        if name not in latest or (child.get("created_at") or "") >= (latest[name].get("created_at") or ""):
            latest[name] = child
    if not slots:
        return "the ticket's board has no review step, so no review can cover this head"
    for name in [s["name"] for s in slots]:
        child = latest.get(name)
        if not child or not child.get("verdict"):
            return f"{name} has no recorded verdict on this ticket"
        judged = str(child.get("verdict_head") or "").lower()
        if child["verdict"] != "approve":
            return f"{name} asked for changes at {judged[:12] or 'an unknown head'}"
        if not judged or not head.startswith(judged):
            return (f"{name} approved {judged[:12] or 'no named head'}, not {head[:12]}; ask {name} for a verdict "
                    "on this head")
    return None


def _qa_failed(c, row, head):
    """The QA subtask that failed this exact head, or one with no named head, or None: a waiver stands in for a
    missing QA result, never for a failed one, and a fail that names no head may be this one's."""
    from . import hubdb as H
    from . import step_flow
    from . import task_relations as TR
    for child_id in TR.child_ids(c, row["id"]):
        child = H.task(c, child_id) or {}
        judged = str(child.get("verdict_head") or "").lower()
        if (step_flow.is_kind(c, row, str(child.get("title") or "").strip(), "qa")
                and child.get("verdict") in ("fail", "changes")
                and (not judged or head.startswith(judged))):
            return child
    return None


def _checks(pr, detail):
    """What stops the hosted checks on this pull request from counting as green, or None. `detail` is a fresh
    GitHub read (review_gate.snapshot), which keeps a failed attempt after a passing re-run and marks skips."""
    checks = (detail or {}).get("checks") or {}
    def names(state):
        return sorted(k.removeprefix("check:").removeprefix("status:") for k, v in checks.items() if v == state)
    if names("failing"):
        return f"CI failing on {pr['url']}: {', '.join(names('failing'))}"
    if names("pending"):
        return f"CI still running on {pr['url']}: {', '.join(names('pending'))}"
    if "passing" not in checks.values():
        return f"no passing hosted check is recorded on {pr['url']}"
    return None


def waive(c, actor, task_id, head, reason, step=None, snapshots=None, manager=False):
    """Record a QA waiver by `actor`, a person who owns the ticket or manages it (`manager`, decided by the route
    from the caller's rights). The caller must already have refused a bot acting for that person (the route's
    `person_only`): `actor` alone cannot tell the two apart. `snapshots` is a fresh GitHub read of the ticket's open pull requests
    (review_gate.snapshot); None means the GitHub App is not connected, and then no waiver can be recorded: the
    webhook-built link counts a skipped check as passing and a re-run's pass replaces an earlier failure."""
    from . import hubdb as H
    from . import review_gate
    row = H.task(c, task_id)
    if not row:
        _refuse(c, actor, None, f"no task {task_id}", "not-found")
    if not H.is_human(actor):
        _refuse(c, actor, row, "A QA waiver is a person's decision: the ticket's owner or a manager records it. "
                               "A bot asks QA for a verdict instead.", "identity")
    if actor != row["owner"] and not manager:
        _refuse(c, actor, row, "Only the ticket's owner or a manager may waive its QA step.", "identity")
    if not review_gate.engineering(c, row):
        _refuse(c, actor, row, "Only a Dev ticket's QA step can be waived.")
    from . import step_flow
    here = step_flow.step(c, row.get("step_id"))
    current = (here or {}).get("name")
    qa_steps = step_flow.checks(c, row.get("type_id"), "qa")
    qa_name = qa_steps[0]["name"] if qa_steps else "QA"
    asked = str(step or "").strip()
    if asked and not any(asked.lower() in (s["name"].lower(), step_flow.label(s).lower()) for s in qa_steps):
        reviews = ", ".join(s["name"] for s in step_flow.checks(c, row.get("type_id"), "review")) or "the reviews"
        why = (f"{asked} is mandatory: CI, {reviews} and release approval cannot be waived"
               if MANDATORY.search(asked)
               else f"Only the {qa_name} step can be waived, not {asked}")
        _refuse(c, actor, row, why + ".")
    if step_flow.check_kind(here) != "qa":
        _refuse(c, actor, row, f"The ticket is at {current or 'no step'}, not {qa_name}: only the current QA step "
                               "can be waived.")
    reason = " ".join(str(reason or "").split())
    if not reason:
        _refuse(c, actor, row, "A QA waiver needs a reason.")
    head = str(head or "").strip().lower()
    if not SHA.match(head):
        _refuse(c, actor, row, "A QA waiver names the pull request's exact head: its full 40-character commit sha.")
    prs = review_gate.links(c, row)
    if not prs:
        _refuse(c, actor, row, "The ticket has no open pull request linked, so there is no head to waive QA for.")
    if snapshots is None:
        _refuse(c, actor, row, "QA waiver refused: hosted checks cannot be verified because the company's GitHub App "
                               "is not connected, and check webhooks alone cannot tell a pass from a skipped check or "
                               "a failure hidden by a re-run. Hosted checks stay mandatory.")
    matched, reads = None, []
    for pr in prs:
        detail = snapshots.get(pr["id"]) or {"error": True, "why": "not read"}
        if detail.get("error"):
            _refuse(c, actor, row, f"Cannot read the current head and CI of {pr['url']} "
                                   f"({detail.get('why') or 'GitHub read failed'}); retry.")
        # The read ran outside the write lock: a push recorded since then makes it stale, as for a review move
        # (review_gate.check). An older cache never overrides a newer read.
        cached = str((H._json(pr.get("detail"), {}) or {}).get("head_sha") or "").lower()
        if (pr.get("updated_at") or "") > (detail.get("at") or "") and cached != str(detail.get("head_sha") or "").lower():
            _refuse(c, actor, row, f"{pr['url']} changed while GitHub was read (now {cached[:12] or 'unknown'}); "
                                   "retry the waiver.")
        reads.append((pr, detail))
        if str(detail.get("head_sha") or "").lower() == head:
            matched = pr
    if not matched:
        known = ", ".join(sorted(review_gate.heads(c, row, snapshots))) or "unknown"
        _refuse(c, actor, row, f"{head[:12]} is not the current head of the ticket's pull request (now {known}). "
                               "A waiver covers one exact head; a new push needs a new QA result or a new waiver.")
    # Every open pull request on the ticket ships with it, so each one's checks must be green, not only the waived one.
    for pr, detail in reads:
        if (blocked := _checks(pr, detail)):
            _refuse(c, actor, row, f"QA waiver refused: {blocked}. Hosted checks stay mandatory.")
    if (blocked := _reviews(c, row, head)):
        _refuse(c, actor, row, f"QA waiver refused: {blocked}. Code reviews stay mandatory.")
    if (failed := _qa_failed(c, row, head)):
        at = str(failed.get("verdict_head") or "")[:12] and head[:12] or "no named head"
        _refuse(c, actor, row, f"QA waiver refused: QA recorded {failed['verdict']} at {at} (task "
                               f"{failed['id']}). A waiver stands in for a missing QA result, not a failed one: fix "
                               "it and push, then ask QA again.")
    pr = matched
    if any(w["head"] == head and w["step"] in (current, *(s["name"] for s in qa_steps)) for w in waivers(c, row)):
        _refuse(c, actor, row, f"QA is already waived for {head[:12]} on this ticket.", "duplicate")
    record = {"head": head, "step": current, "pr": pr["url"]}
    H._task_event(c, row["id"], actor, FIELD, None, json.dumps(record, sort_keys=True), reason)
    if row["owner"] != actor:
        H._wake(c, row, row["owner"], f"{H.actor_id(actor)} waived QA for {pr['url']} at head {head}: {reason}\n\n"
                "This is a waiver, not a QA pass: no QA verdict was recorded. CI and both reviews stay required, "
                "and the waiver covers this exact head only.", refs={"qa_waiver": head})
    return next(w for w in waivers(c, row) if w["head"] == head)


def waivers(c, row, links=None):
    """The ticket's recorded QA waivers, oldest first. `current` is whether the pull request it named is still at
    the waived head (a merge at that head keeps it current; a push voids it). `links` saves a reread when the
    caller has the ticket's links already."""
    from . import hubdb as H
    heads = {}
    for link in H.task_links(c, row["id"]) if links is None else links:
        if link["kind"] == "pr":
            heads[link["url"]] = str((H._json(link.get("detail"), {}) or {}).get("head_sha") or "").lower()
    out = []
    for e in c.execute("SELECT * FROM task_events WHERE task_id=? AND field=? ORDER BY ts, rowid", (row["id"], FIELD)):
        record = H._json(e["new"], {}) or {}
        out.append({"head": record.get("head"), "step": record.get("step"), "pr": record.get("pr"),
                    "actor": e["actor"], "reason": e["note"], "at": e["ts"],
                    "current": bool(record.get("head")) and heads.get(record.get("pr")) == record.get("head")})
    return out
