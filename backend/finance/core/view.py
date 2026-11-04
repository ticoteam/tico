"""View access: what a teammate can see (spec §7.5). A view is the default from where they sit on the org chart, plus
active grants, minus private lines. Every read and every action resolves one and asks it about each object.

Default (company setting, seeded): the owner sees everything; managers the expenses of the teams they lead and the
teams under them, and of everyone who reports to them, directly or not; everyone else their own expenses (lines tagged
with them). Revenue, the balance sheet, payables, receivables and sources are owner-only unless granted. Unattributed
lines show only in company-wide views.
"""
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date

from . import dates
from .errors import FinanceError, denied, invalid
from .ledger import PNL_TYPES, Account, descendants
from .org import SYSTEM, Org

WHAT = ("everything", "pnl", "expenses", "pnl_lines", "balance_sheet", "bs_lines", "payables", "receivables", "sources")
WHOSE = ("own", "team", "company")
FLAGS = ("payables", "receivables", "sources")
LINE_GRANTS = ("pnl_lines", "bs_lines")
GRANT_STATES = ("active", "suspended", "expired", "revoked")
MANAGER_DEFAULTS = ("team_expenses", "own_expenses")
MEMBER_DEFAULTS = ("own_expenses", "none")
PAYROLL_DEFAULTS = ("totals", "hidden")
OTHERS_SEE = ("totals", "hidden")
# What each role sees by default (`fin_view_defaults`, spec §7.5); the owner always sees everything.
MANAGER_POLICIES = ("team_expenses", "own_expenses")
MEMBER_POLICIES = ("own_expenses", "none")
PAYROLL_POLICIES = ("totals", "hidden")
PRIVATE_LABEL = "Payroll (private)"
MIN_PAID = 3                         # a department's payroll cell shows only with at least this many paid people


@dataclass(frozen=True)
class Defaults:
    """The company's default views (`fin_view_defaults`)."""
    manager: str = "team_expenses"
    member: str = "own_expenses"
    manager_payroll: str = "totals"   # "hidden": managers never see their department's payroll total


@dataclass(frozen=True)
class Grant:
    id: str
    subject_kind: str               # teammate | team
    subject: str                    # an actor, or a team id
    what: str
    whose: str = "company"          # own | team | company
    whose_team: str = ""
    account_ids: frozenset[str] = frozenset()
    new_members: bool = False
    members: frozenset[str] = frozenset()   # a team grant's teammates when it was made
    granted_by: str = ""
    expires_at: str = ""
    status: str = "active"


@dataclass(frozen=True)
class PrivateLine:
    account_id: str
    others_see: str = "totals"
    full_detail: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Slice:
    """Part of the P&L a viewer sees: which accounts (`expense`, `pnl`, or the explicit `ids`) and whose lines
    (`company`: all, unattributed included; `teams`: lines tagged with one of `teams`; `mates`: tagged with one of
    `mates`, a manager's reports; `own`: tagged with the viewer)."""
    accounts: str
    whose: str
    teams: frozenset[str] = frozenset()
    ids: frozenset[str] = frozenset()
    mates: frozenset[str] = frozenset()

    def covers_account(self, account: Account) -> bool:
        if self.accounts == "ids":
            return account.id in self.ids
        if self.accounts == "pnl":
            return account.type in PNL_TYPES
        return account.type == "expense"

    def covers_tags(self, tags: Mapping[str, str], viewer: str) -> bool:
        if self.whose == "company":
            return True
        if self.whose == "teams":
            return tags.get("team", "") in self.teams
        if self.whose == "mates":
            return tags.get("teammate", "") in self.mates
        return bool(viewer) and tags.get("teammate", "") == viewer


