# pyright: strict
"""Who owns a page: the one bot you chat with from it, in the shell's Agent bar (web/src/features/threads/OwnerBar.tsx).

A page names only its owner. The bar is the viewer's own private direct chat with that bot, the same one as on the bot's
page, never a shared thread; a message sent from any page carries `from_page` (`check_from`: `refs.from_page` on a
thread post, `from_page` on a post to the Assistant), which the bot's turn reads (runner-rs prompt.rs `from_page`).

One owner per page, changeable on the agent's page, in Settings > General > Page owners or with
`tico settings set pages.<page>.owner` by the owner or an admin. A page follows its owner at the next load, with no release. The choices are kept in
registry_metadata under `page_owners` ({page: bot id, or null for "no owner"}); a page with no choice kept has its
default ("default" in a save drops the choice). Every page has an owner (style guide "Where chat lives"): a chosen
owner or default that is missing, planned or archived gives way to the next in line (the page's default, then the
General Manager, which every company has), so a page has no owner only when a person chose "No owner". That page has
no chat at all. The General Manager stands in only to chat: what a page's owner may do beyond that (the Tools owner's
key guard and key-grant cards, secret_drop.py and key_grants.py) asks for the page's own owner (`fallback=False`).

  PAGES                    the pages that have an owner, with their name and default bot
  read(c)                  {page: {"bot": the owner now ("bot:<id>" or None), "name": the page's name,
                                  "choice": "default", "none" or the chosen "bot:<id>", "default": who Default gives,
                                  "own": the owner is the page's own, not the General Manager standing in}}
  owner(c, page[, fallback])  the page's owner as an actor id, or None
  save(c, actor, page, bot)  sets or clears one page's owner (bot None: no owner; "default": its default); the saved row
  check_from(refs)         a 422 unless refs.from_page, when present, is {page, label[, item {kind, id}]}, each value
                           trimmed in place (an older client's item name is dropped)
"""

import re
from typing import TYPE_CHECKING, Any, cast

from . import access as Access
from .store import H, Problem

if TYPE_CHECKING:
    import sqlite3

KEY = "page_owners"
# The bot that does each page's work, one per company page the menu and the router reach, in the menu's order (Settings
# lists them so). web/src/shell/pageOwner.ts maps every address to its key: a page with an item open (a pipeline) asks
# the item's owner first, then the page's, then the General Manager. A default names a bot a new company has where one
# fits (the built-ins: general-manager, strategy-owner, finance-owner, librarian, botops, automations-owner,
# updates-owner, tools-owner); the others (growth-manager, legal-owner, product-lead) are catalogue bots a company may
# not have hired: their pages fall back to the General Manager. A page may name a second bot in line (`then`): Tools goes
# to an IT Support Specialist a company hired when it has no Tools Owner, and only then to the General Manager.
# Rules and Risk are the Legal Owner's (Rules merged into Legal, Nov 21); the built-in Rules Owner still reads the
# documents added to Rules (backend/counsel.py), but it owns no page.
# A personal page (Calendar, Mail, Needs you, Learned, Account, Flow) has no key: its chat is Tico itself. Neither does a
# page whose chat IS the page (a teammate's page, a thread, a task, a call) or the style guide.
PAGES: dict[str, dict[str, str]] = {
    "tasks": {"name": "Tasks", "default": "general-manager"},  # a pipeline's own manager answers while one is shown
    "roadmap": {"name": "Roadmap", "default": "general-manager"},
    "updates": {"name": "Updates", "default": "updates-owner"},
    "company": {"name": "Company", "default": "general-manager"},  # Company's dashboards, its teams', the Company page
    "strategy": {"name": "Strategy", "default": "strategy-owner"},  # Strategy's other tabs: Market, Org, Brand
    "goals": {"name": "Goals", "default": "strategy-owner"},
    "products": {"name": "Products", "default": "product-lead"},
    "finance": {"name": "Finance", "default": "finance-owner"},  # with Bills (Payables)
    "growth": {"name": "Growth", "default": "growth-manager"},
    "risk": {"name": "Risk", "default": "legal-owner"},  # with Disputes
    "rules": {"name": "Rules", "default": "legal-owner"},
    "automations": {"name": "Automations", "default": "automations-owner"},  # Routines
    "listening": {"name": "Listening", "default": "automations-owner"},
    "usage": {"name": "Usage", "default": "botops"},
    "docs": {"name": "Docs", "default": "librarian"},  # Docs, a doc, and Library > Files
    "contacts": {"name": "Contacts", "default": "librarian"},
    "skills": {"name": "Skills", "default": "botops"},
    "tools": {
        "name": "Tools",
        "default": "tools-owner",
        "then": "it-support-specialist",
    },  # with its keys and inboxes: who sets up tools, keys and levels
    "messaging": {"name": "Messaging", "default": "general-manager"},
    "tags": {"name": "Tags", "default": "general-manager"},
    "settings": {"name": "Settings", "default": "botops"},
    "computers": {"name": "Computers", "default": "botops"},  # Settings > Computers, a computer's screen
    "changelog": {"name": "Changelog", "default": "updates-owner"},  # Settings > Changelog
}
# Every company has the General Manager (onboarding.complete_company): the owner of last resort, to chat with only.
FALLBACK = "general-manager"
DEFAULT = "default"  # a save's bot that drops the page's choice: the page has its default again
FROM_LIMITS = {"page": 40, "label": 80, "kind": 40, "id": 200}
# A from_page value is a short line of words: no newline or other control character (the turn shows it as quoted text).
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


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


def read(c: "sqlite3.Connection") -> dict[str, dict[str, Any]]:
    kept: dict[str, str | None] = _kept(c)
    seen: dict[str, str | None] = {}
    out: dict[str, dict[str, Any]] = {}
    for page, spec in PAGES.items():
        bot = _owner(c, page, kept, seen)
        out[page] = {
            "bot": bot,
            "name": spec["name"],
            "choice": _choice(kept, page),
            "default": _owner(c, page, kept, seen, chosen=False),
            # the page's own owner (its choice or its default), not the General Manager standing in for none
            "own": bot is not None and bot == _owner(c, page, kept, seen, fallback=False),
        }
    return out


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


def _trimmed(fields: dict[str, object]) -> None:
    """Each text value with its surrounding spaces taken off, in place: what is checked is what is stored."""
    for key, value in fields.items():
        if isinstance(value, str):
            fields[key] = value.strip()


def _text(value: object, key: str) -> bool:
    return isinstance(value, str) and 0 < len(value) <= FROM_LIMITS[key] and not _CONTROL.search(value)


def _item_ok(item: object) -> bool:
    """What was open on the page, as a pointer only: {kind, id}, no words (an older client's `name` is dropped), which
    the runner reads under its own rights, so a message naming it shows nothing a reader may not see."""
    if item is None:
        return True
    if not isinstance(item, dict):
        return False
    fields = cast("dict[str, object]", item)
    fields.pop("name", None)
    _trimmed(fields)
    return set(fields) == {"kind", "id"} and all(_text(fields.get(k), k) for k in ("kind", "id"))


def check_from(refs: dict[str, object] | None) -> None:
    """A message's `refs.from_page`, when sent: the page it was written on and what was open there."""
    given = (refs or {}).get("from_page")
    if given is None:
        return
    fields = cast("dict[str, object]", given) if isinstance(given, dict) else None
    if fields is not None:
        _trimmed(fields)
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
            "refs.from_page is {page, label} and an optional item {kind, id}, each a short line of text",
            422,
        )
