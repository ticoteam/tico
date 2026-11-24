# pyright: strict
"""Whom an @ picker offers (`GET /api/v2/mentions`, `tico_mention_search`, `tico mention`): the teammates the caller
sees on the org chart, best match first, each with the handle to insert and whether an @mention of them would reach
them here.

The ranking and the handle are the web picker's (web/src/conversation/mentions.ts `mentionMatches`, `mentionText`);
backend/tests/fixtures/mention_ranking.json is one case list both must pass, so the two never drift."""

import math
import re
from collections.abc import Iterable
from typing import Any

from . import task_roles as _task_roles, threads as _threads

# The thread and task modules are not strictly typed: they are used through Any here (as call_notes.py does).
threads: Any = _threads
task_roles: Any = _task_roles
Row = dict[str, Any]

LIMIT, MAX_LIMIT = 8, 50
_WORDS = re.compile(r"[\s._-]+")
_ONE_WORD = re.compile(r"[\w.-]+", re.ASCII)


def _text(value: Any) -> str | None:
    """A non-blank string as written, else None (`text` in web/src/data/directory.ts)."""
    return value if isinstance(value, str) and value.strip() else None


def no_write(slug: str) -> str:
    """The words of the 403 a send to an agent without Write on it gets (Auth.require)."""
    return f"You can see {slug} but not send it requests. Ask the person who runs it for Write access."


def _access(bot: Row) -> Row:
    level: Row = bot.get("access") or {}
    return level


def actors(chart: Row) -> list[Row]:
    """The org answer's people and agents as the web directory reads them (web/src/data/directory.ts)."""
    rows: list[Row] = chart.get("people") or []
    bots: list[Row] = chart.get("bots") or []
    people: list[Row] = [
        {
            "id": "human:" + p["id"],
            "kind": "person",
            "name": _text(p.get("name")) or p["id"],
            "email": p.get("email") or "",
        }
        for p in rows
    ]
    agents: list[Row] = [
        {
            "id": "bot:" + b["id"],
            "kind": "agent",
            "name": _text(b.get("display_name")) or _text(b.get("slug")) or b["id"],
            "slug": _text(b.get("slug")) or b["id"],
            "lifecycle": b.get("status") or "",
            "write": bool(_access(b).get("write", True)),
        }
        for b in bots
    ]
    return people + agents


def handle_of(actor: Row) -> str:
    """`mentionText`: an agent's slug, a person's id; an agent with no slug, or a person whose id is not one word, by
    name."""
    if actor["kind"] == "agent":
        return "@" + (actor.get("slug") or actor["name"])
    pid = str(actor["id"]).split(":", 1)[-1]
    return "@" + pid if _ONE_WORD.fullmatch(pid) else "@" + actor["name"]


def _rank(actor: Row, query: str) -> int | None:
    """0 a name or handle starting with `query`, 1 a word in one starting with it, 2 inside one; None not at all."""
    if not query:
        return 0
    names = [str(actor["name"]).lower(), handle_of(actor)[1:].lower()]
    if any(n.startswith(query) for n in names):
        return 0
    if any(w.startswith(query) for n in names for w in _WORDS.split(n) if w):
        return 1
    if any(query in n for n in names):
        return 2
    return None


def ranked(
    everyone: Iterable[Row],
    query: str,
    me: str,
    first: Iterable[str] = (),
    only: Iterable[str] | None = None,
    limit: int = LIMIT,
) -> list[tuple[Row, bool]]:
    """`mentionMatches`: whom "@query" may mean, best first: the `first` ids (a task's people) ahead of everyone else,
    then the closer match, then by name. Never `me`, never an archived agent; only the `only` ids when given. Each
    item is (actor, whether it is one of `first`)."""
    q = str(query or "").lower()
    me = str(me or "")
    self = {me, "human:" + me}
    order = {a: i for i, a in enumerate(first)}
    allowed = set(only) if only is not None else None
    found: list[tuple[float, int, str, str, Row]] = []
    for a in everyone:
        if a["id"] in self or a.get("lifecycle") == "archived" or (allowed is not None and a["id"] not in allowed):
            continue
        r = _rank(a, q)
        if r is None:
            continue
        f = order.get(a["id"], order.get("bot:" + str(a["slug"])) if a.get("slug") is not None else None)
        name = str(a["name"])
        found.append((math.inf if f is None else f, r, name.casefold(), name, a))
    found.sort(key=lambda m: m[:4])
    return [(m[4], m[0] != math.inf) for m in found[:limit]]