@dataclass(frozen=True)
class View:
    viewer: str
    everything: bool = False
    slices: tuple[Slice, ...] = ()
    balance_sheet: bool = False
    bs_lines: frozenset[str] = frozenset()
    payables: bool = False
    receivables: bool = False
    sources: bool = False
    full_detail: frozenset[str] = frozenset()   # private accounts this viewer sees line by line
    department_payroll: bool = True
    version: int = 0
    grant_ids: tuple[str, ...] = field(default=())

    def sees(self, account: Account, tags: Mapping[str, str]) -> bool:
        """Whether a line on `account` with `tags` is in this view (private lines are cut later, by `suppress`)."""
        if self.everything:
            return True
        if account.type not in PNL_TYPES:
            return self.balance_sheet or account.id in self.bs_lines
        return any(s.covers_account(account) and s.covers_tags(tags, self.viewer) for s in self.slices)

    def sees_account(self, account: Account) -> bool:
        """Whether any line on `account` could be in this view."""
        if self.everything:
            return True
        if account.type not in PNL_TYPES:
            return self.balance_sheet or account.id in self.bs_lines
        return any(s.covers_account(account) for s in self.slices)

    def sees_anything(self) -> bool:
        """Whether the viewer has a finance view at all: some P&L slice, balance sheet lines, or a side or source."""
        return bool(self.everything or self.slices or self.balance_sheet or self.bs_lines or self.payables
                    or self.receivables or self.sources)

    def company_wide(self, account: Account) -> bool:
        """Whether this viewer sees every line on `account`, unattributed ones included."""
        if self.everything:
            return True
        if account.type not in PNL_TYPES:
            return self.sees_account(account)
        return any(s.whose == "company" and s.covers_account(account) for s in self.slices)

    def company_expenses(self) -> bool:
        return self.everything or any(s.whose == "company" and s.accounts in ("expense", "pnl") for s in self.slices)

    def teams(self, account: Account | None = None) -> frozenset[str]:
        """Every team whose lines (on `account`, or on any expense line) this viewer sees."""
        out: set[str] = set()
        for s in self.slices:
            if s.whose == "teams" and (account is None or s.covers_account(account)):
                out |= s.teams
        return frozenset(out)

    def mates(self, account: Account | None = None) -> frozenset[str]:
        """Every teammate (a manager's reports) whose lines (on `account`, or on any expense line) this viewer sees."""
        out: set[str] = set()
        for s in self.slices:
            if s.whose == "mates" and (account is None or s.covers_account(account)):
                out |= s.mates
        return frozenset(out)

    def covers_team(self, account: Account, team: str, below: frozenset[str]) -> bool:
        """Whether this viewer sees all of `team` (every team in `below`) on `account`."""
        return self.company_wide(account) or bool(below) and below <= self.teams(account)


def whose_slice(accounts: str, whose: str, team_below: frozenset[str], ids: frozenset[str] = frozenset()) -> Slice:
    if whose == "company":
        return Slice(accounts=accounts, whose="company", ids=ids)
    if whose == "team":
        return Slice(accounts=accounts, whose="teams", teams=team_below, ids=ids)
    return Slice(accounts=accounts, whose="own", ids=ids)


def default_slices(org: Org, viewer: str, defaults: Defaults) -> tuple[Slice, ...]:
    """A viewer's default from where they sit: own expenses, and a manager's teams' and reports' expenses. Reporting
    lines and team leads come from the org chart, so the view follows them when they change."""
    out: list[Slice] = []
    led, mates = org.led_by(viewer), org.reports(viewer)
    if defaults.manager == "team_expenses":
        if led:
            out.append(Slice(accounts="expense", whose="teams", teams=led))
        if mates:
            out.append(Slice(accounts="expense", whose="mates", mates=mates))
    if led or mates or defaults.member == "own_expenses":
        out.append(Slice(accounts="expense", whose="own"))
    return tuple(out)


def live(grant: Grant, at: str) -> bool:
    return grant.status == "active" and (not grant.expires_at or grant.expires_at > at)


