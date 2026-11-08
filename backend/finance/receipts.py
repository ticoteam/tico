"""Receipts: a Library file with the amount, date and vendor read from it, and its match to a bank or card row.

A receipt lands in Finance as an unmatched upload (fin_attachments subject `unmatched`) or is filed by hand to a subject
(then `attached`). Its read comes from the Library's indexed text for the file's current version (file_text): a
deterministic parser pulls the total, the first full date and the first line's vendor. No model is called. A PDF or an
image has no indexed text, so its read waits until a bot or person who has looked at it sets one (receipt_read).

Matching: a read receipt is compared with the settled debits (bank and card rows: not pending, not ignored or covered,
no file attached yet) dated within WINDOW_DAYS of its read date. A row scores 0.6 when its amount is exact (0.4 within 1%,
or when the row is the receipt's total plus a tip of up to 30%), 0.3 when its vendor is the same merchant, and up to 0.1
for the date (less the further apart). The best candidate becomes a suggestion when it scores at least MIN_SCORE and is at
least LEAD ahead of the runner-up. A feed row never holds two suggestions: the higher confidence wins, and the other receipt
is matched again with its next candidate. Matching runs when a receipt is read or filed, when a settled bank or card row
arrives (banking.ingest, which every sync path reaches), and in the hourly pass (jobs.watch), which also reads receipts
whose text the index filled in later.

Auto-attach is off: the book setting `receipt_auto_attach_at` (fin_settings) is NULL. When it holds a number, a suggestion
at or above it is attached at once, by the same filing an accept makes.

Actions (never gated, audited; receipt_match is undoable): receipt_read {receipt_id, amount, date, vendor?} sets the read;
receipt_match {receipt_id, decision: accept|reject, feed_id?} attaches the file to the suggested row (or to feed_id) or
refuses the suggestion; receipt_match_undo {receipt_id} removes what an accept attached and restores the earlier state.
Whoever may file a receipt may act on it: finance.book, the uploader, or the holder of the card on the row. Tico never
writes to QuickBooks or Xero from here (no outbox rows).

Reads: `read` (GET /api/v2/finance/receipts?status=), the hub search kind `receipt` (`search`), and `brief_lines` (the
briefing's "Needs you" line: card charges with no receipt, and the matches to check). Who sees a receipt is
visible.receipts_visible.

Imports stay light on purpose: banking and documents import this module, and handlers, banking, tab and visibility import
those, so the handlers, visibility and banking names are imported inside the functions that use them.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from .context import Ctx, load
from .core import kinds as K
from .core import rights as R
from .core import validate as V
from .core.errors import CONFLICT, NOT_FOUND, FinanceError, denied, invalid
from .core.merchant import merchant_key
from .core.org import SYSTEM
from .store import ap as SAP
from .store import banking as SK
from .store import ledger as SL
from .store import receipts as RS

if TYPE_CHECKING:
    from .handlers import Plan

Row = dict[str, Any]
Result = dict[str, object]

RECEIPT_DAYS = 90  # how far back a receipt is matched, and how far back a card charge with no receipt is listed
WINDOW_DAYS = 5  # a bank or card row is a candidate this many days either side of the receipt's date
MIN_SCORE = 0.6
LEAD = 0.1  # the best candidate is this far ahead of the runner-up
AUTO_KEY = "receipt_auto_attach_at"  # fin_settings: NULL (the default) attaches nothing on its own
STATUSES = ("unread", "suggested", "unmatched", "attached", "rejected")
CHUNK = 200  # rows read per page of a listing or a pass
SCAN_MAX = 2000  # rows a listing looks at before it answers with a cursor
CARD_LIST = 50  # card charges the briefing names or counts


# ------------------------------------------------------------------------------------------------ reading a receipt
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATES = (
    (re.compile(r"\b(20\d\d)-(\d{2})-(\d{2})\b"), "ymd"),
    (re.compile(rf"\b{_MON}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(20\d\d)\b", re.I), "mdy"),
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON}\.?,?\s+(20\d\d)\b", re.I), "dmy"),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d\d)\b"), "us"),
)
_NUM = r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
_CUR = r"(?:USD|EUR|GBP)?\s*(?P<cur>[$€£])?\s*(?P<num>" + _NUM + r")"
# A total is the amount right after its label, with a currency sign or cents (so "Total items: 3" is not an amount).
_TOTALS = re.compile(
    r"\b(?:grand total|total due|total paid|total charged|total|amount paid|amount charged|charged)\b[\s:]*" + _CUR,
    re.I,
)
_DUE = re.compile(r"\bamount due\b[\s:]*" + _CUR, re.I)
_MONEY = re.compile(r"(?P<cur>[$€£])\s?(?P<num>" + _NUM + r")|(?<![\w.])(?P<dec>\d{1,3}(?:,\d{3})*\.\d{2})(?!\d)")
_LEAD = re.compile(r"^\s*(?:your\s+)?(?:receipt|invoice|bill|order|payment)\s+(?:from|for)\s+", re.I)
_SPLIT = re.compile(r"\s{2,}|\s[|•·]\s|:\s|\s-\s")
_STOP = re.compile(r"\s(?:receipt|invoice|order|total|date|amount|paid|bill)\b|\s[#№]", re.I)


@dataclass(frozen=True)
class Read:
    """What a receipt says: its total in cents, its date (ISO), its vendor as shown and as a merchant key."""

    amount: int | None = None
    date: str | None = None
    vendor: str = ""
    vendor_key: str = ""

    @property
    def complete(self) -> bool:
        """Enough to match on: a total and a date."""
        return self.amount is not None and self.date is not None


def _cents(num: str) -> int:
    return int((Decimal(num.replace(",", "")) * 100).to_integral_value(rounding=ROUND_HALF_UP))


def amount_of(text: str) -> int | None:
    """The receipt's total in cents: the amount after the last "total" (or "amount paid", "amount due") label, else the
    largest money amount in the text. Negative amounts (refunds, credits) are not totals."""
    for pattern in (_TOTALS, _DUE):
        found: list[int] = []
        for m in pattern.finditer(text):
            num = m.group("num")
            if num.startswith("-") or not (m.group("cur") or "." in num):
                continue
            if _cents(num) > 0:
                found.append(_cents(num))
        if found:
            return found[-1]
    largest = 0
    for m in _MONEY.finditer(text):
        num = m.group("num") or m.group("dec") or ""
        if num and not num.startswith("-"):
            largest = max(largest, _cents(num))
    return largest or None


def date_of(text: str) -> str | None:
    """The first full date in the text (a day with a four-digit year), as an ISO day."""
    found: list[tuple[int, str]] = []
    for pattern, shape in _DATES:
        for m in pattern.finditer(text):
            try:
                if shape == "ymd":
                    day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                elif shape == "mdy":
                    day = date(int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
                elif shape == "dmy":
                    day = date(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
                else:  # US month/day/year
                    day = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
            except (KeyError, ValueError):
                continue
            found.append((m.start(), day.isoformat()))
    return min(found)[1] if found else None


def vendor_of(text: str) -> str:
    """The vendor's name: the first non-empty line, less a "Receipt from" lead and whatever follows the name (a number, a
    date, "Total"), at most six words. A flattened page (one line) gives the name it starts with."""
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    head = _SPLIT.split(_LEAD.sub("", first), maxsplit=1)[0]
    head = _STOP.split(head, maxsplit=1)[0].strip(" .,-:|")
    return " ".join(head.split()[:6])[:60]


def parse(text: str) -> Read:
    """Read a receipt's text: the total, the first full date and the vendor. Pure; a text with none of them reads as an
    empty Read."""
    vendor = vendor_of(text)
    return Read(amount_of(text), date_of(text), vendor, merchant_key(vendor) if vendor else "")


def _from_library(c: sqlite3.Connection, file_id: str) -> Read | None:
    got = RS.library_text(c, file_id)
    if got is None or not got[1].strip():
        return None
    return parse(got[1])


# ------------------------------------------------------------------------------------------------ matching
def _amount_points(read_cents: int, charge: int) -> float:
    if charge == read_cents:
        return 0.6
    if abs(charge - read_cents) * 100 <= read_cents:  # within 1%
        return 0.4
    if read_cents < charge <= read_cents * 13 // 10:  # the receipt's total and a tip on top of it
        return 0.4
    return 0.0


def _vendor_points(read_key: str, feed: Mapping[str, Any]) -> float:
    key = str(feed.get("vendor_key") or "") or merchant_key(str(feed.get("counterparty") or ""))
    if not read_key or not key:
        return 0.0
    short, long_ = sorted((read_key, key), key=len)
    return 0.3 if read_key == key or (len(short) >= 4 and short in long_) else 0.0


def score_of(read_cents: int, read_key: str, read_day: str, feed: Mapping[str, Any]) -> float:
    """How well a bank or card row matches a receipt (0 when it does not: outside the window, or neither amount nor
    vendor agrees)."""
    days = abs((date.fromisoformat(str(feed["txn_date"])) - date.fromisoformat(read_day)).days)
    if days > WINDOW_DAYS:
        return 0.0
    amount = _amount_points(read_cents, -int(feed["amount"]))
    vendor = _vendor_points(read_key, feed)
    if amount == 0 and vendor == 0:
        return 0.0
    return round(amount + vendor + 0.1 * (1 - days / WINDOW_DAYS), 2)


def _window(center: date) -> tuple[str, str]:
    return (center - timedelta(days=WINDOW_DAYS)).isoformat(), (center + timedelta(days=WINDOW_DAYS)).isoformat()


def _scored(c: sqlite3.Connection, book_id: str, row: Row) -> list[tuple[float, str]]:
    """Every candidate row a receipt could match, best first (score, then feed id), taken or not."""
    if row.get("read_amount") is None or not row.get("read_date"):
        return []
    day = str(row["read_date"])
    first, last = _window(date.fromisoformat(day))
    out: list[tuple[float, str]] = []
    for feed in RS.candidates(c, book_id, first, last):
        points = score_of(int(row["read_amount"]), str(row.get("read_vendor_key") or ""), day, feed)
        if points > 0:
            out.append((points, str(feed["id"])))
    out.sort(key=lambda x: (-x[0], x[1]))
    return out


def _qualify(cands: Sequence[tuple[float, str]]) -> tuple[float, str] | None:
    """The one candidate a receipt may be suggested on: enough points, and clearly ahead of the next one."""
    if not cands or cands[0][0] < MIN_SCORE:
        return None
    if len(cands) > 1 and cands[0][0] - cands[1][0] < LEAD - 1e-9:
        return None
    return cands[0]


def _threshold(c: sqlite3.Connection) -> float | None:
    value = SL.setting(c, AUTO_KEY)
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _closure(c: sqlite3.Connection, book_id: str, seeds: Sequence[Row], exclude: frozenset[str]) -> list[Row]:
    """The seed receipts, plus every open receipt suggested on a row one of them could match: the ones it competes with."""
    members: dict[str, Row] = {}
    frontier: list[Row] = []
    for r in seeds:
        rid = str(r["id"])
        if rid not in exclude and rid not in members:
            members[rid] = r
            frontier.append(r)
    while frontier:
        nxt: list[Row] = []
        for r in frontier:
            feeds = [fid for _, fid in _scored(c, book_id, r)]
            for holder in RS.holding(c, book_id, feeds):
                hid = str(holder["id"])
                if hid not in members and hid not in exclude:
                    members[hid] = holder
                    nxt.append(holder)
        frontier = nxt
    return list(members.values())


def _settle(
    c: sqlite3.Connection, book_id: str, rows: Sequence[Row], at: str, no_auto: frozenset[str] = frozenset()
) -> int:
    """Give each receipt its match: greedily, the highest-scoring qualifying pair first, a row taken once. A receipt in
    `no_auto` is never attached on its own. Writes only the receipts whose status, match or confidence moved. How many."""
    threshold = _threshold(c)
    scored: dict[str, list[tuple[float, str]]] = {}
    for r in rows:
        refused = set(RS.rejected(r))
        scored[str(r["id"])] = [x for x in _scored(c, book_id, r) if x[1] not in refused]
    taken: set[str] = set()
    picks: dict[str, tuple[str, float]] = {}
    while True:
        best: tuple[float, str, str] | None = None
        for rid in sorted(scored):
            if rid in picks:
                continue
            q = _qualify([x for x in scored[rid] if x[1] not in taken])
            if q is not None and (best is None or q[0] > best[0] or (q[0] == best[0] and rid < best[1])):
                best = (q[0], rid, q[1])
        if best is None:
            break
        points, rid, feed_id = best
        picks[rid] = (feed_id, points)
        taken.add(feed_id)
    moved = 0
    for r in rows:
        rid = str(r["id"])
        if rid in picks:
            feed_id, conf = picks[rid]
            if threshold is not None and conf >= threshold and rid not in no_auto:
                file_id = str(r["file_id"])
                made = SAP.attach(c, book_id, "feed", feed_id, [file_id], SYSTEM, at)
                SAP.drop_unmatched(c, book_id, [file_id])
                new: Row = {
                    "status": "attached",
                    "match_feed_id": feed_id,
                    "confidence": conf,
                    "attachment_id": made[0] if made else None,
                    "prior_status": r["status"],
                    "prior_feed_id": r["match_feed_id"],
                    "prior_confidence": r["confidence"],
                    "decided_at": at,
                    "decided_by": SYSTEM,
                }
            else:
                new = {"status": "suggested", "match_feed_id": feed_id, "confidence": conf}
        else:
            new = {
                "status": "rejected" if r["status"] == "rejected" else "unmatched",
                "match_feed_id": None,
                "confidence": None,
            }
        if any(r.get(k) != v for k, v in new.items()):
            RS.update(c, rid, {**new, "updated_at": at})
            moved += 1
    return moved


def _refresh(
    c: sqlite3.Connection,
    book_id: str,
    at: str,
    seeds: Sequence[Row],
    exclude: frozenset[str] = frozenset(),
    no_auto: frozenset[str] = frozenset(),
) -> int:
    """Match again the open receipts among `seeds` and the ones they compete with. How many moved."""
    live = [r for r in seeds if str(r["id"]) not in exclude and r["status"] in RS.OPEN and r.get("read_date")]
    if not live:
        return 0
    return _settle(c, book_id, _closure(c, book_id, live, exclude), at, no_auto)


def _around(
    ctx: Ctx, feed_id: str, exclude: frozenset[str] = frozenset(), no_auto: frozenset[str] = frozenset()
) -> int:
    """A feed row changed (filed, freed, refused or newly settled): the open receipts dated near it, and the ones suggested
    on it, are matched again."""
    row = ctx.c.execute("SELECT txn_date FROM fin_feed_txns WHERE id=?", (feed_id,)).fetchone()
    if row is None:
        return 0
    first, last = _window(date.fromisoformat(str(row[0])))
    seeds = RS.open_near(ctx.c, ctx.book.id, first, last) + RS.holding(ctx.c, ctx.book.id, [feed_id])
    return _refresh(ctx.c, ctx.book.id, ctx.at, seeds, exclude, no_auto)


def _fresh(c: sqlite3.Connection, receipt_id: str) -> Row:
    row = RS.get(c, receipt_id)
    if row is None:
        raise FinanceError(NOT_FOUND, "no such receipt")
    return row


def _new_row(book_id: str, file_id: str, by: str, at: str, fields: Mapping[str, Any]) -> Row:
    return {
        "id": SL.new_id(),
        "book_id": book_id,
        "file_id": file_id,
        "read_amount": None,
        "read_date": None,
        "read_vendor_name": "",
        "read_vendor_key": "",
        "read_by": "",
        "match_feed_id": None,
        "confidence": None,
        "status": "unread",
        "rejected_feed_ids": "[]",
        "attachment_id": None,
        "prior_status": None,
        "prior_feed_id": None,
        "prior_confidence": None,
        "created_by": by,
        "decided_at": None,
        "decided_by": None,
        "created_at": at,
        "updated_at": at,
        **fields,
    }


def _read_fields(c: sqlite3.Connection, file_id: str) -> Row:
    """The read columns and status the file's indexed text gives (unread while it gives no total or date)."""
    read = _from_library(c, file_id)
    if read is None:
        return {
            "status": "unread",
            "read_amount": None,
            "read_date": None,
            "read_vendor_name": "",
            "read_vendor_key": "",
            "read_by": "",
        }
    return {
        "status": "unmatched" if read.complete else "unread",
        "read_amount": read.amount,
        "read_date": read.date,
        "read_vendor_name": read.vendor,
        "read_vendor_key": read.vendor_key,
        "read_by": "text",
    }


