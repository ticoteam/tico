# pyright: strict
"""A later review judges a change without the earlier reviews' findings.

A bot that holds an open check on a ticket, where the check comes after a review step (Review 2, or QA once the
reviews are done), reads that ticket blind to those earlier reviews: their subtasks (body, comments, verdict) and every
message that carries their findings. Those messages are marked with `review_step` where they are written (`marks`), and
every read path asks the one predicate here, `hidden_from`: a task read (auth.task_row), a task list (auth.task_sql),
a message read (task_privacy.message_readable and readable) and the deferred deliveries a turn carries
(delivery.carried). Only an open check held by a bot blinds anyone, and only to the earlier reviews of its own ticket:
the ticket's builder, people and every other bot read everything, and the blindness ends when the check closes.
"""

import json
import sqlite3
from collections.abc import Callable, Iterable
from typing import Any, cast

from . import read_cache
from . import step_flow as F
from . import task_relations as TR
from .store import H

REVIEW_STEP = "review_step"  # the ref a finding's message carries: the review step (by id) it is the findings of
INDEPENDENT = "Review independently: judge the change against its spec, diff and evidence, not any earlier review, which you cannot see."

Row = dict[str, Any]

# The helpers below come from modules that are not strict yet. Each is named here at the type this module relies on.
_cache = cast(Callable[[sqlite3.Connection], dict[Any, Any] | None], read_cache.cache)
_task = cast(Callable[[sqlite3.Connection, str], Row | None], H.task)
_is_bot = cast(Callable[[str], bool], H.is_bot)
_conversation = cast(Callable[[sqlite3.Connection, str], Row | None], H.conversation)
_message_task_id = cast(Callable[[Row, Row | None], str | None], H.message_task_id)
_child_check = cast(Callable[[sqlite3.Connection, Row, Row | None], Row | None], F.child_check)
_check_kind = cast(Callable[[Row], str | None], F.check_kind)
_reviews_before = cast(Callable[[sqlite3.Connection, Row], list[Row]], F.reviews_before)
_parent_of = cast(Callable[[sqlite3.Connection, str], str | None], TR.parent_of)
_child_ids = cast(Callable[[sqlite3.Connection, str], list[str]], TR.child_ids)


def _refs_of(raw: object) -> Row:
    """A message's refs as a dict: stored JSON text decoded, a dict as it is, and anything else (or text that does not
    decode to an object) as none."""
    if isinstance(raw, str):
        try:
            value: object = json.loads(raw)
        except ValueError:
            return {}
        return cast(Row, value) if isinstance(value, dict) else {}
    return cast(Row, raw) if isinstance(raw, dict) else {}


