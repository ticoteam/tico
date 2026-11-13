# pyright: strict
"""Who owns a page: the one bot you chat with from it (ui/app/page-chat.ts).

A page names only its owner. The chat it opens is the viewer's ordinary direct chat with that bot, the same one as on
the bot's page; a message sent from the page carries `refs.from_page` (`check_from`), which the bot's turn reads.

One owner per page, changeable in Settings > General > Page owners or with `tico settings set pages.<page>.owner`
by the owner or an admin. A page follows its owner at the next load, with no release. The choices are kept in
registry_metadata under `page_owners` ({page: bot id, or null for "no owner"}); a page with no choice kept has its
default. A page with no owner, or whose owner is gone or archived, has no Chat button.

  PAGES                    the pages that have an owner, with their name and default bot
  read(c)                  {page: {"bot": "bot:<id>" or None, "name": the page's name}}
  owner(c, page)           the page's owner as an actor id, or None
  save(c, actor, page, bot)  sets or clears one page's owner (bot None: no owner); the saved row
  check_from(refs)         a 422 unless refs.from_page, when present, is {page, label[, item {kind, id, name}]}
"""

from typing import TYPE_CHECKING, Any, cast

from . import access as Access
from .store import H, Problem

if TYPE_CHECKING:
    import sqlite3

KEY = "page_owners"
# The bot that does each page's work (spec chat-on-every-page): Listening and Goals so far; the other pages follow.
PAGES: dict[str, dict[str, str]] = {
    "listening": {"name": "Listening", "default": "automations-owner"},
    "tools": {"name": "Tools", "default": "tools-product-owner"},
    "goals": {"name": "Goals", "default": "strategy-owner"},
}
FROM_LIMITS = {"page": 40, "label": 80, "kind": 40, "id": 200, "name": 200}


def _kept(c: "sqlite3.Connection") -> dict[str, str | None]:
    return cast("dict[str, str | None]", Access._load_json(c, KEY) or {})  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType]


def _live(c: "sqlite3.Connection", ref: str | None) -> str | None:
    """The bot's actor id when it exists and is set up (not planned, not archived); else None."""
    row = cast("dict[str, Any] | None", H.resolve_bot(c, ref) if ref else None)  # pyright: ignore[reportUnknownMemberType]
    if not row or row.get("state") in ("planned", "archived"):
        return None
    return "bot:" + str(row["id"])


def owner(c: "sqlite3.Connection", page: str) -> str | None:
    spec = PAGES.get(page)
    if not spec:
        return None
    kept = _kept(c)
    return _live(c, kept[page] if page in kept else spec["default"])


def read(c: "sqlite3.Connection") -> dict[str, dict[str, str | None]]:
    return {page: {"bot": owner(c, page), "name": spec["name"]} for page, spec in PAGES.items()}


def save(c: "sqlite3.Connection", actor: str, page: str, bot: str | None) -> dict[str, str | None]:
    if page not in PAGES:
        raise Problem("not_found", "No page " + repr(page) + "; pages with an owner: " + ", ".join(PAGES), 404)
    chosen = None
    if bot:
        chosen = _live(c, bot)
        if not chosen:
            raise Problem("not_found", "No active bot " + repr(bot), 404)
    kept = _kept(c)
    before = owner(c, page)
    Access._store(c, KEY, {**kept, page: chosen[4:] if chosen else None})  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType]
    H.event(c, actor, "page_owner.updated", page, {"before": before, "after": chosen})  # pyright: ignore[reportUnknownMemberType]
    return {"page": page, "bot": chosen, "name": PAGES[page]["name"]}


def _text(value: object, key: str) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= FROM_LIMITS[key]


def _item_ok(item: object) -> bool:
    if item is None:
        return True
    if not isinstance(item, dict):
        return False
    fields = cast("dict[str, object]", item)
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