def _cleared() -> Row:
    """What a filing or an undo takes off a receipt's match: its suggestion, its attachment and the undo state."""
    return {
        "match_feed_id": None,
        "confidence": None,
        "attachment_id": None,
        "prior_status": None,
        "prior_feed_id": None,
        "prior_confidence": None,
        "decided_at": None,
        "decided_by": None,
    }


# ------------------------------------------------------------------------------------------------ hooks
def filed(ctx: Ctx, who: str, kind: str, subject: str, file_ids: Sequence[str]) -> None:
    """Called by documents.attach once the files are filed. An unmatched upload becomes a receipt to read and match; a file
    filed to a subject is `attached`. A feed row that a receipt was suggested on, or that is now filed to, gives its
    candidates a fresh match."""
    c, book = ctx.c, ctx.book.id
    freed: list[str] = []
    for fid in file_ids:
        row = RS.by_file(c, book, fid)
        if row is not None and row["match_feed_id"]:
            freed.append(str(row["match_feed_id"]))
        if kind == "unmatched":
            if row is None:
                RS.insert(c, _new_row(book, fid, who, ctx.at, _read_fields(c, fid)))
            elif row["status"] == "attached":  # uploaded again after it was filed: a new unmatched receipt
                RS.update(c, str(row["id"]), {**_read_fields(c, fid), **_cleared(), "updated_at": ctx.at})
            continue
        filing: Row = {
            "status": "attached",
            "match_feed_id": subject if kind == "feed" else None,
            "decided_at": ctx.at,
            "decided_by": who,
        }
        if row is None:
            RS.insert(c, _new_row(book, fid, who, ctx.at, {**_read_fields(c, fid), **filing}))
        else:
            RS.update(c, str(row["id"]), {**_cleared(), **filing, "updated_at": ctx.at})
    targets = list(dict.fromkeys([*freed, *([subject] if kind == "feed" and subject else [])]))
    for feed_id in targets:
        _around(ctx, feed_id)
    fresh = [r for r in (RS.by_file(c, book, fid) for fid in file_ids) if r is not None]
    _refresh(c, book, ctx.at, [r for r in fresh if r["status"] == "unmatched"])


