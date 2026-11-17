# pyright: strict
"""Something Tico or a bot needs a person to do, filed as a question to them (docs/tasks.md, "Needs you").

    ask(conn, by, person, title, body, question, why, bot=..., asker=..., **create) -> the task

Needs you holds only what is asked of a person (backend/waiting_on.py `needs`): a plain task given to them lands in
their Tasks and nowhere they are told. So a feature that needs a person to act files a task owned by the bot that
owns the feature (`bot`), and that bot asks the person `question` on it, with `why` it matters: its message on the
task's chat, marked ❓ (backend/question_marks.py). The person finds it in Needs you, and their reply clears it and
reaches the bot. With no such bot here (none named, removed or archived) the task is the person's own. Only bots write
questions, never Tico (`author`): the bot that holds the task or filed it asks when it can run, else BotOps, the
built-in bot that owns Tico's own notices. The ❓ clears once the task ends, so nothing a person is meant to act on is
left out of Needs you, and nothing holds them once it is done.

The question is the message's first line, short and plain, the way the ❓ checks want it (backend/ask_shape.py): it
says what is asked, with no paths or hashes; the why follows it, and the task's body keeps the detail. It is worded so
that whichever bot asks can say it. A line or why that would fail those checks (a name with a path in it) is replaced
by a plain one (`shaped`).
"""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from . import ask_shape, hubdb_core

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

_core: Any = hubdb_core
is_bot: Callable[[object], bool] = _core.is_bot
is_human: Callable[[object], bool] = _core.is_human

WHY_MAX = 200  # the why line under the question
# What a feature's question says when its own words would fail the ask checks (`shaped`).
PLAIN_ASK = "Can you take a look at this?"
PLAIN_WHY = "Only a person can act on it."
# Why a bot's task for a person asks them, in the words of the bot that asks (app.py, POST /api/v2/tasks).
HANDED_WHY = "I need a person to do this before I can carry on."

log = logging.getLogger(__name__)


def _h() -> Any:
    from . import hubdb

    return hubdb


def owner_bot(conn: "sqlite3.Connection", bot: str | None) -> str | None:
    """`bot` as an actor when it is a bot this company has, set up and not archived, else None."""
    if not bot or not is_bot(bot):
        return None
    H = _h()
    found: Any = H.bot(conn, H.actor_id(bot))
    if not found or found.get("state") in (None, "planned", "archived"):
        return None
    return str(H.bot_actor(H.actor_id(bot)))


def botops() -> str:
    """BotOps, the built-in bot that owns Tico's own notices to a person, as an actor."""
    return "bot:" + str(_h().FLEET_MAINTAINER)


def author(conn: "sqlite3.Connection", *bots: str | None) -> str | None:
    """Who writes a feature's question to a person: only bots write questions, never Tico. The first of `bots` that can
    run now, else BotOps when it can; else the first of them, BotOps last, that is here at all (paused, or not set up
    yet: its question waits like any other). None when the company has no such bot."""
    from . import task_wakes

    named = [str(b) for b in bots if b and is_bot(b)]
    here = [b for b in (*named, botops()) if _here(conn, b)]
    ready = next((b for b in here if task_wakes.cannot_run(conn, b) is None), None)
    return ready or next(iter(here), None)


def _here(conn: "sqlite3.Connection", bot: str | None) -> bool:
    """Whether `bot` is a bot of this company that is not archived."""
    H = _h()
    found: Any = H.bot(conn, H.actor_id(bot)) if bot and is_bot(bot) else None
    return bool(found) and found.get("state") != "archived"


def ask(
    conn: "sqlite3.Connection",
    by: str,
    person: str,
    title: str,
    body: str,
    question: str,
    why: str,
    *,
    bot: str | None = None,
    asker: str | None = None,
    **create: Any,
) -> Row:
    """File `title` as a task owned by `bot` (when it is here, else by `person`) and ask `person` `question` on it, with
    `why` it matters. The bot that asks: `asker` when it is here (the bot acting now, whose words the question is, or
    BotOps for a notice no other bot should write), else `author` of the task's bot and `by`; with no bot here at all,
    the task is left without a question, in the person's Tasks. `create` goes to hubdb.task_create as it is (due, private, labels, ...). Returns
    the task as hubdb.task_create gives it, read again after the ask."""
    H = _h()
    target = str(H.resolve_actor(conn, person) or person)
    owner = owner_bot(conn, bot) or target
    create.setdefault("lint", False)
    made: Row = H.task_create(conn, by, title, body, owner, **create)
    writer = asker if _here(conn, asker) else author(conn, owner if is_bot(owner) else None, by)
    if not writer:
        log.warning("No bot here to ask %s about task %s; it stays in their Tasks", target, made["id"])
        return made
    ask_on(conn, writer, made, target, question, why)
    return H.task(conn, made["id"]) or made


def ask_on(
    conn: "sqlite3.Connection",
    asker: str,
    task: Row,
    person: str,
    question: str,
    why: str,
) -> str:
    """`asker`, a bot, asks `person` `question` on `task`, with `why` it matters: its message on the task's chat, marked
    ❓ (backend/question_marks.py). A person who may not read a private task is let in first, as an OK filed for a
    person is (task_asks.ask_ok). Returns the mark's id."""
    from . import question_marks

    if not is_bot(asker):
        raise ValueError("only bots write questions")  # Tico never does (`author`)
    line, said = shaped(question, why)
    asked: Row = question_marks.ask(conn, asker, str(task["id"]), person, f"{line}\n\n{said}", checked=False)
    return str(asked["id"])


def shaped(question: str, why: str) -> tuple[str, str]:
    """`question` and `why` as one line each that the ask checks pass (ask_shape.line_refusal): a line past the limit is
    cut at a word; one that still fails (a path or an id in a name) is PLAIN_ASK, and a failing why is PLAIN_WHY."""
    line = " ".join(str(question or "").split())
    if len(line) > ask_shape.LINE_MAX:
        line = line[: ask_shape.LINE_MAX - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "\u2026"
    said = " ".join(str(why or "").split())
    if len(said) > WHY_MAX:
        said = said[: WHY_MAX - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "\u2026"
    if ask_shape.line_refusal(line):
        log.warning("A feature's question failed the ask checks; asked in plain words instead")
        line = PLAIN_ASK
    if not said or ask_shape.line_refusal(f"{PLAIN_ASK}\n{said}"):
        said = PLAIN_WHY
    return line, said


def task_for(
    conn: "sqlite3.Connection",
    by: str,
    title: str,
    body: str,
    owner: str,
    *,
    question: str,
    why: str,
    bot: str | None = None,
    asker: str | None = None,
    **create: Any,
) -> Row:
    """hubdb.task_create for a feature that files work for `owner`: for a person, `ask` (the task, with its question to
    them, in their Needs you); for a bot, the plain task, as before."""
    if is_human(owner):
        return ask(conn, by, owner, title, body, question, why, bot=bot, asker=asker, **create)
    return _h().task_create(conn, by, title, body, owner, **create)


def first_sentence(text: str) -> str:
    """The first sentence of `text`, for a why (WHY_MAX)."""
    said = " ".join(str(text or "").split())
    end = said.find(". ")
    return (said[: end + 1] if end > 0 else said)[:WHY_MAX]
