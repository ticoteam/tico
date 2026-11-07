"""Platform share: an ad platform's charge (a card, bank or bill line on the platform's own vendor contact) counts for each
bot in proportion to the spend the platform reports for that bot's ad accounts over the window the charge covers.

The window of a charge: the days after the previous posted charge on the same vendor (in the book) through the charge's
entry date; with no previous charge, the 31 days ending on the charge's date. Charges are ordered by entry date, then by
the order they posted, so consecutive windows never share a day and no day's spend counts twice. A charge is a posted
P&L line whose entry is not a reversal and not reversed; a reversal takes its original's shares as they stood.

The shares: each bot's platform spend over the window (store/ads.live_spend, the accounts of the platform's enabled
import, summed by bot) against the platform's whole spend, as basis points by largest remainder (CA.allocate over
WHOLE). The spend no current bot owns (accounts with no bot, or a bot no longer a teammate) is the unattributed rest, so
the parts add up to less than WHOLE. At most MAX_BOTS bots get a share; the rest of the spend joins the rest. No spend in
the window, spend in another currency than the book's, or spend no current bot owns gives no share: the line counts as
its tags, as before (a share with no bot part has nothing to write).

Precedence (attribution.py): a person's or bot's override > a matching rule > this platform share > the line's tags.
Only a platform's own vendor contact qualifies (store/ads.platform_contact), never a descriptor or a lookalike name, and
only for a platform the book imports. Tico-side only: nothing here writes to a provider's books.
"""

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from .context import Ctx
from .core import attribution as CA
from .store import ads as SAD
from .store import ap as SAP
from .store import attribution as ATT

MAX_BOTS = CA.MAX_PARTS - 1  # the rest of the parts a line can have, with the rest of the spend as one more
WINDOW_DAYS = 31  # a platform's first charge covers the days ending on its date, this many

_PNL = "a.type IN ('income','expense')"


def _charges(
    c: Any,
    book_id: str,
    keys: Sequence[str],
    cond: str = "",
    args: Sequence[object] = (),
    order: str = "",
    limit: int = 0,
) -> list[Any]:
    """The posted charge lines on a vendor's keys: (line id, entry date, rowid), in the order given."""
    if not keys:
        return []
    marks = ",".join("?" * len(keys))
    sql = (
        "SELECT l.id, l.entry_date, l.rowid FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id "
        "JOIN fin_accounts a ON a.id=l.account_id WHERE l.book_id=? AND l.contact_id IN (" + marks + ") "
        f"AND {_PNL} AND e.status='posted' AND e.reverses_id IS NULL"
    )
    if cond:
        sql += " AND " + cond
    if order:
        sql += " ORDER BY " + order
    if limit:
        sql += f" LIMIT {limit}"
    return list(c.execute(sql, [book_id, *keys, *args]).fetchall())


def contact_id(ctx: Ctx, platform: str) -> str:
    """The platform's own vendor contact in this book ("" when it has none)."""
    return SAD.platform_contact(ctx.c, ctx.book.id, platform, mirror=ctx.book.mode == "mirror")


def contact_keys(ctx: Ctx, platform: str) -> list[str]:
    """The keys the platform's own vendor contact goes by ([] when the book has none)."""
    contact = contact_id(ctx, platform)
    return SAP.vendor_keys(ctx.c, ctx.book.id, contact) if contact else []


def _contacts(ctx: Ctx) -> dict[str, str]:
    """{vendor contact id: platform} for the platforms this book imports, read once per call."""
    if "platform:contacts" not in ctx.memo:
        enabled = {i.platform for i in SAD.enabled_imports(ctx.c).values()}
        found: dict[str, str] = {}
        for p in SAD.PLATFORMS:
            contact = contact_id(ctx, p) if p in enabled else ""
            if contact:
                found[contact] = p
        ctx.memo["platform:contacts"] = found
    cached: dict[str, str] = ctx.memo["platform:contacts"]
    return cached


def platform_of(ctx: Ctx, keys: Sequence[str]) -> str:
    """The ad platform whose own vendor contact one of these vendor keys is ("" when none)."""
    contacts = _contacts(ctx)
    return next((contacts[k] for k in keys if k in contacts), "")


def _before(ctx: Ctx, keys: Sequence[str], day: str, rid: int | None) -> Any:
    """The charge just before (day, rowid) in charge order; with no rowid, the latest charge on or before `day`."""
    if rid is None:
        cond, args = "l.entry_date<=?", [day]
    else:
        cond, args = "(l.entry_date<? OR (l.entry_date=? AND l.rowid<?))", [day, day, rid]
    found = _charges(ctx.c, ctx.book.id, keys, cond, args, "l.entry_date DESC, l.rowid DESC", 1)
    return found[0] if found else None


