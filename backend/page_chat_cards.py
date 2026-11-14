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

from .store import H, Problem

RECENT_MIN = 30
# The one route each row of a page's card runs: Goals adds goals.
PAGES = {"goals": ("POST", "/api/v2/goals")}
PROPOSER = "page:"
VIA = "page_chat"


def proposer_page(proposer):
    """The page a card's proposer names (`page:goals` -> `goals`), or None for any other proposer."""
    proposer = str(proposer or "")
    return proposer[len(PROPOSER) :] if proposer.startswith(PROPOSER) else None


def steps_of(page, steps):
    """A page card's steps, each exactly its page's route with a body: a stored card can hold nothing else."""
    method, path = PAGES.get(page or "", ("", ""))
    if not isinstance(steps, list) or not 1 <= len(steps) <= 50:
        raise Problem("operation", "A page card carries 1 to 50 rows", 422)
    out = []
    for step in steps:
        if (
            not isinstance(step, dict)
            or set(step) - {"method", "path", "body"}
            or step.get("method") != method
            or step.get("path") != path
            or not isinstance(step.get("body"), dict)
        ):
            raise Problem("operation", "A page card's rows are " + method + " " + path + " only", 422)
        out.append({"method": method, "path": path, "body": dict(step["body"])})
    return out


def members(c, room):
    """A chat's people and bots (a direct chat keeps them as thread members, not participants)."""
    from . import threads

    row = threads.thread_row(c, room["id"]) if room else None
    return set(threads._dm_members(c, row)) if row else set()  # pyright: ignore[reportPrivateUsage]


def writer(c, room):
    """The bot in the person's chat with the page's owner: the card and its result are said by it."""
    bots = [p for p in members(c, room) if p.startswith("bot:")]
    return bots[0][4:] if len(bots) == 1 else None


def _asked_from(c, person, bot, page):
    """The person's newest message to `bot` from `page` within RECENT_MIN minutes, in their own chat with it."""
    since = H.shift(H.now(), minutes=-RECENT_MIN)
    for row in c.execute(
        "SELECT id, conversation_id FROM messages WHERE sender_id=? AND recipient_id=? AND deleted_at IS NULL "
        "AND created_at>=? AND json_extract(refs,'$.from_page.page')=? ORDER BY created_at DESC LIMIT 20",
        (person, bot, since, page),
    ):
        room = H.conversation(c, row["conversation_id"])
        if room and not room.get("closed_at") and members(c, room) == {person, bot}:
            return room
    return None


def propose(c, settings, auth, who, page, for_person, rows, routes):
    from . import assistant, page_owners

    if page not in PAGES:
        raise Problem("page", "Only " + ", ".join(PAGES) + " takes a card", 422)
    owner = page_owners.owner(c, page)
    if who.role != "bot" or who.via or not owner or who.actor != owner:
        raise Problem(
            "forbidden", "Only the " + page_owners.PAGES[page]["name"] + " page's owner bot proposes its card", 403
        )
    person = H.resolve_actor(c, for_person)
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
    steps, shown = [], []
    for row in rows:
        try:
            made_by = auth.target(c, asker, row["owner"]) if row.get("owner") else person
        except Problem:
            raise Problem("owner", "No person or bot " + repr(row["owner"]) + " to own a goal", 422) from None
        body = {"title": row["title"], "owner": made_by}
        if row.get("parent_id"):
            body["parent_id"] = row["parent_id"]
        steps.append({"method": method, "path": path, "body": body})
        shown.append({"field": row["title"], "new": H.actor_name(c, made_by)})
    n = len(steps)
    count = f"{n} goal" + ("" if n == 1 else "s")
    mine = all(s["body"]["owner"] == person for s in steps)
    return assistant.create_proposal(
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
