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

from . import access as Access
from .store import H, Problem

KEY = "page_owners"
# Listening only, this release (spec page-owner-chat): the other pages follow one at a time.
PAGES = {"listening": {"name": "Listening", "default": "automations-owner"}}
FROM_LIMITS = {"page": 40, "label": 80, "kind": 40, "id": 200, "name": 200}


def _kept(c):
    return Access._load_json(c, KEY) or {}


def _live(c, ref):
    """The bot's actor id when it exists and is set up (not planned, not archived); else None."""
    row = H.resolve_bot(c, ref) if ref else None
    if not row or row.get("state") in ("planned", "archived"):
        return None
    return "bot:" + row["id"]


def owner(c, page):
    spec = PAGES.get(page)
    if not spec:
        return None
    kept = _kept(c)
    return _live(c, kept[page] if page in kept else spec["default"])


def read(c):
    return {page: {"bot": owner(c, page), "name": spec["name"]} for page, spec in PAGES.items()}


def save(c, actor, page, bot):
    if page not in PAGES:
        raise Problem("not_found", "No page " + repr(page) + "; pages with an owner: " + ", ".join(PAGES), 404)
    chosen = None
    if bot:
        chosen = _live(c, bot)
        if not chosen:
            raise Problem("not_found", "No active bot " + repr(bot), 404)
    kept = _kept(c)
    before = owner(c, page)
    Access._store(c, KEY, {**kept, page: chosen[4:] if chosen else None})
    H.event(c, actor, "page_owner.updated", page, {"before": before, "after": chosen})
    return {"page": page, "bot": chosen, "name": PAGES[page]["name"]}


def check_from(refs):
    """A message's `refs.from_page`, when sent: the page it was written on and what was open there."""
    given = (refs or {}).get("from_page")
    if given is None:
        return

    def text(value, key):
        return isinstance(value, str) and 0 < len(value.strip()) <= FROM_LIMITS[key]

    item = given.get("item") if isinstance(given, dict) else None
    ok = (
        isinstance(given, dict)
        and set(given) <= {"page", "label", "item"}
        and text(given.get("page"), "page")
        and text(given.get("label"), "label")
        and (
            item is None
            or (
                isinstance(item, dict)
                and set(item) <= {"kind", "id", "name"}
                and all(text(item.get(k), k) for k in ("kind", "id", "name"))
            )
        )
    )
    if not ok:
        raise Problem(
            "from_page",
            "refs.from_page is {page, label} and an optional item {kind, id, name}, each a short text",
            422,
        )