def feed_changed(ctx: Ctx, feed_id: str) -> None:
    """Called by banking.ingest when a settled bank or card row arrives (or a pending one settles)."""
    _around(ctx, feed_id)


def sweep(ctx: Ctx) -> int:
    """The hourly pass: a receipt row for each unmatched upload that has none, a read for each unread receipt whose text the
    index now holds, and a fresh match for the open receipts read in the last RECEIPT_DAYS (a suggestion that no longer
    qualifies is dropped). How many receipts it added, read or moved."""
    c, book = ctx.c, ctx.book.id
    n = 0
    for up in RS.unmatched_files(c, book):
        fid = str(up["file_id"])
        RS.insert(c, _new_row(book, fid, str(up["created_by"]), str(up["created_at"]), _read_fields(c, fid)))
        n += 1
    for r in RS.unread(c, book, CHUNK):
        fields = _read_fields(c, str(r["file_id"]))
        if any(fields[k] != r[k] for k in fields):
            RS.update(c, str(r["id"]), {**fields, "updated_at": ctx.at})
            n += 1
    since = (date.fromisoformat(ctx.today) - timedelta(days=RECEIPT_DAYS)).isoformat()
    return n + _refresh(c, book, ctx.at, RS.open_read(c, book, since))


# ------------------------------------------------------------------------------------------------ who may act
def _receipt(ctx: Ctx, who: str, receipt_id: str) -> Row:
    from .visible import receipts_visible

    row = RS.get(ctx.c, receipt_id)
    if row is None or str(row["book_id"]) != ctx.book.id or receipt_id not in receipts_visible(ctx, who, [row]):
        raise FinanceError(NOT_FOUND, "no such receipt in your view")
    return row


