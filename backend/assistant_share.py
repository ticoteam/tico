# pyright: strict
"""A person lets bots they choose read their own Assistant chat, read only (docs/assistant.md, "Private to you").

Nobody is on a person's list at first. Only that person adds a bot or removes one, on their own click: not their
Assistant's turn, not BotOps acting for them, not the owner or an admin. A bot on the list reads the person's messages,
the Assistant's replies and its cards through `GET /api/v2/assistant/shared/{person}`, and nothing else is opened to it:
every route that posts, reacts or acts on a card in the room still refuses it. Removing a bot refuses its next read at
once, since every read checks the list as it is now. What the Assistant has learned about the person stays out unless
the person also turns on `include_learned`; the company layer is never shared.

Each read is an audit event (`assistant.shared_read`), and the person's list shows each bot's last read, so a share is
never forgotten. The tables are not in SQL (backend/sql.py denies a table it does not name)."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request

from . import models as M
from . import rooms
from .store import H as _H
from .store import Problem
from .store import message_page as _message_page  # pyright: ignore[reportUnknownVariableType]

if TYPE_CHECKING:
    import sqlite3

    from .auth import Identity
    from .config import Settings

H: Any = _H
message_page: Any = _message_page  # pyright: ignore[reportUnknownVariableType]

READ_LIMIT = 200  # messages in one read, the chat page's size; `before` reads the page above
CARD_FIELDS = ("id", "summary", "description", "status", "proposer", "created_at", "decided_at")


def _owner_only(who: "Identity", settings: "Settings") -> str:
    """The person themselves, on their own: never a bot, nor anyone acting for them, and not a personal API token either,
    which may be in an outside assistant's hands (as other consent switches refuse one)."""
    if who.role not in ("owner", "human") or not H.is_human(who.actor) or who.via or who.via_token:
        raise Problem(
            "forbidden",
            "Only you choose who can view your " + settings.assistant_name + " chat, on your own",
            403,
        )
    return who.actor


def _reader_bot(c: "sqlite3.Connection", who: "Identity", settings: "Settings") -> str:
    """The bot reading in its own run: never a person (the owner and admins included) or a bot acting for one."""
    if who.role != "bot" or who.via:
        raise Problem("forbidden", "Only a bot a person chose reads their " + settings.assistant_name + " chat", 403)
    row = H.bot(c, who.actor)
    if not row:
        raise Problem("forbidden", "Only a bot a person chose reads their " + settings.assistant_name + " chat", 403)
    return row["id"]


def _choosable(c: "sqlite3.Connection", settings: "Settings", ref: str) -> dict[str, Any]:
    """The bot a person may add: a live bot of the team other than the Assistant itself, which already reads it."""
    row = H.resolve_bot(c, str(ref or "").removeprefix("bot:"))
    if not row or row["state"] == "archived":
        raise Problem("not_found", "No such bot", 404)
    assistant = H.resolve_bot(c, settings.assistant_bot)
    if assistant and row["id"] == assistant["id"]:
        raise Problem("invalid", "The " + settings.assistant_name + " already reads its own chat", 422)
    return row


def include_learned(c: "sqlite3.Connection", owner: str) -> bool:
    row = c.execute("SELECT include_learned FROM assistant_share_settings WHERE owner=?", (owner,)).fetchone()
    return bool(row and row[0])


def viewers(c: "sqlite3.Connection", owner: str) -> dict[str, Any]:
    """The person's list: each bot they added, by name, with when it last read."""
    rows = c.execute(
        "SELECT bot, created_at, last_read_at FROM assistant_shares WHERE owner=? ORDER BY created_at, bot", (owner,)
    ).fetchall()
    bots: list[dict[str, Any]] = []
    for r in rows:
        bot: dict[str, Any] = H.bot(c, r["bot"]) or {}
        bots.append(
            {
                "bot": r["bot"],
                "slug": bot.get("slug") or r["bot"],
                "name": H.actor_name(c, "bot:" + r["bot"]),
                "added_at": r["created_at"],
                "last_read_at": r["last_read_at"],
            }
        )
    return {"bots": bots, "include_learned": include_learned(c, owner)}