def _principals(who: str | tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """The actor ids `who` acts as: a bot's own identity, and the person a bot's turn acts for when there is one."""
    if isinstance(who, str):
        return (who,)
    return tuple(who)


def _open_checks(c: sqlite3.Connection, actor: str) -> list[tuple[str, str]]:
    """(check id, ticket id) for each open subtask `actor` owns under a ticket. Read once per snapshot."""
    values = _cache(c)
    key = ("review_blind_checks", actor)
    if values is not None and key in values:
        return values[key]
    marks = ",".join("?" * len(H.ACTIVE_STATUSES))
    rows: list[tuple[str, str]] = [
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


def _blind_steps(c: sqlite3.Connection, actor: str, ticket_id: str) -> frozenset[str]:
    """The review steps of the ticket's board that `actor`'s open checks on it leave blind: those before the check's
    own step, when that step is a review. Only a review check is blind to earlier reviews: a QA check after them reads
    their findings as it always did. The builder of a ticket is never blinded by it."""
    values = _cache(c)
    key = ("review_blind_steps", actor, ticket_id)
    if values is not None and key in values:
        return values[key]
    ticket = _task(c, ticket_id)
    out: set[str] = set()
    if ticket and ticket.get("owner_id") != actor:
        for check_id, tid in _open_checks(c, actor):
            if tid != ticket_id:
                continue
            check = _task(c, check_id)
            step = _child_check(c, check, ticket) if check else None
            if step and _check_kind(step) == "review":
                out.update(s["id"] for s in _reviews_before(c, step))
    frozen = frozenset(out)
    if values is not None:
        values[key] = frozen
    return frozen


def hidden_from(
    c: sqlite3.Connection, who: str | tuple[str, ...] | list[str], task_id: str | None, refs: Row | None = None
) -> bool:
    """True when a bot in `who` may not read the task `task_id`, or with `refs` (the refs of a message on that task)
    the message: the task is an earlier review subtask that a bot's open later check blinds, or the message is marked
    with such a review step. False for everyone else, and for every read while no bot holds an open check."""
    if not task_id:
        return False
    for actor in _principals(who):
        if not _is_bot(actor):
            continue
        checks = _open_checks(c, actor)
        if not checks:
            continue
        if refs is not None:
            mark = refs.get(REVIEW_STEP)
            if mark and mark in _blind_steps(c, actor, task_id):
                return True
            continue
        ticket_id = _parent_of(c, task_id)
        if ticket_id and ticket_id in {tid for _, tid in checks}:
            row, ticket = _task(c, task_id), _task(c, ticket_id)
            step = _child_check(c, row, ticket) if row and ticket else None
            if step and step["id"] in _blind_steps(c, actor, ticket_id):
                return True
    return False


def hides_message(c: sqlite3.Connection, who: str | tuple[str, ...] | list[str], message: Row) -> bool:
    """Whether a bot in `who` may not read this message: it is marked with a review step its task blinds, or it refers
    to a task `hidden_from` hides from it (an earlier review's subtask)."""
    principals = _principals(who)
    if not any(_is_bot(a) and _open_checks(c, a) for a in principals):
        return False
    refs = _refs_of(message.get("refs"))
    conversation = _conversation(c, message["conversation_id"]) if message.get("conversation_id") else None
    task_id = _message_task_id({"refs": refs}, conversation)
    if task_id and hidden_from(c, principals, task_id, refs=refs):
        return True
    from . import task_privacy

    # task_privacy is not strict yet: its `references` is named here at the type this module relies on.
    referenced = cast(Callable[[sqlite3.Connection, Row], Iterable[str]], cast(Any, task_privacy).references)
    return any(hidden_from(c, principals, t) for t in referenced(c, refs))


def hidden_task_ids(c: sqlite3.Connection, who: str | tuple[str, ...] | list[str]) -> set[str]:
    """The task ids a bot in `who` may not read because of its open checks, for a task list's SQL (auth.task_sql)."""
    out: set[str] = set()
    for actor in _principals(who):
        if not _is_bot(actor):
            continue
        for ticket_id in {tid for _, tid in _open_checks(c, actor)}:
            steps = _blind_steps(c, actor, ticket_id)
            ticket = _task(c, ticket_id) if steps else None
            if not ticket:
                continue
            for child in _child_ids(c, ticket_id):
                row = _task(c, child)
                step = _child_check(c, row, ticket) if row else None
                if step and step["id"] in steps:
                    out.add(child)
    return out


def has_open_checks(c: sqlite3.Connection, who: str | tuple[str, ...] | list[str]) -> bool:
    """Whether a bot in `who` holds an open check at all: the cheap gate a read asks before the per-task checks. A person
    never holds one, so a person's read runs no query here."""
    return any(_is_bot(a) and bool(_open_checks(c, a)) for a in _principals(who))


def step_marks(step: Row | None) -> dict[str, Any]:
    """The ref a message or history event about a review step carries: the step's id when it is a review, else none."""
    return {REVIEW_STEP: step["id"]} if step and _check_kind(step) == "review" else {}


def marks(c: sqlite3.Connection, check: Row | None, ticket: Row | None, step: Row | None = None) -> dict[str, Any]:
    """The refs a message carrying `check`'s findings on `ticket` is written with: its review step when the check is a
    review, so a later check on the ticket is blind to it and the message is filterable without reading its text."""
    if step is None and check and ticket:
        step = _child_check(c, check, ticket)
    return step_marks(step)


def hides_event(c: sqlite3.Connection, who: str | tuple[str, ...] | list[str], event: Row) -> bool:
    """Whether a bot in `who` may not read this history row of a task: its provenance is marked with a review step the
    task blinds (a verdict's move, wait or skipped round, which name the verdict in their note)."""
    if not has_open_checks(c, who):
        return False
    task_id = event.get("task_id")
    return bool(task_id) and hidden_from(c, who, task_id, refs=_refs_of(event.get("data")))


def blinds(c: sqlite3.Connection, step: Row) -> bool:
    """Whether a check at `step` is one earlier review findings are hidden from: a review check after a review step.
    A QA check reads the findings of the reviews before it."""
    return _check_kind(step) == "review" and bool(_reviews_before(c, step))
