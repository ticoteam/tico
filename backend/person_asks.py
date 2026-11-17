# pyright: strict
"""Something Tico or a bot needs a person to do, filed as a question to them (docs/tasks.md, "Needs you").

    ask(conn, by, person, title, body, question, why, bot=..., **create) -> the task

Needs you holds only what is asked of a person (backend/waiting_on.py `needs`): a plain task given to them lands in
their Tasks and nowhere they are told. So a feature that needs a person to act files a task owned by the bot that
owns the feature (`bot`), and that bot asks the person `question` on it, with `why` it matters: the person finds it in
Needs you, and their answer wakes the bot. With no such bot here (none named, removed or archived) the task is the
person's own, and the bot that filed it asks, else BotOps, the built-in bot that owns Tico's own notices: only bots
write questions. Such a question is taken back once the person finishes the task (task_engine.advance), so nothing a
person is meant to act on is left out of Needs you, and nothing holds them once it is done.

The question is the ask's first line, short and plain, the way the ask checks want it (backend/ask_shape.py): it says
what is asked, with no paths or hashes; the task's body keeps the detail.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from . import hubdb_core

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

_core: Any = hubdb_core
is_bot: Callable[[object], bool] = _core.is_bot
is_human: Callable[[object], bool] = _core.is_human

WHY_MAX = 200  # ask_shape.WHY_MAX


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


def botops(conn: "sqlite3.Connection") -> str | None:
    """BotOps, the built-in bot that owns Tico's own notices to a person, when the company has it."""
    return owner_bot(conn, "bot:" + str(_h().FLEET_MAINTAINER))


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
    **create: Any,
) -> Row:
    """File `title` as a task owned by `bot` (when it is here, else by `person`) and ask `person` `question` on it, with
    `why` it matters. `create` goes to hubdb.task_create as it is (due, private, labels, ...). Returns the task as
    hubdb.task_create gives it, read again after the ask."""
    H = _h()
    target = str(H.resolve_actor(conn, person) or person)
    owner = owner_bot(conn, bot) or target
    create.setdefault("lint", False)
    made: Row = H.task_create(conn, by, title, body, owner, **create)
    asker = owner if is_bot(owner) else by if is_bot(by) or is_human(by) else botops(conn) or H.KEEPER
    ask_on(conn, asker, made, target, question, why, ends_with_task=not is_bot(owner))
    return H.task(conn, made["id"]) or made


def ask_on(
    conn: "sqlite3.Connection",
    asker: str,
    task: Row,
    person: str,
    question: str,
    why: str,
    *,
    ends_with_task: bool = False,
) -> str:
    """`asker` asks `person` `question` on `task`, with `why` it matters: a person who may not read a private task is
    let in first, as an OK filed for a person is (task_asks.ask_ok). Returns the requirement's id."""
    from . import task_engine as E

    H = _h()
    row: Row = H.task(conn, task["id"]) or task
    if not H.task_private_readable(conn, person, row):
        H.task_reach(conn, asker, row, person)
        row = H.task(conn, task["id"]) or row
    line = " ".join(question.split())
    brief: Row = {"why": " ".join(why.split())[:WHY_MAX], "details": None, "recommend": None}
    asked: Row = E.add_requirement(
        conn,
        row["id"],
        asker,
        "answer",
        person,
        line,
        seen_version=row.get("version"),
        brief=brief,
        ends_with_task=ends_with_task,
    )
    return str(asked["requirement_id"])


def handed_why(conn: "sqlite3.Connection", bot: str) -> str:
    """Why a bot's task asks a person: it handed them work only a person can do."""
    name = str(_h().actor_name(conn, bot))
    return f"{name} needs a person to do this before it can carry on."


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
    **create: Any,
) -> Row:
    """hubdb.task_create for a feature that files work for `owner`: for a person, `ask` (the task, with its question to
    them, in their Needs you); for a bot, the plain task, as before."""
    if is_human(owner):
        return ask(conn, by, owner, title, body, question, why, bot=bot, **create)
    return _h().task_create(conn, by, title, body, owner, **create)


def first_sentence(text: str) -> str:
    """The first sentence of `text`, for a why (WHY_MAX)."""
    said = " ".join(str(text or "").split())
    end = said.find(". ")
    return (said[: end + 1] if end > 0 else said)[:WHY_MAX]