def add(c: "sqlite3.Connection", who: "Identity", ref: str, settings: "Settings") -> dict[str, Any]:
    owner = _owner_only(who, settings)
    bot = _choosable(c, settings, ref)
    made = c.execute(
        "INSERT OR IGNORE INTO assistant_shares (owner, bot, created_at) VALUES (?,?,?)", (owner, bot["id"], H.now())
    ).rowcount
    if made:
        H.event(c, owner, "assistant.share_added", "bot:" + bot["id"])
    return viewers(c, owner)


def remove(c: "sqlite3.Connection", who: "Identity", ref: str, settings: "Settings") -> dict[str, Any]:
    owner = _owner_only(who, settings)
    key = str(ref or "").removeprefix("bot:")
    bot = H.resolve_bot(c, key)
    bot_id = bot["id"] if bot else key  # an archived or deleted bot can still be taken off the list
    gone = c.execute("DELETE FROM assistant_shares WHERE owner=? AND bot=?", (owner, bot_id)).rowcount
    if gone:
        H.event(c, owner, "assistant.share_removed", "bot:" + bot_id)
    if not c.execute("SELECT 1 FROM assistant_shares WHERE owner=?", (owner,)).fetchone():
        # With nobody left on the list the switch is not drawn, so it goes back to off: a bot added later never reads
        # the learned layer on a choice made for another.
        c.execute("DELETE FROM assistant_share_settings WHERE owner=?", (owner,))
    return viewers(c, owner)


def set_learned(c: "sqlite3.Connection", who: "Identity", on: bool, settings: "Settings") -> dict[str, Any]:
    owner = _owner_only(who, settings)
    c.execute(
        "INSERT INTO assistant_share_settings (owner, include_learned, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(owner) DO UPDATE SET include_learned=excluded.include_learned, updated_at=excluded.updated_at",
        (owner, 1 if on else 0, H.now()),
    )
    H.event(c, owner, "assistant.share_learned", owner, {"include_learned": bool(on)})
    return viewers(c, owner)


def shared_with(c: "sqlite3.Connection", who: "Identity", settings: "Settings") -> dict[str, Any]:
    """The people who chose this bot: what it may read, and nothing about anyone else."""
    bot = _reader_bot(c, who, settings)
    rows = c.execute(
        "SELECT owner, created_at, last_read_at FROM assistant_shares WHERE bot=? ORDER BY created_at", (bot,)
    ).fetchall()
    return {
        "people": [
            {
                "person": r["owner"],
                "name": H.person_name(c, r["owner"]),
                "since": r["created_at"],
                "last_read_at": r["last_read_at"],
                "include_learned": include_learned(c, r["owner"]),
            }
            for r in rows
        ]
    }


