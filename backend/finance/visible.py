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

from . import attribution as ATTR
from . import platform_share as PS
from .context import Ctx
from .core import attribution as CA
from .core import view as VW
from .core.errors import invalid
from .core.org import manager_of
from .store import ap as SAP
from .store import attribution as SATT
from .store import ledger as SL

KINDS = ("vendor", "campaign")  # the record kinds the rule covers; a campaign takes campaigns_visible, not cells


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


Owner = tuple[
    str, str, str
]  # (platform, team, teammate): a campaign's owner on one ad platform; one of team and teammate
# is blank (the owner an ad account's campaigns count for)


def _platform_attributed(ctx: Ctx, viewer: str, platforms: Iterable[str], first: str, last: str) -> set[Owner]:
    """The platform owners the viewer sees in a platform charge line in [first, last]: a line on a platform's own vendor
    contact whose in-force platform share names the owner, and which the viewer sees. One query for every platform."""
    owner_of_contact: dict[str, str] = {}
    for platform in platforms:
        for key in PS.contact_keys(ctx, platform):
            owner_of_contact[key] = platform
    if not owner_of_contact:
        return set()
    marks = ",".join("?" * len(owner_of_contact))
    platform_of_line: dict[str, str] = {
        str(r[0]): owner_of_contact[str(r[1])]
        for r in ctx.c.execute(
            "SELECT l.id, l.contact_id FROM fin_lines l JOIN fin_entries e ON e.id=l.entry_id JOIN fin_attributions a "
            "ON a.line_id=l.id AND a.superseded_at IS NULL WHERE l.book_id=? AND l.contact_id IN (" + marks + ") "
            "AND l.entry_date BETWEEN ? AND ? AND e.status='posted' AND e.reverses_id IS NULL AND a.source='platform'",
            [ctx.book.id, *owner_of_contact, first, last],
        )
    }
    if not platform_of_line:
        return set()
    found = SL.lines(ctx.c, list(platform_of_line))
    in_force = SATT.in_force_rows(ctx.c, list(found))
    out: set[Owner] = set()
    for line_id, (_, line) in found.items():
        row = in_force.get(line_id)
        if row is None or not ATTR.visible(ctx, viewer, line):
            continue
        platform = platform_of_line[line_id]
        for share in SATT.parse_splits(str(row["splits"])):
            if share.teammate:
                out.add((platform, "", share.teammate))
            if share.team:
                out.add((platform, share.team, ""))
    return out


def campaigns_visible(ctx: Ctx, viewer: str, owners: Iterable[Owner], first: str, last: str) -> set[Owner]:
    """The campaign kind of the view rule: which of the owners' campaigns the viewer sees in [first, last]. Campaigns are
    not ledger cells, so the rule works on owners: a full-detail reader sees all; a campaign's owner teammate sees it;
    the members and leads of an owning team do; a manager of the owner does (someone above a teammate, or a team's lead);
    and anyone who sees a platform charge line whose share names the owner does. The platform lines are read once for
    the whole list."""
    owners = list(owners)
    if ctx.view(viewer).everything:
        return set(owners)
    seen: set[Owner] = set()
    pending: list[Owner] = []
    under = ctx.org.reports(viewer)
    led = ctx.org.led_by(viewer)
    for owner in owners:
        platform, team, mate = owner
        if mate and mate == viewer:
            seen.add(owner)
        elif team and (team in led or ctx.org.team_of.get(viewer, "") in ctx.org.below(team)):
            seen.add(owner)
        elif mate and (mate in under or manager_of(ctx.org, mate) == viewer):
            seen.add(owner)
        else:
            pending.append(owner)
    if pending:
        attributed = _platform_attributed(ctx, viewer, {o[0] for o in pending}, first, last)
        for owner in pending:
            platform, team, mate = owner
            if (platform, team, mate) in attributed:
                seen.add(owner)
    return seen


def _owns(ctx: Ctx, viewer: str, split: CA.Split, under: frozenset[str], led: frozenset[str], own_team: str) -> bool:
    """Whether a share in force on a subscription's latest charge names the viewer: the teammate itself, someone under
    them, or the manager of that teammate; or a team the viewer leads or belongs to (below the team)."""
    if split.teammate:
        return split.teammate == viewer or split.teammate in under or manager_of(ctx.org, split.teammate) == viewer
    if split.team:
        return split.team in led or own_team in ctx.org.below(split.team)
    return False


def subscriptions_visible(
    ctx: Ctx, viewer: str, items: Iterable[tuple[str, Sequence[CA.Split], bool]]
) -> set[str]:
    """The subscription kind of the view rule (subscriptions.py): which subscriptions the viewer sees. Subscriptions are
    not ledger cells, so the rule works on each one's owners and its latest charge: items are (id, the attribution in force
    on its latest charge line, whether the viewer sees that line). A full-detail reader sees all. Otherwise the viewer sees
    a subscription whose owners name them (campaigns_visible's owner rule), or who sees its latest charge line."""
    view = ctx.view(viewer)
    rows = list(items)
    if view.everything:
        return {sid for sid, _, _ in rows}
    under = ctx.org.reports(viewer)
    led = ctx.org.led_by(viewer)
    own_team = ctx.org.team_of.get(viewer, "")
    return {
        sid
        for sid, splits, line_seen in rows
        if line_seen or any(_owns(ctx, viewer, sp, under, led, own_team) for sp in splits)
    }
