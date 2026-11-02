"""Spreadsheet history as raw totals (spec §4.1, §16 decision 4): `import_raw_totals` reads a CSV of each month's
total per line, maps the sheet's lines to accounts, and posts one balanced entry per month against Opening balance
equity. The same file posts nothing again; a month that already holds raw totals is refused until its entry is
reversed. Every refusal that could name a figure or an entry checks the caller's view first."""
from collections.abc import Mapping
from typing import Any

from .context import Ctx
from .core import kinds as K
from .core import ledger as L
from .core import money as M
from .core import raw_totals as RT
from .core import validate as V
from .core.errors import CONFLICT, NOT_FOUND, PERIOD_CLOSED, FinanceError, denied, invalid
from .handlers import Plan, Result, may_write
from .posting import post
from .store import ledger as SL
from .store import raw_totals as SR

LISTED = 20             # line names a refusal lists
SHOWN = 60              # accounts a dry run lists
OUTSIDE = "some of these lines are outside your view"     # the same refusal whatever the lines or the month


def _names(names: list[str]) -> str:
    more = f" and {len(names) - LISTED} more" if len(names) > LISTED else ""
    return ", ".join(names[:LISTED]) + more


def _opening(ctx: Ctx) -> L.Account:
    for account in ctx.accounts.values():
        if account.type == "equity" and account.subtype == RT.OFFSET_SUBTYPE and account.active:
            return account
    raise FinanceError(NOT_FOUND, "Opening balance equity is missing: Finance setup makes it")


def _mapping(ctx: Ctx, raw: object) -> dict[str, L.Account]:
    """The caller's {sheet line name: account id or code}, keyed by each name's folded key. An account is in this book
    and active, and never the opening-balance account the imported months balance against."""
    p = V.obj(raw, "mapping")
    if not p or len(p) > V.MAX_ITEMS:
        raise invalid(f"mapping holds 1 to {V.MAX_ITEMS} lines")
    by_code = {a.code: a for a in ctx.accounts.values()}
    out: dict[str, L.Account] = {}
    for name, target in p.items():
        if not isinstance(target, str) or not target.strip():
            raise invalid(f"the mapping for {name} is an account id or code")
        ref = target.strip()
        account = ctx.accounts.get(ref) or by_code.get(ref)
        if account is None:
            raise FinanceError(NOT_FOUND, f"no account {ref} for {name}")
        if not account.active:
            raise invalid(f"account {account.code} is inactive")
        if account.type == "equity" and account.subtype == RT.OFFSET_SUBTYPE:
            raise invalid(f"{name} maps to the opening balance account")
        key = L.name_key(RT.line_name(name))
        if key in out:
            raise invalid(f"two mapping names fold to {key}")
        out[key] = account
    return out


def _sees(ctx: Ctx, who: str, account_id: str, tags: Mapping[str, str]) -> bool:
    """Whether `who` sees this line in the reads and may write it. A private account's line shows only to a viewer with
    its detail (§7.5), so one they lack the detail of is refused like a line outside the view."""
    if account_id in ctx.private() and account_id not in ctx.view(who).full_detail:
        return False
    return may_write(ctx, who, account_id, tags)


def _covered(ctx: Ctx, who: str, lines: list[tuple[str, dict[str, str]]]) -> bool:
    """Whether `who` sees every one of these (account, tags) lines."""
    return all(_sees(ctx, who, account_id, tags) for account_id, tags in lines)


def _check_month(ctx: Ctx, who: str, month: str) -> None:
    """A month is open, and empty of raw totals and of itemized transactions the caller may not see. An entry's id is
    named only to a caller who sees all of that entry's lines, so a scoped caller learns nothing from a refusal about a
    month."""
    end = RT.month_end(month)
    if SL.closed_on(ctx.c, ctx.book.id, end):
        raise FinanceError(PERIOD_CLOSED, f"{month} is closed: the owner reopens it before raw totals post")
    start = f"{month}-01"
    held = SR.live_entry_in(ctx.c, ctx.book.id, start, end)
    if held is not None:
        if not _covered(ctx, who, SR.entry_lines(ctx.c, str(held["id"]))):
            raise denied(OUTSIDE)
        raise FinanceError(CONFLICT, f"{month} already has raw totals in entry {held['id']}: reverse it first")
    itemized = SR.itemized_in(ctx.c, ctx.book.id, start, end)
    if itemized is None:
        return
    if not _covered(ctx, who, SR.entry_lines(ctx.c, str(itemized["id"]))):
        raise denied(OUTSIDE)
    raise FinanceError(CONFLICT, f"{month} already has transactions in entry {itemized['id']}: raw totals post only "
                                 "to months with none")