def applies(org: Org, grant: Grant, viewer: str) -> bool:
    """Whether `grant` reaches `viewer` now: its teammate, or a member of its team. A team grant reaches the members it
    was made for (`members`, a snapshot) only while they are still on the team, so anyone who leaves loses it at once.
    A later joiner gets it only with new_members, which reads the team as it is now (spec §7.5)."""
    if grant.subject_kind == "teammate":
        return grant.subject == viewer
    on_team = org.team_of.get(viewer, "") in org.below(grant.subject)
    if grant.new_members:
        return on_team
    return viewer in grant.members and on_team


def resolve(org: Org, viewer: str, *, grants: Iterable[Grant], defaults: Defaults,
            private: Mapping[str, PrivateLine], accounts: Mapping[str, Account], at: str, version: int = 0) -> View:
    """The viewer's view now. System work sees everything; the owner sees everything, private detail included."""
    if viewer == SYSTEM or (org.owner and viewer == org.owner):
        return View(viewer=viewer, everything=True, full_detail=frozenset(private), version=version)
    slices = list(default_slices(org, viewer, defaults))
    everything = balance_sheet = payables = receivables = sources = False
    bs_lines: set[str] = set()
    used: list[str] = []
    for grant in grants:
        if not live(grant, at) or not applies(org, grant, viewer) or not standing(org, grant, defaults, accounts):
            continue
        used.append(grant.id)
        below = org.below(grant.whose_team) if grant.whose == "team" else frozenset[str]()
        ids = frozenset[str]().union(*(descendants(accounts, a) for a in grant.account_ids)) if grant.account_ids \
            else frozenset[str]()
        if grant.what == "everything":
            everything = True
        elif grant.what == "pnl":
            slices.append(whose_slice("pnl", grant.whose, below))
        elif grant.what == "expenses":
            slices.append(whose_slice("expense", grant.whose, below))
        elif grant.what == "pnl_lines":
            slices.append(whose_slice("ids", grant.whose, below, ids))
        elif grant.what == "balance_sheet":
            balance_sheet = True
        elif grant.what == "bs_lines":
            bs_lines |= ids
        elif grant.what == "payables":
            payables = True
        elif grant.what == "receivables":
            receivables = True
        elif grant.what == "sources":
            sources = True
    detail = frozenset(a for a, p in private.items() if viewer in p.full_detail)
    manager_payroll = not org.manages(viewer) or defaults.manager_payroll == "totals"
    return View(viewer=viewer, everything=everything, slices=tuple(slices), balance_sheet=balance_sheet,
                bs_lines=frozenset(bs_lines), payables=payables, receivables=receivables, sources=sources,
                full_detail=detail, department_payroll=manager_payroll, version=version, grant_ids=tuple(used))


# ------------------------------------------------------------------------------------------------- who may grant
def check_grant(org: Org, grantor: str, grant: Grant, defaults: Defaults, accounts: Mapping[str, Account]) -> str:
    """Whether `grantor` may make `grant`: "ok", "approval" (a bot's grant, which a person approves), or a denial.

    Owners grant anything. A manager grants to teammates in the teams they lead and to those who report to them, only
    from their own default view (their teams' expenses, or expense lines of them), never company-wide and never what
    they were granted. Nobody grants to themselves."""
    check_shape(grant, org, accounts)
    if grant.subject_kind == "teammate" and grant.subject == grantor:
        raise denied("nobody grants access to themselves")
    if org.owner and grantor == org.owner:
        return "ok"
    led, mates = org.led_by(grantor), org.reports(grantor)
    if not (led or mates) or defaults.manager != "team_expenses":
        raise denied("only the owner or the subject's manager grants Finance access")
    if grant.subject_kind == "team":
        theirs = grant.subject in led
    else:
        theirs = org.team_of.get(grant.subject, "") in led or grant.subject in mates
    if not theirs:
        raise denied("a manager grants only to teammates in the teams they manage")
    if grant.what not in ("expenses", "pnl_lines") or grant.whose == "company":
        raise denied("a manager grants only their teams' expenses")
    if grant.whose == "team" and grant.whose_team not in led:
        raise denied("a manager grants only the expenses of teams they manage")
    if grant.what == "pnl_lines" and any(accounts[a].type != "expense" for a in grant.account_ids):
        raise denied("a manager grants only expense lines")
    return "approval" if grantor in org.bots else "ok"


