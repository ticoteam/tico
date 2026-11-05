"""What one Finance call works from: the caller's connection and clock, the book and its chart, the org chart, and
each teammate's rights and view, loaded once per call and never across calls (a revoked grant is gone on the next)."""
import sqlite3
from dataclasses import dataclass, field

from .core import budgets as B
from .core import view as VW
from .core.errors import FinanceError, NOT_FOUND
from .core.ledger import Account, Book
from .core.org import SYSTEM, Org
from .store import access as SA
from .store import budgets as SB
from .store import ledger as SL
from .store import tico

OWNER_KEY = "owner"                       # who Finance was set up for: the owner when Tico has no owner record
APPROVER_KEY = "finance_approver"         # the one person finance-admin approvals go to (else the owner)


def now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class Ctx:
    c: sqlite3.Connection
    at: str
    book: Book
    accounts: dict[str, Account]
    org: Org
    _rights: dict[str, frozenset[str]] = field(default_factory=lambda: dict[str, frozenset[str]]())
    _views: dict[str, VW.View] = field(default_factory=lambda: dict[str, VW.View]())
    _budgets: dict[str, B.Budget] | None = None
    _private: dict[str, VW.PrivateLine] | None = None
    deciding: bool = False       # set while an approval is rechecked: outcomes then use the true figures
    decider: str = ""            # the person whose decision an approved action commits on (approval_decided), or ""

    @property
    def today(self) -> str:
        return self.at[:10]

    def rights(self, actor: str) -> frozenset[str]:
        if actor not in self._rights:
            self._rights[actor] = SA.rights_of(self.c, actor, self.at)
        return self._rights[actor]

    def view(self, viewer: str) -> VW.View:
        if viewer not in self._views:
            self._views[viewer] = VW.resolve(self.org, viewer, grants=SA.grants(self.c), defaults=SA.defaults(self.c),
                                             private=SA.private_lines(self.c), accounts=self.accounts, at=self.at,
                                             version=SA.view_version(self.c))
        return self._views[viewer]

    def budgets(self) -> dict[str, B.Budget]:
        if self._budgets is None:
            self._budgets = SB.budgets(self.c, self.book.id)
        return self._budgets

    def private(self) -> dict[str, VW.PrivateLine]:
        if self._private is None:
            self._private = SA.private_lines(self.c)
        return self._private

    def changed(self) -> None:
        """Forget what this call cached after it writes accounts, budgets or access."""
        self.accounts = SL.accounts(self.c, self.book.id)
        self._budgets = None
        self._private = None
        self._views.clear()
        self._rights.clear()

    def approver_setting(self) -> str:
        value = SL.setting(self.c, APPROVER_KEY)
        return value if isinstance(value, str) else ""


def owner(c: sqlite3.Connection) -> str:
    fallback = SL.setting(c, OWNER_KEY)
    return tico.owner_person(c, fallback if isinstance(fallback, str) else "")


def load(c: sqlite3.Connection, at: str | None = None) -> Ctx:
    book = SL.book(c)
    if book is None:
        raise FinanceError(NOT_FOUND, "Finance is not set up on this install")
    return Ctx(c=c, at=at or now(), book=book, accounts=SL.accounts(c, book.id), org=tico.org(c, owner(c)))


def is_set_up(c: sqlite3.Connection) -> bool:
    return SL.book(c) is not None


__all__ = ["Ctx", "load", "owner", "now", "is_set_up", "SYSTEM"]