def _feed(ctx: Ctx, who: str, feed_id: str) -> Row:
    from .banking import seen

    row = ctx.c.execute(
        "SELECT f.*, m.book_id AS book_id FROM fin_feed_txns f JOIN fin_money_accounts m ON m.id=f.money_account_id "
        "WHERE f.id=?",
        (feed_id,),
    ).fetchone()
    if row is None or str(row["book_id"]) != ctx.book.id or not seen(ctx, who, row):
        raise FinanceError(NOT_FOUND, "no such feed row in your view")
    return dict(row)


def _may_file(ctx: Ctx, who: str, row: Row, feed: Row) -> bool:
    """Whoever may file the receipt today: finance.book, its uploader, or the holder of the card on the row."""
    if R.has(ctx.org, who, R.BOOK, ctx.rights(who)) or str(row["created_by"]) == who:
        return True
    card_id = str(feed.get("card_id") or "")
    card = SK.get(ctx.c, "fin_cards", card_id) if card_id else None
    return card is not None and str(card["holder"]) == who


def _money(cents: int | None) -> str:
    return "" if cents is None else f"{abs(cents) / 100:,.2f}"


def _title(c: sqlite3.Connection, row: Row) -> str:
    return RS.file_titles(c, [str(row["file_id"])]).get(str(row["file_id"])) or "receipt"


