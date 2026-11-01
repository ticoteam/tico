"""Finance rights: what a teammate may ask for (spec §7.4). Seeing is separate (view.py); amounts are gate rules."""
from collections.abc import Collection

from .org import SYSTEM, Org

BOOK, AP, AR, PAY, CARDS, BUDGETS, RULES, ADMIN = (
    "finance.book", "finance.ap", "finance.ar", "finance.pay", "finance.cards", "finance.budgets", "finance.rules",
    "finance.admin")
RECORDS = "finance.records"           # items, locations, projects, equipment and due dates (backend/records)
RIGHTS = (BOOK, AP, AR, PAY, CARDS, BUDGETS, RULES, ADMIN, RECORDS)
# System work posts usage and keeps budgets current; it never grants, pays or changes access.
SYSTEM_RIGHTS = frozenset({BOOK, BUDGETS})
# What a default Bookkeeper is set up with (spec §7.4).
BOOKKEEPER = (BOOK, AP, AR, PAY, CARDS)


def has(org: Org, actor: str, right: str, held: Collection[str]) -> bool:
    """Whether `actor` holds `right`: the owner holds every right, `finance.admin` implies the rest, and a bot holds
    only the rights granted to it."""
    if actor == SYSTEM:
        return right in SYSTEM_RIGHTS
    if org.owner and actor == org.owner:
        return True
    return right in held or ADMIN in held


def is_admin(org: Org, actor: str, held: Collection[str]) -> bool:
    """Finance admin: the owner and anyone granted `finance.admin`."""
    return actor != SYSTEM and has(org, actor, ADMIN, held)
