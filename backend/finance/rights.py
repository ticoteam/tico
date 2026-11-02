"""Setting up a helper's Finance rights in one step (the Bookkeeper bot gets finance.book, ap, ar, pay, cards and rules
when it is added), inside the caller's transaction. What a right lets the holder ask is core/rights and core/kinds; this only
writes the grants a hire comes with."""
import sqlite3
from collections.abc import Iterable

from .core import rights as R
from .core.errors import invalid
from .core.org import SYSTEM
from .store import access as SA
from .store import tico


def grant_defaults(c: sqlite3.Connection, teammate: str, rights: Iterable[str] = R.BOOKKEEPER, *, by: str = SYSTEM,
                   at: str | None = None) -> list[str]:
    """Grant each right `teammate` does not hold yet (finance.admin is the owner's to give, never a default). The
    rights newly granted; a second call grants nothing."""
    wanted = list(dict.fromkeys(rights))
    if any(r not in R.RIGHTS for r in wanted):
        raise invalid(f"rights are {'|'.join(R.RIGHTS)}")
    if R.ADMIN in wanted:
        raise invalid("finance.admin is the owner's to give")
    when = at or tico.now()
    held = SA.rights_of(c, teammate, when)
    added = [r for r in wanted if r not in held]
    for r in added:
        SA.grant_right(c, teammate, r, by, None, when)
    return added


__all__ = ["grant_defaults"]
