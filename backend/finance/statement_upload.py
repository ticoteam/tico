"""Statement uploads (Finance step 8): a CSV, OFX or QFX statement for a bank or card money account Tico cannot connect.

`preview` parses a file and says what posting it would do: each row New or Already in Tico, its vendor, the rule that
would attribute it, and the tie-out against the statement's balances. `statement_post` puts the New rows into the feed
as settled rows, which then post through the same path as a synced row. `statement_undo` takes back what one upload
put in, while nothing it added is reconciled. The file is kept nowhere but in its action's payload, as raw totals keep
theirs."""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from . import banking, posting_rules as PR, vendors
from .context import Ctx
from .core import feeds as F
from .core import kinds as K
from .core import rights as R
from .core import statement_files as SF
from .core import validate as V
from .core.errors import CONFLICT, NOT_AVAILABLE, NOT_FOUND, FinanceError, denied, invalid
from .core.merchant import merchant_key
from .handlers import Plan, Planner, Result, may_write
from .store import banking as SK
from .store import ledger as SL
from .store import statement_uploads as SS

NEW = "new"
DUPLICATE = "duplicate"
UPLOAD_PROVIDER = "upload:"  # a row an upload put in is keyed by the file's own id for it (FITID, or the CSV hash)
FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"  # context.now()'s format, so stored times compare as text
PREVIEW_TTL = timedelta(minutes=30)
B64_MAX = (SF.MAX_BYTES + 2) // 3 * 4  # the longest base64 text a file of MAX_BYTES takes
OUTSIDE = "some of these rows are outside your view"


@dataclass(frozen=True)
class Judged:
    row: SF.Row
    status: str
    vendor: dict[str, Any]
    attribution: dict[str, Any] | None


@dataclass(frozen=True)
class Tie:
    opening: int
    added: int
    removed: int
    ends: int
    statement_says: int | None
    off_by: int


def _day(text: str, days: int) -> str:
    return (date.fromisoformat(text) + timedelta(days=days)).isoformat()


def _near(a: str, b: str) -> bool:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days) <= 1


def _display(money: Any, value: int) -> int:
    """A balance as a statement shows it (a card owes a positive amount): banking._signed, which is its own inverse."""
    return -value if str(money["kind"]) == "card_program" else value


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _digest(judged: list[Judged]) -> str:
    """What the post will insert, as a token's preview recorded it: the ids of the New rows."""
    keys = sorted(j.row.external_id for j in judged if j.status == NEW)
    return hashlib.sha256("\n".join(keys).encode()).hexdigest()


def _money(ctx: Ctx, who: str, money_account_id: str) -> Any:
    from .tab import sees_money_account  # lazy: tab lists the money accounts, and lists this module's off-by figure too

    money = SK.get(ctx.c, "fin_money_accounts", money_account_id)
    if money is None or not sees_money_account(ctx, ctx.view(who), money):
        raise FinanceError(NOT_FOUND, "no such money account")
    if str(money["kind"]) not in ("bank", "card_program"):
        raise invalid("a statement file uploads to a bank or card account")
    if ctx.book.mode == "mirror":
        raise FinanceError(NOT_AVAILABLE, "a mirrored book's rows come from its provider: a statement file cannot post")
    return money


def _raw(p: V.Payload) -> bytes:
    try:
        raw = base64.b64decode(V.text(p, "file", B64_MAX), validate=True)
    except ValueError as e:
        raise invalid("the statement file is not base64 text") from e
    if not raw:
        raise invalid("the statement file is empty")
    if len(raw) > SF.MAX_BYTES:
        raise invalid("a statement file is 4 MB at most")
    return raw


def _parse(raw: bytes) -> SF.Parsed:
    try:
        return SF.parse(raw)
    except SF.StatementError as e:
        raise invalid(str(e)) from e


def _feed_keys(row: Any) -> set[str]:
    found = {
        str(row["vendor_key"] or ""),
        merchant_key(str(row["counterparty"] or "")),
        merchant_key(str(row["description"] or "")),
    }
    return {k for k in found if k}


