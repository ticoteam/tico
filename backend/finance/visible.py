"""One view rule for records (plan v2, "Who sees what"): what a reader sees of a record. Every read that names a record
(a vendor today; campaigns, subscriptions and receipts take the same rule with their own kind) gets its answer here, so
the reads agree with each other and with View as.

The rule, for one record in one window. Its totals are built from the reader's cells (core/view.py Cell: the lines the
reader's view takes in, before any suppression):

- Totals add only the lines in the reader's view. A line on a private account the reader has no full detail for is left
  out, and the total it was in is marked `excl`, so nobody can work out a private line by subtracting.
- The record's name and details (vendor name, aliases) show when the reader sees at least one of its lines in the window.
  Private lines win: a record whose only lines are private (a payroll provider, a law firm) shows its name to no reader
  without full detail.
- With no line in the window, the name shows to a reader who sees every contact of that kind (full detail, or the
  payables view), or when a vendor attribution rule names the reader or someone they manage (a teammate under them, or a
  team they lead).
- Counts (queues, "Other · n" folds) are built from the reader's slice, so they agree with the totals.

Nothing here reads a table directly: the store layer (store/ap.py, store/attribution.py) does, and the lint test
(backend/tests/test_finance_visible_lint.py) keeps every other read off the contact tables.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .context import Ctx
from .core import view as VW
from .core.errors import invalid
from .store import ap as SAP
from .store import attribution as SATT
from .store import ledger as SL

KINDS = ("vendor",)  # the record kinds the rule covers today; later steps add theirs here


@dataclass(frozen=True)
class Visibility:
    """What one reader sees of one record in the window."""

    kind: str
    record_id: str
    named: bool  # the name and details may show
    amount: int  # the reader's share of the record's total
    lines: int  # the lines that share is made of
    excl: bool  # a hidden line was left out of the total


def private_accounts(ctx: Ctx, view: VW.View) -> frozenset[str]:
    """The private accounts this reader has no full detail for: no line on one reaches a record's total or name."""
    return frozenset(a for a in ctx.private() if a not in view.full_detail)


def _record_of(kind: str, cell: VW.Cell) -> str:
    return cell.vendor if kind == "vendor" else ""


def _keys(ctx: Ctx, kind: str, record_id: str) -> Sequence[str]:
    """Every key a record's lines carry (a vendor's contact id and the names it went by): what attribution rules match."""
    if kind == "vendor":
        return SAP.vendor_keys(ctx.c, ctx.book.id, record_id)
    return ()


def _attributed(ctx: Ctx, viewer: str, keys: Sequence[str]) -> bool:
    """Whether a live, enabled attribution rule on one of `keys` names the viewer, a teammate under them, or a team they
    lead. A rule's own shares are what it names; the lines it puts on the record show through the viewer's slices."""
    if not keys:
        return False
    under = ctx.org.reports(viewer)
    led = ctx.org.led_by(viewer)
    for rule in SATT.rules(ctx.c, ctx.book.id):
        if str(rule["match_kind"]) != "vendor" or str(rule["match_id"]) not in keys or not int(rule["enabled"]):
            continue
        for share in SATT.rule_splits(rule):
            if share.teammate and (share.teammate == viewer or share.teammate in under):
                return True
            if share.team and share.team in led:
                return True
    return False


def named_all_time(ctx: Ctx, view: VW.View, contact_ids: Iterable[str]) -> dict[str, bool]:
    """Whether each vendor's name shows, from every line the book holds for it (a contact list has no period): a line the
    reader sees names it; with none seen, a private line it holds names nothing, and otherwise the viewer's full
    detail or an attribution rule naming them does."""
    cut = private_accounts(ctx, view)
    out: dict[str, bool] = {}
    for contact in contact_ids:
        keys = SAP.vendor_keys(ctx.c, ctx.book.id, contact)
        seen = lost = False
        for account_id, team, teammate in SL.vendor_slices(ctx.c, ctx.book.id, keys):
            account = ctx.accounts.get(account_id)
            if account is None or not view.sees(account, {"team": team, "teammate": teammate}):
                continue
            if account_id in cut:
                lost = True
            else:
                seen = True
                break
        out[contact] = seen or (not lost and (view.everything or _attributed(ctx, view.viewer, keys)))
    return out


def records_visible(
    ctx: Ctx,
    view: VW.View,
    kind: str,
    cells: Iterable[VW.Cell],
    *,
    record_ids: Iterable[str] = (),
    all_contacts: bool = False,
    window: tuple[str, str] | None = None,
) -> dict[str, Visibility]:
    """Each record's Visibility from the cells of one window (the reader's slice before suppression). `window` (ISO
    days, inclusive) keeps only the cells inside it: a private account's cells come whole fiscal quarters around a
    window (reads._cells), and a total is for the window alone.

    `record_ids` adds records that have no cell in the window, so a contact with no spend this month still gets its name
    for a reader who sees every contact. `all_contacts` says the reader sees every contact of this kind (the payables
    view, or full detail). A linked cell (private payroll netted out of it, see reads._without_settled) already has its
    lines out of the total, so it counts in the amount without marking the record excl: the read marks that itself."""
    if kind not in KINDS:
        raise invalid(f"record kind is {'|'.join(KINDS)}")
    cut = private_accounts(ctx, view)
    shown: dict[str, list[VW.Cell]] = {}
    lost: set[str] = set()
    for cell in cells:
        if window is not None and not window[0] <= cell.day <= window[1]:
            continue
        record = _record_of(kind, cell)
        if not record:
            continue
        if cell.linked or cell.account_id not in cut:
            shown.setdefault(record, []).append(cell)
        else:
            lost.add(record)
    out: dict[str, Visibility] = {}
    for record in dict.fromkeys([*shown, *sorted(lost), *record_ids]):
        if not record:
            continue
        cells_of = shown.get(record, [])
        if cells_of:
            named = True
        elif record in lost:
            named = False  # private lines win: nothing of this record is in view, so its name is not either
        else:
            named = all_contacts or view.everything or _attributed(ctx, view.viewer, _keys(ctx, kind, record))
        out[record] = Visibility(
            kind=kind,
            record_id=record,
            named=named,
            amount=sum(x.amount for x in cells_of),
            lines=sum(x.lines for x in cells_of),
            excl=record in lost,
        )
    return out

