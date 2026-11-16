# pyright: strict
"""Who owns a page: the one bot you chat with from it (ui/app/page-chat.ts).

A page names only its owner. The chat it opens is the viewer's ordinary direct chat with that bot, the same one as on
the bot's page; a message sent from the page carries `refs.from_page` (`check_from`), which the bot's turn reads.

One owner per page, changeable in Settings > General > Page owners or with `tico settings set pages.<page>.owner`
by the owner or an admin. A page follows its owner at the next load, with no release. The choices are kept in
registry_metadata under `page_owners` ({page: bot id, or null for "no owner"}); a page with no choice kept has its
default ("default" in a save drops the choice). Every page has an owner (style guide "Where chat lives"): a chosen
owner or default that is missing, planned or archived gives way to the next in line (the page's default, then the
General Manager, which every company has), so a page has no owner only when a person chose "No owner". That page has
no chat at all. The General Manager stands in only to chat: what a page's owner may do beyond that (the Tools owner's
key guard and key-grant cards, secret_drop.py and key_grants.py) asks for the page's own owner (`fallback=False`).

  PAGES                    the pages that have an owner, with their name and default bot
  read(c)                  {page: {"bot": the owner now ("bot:<id>" or None), "name": the page's name,
                                  "choice": "default", "none" or the chosen "bot:<id>", "default": who Default gives}}
  owner(c, page[, fallback])  the page's owner as an actor id, or None
  save(c, actor, page, bot)  sets or clears one page's owner (bot None: no owner; "default": its default); the saved row
  check_from(refs)         a 422 unless refs.from_page, when present, is {page, label[, item {kind, id, name}]}; an
                           item's name longer than its limit is shortened in place
"""

from typing import TYPE_CHECKING, Any, cast

from . import access as Access
from .store import H, Problem

if TYPE_CHECKING:
    import sqlite3

KEY = "page_owners"
# The bot that does each page's work (spec chat-on-every-page), one per page the sidebar and the router reach, in the
# sidebar's order (Settings lists them so). ui/app/page-chat.ts maps each address to its key. A default names a bot a new
# company has where one fits (the built-ins: general-manager, strategy-owner, finance-owner, rules-owner, librarian,
# botops, automations-owner, updates-owner, tools-owner); growth-manager is the catalogue's bot for the work, which a company
# may not have installed: its page falls back to the General Manager. A page may name a second bot in line (`then`): Tools
# goes to an IT Support Specialist a company hired when it has no Tools Owner (one from before it was built in, until a
# start makes it), and only then to the General Manager.
# Pages with no key have no page chat because the chat IS the page (a bot's or person's page, a thread, a task, the
# Assistant) or it is setup or the style guide. Calendar and Meetings have none of their own either: the Assistant owns
# them, and their chat is Ask itself, in the sidebar (page-chat.ts PGC_ASK), never a second Assistant chat.
PAGES: dict[str, dict[str, str]] = {
    "tasks": {"name": "Tasks", "default": "general-manager"},  # a pipeline's manager answers while one is shown
    "updates": {"name": "Updates", "default": "updates-owner"},
    "company": {"name": "Company", "default": "general-manager"},  # Company's dashboards, its teams', the Company page
    "strategy": {
        "name": "Strategy",
        "default": "strategy-owner",
    },  # Strategy's other tabs: Market, Org, Budget, Lenses, Brand
    "goals": {"name": "Goals", "default": "strategy-owner"},
    "finance": {"name": "Finance", "default": "finance-owner"},  # with Bills (Payables)
    "growth": {"name": "Growth", "default": "growth-manager"},
    "automations": {"name": "Automations", "default": "automations-owner"},
    "listening": {"name": "Listening", "default": "automations-owner"},
    "rules": {"name": "Rules", "default": "rules-owner"},
    "docs": {"name": "Docs", "default": "librarian"},  # Docs and Library > Files
    "skills": {"name": "Skills", "default": "botops"},
    "contacts": {"name": "Contacts", "default": "librarian"},
    "tools": {
        "name": "Tools",
        "default": "tools-owner",
        "then": "it-support-specialist",
    },  # with its keys and inboxes: who sets up tools, keys and levels
    "mail": {"name": "Mail", "default": "general-manager"},
    "messaging": {"name": "Messaging", "default": "general-manager"},
    "settings": {"name": "Settings", "default": "botops"},
    "learnings": {"name": "Learnings", "default": "botops"},
    "usage": {"name": "Usage", "default": "botops"},
    "changelog": {"name": "Changelog", "default": "updates-owner"},
    "replay": {"name": "Replay", "default": "botops"},
    "roadmap": {"name": "Roadmap", "default": "general-manager"},
    "tags": {"name": "Tags", "default": "general-manager"},
    "computers": {"name": "Computers", "default": "botops"},  # a computer's screen and performance pages
    "sql": {"name": "SQL", "default": "botops"},
    "seat": {"name": "Hiring", "default": "botops"},  # a seat being filled (recruit.ts)
}
# Every company has the General Manager (onboarding.complete_company): the owner of last resort, to chat with only.
FALLBACK = "general-manager"
DEFAULT = "default"  # a save's bot that drops the page's choice: the page has its default again
FROM_LIMITS = {"page": 40, "label": 80, "kind": 40, "id": 200, "name": 200}