# ------------------------------------------------------------------------------------------------ actions
def receipt_read(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Set a receipt's amount, date and vendor, read from the file by whoever looked at it. Matching runs right after."""
    from .handlers import Plan

    V.only(p, ("receipt_id", "amount", "date", "vendor"))
    rid = V.text(p, "receipt_id", 80)
    amount = V.amount(p, "amount")
    day = V.day(p, "date")
    vendor = V.opt_text(p, "vendor", 80) or ""
    if amount <= 0:
        raise invalid("the amount is more than zero")
    row = _receipt(ctx, who, rid)

    def apply(aid: str) -> Result:
        fields: Row = {
            "read_amount": amount,
            "read_date": day,
            "read_by": who,
            "status": "unmatched" if row["status"] == "unread" else row["status"],
            "updated_at": ctx.at,
        }
        if vendor:
            fields |= {"read_vendor_name": vendor, "read_vendor_key": merchant_key(vendor)}
        RS.update(ctx.c, rid, fields)
        _refresh(ctx.c, ctx.book.id, ctx.at, [_fresh(ctx.c, rid)])
        now = _fresh(ctx.c, rid)
        return {"receipt_id": rid, "status": str(now["status"]), "feed_id": now["match_feed_id"]}

    return Plan(K.ALLOW, f"Read {_title(ctx.c, row)}: {_money(amount)} on {day}", apply)


def _accept(ctx: Ctx, who: str, row: Row, named: str) -> Plan:
    from .handlers import Plan

    rid = str(row["id"])
    book = ctx.book.id
    target = named or str(row["match_feed_id"] or "")
    if not target:
        raise invalid("nothing is suggested on that receipt: name a feed_id")
    if row["status"] == "attached":
        raise FinanceError(CONFLICT, "that receipt is already attached")
    feed = _feed(ctx, who, target)
    if not _may_file(ctx, who, row, feed):
        raise denied("filing that receipt needs finance.book, or it is yours or on your card")

    def apply(aid: str) -> Result:
        file_id = str(row["file_id"])
        made = SAP.attach(ctx.c, book, "feed", target, [file_id], who, ctx.at)
        SAP.drop_unmatched(ctx.c, book, [file_id])
        RS.update(
            ctx.c,
            rid,
            {
                "status": "attached",
                "match_feed_id": target,
                "confidence": row["confidence"] if target == row["match_feed_id"] else None,
                "attachment_id": made[0] if made else None,
                "prior_status": row["status"],
                "prior_feed_id": row["match_feed_id"],
                "prior_confidence": row["confidence"],
                "decided_at": ctx.at,
                "decided_by": who,
                "updated_at": ctx.at,
            },
        )
        _around(ctx, target)
        return {"receipt_id": rid, "status": "attached", "feed_id": target, "attachment_ids": made}

    return Plan(K.ALLOW, f"Attach {_title(ctx.c, row)} to the bank or card row {target}", apply)


def _refuse(ctx: Ctx, who: str, row: Row, named: str) -> Plan:
    from .handlers import Plan

    rid = str(row["id"])
    if not row["match_feed_id"]:
        raise FinanceError(CONFLICT, "nothing is suggested on that receipt")
    target = str(row["match_feed_id"])
    if named and named != target:
        raise invalid("the suggestion on that receipt is another row")
    if not _may_file(ctx, who, row, _feed(ctx, who, target)):
        raise denied("refusing that match needs finance.book, or it is yours or on your card")

    def apply(aid: str) -> Result:
        refused = list(dict.fromkeys([*RS.rejected(row), target]))
        RS.update(
            ctx.c,
            rid,
            {
                "status": "rejected",
                "match_feed_id": None,
                "confidence": None,
                "rejected_feed_ids": json.dumps(refused),
                "decided_at": ctx.at,
                "decided_by": who,
                "updated_at": ctx.at,
            },
        )
        _around(ctx, target)
        return {"receipt_id": rid, "status": "rejected", "feed_id": None}

    return Plan(K.ALLOW, f"Refuse the suggested match for {_title(ctx.c, row)}", apply)


def receipt_match(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Accept the receipt's suggested row (or another row the asker may see), or refuse the suggestion. An accept files the
    file to the row through the same filing a hand filing makes, so the Watch findings move with it."""
    V.only(p, ("receipt_id", "decision", "feed_id"))
    rid = V.text(p, "receipt_id", 80)
    decision = V.choice(p, "decision", ("accept", "reject"))
    named = V.opt_text(p, "feed_id", 80) or ""
    row = _receipt(ctx, who, rid)
    if decision == "accept":
        return _accept(ctx, who, row, named)
    return _refuse(ctx, who, row, named)


def receipt_match_undo(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    """Put back what an accept changed: the attachment it made goes, the file is unmatched again, and the receipt is back in
    the state it had before (its suggestion, or unmatched). The restored state is not attached again on its own."""
    from .handlers import Plan

    V.only(p, ("receipt_id",))
    rid = V.text(p, "receipt_id", 80)
    row = _receipt(ctx, who, rid)
    if row["status"] != "attached" or not row["prior_status"] or not row["match_feed_id"]:
        raise FinanceError(CONFLICT, "nothing to undo on that receipt")
    feed_id = str(row["match_feed_id"])
    if not _may_file(ctx, who, row, _feed(ctx, who, feed_id)):
        raise denied("undoing that match needs finance.book, or it is yours or on your card")
    book = ctx.book.id

    def apply(aid: str) -> Result:
        file_id = str(row["file_id"])
        if row["attachment_id"]:
            ctx.c.execute("DELETE FROM fin_attachments WHERE id=? AND book_id=?", (row["attachment_id"], book))
        if not ctx.c.execute(
            "SELECT 1 FROM fin_attachments WHERE book_id=? AND subject_kind='unmatched' AND file_id=?", (book, file_id)
        ).fetchone():
            SAP.attach(ctx.c, book, "unmatched", "", [file_id], who, ctx.at)
        RS.update(
            ctx.c,
            rid,
            {
                "status": row["prior_status"],
                "match_feed_id": row["prior_feed_id"],
                "confidence": row["prior_confidence"],
                "attachment_id": None,
                "prior_status": None,
                "prior_feed_id": None,
                "prior_confidence": None,
                "decided_at": ctx.at,
                "decided_by": who,
                "updated_at": ctx.at,
            },
        )
        _around(ctx, feed_id, no_auto=frozenset({rid}))
        now = _fresh(ctx.c, rid)
        return {"receipt_id": rid, "status": str(now["status"]), "feed_id": now["match_feed_id"]}

    return Plan(K.ALLOW, f"Undo the match of {_title(ctx.c, row)}", apply)


PLANNERS: dict[str, Any] = {
    "receipt_read": receipt_read,
    "receipt_match": receipt_match,
    "receipt_match_undo": receipt_match_undo,
}


# ------------------------------------------------------------------------------------------------ reads
def _label(row: Mapping[str, Any]) -> str:
    return str(row.get("counterparty") or row.get("description") or "Card charge").strip()[:30]


def _day_label(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.strftime('%b')} {d.day}"


def brief_lines(c: sqlite3.Connection, reader: str, at: str) -> list[str]:
    """The briefing's receipt line for a reader: "Needs you: 2 receipts missing (Canva Nov 1, Later Nov 3) · 3 matches to
    check." The reader's card charges from the last RECEIPT_DAYS with no receipt (oldest first, two named, then "+n"), and
    the suggested matches the reader sees. Nothing when both are zero."""
    from . import tab
    from .visible import receipts_visible

    ctx = load(c, at)
    view = ctx.view(reader)
    money_ids, card_ids = tab.feed_scope(ctx, view)
    since = (date.fromisoformat(ctx.today) - timedelta(days=RECEIPT_DAYS)).isoformat()
    raw = SAP.card_rows_without_receipt(c, money_ids, card_ids, since, CARD_LIST)
    hidden = tab.private_feed(ctx, view, raw)
    missing = sorted(
        (dict(r) for r in raw if str(r["id"]) not in hidden), key=lambda r: (str(r["txn_date"]), str(r["id"]))
    )
    matches = len(receipts_visible(ctx, reader, RS.suggested(c, ctx.book.id, CHUNK)))
    parts: list[str] = []
    if missing:
        names = [f"{_label(r)} {_day_label(str(r['txn_date']))}" for r in missing[:2]]
        if len(missing) > 2:
            names.append(f"+{len(missing) - 2}")
        parts.append(f"{len(missing)} receipt{'' if len(missing) == 1 else 's'} missing ({', '.join(names)})")
    if matches:
        parts.append(f"{matches} match{'' if matches == 1 else 'es'} to check")
    return [f"Needs you: {' · '.join(parts)}."] if parts else []


def _json(ctx: Ctx, row: Row, titles: Mapping[str, str], feed_shown: bool) -> Row:
    c = ctx.c
    feed: Row | None = None
    if row["match_feed_id"] and feed_shown:
        got = c.execute(
            "SELECT id, txn_date, amount, counterparty, description FROM fin_feed_txns WHERE id=?",
            (row["match_feed_id"],),
        ).fetchone()
        if got is not None:
            feed = {
                "id": str(got["id"]),
                "date": str(got["txn_date"]),
                "amount": int(got["amount"]),
                "description": str(got["counterparty"] or got["description"] or ""),
            }
    key = str(row["read_vendor_key"] or "")
    fid = str(row["file_id"])
    confidence = None if row["confidence"] is None else float(row["confidence"])
    vendor = (
        {"id": SAP.vendor_resolved(c, ctx.book.id, key), "name": str(row["read_vendor_name"] or key)} if key else None
    )
    return {
        "id": str(row["id"]),
        "file": {"id": fid, "name": titles.get(fid, "")},
        "read": {"amount": row["read_amount"], "date": row["read_date"], "vendor": vendor},
        "match": None if feed is None else {"feed": feed, "confidence": confidence},
        "status": str(row["status"]),
        "as_of": ctx.at,
    }


def _listing(
    ctx: Ctx, viewer: str, statuses: Sequence[str], cursor: str | None, most: int, needle: str
) -> tuple[list[Row], str | None]:
    """The receipts the viewer sees, newest first, with a status filter and a file-name or vendor search. Paged by the
    cursor over the book's rows, so a page may come back short when the viewer sees fewer of them."""
    from . import tab
    from .visible import feed_rows_visible, receipts_visible

    after = tab.decode_cursor(cursor)
    out: list[Row] = []
    scanned = 0
    while len(out) < most and scanned < SCAN_MAX:
        chunk = RS.page(ctx.c, ctx.book.id, statuses, after, CHUNK)
        if not chunk:
            return out, None
        titles = RS.file_titles(ctx.c, [str(r["file_id"]) for r in chunk])
        visible = receipts_visible(ctx, viewer, chunk)
        feeds = feed_rows_visible(ctx, viewer, [str(r["match_feed_id"]) for r in chunk if r["match_feed_id"]])
        for i, r in enumerate(chunk):
            scanned += 1
            after = [str(r["created_at"]), str(r["id"])]
            if str(r["id"]) in visible and (
                not needle or needle in f"{titles.get(str(r['file_id']), '')} {r['read_vendor_name']}".lower()
            ):
                out.append(_json(ctx, r, titles, str(r["match_feed_id"] or "") in feeds))
                if len(out) == most:
                    more = i + 1 < len(chunk) or len(chunk) == CHUNK
                    return out, tab.encode_cursor(after) if more else None
        if len(chunk) < CHUNK:
            return out, None
    return out, tab.encode_cursor(after)


def read(
    c: sqlite3.Connection,
    viewer: str,
    *,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
    at: str | None = None,
) -> Row:
    """GET /api/v2/finance/receipts: the receipts the viewer sees, newest first, with a cursor."""
    from . import tab

    if status is not None and status not in STATUSES:
        raise invalid(f"status is {'|'.join(STATUSES)}")
    ctx = load(c, at)
    rows, nxt = _listing(ctx, viewer, (status,) if status else (), cursor, tab.page_size(limit), "")
    return {"rows": rows, "next": nxt, "as_of": ctx.at}


def search(ctx: Ctx, viewer: str, *, q: str, status: str | None, most: int) -> list[Row]:
    """The hub search kind `receipt`: the viewer's receipts whose file name or vendor contains `q`, in the read's shape."""
    if status is not None and status not in STATUSES:
        raise invalid(f"status is {'|'.join(STATUSES)}")
    rows, _ = _listing(ctx, viewer, (status,) if status else (), None, most, q.strip().lower())
    return [{**r, "kind": "receipt", "label": str(r["file"]["name"] or "Receipt")} for r in rows]


__all__ = [
    "MIN_SCORE",
    "PLANNERS",
    "RECEIPT_DAYS",
    "Read",
    "amount_of",
    "brief_lines",
    "date_of",
    "feed_changed",
    "filed",
    "parse",
    "read",
    "score_of",
    "search",
    "sweep",
    "vendor_of",
]
