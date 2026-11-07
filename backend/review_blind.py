"""A later review judges a change without the earlier reviews' findings.

A bot that holds an open check on a ticket, where the check comes after a review step (Review 2, or QA once the
reviews are done), reads that ticket blind to those earlier reviews: their subtasks (body, comments, verdict) and every
message that carries their findings. Those messages are marked with `review_step` where they are written (`marks`), and
every read path asks the one predicate here, `hidden_from`: a task read (auth.task_row), a task list (auth.task_sql),
a message read (task_privacy.message_readable and readable) and the deferred deliveries a turn carries
(delivery.carried). Only an open check held by a bot blinds anyone, and only to the earlier reviews of its own ticket:
the ticket's builder, people and every other bot read everything, and the blindness ends when the check closes.
"""

from . import read_cache
from . import step_flow as F
from . import task_relations as TR
from .store import H

REVIEW_STEP = "review_step"  # the ref a finding's message carries: the review step (by id) it is the findings of
INDEPENDENT = "Review independently: judge the change against its spec, diff and evidence, not any earlier review, which you cannot see."


def _principals(who):
    """The actor ids `who` acts as: a bot's own identity, and the person a bot's turn acts for when there is one."""
    return tuple(who) if isinstance(who, (tuple, list)) else (who,)


def _open_checks(c, actor):
    """(check id, ticket id) for each open subtask `actor` owns under a ticket. Read once per snapshot."""
    values = read_cache.cache(c)
    key = ("review_blind_checks", actor)
    if values is not None and key in values:
        return values[key]
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    rows = [
        (r[0], r[1])
        for r in c.execute(
            "SELECT t.id, r.to_task FROM tasks t JOIN task_relations r ON r.from_task=t.id AND r.kind='parent' "
            f"WHERE t.owner_id=? AND t.status IN ({marks})",
            (actor, *H.ACTIVE_STATUSES),
        )
    ]
    if values is not None:
        values[key] = rows
    return rows


def _blind_steps(c, actor, ticket_id):
    """The review steps of the ticket's board that `actor`'s open checks on it leave blind: those before the check's
    own step, when that step comes after a review. The builder of a ticket is never blinded by it."""
    values = read_cache.cache(c)
    key = ("review_blind_steps", actor, ticket_id)
    if values is not None and key in values:
        return values[key]
    ticket = H.task(c, ticket_id)
    out = set()
    if ticket and ticket.get("owner_id") != actor:
        for check_id, tid in _open_checks(c, actor):
            if tid != ticket_id:
                continue
            check = H.task(c, check_id)
            step = F.child_check(c, check, ticket) if check else None
            if step:
                out.update(s["id"] for s in F.reviews_before(c, step))
    out = frozenset(out)
    if values is not None:
        values[key] = out
    return out


def hidden_from(c, who, task_id, refs=None):
    """True when a bot in `who` may not read the task `task_id`, or with `refs` (the refs of a message on that task)
    the message: the task is an earlier review subtask that a bot's open later check blinds, or the message is marked
    with such a review step. False for everyone else, and for every read while no bot holds an open check."""
    if not task_id:
        return False
    for actor in _principals(who):
        if not H.is_bot(actor):
            continue
        checks = _open_checks(c, actor)
        if not checks:
            continue
        if refs is not None:
            mark = refs.get(REVIEW_STEP) if isinstance(refs, dict) else None
            if mark and mark in _blind_steps(c, actor, task_id):
                return True
            continue
        ticket_id = TR.parent_of(c, task_id)
        if ticket_id and ticket_id in {tid for _, tid in checks}:
            row, ticket = H.task(c, task_id), H.task(c, ticket_id)
            step = F.child_check(c, row, ticket) if row and ticket else None
            if step and step["id"] in _blind_steps(c, actor, ticket_id):
                return True
    return False


def hides_message(c, who, message):
    """Whether a bot in `who` may not read this message: it is marked with a review step its task blinds, or it refers
    to a task `hidden_from` hides from it (an earlier review's subtask)."""
    principals = _principals(who)
    if not any(H.is_bot(a) and _open_checks(c, a) for a in principals):
        return False
    refs = message.get("refs")
    refs = H._json(refs, {}) if isinstance(refs, str) else (refs or {})
    if not isinstance(refs, dict):
        refs = {}
    conversation = H.conversation(c, message["conversation_id"]) if message.get("conversation_id") else None
    task_id = H.message_task_id({"refs": refs}, conversation)
    if task_id and hidden_from(c, principals, task_id, refs=refs):
        return True
    from .task_privacy import references

    return any(hidden_from(c, principals, t) for t in references(c, refs))


def hidden_task_ids(c, who):
    """The task ids a bot in `who` may not read because of its open checks, for a task list's SQL (auth.task_sql)."""
    out = set()
    for actor in _principals(who):
        if not H.is_bot(actor):
            continue
        for ticket_id in {tid for _, tid in _open_checks(c, actor)}:
            steps = _blind_steps(c, actor, ticket_id)
            ticket = H.task(c, ticket_id) if steps else None
            if not ticket:
                continue
            for child in TR.child_ids(c, ticket_id):
                row = H.task(c, child)
                step = F.child_check(c, row, ticket) if row else None
                if step and step["id"] in steps:
                    out.add(child)
    return out


def marks(c, check, ticket, step=None):
    """The refs a message carrying `check`'s findings on `ticket` is written with: its review step when the check is a
    review, so a later check on the ticket is blind to it and the message is filterable without reading its text."""
    step = F.child_check(c, check, ticket) if step is None else step
    return {REVIEW_STEP: step["id"]} if step and F.check_kind(step) == "review" else {}


def blinds(c, step):
    """Whether a check at `step` is one earlier review findings are hidden from: a check after a review step."""
    return bool(F.check_kind(step)) and bool(F.reviews_before(c, step))
