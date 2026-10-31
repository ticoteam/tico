"""Spreadsheet history as raw totals (spec §4.1, §16 decision 4): `import_raw_totals` reads a CSV of each month's
total per line, maps the sheet's lines to accounts, and posts one balanced entry per month against Opening balance
equity. The same file posts nothing again; a month that already holds raw totals is refused until its entry
is reversed."""
from typing import Any

from .context import Ctx
from .core import kinds as K
from .core import ledger as L
from .core import raw_totals as RT
from .core import validate as V
from .core.errors import CONFLICT, NOT_FOUND, PERIOD_CLOSED, FinanceError, denied, invalid
from .handlers import Plan, Result, may_write
from .posting import post
from .store import ledger as SL
from .store import raw_totals as SR

LISTED = 20             # line names a refusal lists
SHOWN = 60              # accounts a dry run lists


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
        key = L.name_key(name)
        if key in out:
            raise invalid(f"two mapping names fold to {key}")
        out[key] = account
    return out


def _check_month(ctx: Ctx, who: str, month: str, lines: tuple[L.Line, ...]) -> None:
    end = RT.month_end(month)
    if SL.closed_on(ctx.c, ctx.book.id, end):
        raise FinanceError(PERIOD_CLOSED, f"{month} is closed: the owner reopens it before raw totals post")
    held = SR.live_entry_in(ctx.c, ctx.book.id, f"{month}-01", end)
    if held is not None:
        raise FinanceError(CONFLICT, f"{month} already has raw totals in entry {held['id']}: reverse it first")
    if not all(may_write(ctx, who, x.account_id, x.tags) for x in lines):
        raise denied(f"some lines in {month} are outside your view")


def _again(row: Any) -> Plan:
    """The file is already in the books: nothing posts, and the result names the import that holds it."""
    done: Result = {"batch_id": str(row["id"]), "basis": str(row["basis"]), "already_imported": True}
    return Plan(gate=K.ALLOW, summary=f"Raw totals already imported as {row['id']}: nothing posts",
                apply=lambda _aid: dict(done), impact={"already_imported": str(row["id"])})


def import_raw_totals(ctx: Ctx, who: str, p: V.Payload) -> Plan:
    V.only(p, ("csv", "basis", "mapping", "source_name"))
    sheet = RT.parse(V.text(p, "csv", RT.MAX_BYTES), V.choice(p, "basis", RT.BASES))
    prior = SR.live_by_digest(ctx.c, ctx.book.id, sheet.digest)
    if prior is not None:
        return _again(prior)
    by_key = _mapping(ctx, p.get("mapping"))
    missing = RT.unmapped(sheet, by_key)
    if missing:
        raise invalid(f"no account is mapped for: {_names(missing)}")
    teams = RT.unknown_teams(sheet, ctx.org.teams)
    if teams:
        raise invalid(f"no team on the org chart for: {_names(teams)}")
    months = RT.lines_by_month(sheet, by_key, _opening(ctx), ctx.book.base_currency)
    for month, lines in months.items():
        _check_month(ctx, who, month, lines)
    source = V.opt_text(p, "source_name", 120) or ""
    memo = f"Imported raw totals, {sheet.basis} basis" + (f": {source}" if source else "")
    gross = sum(abs(r.amount) for r in sheet.rows)
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
        return {"batch_id": batch, "basis": sheet.basis, "months": posted}

    return Plan(gate=K.default_gate(ctx.org, who, "import_raw_totals"), apply=apply,
                summary=f"Import {len(sheet.rows)} raw total rows across {len(months)} months ({sheet.basis} basis)",
                impact={"rows": len(sheet.rows), "zero_rows": sheet.zero_rows, "basis": sheet.basis,
                        "months": len(months), "accounts": accounts},
                facts={"amount": gross, "months": len(months), "accounts": sorted(totals)})


PLANNERS = {"import_raw_totals": import_raw_totals}