def _card_ref(refs: object) -> str | None:
    """The card a message shows, from its refs, or None."""
    action = refs.get("action") if isinstance(refs, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return str(action) if action else None  # pyright: ignore[reportUnknownArgumentType]


def _card(row: "sqlite3.Row") -> dict[str, Any]:
    """A card as the reader sees it: what it proposed and what became of it, never its request body or its result,
    which can carry what a confirm showed the person once."""
    return {k: row[k] for k in CARD_FIELDS if k in row.keys()}


def read(
    c: "sqlite3.Connection",
    who: "Identity",
    person: str,
    settings: "Settings",
    before: str | None = None,
    limit: int = READ_LIMIT,
) -> dict[str, Any]:
    """One page of the person's Assistant chat for a bot they chose, recorded in the audit log and as its last read."""
    from . import assistant

    find_room: Any = assistant.find_room  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    bot = _reader_bot(c, who, settings)
    owner = person if str(person).startswith("human:") else "human:" + str(person)
    if not c.execute("SELECT 1 FROM assistant_shares WHERE owner=? AND bot=?", (owner, bot)).fetchone():
        raise Problem(
            "not_shared", "This person has not chosen you to view their " + settings.assistant_name + " chat", 403
        )
    from . import auth as A

    standing: Any = A.standing  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if standing(c, H.actor_id(owner)):
        # A person who left or whose sign-in is off shares nothing more, as their own Assistant stops for them.
        raise Problem("not_shared", "This person's " + settings.assistant_name + " chat is not shared now", 403)
    room = find_room(c, owner)
    page: dict[str, Any] = (
        message_page(c, room["id"], before=before, limit=max(1, min(int(limit or READ_LIMIT), READ_LIMIT)))
        if room and room.get("room_key") == rooms.ASSISTANT_ROOM
        else {"messages": [], "has_more": False, "next_before": None}
    )
    rows_in: list[dict[str, Any]] = page["messages"]
    messages: list[dict[str, Any]] = [
        {
            "id": m["id"],
            "created_at": m["created_at"],
            "sender": m["sender_id"],
            "from_assistant": not H.is_human(m["sender_id"]),
            "body": m["body"],
            "card": _card_ref(m.get("refs")),
        }
        for m in rows_in
    ]
    ids = [m["card"] for m in messages if m["card"]]
    cards: dict[str, dict[str, Any]] = {}
    if ids:
        rows = c.execute(
            "SELECT * FROM assistant_actions WHERE owner=? AND id IN (%s)" % ",".join("?" * len(ids)), (owner, *ids)
        ).fetchall()
        cards = {r["id"]: _card(r) for r in rows}
    learned: list[dict[str, Any]] | None = None
    if include_learned(c, owner):
        from . import learned as L

        kept: list[dict[str, Any]] = L.entries(c, "personal", owner)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        learned = [{"text": e["text"], "created_at": e["created_at"]} for e in kept]
    now = H.now()
    c.execute("UPDATE assistant_shares SET last_read_at=? WHERE owner=? AND bot=?", (now, owner, bot))
    H.event(c, who.actor, "assistant.shared_read", owner, {"messages": len(messages), "learned": learned is not None})
    return {
        "person": owner,
        "name": H.person_name(c, owner),
        "messages": messages,
        "cards": cards,
        "has_more": page["has_more"],
        "next_before": page["next_before"],
        "learned": learned,
    }


def install(app: FastAPI, store: Any, auth: Any, mutate: Callable[..., Any]) -> None:
    settings = store.settings

    @app.get("/api/v2/assistant/viewers")
    def viewers_show(request: Request):
        """The bots you chose to view your Assistant chat, read only, each with its last read."""
        who = request.state.identity
        with store.read() as c:
            return viewers(c, _owner_only(who, settings))

    @app.post("/api/v2/assistant/viewers")
    def viewers_add(request: Request, body: M.AssistantViewerAdd):
        """Let one more bot view your Assistant chat, read only. Only you, on your own."""
        who = request.state.identity

        def work(c: "sqlite3.Connection") -> dict[str, Any]:
            return add(c, who, body.bot, settings)

        return mutate(request, body, work)

    @app.delete("/api/v2/assistant/viewers/{bot}")
    def viewers_remove(request: Request, bot: str):
        """Take a bot off your list: its next read is refused."""
        who = request.state.identity

        def work(c: "sqlite3.Connection") -> dict[str, Any]:
            return remove(c, who, bot, settings)

        return mutate(request, M.Empty(), work)

    @app.put("/api/v2/assistant/viewers/learned")
    def viewers_learned(request: Request, body: M.AssistantViewerLearned):
        """Whether the bots you chose also read what your Assistant has learned about you."""
        who = request.state.identity

        def work(c: "sqlite3.Connection") -> dict[str, Any]:
            return set_learned(c, who, body.include_learned, settings)

        return mutate(request, body, work)

    @app.get("/api/v2/assistant/shared")
    def shared_list(request: Request):
        """For a bot: the people who chose it to view their Assistant chat."""
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return shared_with(c, who, settings)

    @app.get("/api/v2/assistant/shared/{person}")
    def shared_read(request: Request, person: str, before: str | None = None, limit: int = READ_LIMIT):
        """For a bot a person chose: one page of their Assistant chat, read only, and recorded in the audit log."""
        who = request.state.identity
        auth.domain(who)
        with store.transaction() as c:
            return read(c, who, person, settings, before=before, limit=limit)