def _attribution(ctx: Ctx, money: Any, r: SF.Row) -> dict[str, Any] | None:
    """The account a posting rule would post this row to, when one matches and still posts (else None: unattributed)."""
    facts = PR.feed_facts(
        {"amount": r.amount, "counterparty": r.description, "description": r.description, "kind": ""}, money
    )
    rule = PR.first_for(ctx, facts)
    if rule is None or not PR.usable(ctx, rule.then):
        return None
    ids = [s.account_id for s in rule.then.split] or [rule.then.account_id]
    return {"account": ", ".join(ctx.accounts[i].name for i in ids if i in ctx.accounts), "rule": rule.name}


def _judge(ctx: Ctx, who: str, money: Any, gl_id: str, parsed: SF.Parsed) -> list[Judged]:
    """Each row of the file, New or Already in Tico. A row is already in Tico when a row an upload put in has its
    file id (exact), else when a feed row or a journal line a person posted to this account matches it on amount,
    a day either side and the same merchant. Fuzzy matches are one for one: two of a kind match two, not one."""
    rows = parsed.rows
    ids = [r.external_id for r in rows]
    if len(set(ids)) != len(ids):
        raise invalid("two rows of this file share an id")
    money_id = str(money["id"])
    spans = [parsed.facts.start, parsed.facts.end, *(r.date for r in rows)]
    lo, hi = _day(min(spans), -1), _day(max(spans), 1)
    feed = SS.feed_rows_between(ctx.c, money_id, lo, hi)
    journal = SS.journal_lines_between(ctx.c, ctx.book.id, gl_id, lo, hi)
    duplicate: set[str] = set()
    taken_feed: set[str] = set()
    taken_lines: set[int] = set()
    for r in rows:
        hit = SK.feed_row(ctx.c, money_id, UPLOAD_PROVIDER + r.external_id)
        if hit is not None:
            duplicate.add(r.external_id)
            taken_feed.add(str(hit["id"]))
    for r in rows:
        key = merchant_key(r.description)
        if r.external_id in duplicate or not key:
            continue
        feed_hit = next(
            (
                f
                for f in feed
                if str(f["id"]) not in taken_feed
                and int(f["amount"]) == r.amount
                and _near(r.date, str(f["txn_date"]))
                and key in _feed_keys(f)
            ),
            None,
        )
        if feed_hit is not None:
            taken_feed.add(str(feed_hit["id"]))
            duplicate.add(r.external_id)
            continue
        line_hit = next(
            (
                i
                for i, line in enumerate(journal)
                if i not in taken_lines
                and int(line["amount"]) == r.amount
                and _near(r.date, str(line["entry_date"]))
                and merchant_key(str(line["memo"] or "")) == key
            ),
            None,
        )
        if line_hit is not None:
            taken_lines.add(line_hit)
            duplicate.add(r.external_id)
    view = ctx.view(who)
    from .tab_ap import sees_contact_kind  # lazy: tab_ap reads the bills side, which this module does not need

    names = vendors.feed_vendors(
        ctx.c,
        ctx.book.id,
        [{"id": r.external_id, "vendor_key": merchant_key(r.description), "counterparty": r.description} for r in rows],
        lambda kind: sees_contact_kind(ctx, view, kind),
    )
    out: list[Judged] = []
    for r in rows:
        dup = r.external_id in duplicate
        out.append(
            Judged(
                row=r,
                status=DUPLICATE if dup else NEW,
                vendor=dict(names[r.external_id]),
                attribution=None if dup else _attribution(ctx, money, r),
            )
        )
    return out


