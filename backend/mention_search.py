# pyright: strict
"""Whom an @ picker offers (`GET /api/v2/mentions`, `tico_mention_search`, `tico mention`): the people and agents the
caller sees on the org chart, best match first, each with the handle to insert.

The ranking is the web picker's (web/src/conversation/mentions.ts `mentionMatches`, `mentionText`):
backend/tests/fixtures/mention_ranking.json is one case list both must pass, so the two never drift. The handle starts
from the web's (`handle_of`) and is checked with the matcher a post uses (`threads.mention_names`, `threads._spot`). Whether a post
reached them is the post's own answer (`woke`, `delivered`, `warnings`), not this one's."""

import math
import re
from collections.abc import Iterable
from typing import Any

from . import (
    people as _people,
    strategy_bot as _strategy_bot,
    task_roles as _task_roles,
    teammate_access as _teammate_access,
    threads as _threads,
    views as _views,
)
from .store import H as _H

# The store, roster and thread modules are not strictly typed: they are used through Any here (as call_notes.py does).
H: Any = _H
P: Any = _people
strategy_bot: Any = _strategy_bot
task_roles: Any = _task_roles
teammate_access: Any = _teammate_access
threads: Any = _threads
views: Any = _views
Row = dict[str, Any]

LIMIT, MAX_LIMIT = 8, 50
_WORDS = re.compile(r"[\s._-]+")
_ONE_WORD = re.compile(r"[\w.-]+", re.ASCII)


def _text(value: Any) -> str | None:
    """A non-blank string as written, else None (`text` in web/src/data/directory.ts)."""
    return value if isinstance(value, str) and value.strip() else None


def roster(c: Any, auth: Any, who: Any, bots: list[Row]) -> list[Row]:
    """The people and agents `GET /api/v2/org` shows this caller, as the web directory reads them
    (web/src/data/directory.ts): the people on the roster they may see, and the configured agents they may see that are
    not archived, less the Strategy Owner when they hid it from their chart."""
    hidden: set[str] = teammate_access.hidden(c, who, "see", ("human",), auth)
    people: list[Row] = [
        {
            "id": "human:" + p["id"],
            "kind": "person",
            "name": _text(p.get("name")) or p["id"],
            "email": str(p.get("email") or ""),
        }
        for p in P.visible_people(views.roster(c, shared=True))
        if "human:" + p["id"] not in hidden
    ]
    entries: Row = views.entries(c, shared=True)
    access: Row = auth.bot_accesses(c, who)
    live: dict[str, Row] = {row["id"]: row for row in bots}
    no_strategy = strategy_bot.hidden_for(c, who)
    agents: list[Row] = []
    for slug, entry in entries.items():
        bot: Row = live.get(slug) or {}
        config: Row = entry or {}
        if bot.get("state") == "archived" or not access.get(slug, auth.FULL)["see"]:
            continue
        if no_strategy and slug == strategy_bot.SLUG:
            continue
        name = _text(bot.get("slug")) or slug
        agents.append(
            {
                "id": "bot:" + slug,
                "kind": "agent",
                "name": _text(bot.get("display_name")) or _text(config.get("display_name")) or name,
                "slug": name,
            }
        )
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

    def among(a: Row, ids: Iterable[str]) -> bool:
        return a["id"] in ids or (a.get("slug") is not None and "bot:" + str(a["slug"]) in ids)

    found: list[tuple[float, int, str, str, Row]] = []
    for a in everyone:
        if a["id"] in self or a.get("lifecycle") == "archived" or (allowed is not None and not among(a, allowed)):
            continue
        r = _rank(a, q)
        if r is None:
            continue
        f = order.get(a["id"], order.get("bot:" + str(a["slug"])) if a.get("slug") is not None else None)
        name = str(a["name"])
        found.append((math.inf if f is None else f, r, name.casefold(), name, a))
    found.sort(key=lambda m: m[:4])
    return [(m[4], m[0] != math.inf) for m in found[:limit]]


# What a pick is checked in: a handle followed by more words, as a post writes it, so a longer name that starts with it
# (`@Money Desk` beside `Money Desk Can`) is caught by the same matcher a post uses.
_PROBE = "{} can you check?"


def _named(text: str, index: list[tuple[str, list[str]]], me: str) -> set[str]:
    """Whom a post of `text` by `me` @mentions: `threads.mentions` over names already read (never its author)."""
    return {actor for actor, known in index if actor != me and any(threads._spot(text, n) is not None for n in known)}


def handle(actor: Row, index: dict[str, list[str]], pairs: list[tuple[str, list[str]]], me: str) -> str | None:
    """The text to insert: the first one-word name of theirs (`handle_of`'s first) that a post reads as this one actor
    and no one else; None when none does. Never a name with a space: the words after it could make a longer name."""
    tried = [handle_of(actor)[1:], *index.get(actor["id"], [])]
    for name in dict.fromkeys(n for n in tried if n and not any(ch.isspace() for ch in n)):
        if _named(_PROBE.format("@" + name), pairs, me) == {actor["id"]}:
            return "@" + name
    return None


def _task_people(row: Row, roles: dict[str, list[str]]) -> list[str]:
    """`onTask` in web/src/features/task/TaskPage.tsx: who asked, who it is for, who has it, then its roles' people."""
    ids: list[Any] = [row.get("requester_id"), row.get("assignee_id"), row.get("owner_id")]
    ids += [a for people in roles.values() for a in people]
    return list(dict.fromkeys(str(a) for a in ids if a))


def search(
    c: Any,
    auth: Any,
    who: Any,
    *,
    q: str = "",
    task: str | None = None,
    thread: str | None = None,
    limit: int = LIMIT,
) -> Row:
    """The picker's answer for this caller."""
    auth.domain(who)
    limit = max(1, min(limit, MAX_LIMIT))
    me = str(getattr(who, "actor", "") or "")
    first: list[str] = []
    only: list[str] | None = None
    if task:
        row: Row = auth.task(c, who, auth.resolve_task(c, who, task), tags=False)
        first = _task_people(row, task_roles.roles_of(c, row["id"]))
    if thread:
        # A private chat's @ reaches only its members, its sub-threads' too (an outsider gets `mention_outside`; only a
        # task's thread widens): the list offers just them.
        root = threads.root_of(c, threads.require(c, threads.viewer(auth, who), thread))
        if threads.audience_of(root) in ("members", "personal", "participants"):
            only = threads._dm_members(c, root)
    bots: list[Row] = H.bots(c)
    picked = ranked(roster(c, auth, who, bots), q, me, first, only, limit)
    pairs: list[tuple[str, list[str]]] = threads.mention_names(c, bots=bots) if picked else []
    index = dict(pairs)
    return {
        "candidates": [
            {
                "id": a["id"],
                "kind": a["kind"],
                "name": a["name"],
                "handle": handle(a, index, pairs, me),
                "on_task": on_task,
            }
            for a, on_task in picked
        ]
    }
