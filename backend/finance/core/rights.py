"""Finance rights: what a teammate may ask for (spec §7.4). Seeing is separate (view.py); amounts are gate rules."""
from collections.abc import Collection

from .org import SYSTEM, Org

BOOK, AP, AR, PAY, CARDS, BUDGETS, RULES, ADMIN, RECORDS = (
    "finance.book", "finance.ap", "finance.ar", "finance.pay", "finance.cards", "finance.budgets", "finance.rules",
    "finance.admin", "finance.records")
RIGHTS = (BOOK, AP, AR, PAY, CARDS, BUDGETS, RULES, ADMIN, RECORDS)
# System work posts usage, keeps budgets current and writes the shared records the books hold (the books import and
# the mirror, which write rows directly); it never grants, pays or changes access.
SYSTEM_RIGHTS = frozenset({BOOK, BUDGETS, RECORDS})
# What a default Bookkeeper is set up with (spec §7.4). Records came with the Bookkeeper's book rights before the
# records right existed, so a new Bookkeeper keeps writing records.
BOOKKEEPER = (BOOK, AP, AR, PAY, CARDS, RECORDS)


def has(org: Org, actor: str, right: str, held: Collection[str]) -> bool:
    """Whether `actor` holds `right`: the owner holds every right, a company Admin holds the records right, and
    `finance.admin` implies the rest. A bot holds only the rights granted to it."""
    if actor == SYSTEM:
        return right in SYSTEM_RIGHTS
    if org.owner and actor == org.owner:
        return True
    if right == RECORDS and actor in org.admins:
        return True
    return right in held or ADMIN in held


def is_admin(org: Org, actor: str, held: Collection[str]) -> bool:
    """Finance admin: the owner and anyone granted `finance.admin`."""
    return actor != SYSTEM and has(org, actor, ADMIN, held)
