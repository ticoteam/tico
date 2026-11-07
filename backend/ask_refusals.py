# pyright: strict
"""Two plain refusals at the moment a bot puts a question to a person, before anything is written (Nov 6: Chris's
plate had 60 bot questions, 33 with a safe default and many asked two to six times).

1. `ask_default_go_ahead`: the question names a reversible, internal default (`if_no_answer.kind` is `reversible`).
   That is not a question: the bot does the default now and says what it did in one line on its task (the person can
   undo it). A send, a spend or a delete default, and a question with no default, are still asked. When the owner
   turned off `bots_go_ahead` (backend/team_rules.py) the question is asked as before.
2. `ask_duplicate`: an open question to the same person already covers the same subject, from any bot: the same
   task, the same question text, or a title or text that is almost the same words. The bot waits on that one.

Deterministic, no model. A person's own asks never come here (only a bot's question does). Nothing is recorded for a
refusal: it is a `Problem`, so it counts toward no quarantine or org-rule hold, like ask_ingest's refusals.
"""

import re
import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from . import ask_deadlines, ask_ingest, hubdb as H, question_tasks as QT, task_relations as TR, team_rules
from .store import Problem

if TYPE_CHECKING:
    from .auth import Identity

SIMILAR = 0.8  # Jaccard share of the same words that makes two titles or questions the same subject
MIN_WORDS = 3  # fewer words than this match only when identical


def words(text: str | None) -> frozenset[str]:
    """The text's words, lowercase, without punctuation."""
    return frozenset(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def same_words(a: str | None, b: str | None) -> bool:
    """Whether two texts say the same thing in (nearly) the same words."""
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return False
    if wa == wb:
        return True
    if min(len(wa), len(wb)) < MIN_WORDS:
        return False
    return len(wa & wb) / len(wa | wb) >= SIMILAR


def _question_text(card: dict[str, Any]) -> str:
    questions: list[dict[str, Any]] = QT.fields(card)["questions"] or []
    return " ".join(str(q.get("question") or "") for q in questions)


def go_ahead(c: sqlite3.Connection, default: dict[str, Any] | None) -> str | None:
    """The refusal text when `default` is a reversible, internal step the bot should just take, else None."""
    if not default or default.get("kind") != ask_deadlines.GOES_AHEAD or not str(default.get("do") or "").strip():
        return None
    if not cast(Callable[[sqlite3.Connection], dict[str, Any]], team_rules.load)(c)["bots_go_ahead"]:
        return None
    do = str(default["do"]).strip()
    return (
        f"This is a reversible, internal choice, so do not ask: do your default now (\"{do}\") and record what you "
        "did in one line on the task (a comment: it shows in Updates). The person can undo it. Ask only when the step "
        "sends something out, spends money or deletes."
    )


def duplicate(
    c: sqlite3.Connection,
    person: str,
    title: str | None,
    question: str,
    parent_id: str | None,
    topic: str = "",
    private: bool = False,
) -> dict[str, Any] | None:
    """The open question task to `person` that already covers this one, or None. Checked over every bot's open
    questions to the person: the same parent task, the same question text, or nearly the same title or text. Two
    questions that name different topics on one task are two decisions, not one, and so are a keyed and an unkeyed one
    (backend/question_tasks.py topics). A private question is never matched with a public one, nor a public with a
    private one: either way the private words would reach the other card."""
    for row in QT.open_rows(c):
        if row["owner_id"] != person:
            continue
        card = cast(Callable[[sqlite3.Connection, str], dict[str, Any] | None], H.task)(c, row["id"])
        if card is None or bool(card.get("private")) != private:
            continue
        theirs = cast(Callable[[object], str | None], H.question_topic)(card.get("question_topic"))
        if topic and theirs and topic != theirs:
            continue  # two named topics are two decisions, whatever their words
        distinct = (topic or None) != (theirs or None)
        if (
            parent_id
            and not distinct
            and cast(Callable[[sqlite3.Connection, str], str | None], TR.parent_of)(c, card["id"]) == parent_id
            or same_words(_question_text(card), question)
            or same_words(card.get("title"), title)
            or same_words(card.get("title"), question)
        ):
            return card
    return None


def check(
    c: sqlite3.Connection,
    who: "Identity",
    person: str,
    *,
    title: str,
    question: str,
    parent: dict[str, Any] | None = None,
    default: dict[str, Any] | None = None,
    folds: dict[str, Any] | None = None,
    topic: str | None = "",
    private: bool = False,
) -> None:
    """Raise the refusal for a bot's question to `person`, or return. `parent` is the task it is filed under, `default`
    its `if_no_answer` ({do, kind, by}), `folds` the open question it joins by topic (never a duplicate of itself)."""
    why = go_ahead(c, default)
    if why:
        raise Problem("ask_default_go_ahead", why, 422)
    if folds:
        return
    found = duplicate(
        c,
        person,
        title,
        question,
        parent["id"] if parent else None,
        cast(Callable[[object], str | None], H.question_topic)(topic) or "",
        private,
    )
    if not found:
        return
    actor_id = cast(Callable[[str], str], H.actor_id)
    name = cast(Callable[[sqlite3.Connection, str], str], H.person_name)(c, actor_id(person)) or actor_id(person)
    if cast(Callable[[sqlite3.Connection, str, dict[str, Any]], bool], ask_ingest.source_readable)(c, who.actor, found):
        named = f"“{found['title']}” ({found['id']})"
        fix = f"Wait on that one instead: hub task relate <your task> blocked_by {found['id']}. Their answer comes back to you."
    else:
        named = "one you cannot read"
        fix = "Wait for their answer there instead of asking again."
    raise Problem(
        "ask_duplicate",
        f"{name} already has an open question that covers this: {named}. Do not ask again. {fix}",
        422,
    )
