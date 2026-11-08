"""The org chart as Finance reads it, who is acting, and who decides an approval (spec §5, §7.4, §7.5).

Teammates are actor strings (`human:<id>`, `bot:<id>`), teams are org group ids. The store builds an `Org` from the
roster once per request; nothing here reads it.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field

SYSTEM = "system:finance"           # sync, posting rules, setup and usage postings
APPROVERS = ("owner", "finance_admin", "manager")


@dataclass(frozen=True)
class Team:
    id: str
    name: str
    parent: str = ""
    lead: str = ""                  # an actor, "" when the group has no lead


@dataclass(frozen=True)
class Org:
    owner: str                      # the install owner as `human:<id>`
    teams: Mapping[str, Team] = field(default_factory=lambda: dict[str, Team]())
    team_of: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())
    reports_to: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())
    humans: frozenset[str] = frozenset()
    bots: frozenset[str] = frozenset()

    def below(self, team: str) -> frozenset[str]:
        """`team` and every team nested under it."""
        if team not in self.teams:
            return frozenset()
        found, changed = {team}, True
        while changed:
            changed = False
            for other in self.teams.values():
                if other.id not in found and other.parent in found:
                    found.add(other.id)
                    changed = True
        return frozenset(found)

    def chain(self, team: str) -> list[str]:
        """`team` and the teams above it, nearest first."""
        out: list[str] = []
        at = team
        while at in self.teams and at not in out:
            out.append(at)
            at = self.teams[at].parent
        return out

    def department(self, team: str) -> str:
        """The top-level team `team` sits in: what payroll totals are grouped by."""
        chain = self.chain(team)
        return chain[-1] if chain else ""

    def led_by(self, actor: str) -> frozenset[str]:
        """The teams `actor` leads and every team under them: what a manager's default view covers."""
        out: set[str] = set()
        for team in self.teams.values():
            if team.lead and team.lead == actor:
                out |= self.below(team.id)
        return frozenset(out)

    def reports(self, actor: str) -> frozenset[str]:
        """Everyone who reports to `actor`, directly or not: with `led_by`, what a manager's default covers."""
        under: dict[str, list[str]] = {}
        for mate, boss in self.reports_to.items():
            under.setdefault(boss, []).append(mate)
        found: set[str] = set()
        todo = [actor]
        while todo:
            for mate in under.get(todo.pop(), []):
                if mate != actor and mate not in found:
                    found.add(mate)
                    todo.append(mate)
        return frozenset(found)

    def manages(self, actor: str) -> bool:
        """Whether `actor` is a manager: leads a team or has anyone reporting to them."""
        return bool(self.led_by(actor) or self.reports(actor))

    def members(self, teams: frozenset[str]) -> frozenset[str]:
        return frozenset(actor for actor, team in self.team_of.items() if team in teams)

    def is_person(self, actor: str) -> bool:
        return actor in self.humans


@dataclass(frozen=True)
class Principal:
    """Who is acting. `actor` made the call; `for_actor` is whose authority it uses (the Assistant and BotOps act as
    the teammate who asked, BotOps parity), and `rule` the finance rule it runs under. Rights and view are always the
    effective teammate's (`who`), never a manager's."""
    actor: str
    for_actor: str = ""
    rule: str = ""

    @property
    def who(self) -> str:
        return self.for_actor or self.actor


def principal(value: "Principal | str") -> Principal:
    return value if isinstance(value, Principal) else Principal(actor=str(value))


def manager_of(org: Org, actor: str) -> str:
    """The nearest person above `actor`: up the reports-to chain (through bot managers), then up the team leads.
    A bot never decides, and nobody is their own manager. "" when there is none."""
    seen = {actor}
    at = org.reports_to.get(actor, "")
    while at and at not in seen:
        if org.is_person(at):
            return at
        seen.add(at)
        at = org.reports_to.get(at, "")
    for team in org.chain(org.team_of.get(actor, "")):
        lead = org.teams[team].lead
        if lead and lead != actor:
            if org.is_person(lead):
                return lead
            above = manager_of(org, lead) if lead not in seen else ""
            if above and above != actor:
                return above
    return ""


def approver(org: Org, who: str, requester: str, finance_approver: str = "", budget_owner: str = "") -> str:
    """The one person who decides: `owner` is the install owner; `finance_admin` the named finance approver when that
    is a person, else the owner; `manager` the nearest person above the requester, else the owner; `budget_owner` the
    parent budget's owner when that is a person, else the owner."""
    if who == "finance_admin" and finance_approver and org.is_person(finance_approver):
        return finance_approver
    if who == "manager":
        return manager_of(org, requester) or org.owner
    if who == "budget_owner" and org.is_person(budget_owner):
        return budget_owner
    return org.owner
