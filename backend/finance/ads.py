"""Ad spend settings, for a Google Ads or Meta Ads tool's page (finance admins only): whether Finance imports its
spend, from which day, which books line it books as, which stored keys hold each value, and which bot each ad account
counts for.

At most one enabled import per platform: two tools reading the same login (TIDY has a company set of keys and bots'
copies) would count the same spend twice. Saving or asking for a run rings the import worker (ad_import.py); the
routes ring it after the commit."""
import re
import sqlite3
from datetime import date

from .context import Ctx, load
from .core import dates
from .core import rights as R
from .core.errors import CONFLICT, DENIED, INVALID, NOT_FOUND, FinanceError
from .store import ads as SA
from . import ad_import as AI

Row = dict[str, object]
DENIED_TEXT = "Ad spend is for finance admins"
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _admin(c: sqlite3.Connection, viewer: str, at: str | None) -> Ctx:
    ctx = load(c, at)
    if not R.is_admin(ctx.org, viewer, ctx.rights(viewer)):
        raise FinanceError(DENIED, DENIED_TEXT)
    return ctx


def _tool(c: sqlite3.Connection, tool_id: str) -> tuple[str, str]:
    """(platform, tool name) for a Google Ads or Meta Ads tool."""
    row = c.execute("SELECT name, service FROM tools WHERE id=?", (tool_id,)).fetchone()
    platform = SA.PLATFORM_OF.get(str(row["service"])) if row is not None else None
    if row is None or platform is None:
        raise FinanceError(NOT_FOUND, "Not a Google Ads or Meta Ads tool")
    return platform, str(row["name"])


def books_lines(ctx: Ctx) -> list[Row]:
    return [{"id": a.id, "code": a.code, "name": a.name} for a in sorted(ctx.accounts.values(), key=lambda a: a.code)
            if a.type == "expense" and a.active]


def default_line(ctx: Ctx, platform: str) -> str:
    """The expense line named for advertising (one naming the platform first), else for marketing, else ""."""
    word = "google" if platform == "google_ads" else "meta|facebook"
    lines = [a for a in sorted(ctx.accounts.values(), key=lambda a: a.code) if a.type == "expense" and a.active]
    for pattern in (rf"advertis.*({word})|({word}).*advertis|({word})\s*ads", r"advertis", r"marketing"):
        for a in lines:
            if re.search(pattern, a.name, re.IGNORECASE):
                return a.id
    return ""


def _jan_first(at: str) -> str:
    return date(int(at[:4]), 1, 1).isoformat()


def _current(ctx: Ctx, tool_id: str, platform: str) -> SA.AdImport:
    """The tool's saved import, or what one would start with: off, from Jan 1, the default line and keys."""
    saved = SA.import_for(ctx.c, tool_id)
    if saved is not None:
        return saved
    return SA.AdImport(id="", tool_id=tool_id, platform=platform, enabled=False, history_from=_jan_first(ctx.at),
                       account_id=default_line(ctx, platform),
                       credentials=AI.default_mapping(ctx.c, platform, tool_id))


def _elsewhere(c: sqlite3.Connection, tool_id: str, platform: str) -> Row | None:
    other = SA.enabled_platforms(c).get(platform)
    if other is None or other.tool_id == tool_id:
        return None
    row = c.execute("SELECT name FROM tools WHERE id=?", (other.tool_id,)).fetchone()
    return {"tool_id": other.tool_id, "tool_name": str(row["name"]) if row else other.tool_id}