def _again(row: Any) -> Plan:
    """The file is already in the books: nothing posts, and the result names the import that holds it."""
    done: Result = {"batch_id": str(row["id"]), "basis": str(row["basis"]), "already_imported": True}
    return Plan(gate=K.ALLOW, summary=f"Raw totals already imported as {row['id']}: nothing posts",
                apply=lambda _aid: dict(done), impact={"already_imported": str(row["id"])})


def import_raw_totals(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("csv", "basis", "mapping", "source_name"))
    sheet = RT.parse(V.text(p, "csv", RT.MAX_BYTES), V.choice(p, "basis", RT.BASES))
    by_key = _mapping(ctx, p.get("mapping"))
    missing = RT.unmapped(sheet, by_key)
    if missing:
        raise invalid(f"no account is mapped for: {_names(missing)}")
    teams = RT.unknown_teams(sheet, ctx.org.teams)
    if teams:
        raise invalid(f"no team on the org chart for: {_names(teams)}")
    months = RT.lines_by_month(sheet, by_key, _opening(ctx), ctx.book.base_currency)
    # The view comes first: a replay, a closed month and an occupied month all reveal something, so none of them
    # answers a caller who cannot see every line of the file.
    if not all(_sees(ctx, who, x.account_id, x.tags) for lines in months.values() for x in lines):
        raise denied(OUTSIDE)
    prior = SR.live_by_digest(ctx.c, ctx.book.id, sheet.digest)
    if prior is not None:
        if not _covered(ctx, who, SR.batch_lines(ctx.c, ctx.book.id, str(prior["id"]))):
            raise denied(OUTSIDE)
        return _again(prior)
    for month in months:
        _check_month(ctx, who, month)
    source = V.opt_text(p, "source_name", 120) or ""
    memo = f"Imported raw totals, {sheet.basis} basis" + (f": {source}" if source else "")
    # The gross is stored in a 64-bit column, and each month's net (never more than the gross) posts as one line. A sheet
    # past that range is refused here, so the dry run says what the commit would.
    gross = M.minor(sum(abs(r.amount) for r in sheet.rows), "the sum of the sheet's amounts")
    totals: dict[str, int] = {}
    names: dict[str, str] = {}
    for r in sheet.rows:
        account = by_key[r.key]
        totals[account.code] = totals.get(account.code, 0) + RT.natural(r.amount, account.type)
        names[account.code] = account.name
    accounts: list[object] = [{"code": code, "name": names[code], "total": total}
                              for code, total in list(totals.items())[:SHOWN]]

    def apply(aid: str) -> Result:
        batch = SL.new_id()
        posted: list[object] = []
        for month, lines in months.items():
            entry = L.Entry(book_id=ctx.book.id, entry_date=RT.month_end(month), memo=memo, source="import",
                            source_id=f"raw_totals:{batch}:{month}", lines=lines, basis="both")
            entry_id, _ = post(ctx, entry, by=who, action_id=aid)
            posted.append({"month": month, "entry_id": entry_id})
        SR.insert(ctx.c, {"id": batch, "book_id": ctx.book.id, "content_hash": sheet.digest, "basis": sheet.basis,
                          "source_name": source, "periods": len(months), "row_count": len(sheet.rows),
                          "gross": gross, "action_id": aid, "created_at": ctx.at, "created_by": who})
        from .banking import refresh_covered    # the months just posted cover the feed rows dated on or before them
        refresh_covered(ctx)
        return {"batch_id": batch, "basis": sheet.basis, "months": posted}

    return Plan(gate=K.default_gate(ctx.org, who, "import_raw_totals"), apply=apply,
                summary=f"Import {len(sheet.rows)} raw total rows across {len(months)} months ({sheet.basis} basis)",
                impact={"rows": len(sheet.rows), "zero_rows": sheet.zero_rows, "basis": sheet.basis,
                        "months": len(months), "accounts": accounts,
                        "warnings": [w for w in (RT.cents_warning(sheet),) if w]},
                facts={"amount": gross, "months": len(months), "accounts": sorted(totals)})


PLANNERS = {"import_raw_totals": import_raw_totals}