def _tie(ctx: Ctx, money: Any, gl_id: str, parsed: SF.Parsed, amounts: list[int]) -> Tie:
    """The tie-out in the balance's own terms: the opening (the file's, else the books' balance the day before the
    file starts), what the amounts added and removed, where that ends, and the gap to the statement's closing balance
    (0 when the file gives none). `amounts` are the file's rows the books hold once the post lands."""
    if parsed.facts.opening is not None:
        opening = parsed.facts.opening
    else:
        book = SK.balances(ctx.c, ctx.book.id, _day(parsed.facts.start, -1), [gl_id]).get(gl_id, 0)
        opening = _display(money, book)
    sign = -1 if str(money["kind"]) == "card_program" else 1
    added = removed = 0
    for amount in amounts:
        change = sign * amount
        if change >= 0:
            added += change
        else:
            removed -= change
    ends = opening + added - removed
    closing = parsed.facts.closing
    return Tie(
        opening=opening,
        added=added,
        removed=removed,
        ends=ends,
        statement_says=closing,
        off_by=0 if closing is None else ends - closing,
    )


def _checked_token(ctx: Ctx, p: V.Payload, money_id: str, file_hash: str, digest: str) -> str | None:
    """The token hash a post presents, when it carries a preview token: that preview must be for this file and account,
    unexpired, and for the same New rows. None when the post carries no token."""
    token = V.opt_text(p, "preview_token", 200)
    if token is None:
        return None
    h = _token_hash(token)
    row = SS.preview_get(ctx.c, h)
    if row is None or str(row["money_account_id"]) != money_id or str(row["file_hash"]) != file_hash:
        raise FinanceError(CONFLICT, "that preview is not for this file and account: preview the file again")
    if str(row["expires_at"]) <= ctx.at:
        raise FinanceError(CONFLICT, "that preview has expired: preview the file again")
    if str(row["new_digest"]) != digest:
        raise FinanceError(CONFLICT, "the new rows changed since that preview: preview the file again")
    return h


def statement_post(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("money_account_id", "file", "file_name", "preview_token", "skip"))
    money = _money(ctx, who, V.text(p, "money_account_id", 80))
    gl = banking.gl_account(ctx, money)
    if not may_write(ctx, who, gl.id, {}):
        raise denied(OUTSIDE)
    raw = _raw(p)
    parsed = _parse(raw)
    file_hash = hashlib.sha256(raw).hexdigest()
    prior = SS.upload_by_hash(ctx.c, str(money["id"]), file_hash)
    if prior is not None:
        raise FinanceError(
            CONFLICT,
            f"this file is already posted to {money['name']} as upload {prior['id']}: "
            "undo that upload to post it again",
        )
    judged = _judge(ctx, who, money, gl.id, parsed)
    skip = set(V.texts(p, "skip", SF.MAX_ROWS)) if p.get("skip") else set()
    unknown = skip - {j.row.external_id for j in judged}
    if unknown:
        raise invalid(f"{len(unknown)} skipped row(s) are not in this file")
    posting = [j for j in judged if j.status == NEW and j.row.external_id not in skip]
    if not posting:
        raise invalid("no new rows to post: every row is already in Tico or skipped")
    token_hash = _checked_token(ctx, p, str(money["id"]), file_hash, _digest(judged))
    # The books hold every row already in Tico, and each row this post takes in; a skipped new row is in neither.
    amounts = [j.row.amount for j in judged if j.status == DUPLICATE or j.row.external_id not in skip]
    tie = _tie(ctx, money, gl.id, parsed, amounts)
    file_name = V.opt_text(p, "file_name", 200) or ""
    duplicates = sum(1 for j in judged if j.status == DUPLICATE)
    skipped = sum(1 for j in judged if j.status == NEW and j.row.external_id in skip)

    def apply(aid: str) -> Result:
        return _post(ctx, who, aid, money, parsed, judged, skip, tie, file_hash, file_name, token_hash)

    return Plan(
        gate=K.default_gate(ctx.org, who, "statement_post"),
        summary=f"Post {len(posting)} statement rows to {money['name']}",
        apply=apply,
        impact={"new": len(posting), "duplicate": duplicates, "skipped": skipped, "off_by": tie.off_by},
    )


