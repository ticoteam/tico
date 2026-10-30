"""View access: what a teammate can see (spec §7.5). A view is the default from where they sit on the org chart, plus
active grants, minus private lines. Every read and every action resolves one and asks it about each object.

Default (company setting, seeded): the owner sees everything; managers the expenses of the teams they lead and the
teams under them; everyone else their own expenses (lines tagged with them). Revenue, the balance sheet, payables,
receivables and sources are owner-only unless granted. Unattributed lines show only in company-wide views.
"""
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .errors import denied, invalid
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
PRIVATE_LABEL = "Payroll (private)"
MIN_PAID = 3                         # a department's payroll total shows only with at least this many paid people


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
    (`company`: all, unattributed included; `teams`: lines tagged with one of `teams`; `own`: tagged with the viewer)."""
    accounts: str
    whose: str
    teams: frozenset[str] = frozenset()
    ids: frozenset[str] = frozenset()

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
    """A viewer's default from where they sit: own expenses, and a manager's teams' expenses."""
    out: list[Slice] = []
    led = org.led_by(viewer)
    if led and defaults.manager == "team_expenses":
        out.append(Slice(accounts="expense", whose="teams", teams=led))
    if led or defaults.member == "own_expenses":
        out.append(Slice(accounts="expense", whose="own"))
    return tuple(out)


def live(grant: Grant, at: str) -> bool:
    return grant.status == "active" and (not grant.expires_at or grant.expires_at > at)


def applies(org: Org, grant: Grant, viewer: str) -> bool:
    """Whether `grant` reaches `viewer`: its teammate, or a member of its team (a later joiner only if it says so)."""
    if grant.subject_kind == "teammate":
        return grant.subject == viewer
    if grant.new_members:
        return org.team_of.get(viewer, "") in org.below(grant.subject)
    return viewer in grant.members


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
        if not live(grant, at) or not applies(org, grant, viewer):
            continue
        used.append(grant.id)
        below = org.below(grant.whose_team) if grant.whose == "team" else frozenset[str]()
        ids = frozenset().union(*(descendants(accounts, a) for a in grant.account_ids)) if grant.account_ids \
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
    manager_payroll = not org.led_by(viewer) or defaults.manager_payroll == "totals"
    return View(viewer=viewer, everything=everything, slices=tuple(slices), balance_sheet=balance_sheet,
                bs_lines=frozenset(bs_lines), payables=payables, receivables=receivables, sources=sources,
                full_detail=detail, department_payroll=manager_payroll, version=version, grant_ids=tuple(used))


# ------------------------------------------------------------------------------------------------- who may grant
def check_grant(org: Org, grantor: str, grant: Grant, defaults: Defaults, accounts: Mapping[str, Account]) -> str:
    """Whether `grantor` may make `grant`: "ok", "approval" (a bot's grant, which a person approves), or a denial.

    Owners grant anything. A manager grants to teammates in the teams they lead, only from their own default view
    (their teams' expenses, or expense lines of them), never company-wide and never what they were granted. Nobody
    grants to themselves."""
    check_shape(grant, org, accounts)
    if grant.subject_kind == "teammate" and grant.subject == grantor:
        raise denied("nobody grants access to themselves")
    if org.owner and grantor == org.owner:
        return "ok"
    led = org.led_by(grantor)
    if not led or defaults.manager != "team_expenses":
        raise denied("only the owner or the subject's manager grants Finance access")
    subject_team = grant.subject if grant.subject_kind == "team" else org.team_of.get(grant.subject, "")
    if subject_team not in led:
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


@dataclass(frozen=True)
class Suppressed:
    cells: tuple[Cell, ...]
    hidden: frozenset[str]          # private accounts with something cut: they show as "Payroll (private)"
    excluded: bool                  # totals built from `cells` are "excl. payroll"


def suppress(view: View, org: Org, private: Mapping[str, PrivateLine], accounts: Mapping[str, Account],
             cells: Iterable[Cell], narrow: bool) -> Suppressed:
    """The one suppression function every surface uses (spec §7.5). Without full detail a private account (payroll by
    person) shows only as one total per department of at least MIN_PAID paid people, in every month of the window,
    and only when the view covers the whole department. A narrower read (by team below a department, by teammate, by
    vendor) shows none of it. Unattributed private lines never show. A teammate always sees their own lines. What is
    cut is cut from every total, so nothing can be worked out by subtraction."""
    rows = list(cells)
    cut: set[str] = set()
    kept: list[Cell] = []
    groups: dict[tuple[str, str], list[Cell]] = {}
    for cell in rows:
        rule = private.get(cell.account_id)
        if rule is None or cell.account_id in view.full_detail:
            kept.append(cell)
            continue
        own = bool(cell.teammate) and cell.teammate == view.viewer
        department = org.department(cell.team) if cell.team else ""
        account = accounts.get(cell.account_id)
        whole = account is not None and department != "" and \
            view.covers_team(account, department, org.below(department))
        if rule.others_see == "hidden" or narrow or not view.department_payroll or not whole:
            if own:
                kept.append(cell)
            else:
                cut.add(cell.account_id)
            continue
        groups.setdefault((cell.account_id, department), []).append(cell)
    for (account_id, _department), group in groups.items():
        paid: dict[str, set[str]] = {}
        for cell in group:
            if cell.teammate:
                paid.setdefault(cell.day[:7], set()).add(cell.teammate)
        months = {cell.day[:7] for cell in group}
        if all(len(paid.get(m, set())) >= MIN_PAID for m in months):
            kept.extend(group)
            continue
        kept.extend(cell for cell in group if cell.teammate and cell.teammate == view.viewer)
        cut.add(account_id)
    return Suppressed(cells=tuple(kept), hidden=frozenset(cut), excluded=bool(cut))