def _kept(c: "sqlite3.Connection") -> dict[str, str | None]:
    return cast("dict[str, str | None]", Access._load_json(c, KEY) or {})  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType]


def _live(c: "sqlite3.Connection", ref: str | None) -> str | None:
    """The bot's actor id when it exists and is set up (not planned, not archived); else None."""
    row = cast("dict[str, Any] | None", H.resolve_bot(c, ref) if ref else None)  # pyright: ignore[reportUnknownMemberType]
    if not row or row.get("state") in ("planned", "archived"):
        return None
    return "bot:" + str(row["id"])


def _owner(
    c: "sqlite3.Connection",
    page: str,
    kept: dict[str, str | None],
    seen: dict[str, str | None],
    *,
    chosen: bool = True,
    fallback: bool = True,
) -> str | None:
    """The page's owner: the chosen bot, else its default (then its `then`), else the General Manager, the first that is live. None only
    when a person chose no owner (or, with `fallback` False, when neither the choice nor the default is live).
    `chosen` False: who the page's default gives, whatever is chosen. `seen` keeps each bot looked up once per read."""
    if chosen and page in kept and kept[page] is None:
        return None
    spec = PAGES[page]
    line = (kept.get(page) if chosen else None, spec["default"], spec.get("then"), FALLBACK if fallback else None)
    for ref in line:
        if ref and ref not in seen:
            seen[ref] = _live(c, ref)
        if ref and seen[ref]:
            return seen[ref]
    return None


def owner(c: "sqlite3.Connection", page: str, *, fallback: bool = True) -> str | None:
    """The page's owner as an actor id. `fallback` False: the page's own owner only (its choice or its default), never
    the General Manager standing in: what the page's owner may do beyond chatting asks for this one."""
    if page not in PAGES:
        return None
    return _owner(c, page, _kept(c), {}, fallback=fallback)


def _choice(kept: dict[str, str | None], page: str) -> str:
    if page not in kept:
        return DEFAULT
    bot = kept[page]
    return "bot:" + bot if bot else "none"


def read(c: "sqlite3.Connection") -> dict[str, dict[str, str | None]]:
    kept: dict[str, str | None] = _kept(c)
    seen: dict[str, str | None] = {}
    return {
        page: {
            "bot": _owner(c, page, kept, seen),
            "name": spec["name"],
            "choice": _choice(kept, page),
            "default": _owner(c, page, kept, seen, chosen=False),
        }
        for page, spec in PAGES.items()
    }


def save(c: "sqlite3.Connection", actor: str, page: str, bot: str | None) -> dict[str, str | None]:
    if page not in PAGES:
        raise Problem("not_found", "No page " + repr(page) + "; pages with an owner: " + ", ".join(PAGES), 404)
    kept = _kept(c)
    before = owner(c, page)
    if bot == DEFAULT:
        Access._store(c, KEY, {k: v for k, v in kept.items() if k != page})  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType]
        after = owner(c, page)
        H.event(c, actor, "page_owner.updated", page, {"before": before, "after": after, "default": True})  # pyright: ignore[reportUnknownMemberType]
        return {"page": page, "bot": after, "name": PAGES[page]["name"], "choice": DEFAULT}
    chosen = None
    if bot:
        chosen = _live(c, bot)
        if not chosen:
            raise Problem("not_found", "No active bot " + repr(bot), 404)
    Access._store(c, KEY, {**kept, page: chosen[4:] if chosen else None})  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType]
    H.event(c, actor, "page_owner.updated", page, {"before": before, "after": chosen})  # pyright: ignore[reportUnknownMemberType]
    return {"page": page, "bot": chosen, "name": PAGES[page]["name"], "choice": chosen or "none"}


def _text(value: object, key: str) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= FROM_LIMITS[key]


def _item_ok(item: object) -> bool:
    if item is None:
        return True
    if not isinstance(item, dict):
        return False
    fields = cast("dict[str, object]", item)
    name = fields.get("name")
    if isinstance(name, str) and len(name.strip()) > FROM_LIMITS["name"]:
        # A long title (a doc's, a goal's) is only what the chat shows: shortened, never refused.
        fields["name"] = name.strip()[: FROM_LIMITS["name"] - 1].rstrip() + "…"
    return set(fields) <= {"kind", "id", "name"} and all(_text(fields.get(k), k) for k in ("kind", "id", "name"))


def check_from(refs: dict[str, object] | None) -> None:
    """A message's `refs.from_page`, when sent: the page it was written on and what was open there."""
    given = (refs or {}).get("from_page")
    if given is None:
        return
    fields = cast("dict[str, object]", given) if isinstance(given, dict) else None
    ok = (
        fields is not None
        and set(fields) <= {"page", "label", "item"}
        and _text(fields.get("page"), "page")
        and _text(fields.get("label"), "label")
        and _item_ok(fields.get("item"))
    )
    if not ok:
        raise Problem(
            "from_page",
            "refs.from_page is {page, label} and an optional item {kind, id, name}, each a short text",
            422,
        )