def _post(
    ctx: Ctx,
    who: str,
    aid: str,
    money: Any,
    parsed: SF.Parsed,
    judged: list[Judged],
    skip: set[str],
    tie: Tie,
    file_hash: str,
    file_name: str,
    token_hash: str | None,
) -> Result:
    money_id = str(money["id"])
    upload_id = SL.new_id()
    posted = 0
    for j in judged:
        if j.status != NEW or j.row.external_id in skip:
            continue
        r = j.row
        fid = SS.insert_feed(
            ctx.c,
            {
                "money_account_id": money_id,
                "provider_id": UPLOAD_PROVIDER + r.external_id,
                "txn_date": r.date,
                "amount": r.amount,
                "currency": ctx.book.base_currency,
                "counterparty": r.description,
                "description": r.description,
                "vendor_key": merchant_key(r.description),
                "status": "new",
                "provider_status": "posted",
                "raw": SK.dump({"statement_upload": upload_id, "statement_row": r.external_id}),
                "upload_id": upload_id,
                "created_at": ctx.at,
                "created_by": who,
                "updated_at": ctx.at,
            },
        )
        banking.settle_taken_in(
            ctx,
            fid,
            money,
            F.FeedTxn(
                provider_id=UPLOAD_PROVIDER + r.external_id,
                account_id="",
                amount=r.amount,
                txn_date=r.date,
                status=F.POSTED,
                counterparty=r.description,
                description=r.description,
                raw={"statement_upload": upload_id},
            ),
        )
        posted += 1
    stmt_id: str | None = None
    if parsed.facts.closing is not None and SK.statement(ctx.c, money_id, parsed.facts.end) is None:
        stmt_id = SL.new_id()
        SS.insert_statement(
            ctx.c,
            {
                "id": stmt_id,
                "money_account_id": money_id,
                "provider_id": None,
                "start_date": parsed.facts.start,
                "end_date": parsed.facts.end,
                "starting_balance": parsed.facts.opening,
                "ending_balance": parsed.facts.closing,
                "currency": ctx.book.base_currency,
                "source": "manual",
                "created_at": ctx.at,
                "created_by": who,
            },
        )
    SS.insert_upload(
        ctx.c,
        {
            "id": upload_id,
            "book_id": ctx.book.id,
            "money_account_id": money_id,
            "file_hash": file_hash,
            "file_name": file_name,
            "file_format": parsed.facts.format,
            "start_date": parsed.facts.start,
            "end_date": parsed.facts.end,
            "opening_balance": tie.opening,
            "closing_balance": parsed.facts.closing,
            "ends_at": tie.ends,
            "off_by": tie.off_by,
            "row_count": len(judged),
            "new_count": posted,
            "duplicate_count": sum(1 for j in judged if j.status == DUPLICATE),
            "skipped_count": sum(1 for j in judged if j.status == NEW and j.row.external_id in skip),
            "statement_id": stmt_id,
            "action_id": aid,
            "created_at": ctx.at,
            "created_by": who,
        },
    )
    if token_hash is not None:
        SS.preview_delete(ctx.c, token_hash)
    return {
        "upload_id": upload_id,
        "new": posted,
        "duplicate": sum(1 for j in judged if j.status == DUPLICATE),
        "skipped": sum(1 for j in judged if j.status == NEW and j.row.external_id in skip),
        "off_by": tie.off_by,
        "statement_id": stmt_id,
    }