def check_shape(grant: Grant, org: Org, accounts: Mapping[str, Account]) -> None:
    if grant.what not in WHAT:
        raise invalid(f"what is {'|'.join(WHAT)}")
    if grant.whose not in WHOSE:
        raise invalid(f"whose is {'|'.join(WHOSE)}")
    if grant.subject_kind == "team" and grant.subject not in org.teams:
        raise invalid("no such team")
    if grant.subject_kind == "teammate" and grant.subject not in org.humans | org.bots:
        raise invalid("no such teammate")
    if grant.whose == "team" and grant.whose_team not in org.teams:
        raise invalid("whose names no team")
    if grant.what in LINE_GRANTS:
        if not grant.account_ids:
            raise invalid(f"{grant.what} names the account ids")
        for account_id in grant.account_ids:
            account = accounts.get(account_id)
            if account is None:
                raise invalid("no such account")
            if (account.type in PNL_TYPES) != (grant.what == "pnl_lines"):
                raise invalid("pnl_lines names P&L accounts and bs_lines balance sheet accounts")
    elif grant.account_ids:
        raise invalid(f"{grant.what} takes no account ids")
    if grant.what in ("balance_sheet", "bs_lines", "everything") + FLAGS and grant.whose != "company":
        raise invalid(f"{grant.what} is always company-wide")


def standing(org: Org, grant: Grant, defaults: Defaults, accounts: Mapping[str, Account]) -> bool:
    """Whether the grantor could still make `grant` now. One that exceeds its grantor's authority (they no longer lead
    the team, the subject moved, the owner changed) counts as suspended from the moment it does, written or not."""
    if not grant.granted_by or grant.granted_by == SYSTEM:
        return True
    try:
        check_grant(org, grant.granted_by, grant, defaults, accounts)
    except FinanceError:
        return False
    return True


def unstanding(org: Org, grants: Iterable[Grant], defaults: Defaults, accounts: Mapping[str, Account]) -> list[str]:
    """Active grants their grantor could not make now: what a write path marks suspended."""
    return [g.id for g in grants if g.status == "active" and not standing(org, g, defaults, accounts)]


def left_team(org: Org, grant: Grant) -> frozenset[str]:
    """The members a team grant was made for who are no longer on its team (spec §7.5: they lose it at once). Such a
    member is forgotten from the grant's snapshot, so rejoining the team does not bring the grant back."""
    if grant.subject_kind != "team" or grant.new_members:
        return frozenset()
    below = org.below(grant.subject)
    return frozenset(m for m in grant.members if org.team_of.get(m, "") not in below)


def may_revoke(org: Org, actor: str, grant: Grant) -> bool:
    return actor == grant.granted_by or bool(org.owner) and actor == org.owner


# ------------------------------------------------------------------------------------------------- private lines
@dataclass(frozen=True)
class Cell:
    """A total behind a read: one account on one day for one team, teammate and vendor."""
    account_id: str
    day: str
    team: str
    teammate: str
    vendor: str
    amount: int
    linked: bool = False            # settled private payroll lines were netted out of it (reads._cells); totals say so
    location: str = ""                 # the location, class and project tag values of its lines ("" when none)
    class_id: str = ""
    project: str = ""


@dataclass(frozen=True)
class Suppressed:
    cells: tuple[Cell, ...]
    hidden: frozenset[str]          # private accounts with something cut: they show as "Payroll (private)"
    excluded: bool                  # totals built from `cells` are "excl. payroll"