def window(ctx: Ctx, keys: Sequence[str], day: str, rid: int | None = None) -> tuple[str, str]:
    """The days a charge on `day` (rowid `rid`, None for one not yet posted) covers, [first, last] ISO days. An empty
    window (first after last) when the charge before it is on the same day."""
    prev = _before(ctx, keys, day, rid)
    last = date.fromisoformat(day)
    first = date.fromisoformat(str(prev[1])) + timedelta(days=1) if prev else last - timedelta(days=WINDOW_DAYS - 1)
    return first.isoformat(), last.isoformat()


def successor(ctx: Ctx, keys: Sequence[str], day: str, rid: int | None = None) -> str:
    """The charge right after (day, rowid) on these keys, "" when none."""
    if rid is None:
        cond, args = "l.entry_date>?", [day]
    else:
        cond, args = "(l.entry_date>? OR (l.entry_date=? AND l.rowid>?))", [day, day, rid]
    found = _charges(ctx.c, ctx.book.id, keys, cond, args, "l.entry_date, l.rowid", 1)
    return str(found[0][0]) if found else ""


def charges_from(ctx: Ctx, keys: Sequence[str], day: str) -> list[tuple[str, str, int]]:
    """Every charge on these keys dated on or after `day`, in charge order: (line id, entry date, rowid)."""
    return [
        (str(r[0]), str(r[1]), int(r[2]))
        for r in _charges(ctx.c, ctx.book.id, keys, "l.entry_date>=?", [day], "l.entry_date, l.rowid")
    ]


def shares(ctx: Ctx, platform: str, first: str, last: str) -> tuple[CA.Split, ...]:
    """The bot shares of a platform's spend over [first, last] (module docstring). () when there is nothing to split."""
    if first > last:
        return ()
    rows = [r for r in SAD.live_spend(ctx.c, first, last) if r.platform == platform and r.amount]
    if not rows or any(r.currency != ctx.book.base_currency for r in rows):
        return ()
    total = sum(r.amount for r in rows)
    if total <= 0:
        return ()
    by_bot: dict[str, int] = {}
    for r in rows:
        mate = f"bot:{r.bot_slug}" if r.bot_slug else ""
        if mate and mate in ctx.org.bots:
            by_bot[mate] = by_bot.get(mate, 0) + r.amount
    kept = sorted(((a, m) for m, a in by_bot.items() if a > 0), key=lambda x: (-x[0], x[1]))[:MAX_BOTS]
    if not kept:
        return ()
    rest = total - sum(a for a, _ in kept)
    bps = CA.allocate(CA.WHOLE, [a for a, _ in kept] + [rest])
    return tuple(CA.Split(m, "", share) for (_, m), share in zip(kept, bps, strict=False) if share > 0)


def claim_at_posting(ctx: Ctx, keys: Sequence[str], day: str) -> Any:
    """The platform share a charge posting on `day` on these keys takes (an ATT.Claim), or None."""
    platform = platform_of(ctx, keys)
    if not platform:
        return None
    first, last = window(ctx, keys, day)
    splits = shares(ctx, platform, first, last)
    return ATT.Claim(splits, "platform") if splits else None


def claim_for_line(ctx: Ctx, line_id: str) -> Any:
    """The platform share a posted charge line takes now (an ATT.Claim), or None when it is not a platform charge or
    its window has no spend to split."""
    row = ctx.c.execute(
        "SELECT contact_id, entry_date, rowid FROM fin_lines WHERE id=? AND book_id=?", (line_id, ctx.book.id)
    ).fetchone()
    if row is None or not row[0]:
        return None
    keys = SAP.vendor_keys(ctx.c, ctx.book.id, str(row[0]))
    if not _charges(ctx.c, ctx.book.id, keys, "l.id=?", [line_id], limit=1):
        return None
    platform = platform_of(ctx, keys)
    if not platform:
        return None
    first, last = window(ctx, keys, str(row[1]), int(row[2]))
    splits = shares(ctx, platform, first, last)
    return ATT.Claim(splits, "platform") if splits else None


__all__ = [
    "MAX_BOTS",
    "WINDOW_DAYS",
    "charges_from",
    "claim_at_posting",
    "claim_for_line",
    "contact_id",
    "contact_keys",
    "platform_of",
    "shares",
    "successor",
    "window",
]