def _round_trips(c: Any, actor: Row, handle: str) -> bool:
    return threads.mentions(c, None, handle) == [(actor["id"], "mention")]


def handle(c: Any, actor: Row) -> str:
    """The text to insert: `handle_of` when `threads.mentions` reads it back as this one actor and no one else, else the
    first of their other names that does (a slug that is also a person's id reaches both); `handle_of` when none
    does."""
    first = handle_of(actor)
    if _round_trips(c, actor, first):
        return first
    if actor["kind"] == "agent":
        others = [actor.get("slug"), actor["name"]]
    else:
        others = [str(actor["id"]).split(":", 1)[-1], str(actor.get("email") or "").split("@")[0], actor["name"]]
    for name in dict.fromkeys(str(n) for n in others if n):
        if "@" + name != first and _round_trips(c, actor, "@" + name):
            return "@" + name
    return first


def _task_people(row: Row, roles: dict[str, list[str]]) -> list[str]:
    """`onTask` in web/src/features/task/TaskPage.tsx: who asked, who it is for, who has it, then its roles' people."""
    ids: list[Any] = [row.get("requester_id"), row.get("assignee_id"), row.get("owner_id")]
    ids += [a for people in roles.values() for a in people]
    return list(dict.fromkeys(str(a) for a in ids if a))


def _reach(c: Any, thread: Row, author: str, actor: str) -> str | None:
    """Why an @mention of `actor` by `author` in `thread` would reach nobody, or None when it reaches them: someone
    outside it, unless the mention would add them to a private task (threads.reaches)."""
    if threads.audience_has(c, thread, actor) or threads._widens(c, threads.root_of(c, thread), author, actor):
        return None
    if threads.audience_of(threads.root_of(c, thread)) == "task":
        return "Cannot read this task, and an @mention would not add them."
    return "Not in this chat: an @mention reaches only its members."


def search(
    c: Any,
    auth: Any,
    who: Any,
    chart: Row,
    *,
    q: str = "",
    task: str | None = None,
    thread: str | None = None,
    limit: int = LIMIT,
) -> Row:
    """The picker's answer for this caller (`chart`: their `GET /api/v2/org`)."""
    limit = max(1, min(limit, MAX_LIMIT))
    me = str(getattr(who, "actor", "") or "")
    first: list[str] = []
    only: list[str] | None = None
    here: Row | None = None
    if task:
        row: Row = auth.task(c, who, auth.resolve_task(c, who, task))
        first = _task_people(row, task_roles.roles_of(c, row["id"]))
        here = threads.thread_row(c, row.get("conversation_id")) if row.get("conversation_id") else None
    if thread:
        chat: Row = threads.require(c, threads.viewer(auth, who), thread)
        here, root = chat, threads.root_of(c, chat)
        if threads.audience_of(root) in ("personal", "participants") and not chat.get("parent_id"):
            only = threads._dm_members(c, root)
    out: list[Row] = []
    for a, on_task in ranked(actors(chart), q, me, first, only, limit):
        # A person's @mention wakes an agent only with Write on it (threads.mentions); an agent's has its own rules.
        reason = no_write(str(a["slug"])) if a["kind"] == "agent" and not a["write"] and who.role != "bot" else None
        if reason is None and here is not None:
            reason = _reach(c, here, me, a["id"])
        out.append(
            {
                "id": a["id"],
                "kind": a["kind"],
                "name": a["name"],
                "handle": handle(c, a),
                "on_task": on_task,
                "wakes": reason is None,
                **({"reason": reason} if reason else {}),
            }
        )
    return {"candidates": out}
