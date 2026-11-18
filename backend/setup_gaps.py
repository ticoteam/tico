# pyright: strict
"""Setup gaps: which parts of the company still wait on a person, and the bot that asks them (skills/setup-in-flow).

`GET /api/v2/setup/gaps` answers one row per area, most important first: `area`, `needs` (the area is still empty and
the caller is someone who sets the company up: its owner or an admin; a bot reads the company's state), `bot` (the bot
that asks for it and fills it in: `id` as an actor, `slug`, `name`; null when the caller may not read it or none is
live) and `href` (the page or tab to open). Each area is one or two indexed `LIMIT 1` reads, so the read is cheap enough
to repeat on every `setup` event (backend/events.py: a change that may open or close a gap rings it).

  market    no active market entity, no market page past the seed, and no open "Set up the market map" task:
            the Librarian, which builds the market (backend/librarian_setup.py)
  strategy  no strategy summary (draft or published) or no brand (draft or published): the Strategy page's owner
  goals     no goal: the Goals page's owner
  org       no bot of the company's own (only the built-ins): BotOps, which hires
  tools     no tool: the Tools page's owner (the Tools Owner)
  budget    no company spending limit (a pool or the default for each bot): BotOps, which sets limits for a person

A page's owner is the one Settings > Page owners names (backend/page_owners.py), so a company that chose another bot
for Strategy is asked by that bot. Nothing here writes, calls a model or keeps a table: the gaps are the data itself,
so a mark goes the moment its area is filled, by whoever fills it.
"""

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from . import groups as groups_
from . import hubdb as H_
from . import page_owners
from . import views as views_
from .api_contract import api_v2
from .getting_started import MARKET_TASK
from .librarian import LIBRARIAN

Row = dict[str, Any]
H: Any = H_
views: Any = views_
SYSTEM_BOTS: tuple[str, ...] = tuple(groups_.SYSTEM_BOTS)

AREAS = ("market", "strategy", "goals", "org", "tools", "budget")
NAMES = {
    "market": "Market",
    "strategy": "Strategy",
    "goals": "Goals",
    "org": "Org",
    "tools": "Tools",
    "budget": "Budget",
}
HREF = {
    "market": "#/strategy/market",
    "strategy": "#/strategy",
    "goals": "#/strategy/goals",
    "org": "#/strategy/org",
    "tools": "#/tools",
    "budget": "#/strategy/budget",
}
# (the page whose owner asks, or None; the bot when the page names none that is live).
OWNER: dict[str, tuple[str | None, str]] = {
    "market": (None, LIBRARIAN),
    "strategy": ("strategy", "strategy-owner"),
    "goals": ("goals", "strategy-owner"),
    "org": (None, "botops"),
    "tools": ("tools", "botops"),
    "budget": (None, "botops"),
}


def _one(c: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> bool:
    return c.execute(sql + " LIMIT 1", args).fetchone() is not None


def _filled(body: object) -> bool:
    try:
        value: Any = json.loads(str(body or "")) if body else None
    except ValueError:
        return False
    if not isinstance(value, dict):
        return False
    values: list[Any] = list(value.values())  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
    return any(values)


def _market(c: sqlite3.Connection) -> bool:
    if _one(c, "SELECT 1 FROM entities WHERE status='active'") or _one(c, "SELECT 1 FROM market_pages WHERE seeded=0"):
        return False
    return not _one(
        c,
        "SELECT 1 FROM tasks WHERE owner_id=? AND status='open' AND (title=? OR title LIKE ?)",
        ("bot:" + LIBRARIAN, MARKET_TASK, MARKET_TASK + " (%"),
    )


def _strategy(c: sqlite3.Connection) -> bool:
    if not _one(c, "SELECT 1 FROM strategy_versions WHERE summary<>''"):
        return True
    if _one(c, "SELECT 1 FROM brand_versions"):
        return False
    row = c.execute("SELECT body FROM brand_drafts WHERE id=1").fetchone()
    return not (row and _filled(row[0]))


def _org(c: sqlite3.Connection) -> bool:
    marks = ",".join("?" * len(SYSTEM_BOTS))
    return not _one(
        c, f"SELECT 1 FROM bots WHERE state NOT IN ('archived','planned') AND slug NOT IN ({marks})", SYSTEM_BOTS
    )


OPEN: dict[str, Callable[[sqlite3.Connection], bool]] = {
    "market": _market,
    "strategy": _strategy,
    "goals": lambda c: not _one(c, "SELECT 1 FROM goals"),
    "org": _org,
    "tools": lambda c: not _one(c, "SELECT 1 FROM tools"),
    "budget": lambda c: not _one(c, "SELECT 1 FROM budget_nodes WHERE scope='company'"),
}


def owner(c: sqlite3.Connection, area: str) -> Row | None:
    """The bot that asks for `area` and fills it in, as a bots row; None when none is live."""
    page, fallback = OWNER[area]
    actor = page_owners.owner(c, page, fallback=False) if page else None
    for ref in (actor, fallback):
        row: Any = H.resolve_bot(c, ref) if ref else None
        if row and row.get("state") not in ("planned", "archived"):
            return dict(row)
    return None


def sets_up(auth: Any, who: Any) -> bool:
    """Whether the caller is one a setup gap waits on: the company owner or an admin, or a bot reading for one."""
    return who.role == "bot" or who.role in ("owner", "human") and auth.company_role(who) in ("owner", "admin")


def open_areas(c: sqlite3.Connection) -> list[str]:
    """The areas still empty, most important first."""
    return [a for a in AREAS if OPEN[a](c)]


def read(c: sqlite3.Connection, auth: Any, who: Any) -> list[Row]:
    may = sets_up(auth, who)
    hidden: set[str] = set(auth.unreadable_bots(c, who)) if who.role != "bot" else set()
    out: list[Row] = []
    for area in AREAS:
        bot = owner(c, area)
        named = None
        if bot and bot["id"] not in hidden and bot.get("slug") not in hidden:
            named = {
                "id": "bot:" + bot["id"],
                "slug": bot.get("slug") or bot["id"],
                "name": H.actor_name(c, "bot:" + bot["id"]),
            }
        out.append({"area": area, "needs": may and OPEN[area](c), "bot": named, "href": HREF[area]})
    return out


def install(app: Any, store: Any, auth: Any) -> None:
    from fastapi import Request

    @app.get(
        "/api/v2/setup/gaps",
        **api_v2(
            "Needs you",
            "getSetupGaps",
            "What of the company's setup still waits on a person, one row per area (market, strategy, goals, org, tools, "
            "budget), most important first: `needs` (still empty, and you are the owner or an admin; a bot reads the "
            "company's state), the `bot` that asks for it and fills it in, and the page to open (`href`). The `setup` "
            "event says when to read it again",
            "SetupGaps",
        ),
    )
    def setup_gaps(request: Request) -> Row:  # pyright: ignore[reportUnusedFunction]
        """What of the company's setup still waits on a person, area by area, and the bot that asks for each."""
        who = request.state.identity
        if who.role != "bot":
            views.human_only(who)
        with store.read() as c:
            return {"areas": read(c, auth, who)}
