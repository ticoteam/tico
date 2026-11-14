# pyright: strict
"""A page chat's card (spec chat-on-every-page, Release 2): the page's owner bot turns a list the person pasted into one
Confirm card in their chat with it. Goals only: one row per goal, made only on the person's Confirm, as them.

The card is an ordinary bulk Confirm card (backend/assistant.py `create_proposal`) whose steps are `POST /api/v2/goals`,
proposed by `page:<page>`: Confirm runs each step the person kept (`drop`) as the person, recorded via `page_chat`
(backend/auth.py). Nothing is made before the click; the bot never makes the goals itself through this route.

Only the page's current owner bot proposes one, and only for a person who sent it a message from that page
(`refs.from_page.page`) in the last RECENT_MIN minutes, in their own chat with it: else 403 and no card. The card lands in
that chat.

  PAGES                      the pages that take cards, with the route each row runs
  propose(c, settings, auth, who, page, for_person, rows, routes)
                             the card, as create_proposal answers it
  steps_of(page, steps)      a stored card's steps, checked against its page's one route (422 otherwise)
  writer(c, room)            the bot that speaks for a page card in its room
"""

from typing import Any, cast

from .store import H, Problem

Row = dict[str, Any]

RECENT_MIN = 30
# The one route each row of a page's card runs: Goals adds goals.
PAGES = {"goals": ("POST", "/api/v2/goals")}
PROPOSER = "page:"
VIA = "page_chat"


def proposer_page(proposer: object) -> str | None:
    """The page a card's proposer names (`page:goals` -> `goals`), or None for any other proposer."""
    proposer = str(proposer or "")
    return proposer[len(PROPOSER) :] if proposer.startswith(PROPOSER) else None


def steps_of(page: str | None, steps: object) -> list[Row]:
    """A page card's steps, each exactly its page's route with a body: a stored card can hold nothing else."""
    method, path = PAGES.get(page or "", ("", ""))
    given = cast("list[object]", steps) if isinstance(steps, list) else []
    if not 1 <= len(given) <= 50:
        raise Problem("operation", "A page card carries 1 to 50 rows", 422)
    out: list[Row] = []
    for item in given:
        step = cast("Row", item) if isinstance(item, dict) else None
        body = step.get("body") if step is not None else None
        if (
            step is None
            or set(step) - {"method", "path", "body"}
            or step.get("method") != method
            or step.get("path") != path
            or not isinstance(body, dict)
        ):
            raise Problem("operation", "A page card's rows are " + method + " " + path + " only", 422)
        out.append({"method": method, "path": path, "body": dict(cast("Row", body))})
    return out


def members(c: Any, room: Row | None) -> set[str]:
    """A chat's people and bots (a direct chat keeps them as thread members, not participants)."""
    from . import threads

    row = cast("Row | None", threads.thread_row(c, room["id"]) if room else None)  # pyright: ignore[reportUnknownMemberType]
    found = cast("list[Any]", threads._dm_members(c, row) if row else [])  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType]
    return {str(m) for m in found}


def writer(c: Any, room: Row | None) -> str | None:
    """The bot in the person's chat with the page's owner: the card and its result are said by it."""
    bots = [p for p in members(c, room) if p.startswith("bot:")]
    return bots[0][4:] if len(bots) == 1 else None


def _asked_from(c: Any, person: str, bot: str, page: str) -> Row | None:
    """The person's newest message to `bot` from `page` within RECENT_MIN minutes, in their own chat with it."""
    since: str = H.shift(H.now(), minutes=-RECENT_MIN)  # pyright: ignore[reportUnknownMemberType]
    for row in c.execute(
        "SELECT id, conversation_id FROM messages WHERE sender_id=? AND recipient_id=? AND deleted_at IS NULL "
        "AND created_at>=? AND json_extract(refs,'$.from_page.page')=? ORDER BY created_at DESC LIMIT 20",
        (person, bot, since, page),
    ):
        room: Row | None = H.conversation(c, row["conversation_id"])  # pyright: ignore[reportUnknownMemberType]
        if room and not room.get("closed_at") and members(c, room) == {person, bot}:
            return room
    return None


def propose(
    c: Any, settings: Any, auth: Any, who: Any, page: str, for_person: str, rows: list[Row], routes: Any
) -> Row:
    """The card for `for_person`, proposed by `who` (the page's owner bot), from `rows` ({title, owner?, parent_id?})."""
    from . import assistant, page_owners

    if page not in PAGES:
        raise Problem("page", "Only " + ", ".join(PAGES) + " takes a card", 422)
    owner = page_owners.owner(c, page)
    if who.role != "bot" or who.via or not owner or who.actor != owner:
        raise Problem(
            "forbidden", "Only the " + page_owners.PAGES[page]["name"] + " page's owner bot proposes its card", 403
        )
    person = H.resolve_actor(c, for_person) or ""
    room = _asked_from(c, person, owner, page) if person and person.startswith("human:") else None
    if not room:
        raise Problem(
            "forbidden",
            "A card goes only to a person who wrote to you from "
            + page_owners.PAGES[page]["name"]
            + f" in the last {RECENT_MIN} minutes",
            403,
        )
    asker = auth.identity_for_actor(c, person)
    method, path = PAGES[page]
    steps: list[Row] = []
    shown: list[Row] = []
    for row in rows:
        try:
            made_by = str(auth.target(c, asker, row["owner"])) if row.get("owner") else person
        except Problem:
            raise Problem("owner", "No person or bot " + repr(row["owner"]) + " to own a goal", 422) from None
        body: Row = {"title": row["title"], "owner": made_by}
        if row.get("parent_id"):
            body["parent_id"] = row["parent_id"]
        steps.append({"method": method, "path": path, "body": body})
        shown.append({"field": row["title"], "new": H.actor_name(c, made_by)})  # pyright: ignore[reportUnknownMemberType]
    n = len(steps)
    count = f"{n} goal" + ("" if n == 1 else "s")
    mine = all(s["body"]["owner"] == person for s in steps)
    return assistant.create_proposal(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        c,
        settings,
        asker,
        room,
        PROPOSER + page,
        "Add " + count,
        "POST",
        assistant.BATCH_PATH,
        {"actions": steps},
        what=count + (", owned by you" if mine else "") + ". Drop any you don't want.",
        routes=routes,
        shown=shown,
    )