def view(c: sqlite3.Connection, viewer: str, tool_id: str, at: str | None = None) -> Row:
    """What the tool page's Finance card shows."""
    ctx = _admin(c, viewer, at)
    platform, name = _tool(c, tool_id)
    imp = _current(ctx, tool_id, platform)
    start, _ = dates.month_bounds(dates.day(ctx.today))
    spent = SA.account_spend(c, tool_id, start.isoformat(), ctx.today)
    options: dict[str, list[Row]] = {}
    for cand in AI.candidates(c, platform):
        options.setdefault(cand.slot, []).append({"id": cand.id, "name": cand.name})
    line = ctx.accounts.get(imp.account_id)
    return {
        "tool_id": tool_id, "tool_name": name, "platform": platform, "platform_label": SA.LABEL[platform],
        "import": {"saved": bool(imp.id), "enabled": imp.enabled, "history_from": imp.history_from,
                   "account_id": imp.account_id, "account_name": line.name if line else "",
                   "status": imp.status, "last_import_at": imp.last_import_at, "last_error": imp.last_error,
                   "run_requested": bool(imp.run_requested_at), "every_hours": int(AI.EVERY.total_seconds() // 3600)},
        "slots": [{"slot": s, "label": AI.SLOT_LABEL[s], "required": s in AI.REQUIRED[platform],
                   "credential_id": imp.credentials.get(s, ""), "options": options.get(s, [])}
                  for s in AI.SLOTS[platform]],
        "books_lines": books_lines(ctx),
        "elsewhere": _elsewhere(c, tool_id, platform),
        "month": start.isoformat()[:7], "month_label": start.strftime("%B"),
        "accounts": [{"id": a.id, "external_id": a.external_id, "name": a.name, "currency": a.currency,
                      "bot_slug": a.bot_slug or None, "active": a.active, "month_to_date": spent.get(a.id, 0)}
                     for a in SA.accounts(c, tool_id)],
    }


def configure(c: sqlite3.Connection, viewer: str, tool_id: str, *, enabled: bool, history_from: str | None,
              account_id: str | None, credentials: dict[str, str | None] | None, at: str | None = None) -> Row:
    """Save a tool's import settings; switching it on asks for a run. Refused: a second enabled import on the
    platform, a day that is not one, a line that is not an active expense line, a key that is not one the platform can
    use, or switching on without the required keys or a line."""
    ctx = _admin(c, viewer, at)
    platform, _ = _tool(c, tool_id)
    imp = _current(ctx, tool_id, platform)
    start = history_from or imp.history_from
    if not DAY.match(start):
        raise FinanceError(INVALID, "history_from is a day, YYYY-MM-DD")
    try:
        date.fromisoformat(start)
    except ValueError:
        raise FinanceError(INVALID, "history_from is a day, YYYY-MM-DD") from None
    if start > ctx.today:
        raise FinanceError(INVALID, "history_from can't be in the future")
    line = imp.account_id if account_id is None else account_id
    if line and (line not in ctx.accounts or ctx.accounts[line].type != "expense" or not ctx.accounts[line].active):
        raise FinanceError(INVALID, "Pick an active expense line")
    mapping = dict(imp.credentials)
    if credentials is not None:
        allowed = {cand.id: cand for cand in AI.candidates(c, platform)}
        for slot, cid in credentials.items():
            if slot not in AI.SLOTS[platform]:
                raise FinanceError(INVALID, f"{SA.LABEL[platform]} has no {slot}")
            if not cid:
                mapping.pop(slot, None)
            elif cid in allowed or c.execute("SELECT 1 FROM credentials WHERE id=? AND ciphertext IS NOT NULL "
                                                                    "AND source<>'finance'",
                                             (cid,)).fetchone():
                mapping[slot] = cid
            else:
                raise FinanceError(INVALID, "No such stored key")
    if enabled:
        if _elsewhere(c, tool_id, platform) is not None:
            raise FinanceError(CONFLICT, f"{SA.LABEL[platform]} spend already imports from another tool: switch "
                                         "that one off first")
        gaps = AI.missing(platform, mapping)
        if gaps:
            raise FinanceError(INVALID, "Choose the keys for " + ", ".join(AI.SLOT_LABEL[s] for s in gaps))
        if not line:
            raise FinanceError(INVALID, "Choose the books line ad spend goes to")
    changed = AI.settings_changed(imp, start) if imp.id else imp
    asked = ctx.at if enabled and (not imp.enabled or changed.last_import_at is None) else imp.run_requested_at
    SA.save_import(c, SA.AdImport(
        id=imp.id, tool_id=tool_id, platform=platform, enabled=enabled, history_from=start, account_id=line,
        credentials=mapping, status=imp.status, last_import_at=changed.last_import_at, last_error=imp.last_error,
        run_requested_at=asked if enabled else None, created_by=imp.created_by or viewer,
        created_at=imp.created_at or ctx.at), ctx.at)
    return view(c, viewer, tool_id, ctx.at)


def run_now(c: sqlite3.Connection, viewer: str, tool_id: str, at: str | None = None) -> Row:
    ctx = _admin(c, viewer, at)
    _tool(c, tool_id)
    imp = SA.import_for(c, tool_id)
    if imp is None or not imp.enabled:
        raise FinanceError(CONFLICT, "Ad spend is off for this tool")
    SA.request_run(c, imp.id, ctx.at)
    return view(c, viewer, tool_id, ctx.at)


def assign(c: sqlite3.Connection, viewer: str, ad_account_id: str, bot_slug: str | None,
           at: str | None = None) -> Row:
    """Which bot an ad account's spend counts for (None: not assigned)."""
    ctx = _admin(c, viewer, at)
    acct = SA.account(c, ad_account_id)
    if acct is None:
        raise FinanceError(NOT_FOUND, "No such ad account")
    slug = (bot_slug or "").strip()
    if slug and "bot:" + slug not in ctx.org.bots:
        raise FinanceError(INVALID, "No such bot")
    SA.set_bot(c, ad_account_id, slug or None, ctx.at)
    found = SA.account(c, ad_account_id)
    assert found is not None
    return {"id": found.id, "tool_id": found.tool_id, "external_id": found.external_id, "name": found.name,
            "currency": found.currency, "bot_slug": found.bot_slug or None, "active": found.active}