def suppress(view: View, org: Org, private: Mapping[str, PrivateLine], accounts: Mapping[str, Account],
             cells: Iterable[Cell], narrow: bool, *, window: tuple[date, date], closed: frozenset[str],
             fiscal_start: int = 1) -> Suppressed:
    """The one suppression function every surface uses (spec §7.5). It returns the cells in `window`.

    Without full detail a private account (payroll by person) shows only as fixed cells: department x pay run, where a
    run is that account's lines on one pay date. A run with MIN_PAID or more paid people in the department is one cell
    on its pay date; smaller (off-cycle) runs show only inside one cell per department and fiscal quarter, dated the
    quarter's last day, when those runs paid MIN_PAID or more people. A cell shows only when its whole run (or quarter)
    is in closed periods, so it never changes once seen, and only to a viewer who covers the whole department. The cells
    come from the runs, never from the caller's dates: a window takes in a cell whole by its date or not at all, so
    `cells` must reach the whole fiscal quarters around `window`. A narrower read (a team below a department, a teammate,
    a vendor) shows none of it. A teammate sees their own lines only in a result with no department cell on that
    account, so own lines and a department total never sit side by side. Unattributed private lines never show.

    A `linked` cell has already had a settled private payroll entry's lines taken out of it, so it only marks the
    totals "excl. payroll", and stays only while anything of its own is left."""
    lo, hi = window[0].isoformat(), window[1].isoformat()

    def inside(day: str) -> bool:
        return lo <= day <= hi
    kept: list[Cell] = []
    cut: set[str] = set()
    own: list[Cell] = []
    runs: dict[tuple[str, str, str], list[Cell]] = {}
    settled = False
    for cell in cells:
        if cell.linked:
            if inside(cell.day):
                settled = True
                if cell.amount:
                    kept.append(cell)
            continue
        rule = private.get(cell.account_id)
        if rule is None or cell.account_id in view.full_detail:
            if inside(cell.day):
                kept.append(cell)
            continue
        department = org.department(cell.team) if cell.team else ""
        account = accounts.get(cell.account_id)
        whole = account is not None and department != "" and \
            view.covers_team(account, department, org.below(department))
        if rule.others_see == "hidden" or narrow or not view.department_payroll or not whole:
            _unshown(view, [cell], inside, own, cut)
            continue
        runs.setdefault((cell.account_id, department, cell.day), []).append(cell)
    shown: list[Cell] = []
    quarters: dict[tuple[str, str, date, date], list[Cell]] = {}
    for (account_id, department, day), group in runs.items():
        if len({x.teammate for x in group if x.teammate}) < MIN_PAID:
            q = dates.fiscal_bounds(dates.day(day), 3, fiscal_start)
            quarters.setdefault((account_id, department, q[0], q[1]), []).extend(group)
        elif inside(day) and day[:7] in closed:
            shown.append(Cell(account_id, day, department, "", "", sum(x.amount for x in group)))
        else:
            _unshown(view, group, inside, own, cut)
    for (account_id, department, start, end), group in quarters.items():
        months = {dates.add_months(start, n).isoformat()[:7] for n in range(3)}
        if inside(end.isoformat()) and months <= closed and \
                len({x.teammate for x in group if x.teammate}) >= MIN_PAID:
            shown.append(Cell(account_id, end.isoformat(), department, "", "", sum(x.amount for x in group)))
        else:
            _unshown(view, group, inside, own, cut)
    with_cell = {x.account_id for x in shown}
    for cell in own:
        if cell.account_id in with_cell:
            cut.add(cell.account_id)
        else:
            kept.append(cell)
    kept.extend(shown)
    return Suppressed(cells=tuple(kept), hidden=frozenset(cut), excluded=bool(cut) or settled)


def _unshown(view: View, group: Iterable[Cell], inside: Callable[[str], bool], own: list[Cell],
             cut: set[str]) -> None:
    """Private cells in the window that no department cell shows: the viewer's own wait in `own`, the rest are cut."""
    for cell in group:
        if not inside(cell.day):
            continue
        if cell.teammate and cell.teammate == view.viewer:
            own.append(cell)
        else:
            cut.add(cell.account_id)