def statement_undo(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("upload_id",))
    up = SS.upload_get(ctx.c, V.text(p, "upload_id", 80))
    if up is None:
        raise FinanceError(NOT_FOUND, "no such statement upload")
    money = _money(ctx, who, str(up["money_account_id"]))
    if up["undone_at"] is not None:
        raise FinanceError(CONFLICT, "this statement upload is already undone")
    gl = banking.gl_account(ctx, money)
    if not may_write(ctx, who, gl.id, {}):
        raise denied(OUTSIDE)
    upload_id = str(up["id"])
    rows = SS.upload_rows(ctx.c, upload_id)
    last = SK.last_reconciliation(ctx.c, str(money["id"]))
    cleared = any(r["reconciliation_id"] for r in rows)
    if cleared or (last is not None and str(last["statement_end"]) >= str(up["start_date"])):
        through = str(last["statement_end"]) if last is not None else str(up["end_date"])
        raise FinanceError(
            CONFLICT, f"this account is reconciled through {through}: the rows of this upload stay until it is undone"
        )

    def apply(aid: str) -> Result:
        for r in rows:
            banking.unsettle_uploaded(ctx, r, f"statement upload {upload_id} undone")
        removed = SS.delete_rows(ctx.c, upload_id)
        if up["statement_id"]:
            SS.delete_statement(ctx.c, str(up["statement_id"]))
        SS.set_upload(ctx.c, upload_id, {"undone_at": ctx.at, "undone_by": who})
        return {"upload_id": upload_id, "removed": removed}

    return Plan(
        gate=K.default_gate(ctx.org, who, "statement_undo"),
        summary=f"Undo statement upload of {len(rows)} rows to {money['name']}",
        apply=apply,
        impact={"rows": len(rows)},
    )


def _tie_out(t: Tie) -> dict[str, Any]:
    return {
        "opening": t.opening,
        "added": t.added,
        "removed": t.removed,
        "ends": t.ends,
        "statement_says": t.statement_says,
        "off_by": t.off_by,
    }


def preview(ctx: Ctx, who: str, p: V.Payload) -> dict[str, Any]:
    """The review of a statement file (POST /api/v2/finance/statements/preview): every row New or Already in Tico, and
    the tie-out. It writes nothing but a short-lived token, which a post presents to show it posts what was shown."""
    V.only(p, ("money_account_id", "file", "file_name"))
    if R.BOOK not in ctx.rights(who):
        raise denied("posting a statement needs the finance.book right")
    money = _money(ctx, who, V.text(p, "money_account_id", 80))
    gl = banking.gl_account(ctx, money)
    if not may_write(ctx, who, gl.id, {}):
        raise denied(OUTSIDE)
    raw = _raw(p)
    parsed = _parse(raw)
    file_hash = hashlib.sha256(raw).hexdigest()
    judged = _judge(ctx, who, money, gl.id, parsed)
    tie = _tie(ctx, money, gl.id, parsed, [j.row.amount for j in judged])
    token = secrets.token_urlsafe(24)
    expires = (datetime.strptime(ctx.at, FORMAT) + PREVIEW_TTL).strftime(FORMAT)
    SS.preview_purge(ctx.c, ctx.at)
    SS.preview_put(
        ctx.c,
        {
            "token_hash": _token_hash(token),
            "book_id": ctx.book.id,
            "money_account_id": str(money["id"]),
            "file_hash": file_hash,
            "new_digest": _digest(judged),
            "created_at": ctx.at,
            "created_by": who,
            "expires_at": expires,
        },
    )
    return {
        "token": token,
        "expires_at": expires,
        "money_account_id": str(money["id"]),
        "file_name": V.opt_text(p, "file_name", 200) or "",
        "file_format": parsed.facts.format,
        "start": parsed.facts.start,
        "end": parsed.facts.end,
        "card": parsed.facts.card,
        "last4": parsed.facts.last4,
        "new": sum(1 for j in judged if j.status == NEW),
        "duplicate": sum(1 for j in judged if j.status == DUPLICATE),
        "tie": _tie_out(tie),
        "rows": [
            {
                "id": j.row.external_id,
                "date": j.row.date,
                "description": j.row.description,
                "amount": j.row.amount,
                "vendor": j.vendor,
                "attribution": j.attribution,
                "status": j.status,
            }
            for j in judged
        ],
    }


PLANNERS: dict[str, Planner] = {"statement_post": statement_post, "statement_undo": statement_undo}
