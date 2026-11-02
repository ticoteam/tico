"""Record shapes that need no I/O. Callers check their own rights and accounts and turn a RecordError into their own
error."""
from collections.abc import Sequence

LOCATION_KINDS = ("store", "office", "warehouse", "plant", "customer_site", "vehicle", "other")
ITEM_KINDS = ("service", "inventory", "non_inventory", "bundle", "category", "raw_material", "assembly")
# Item kinds QuickBooks and Xero have no item type for: a mirrored book does not take them.
TICO_ONLY_ITEM_KINDS = ("raw_material", "assembly")
PROJECT_STATUSES = ("open", "closed")
# Owned equipment (a van, a tool) is the company's; a customer's unit sits at that customer.
EQUIPMENT_KINDS = ("owned", "customer")
MAX_NAME = 200


class RecordError(ValueError):
    """A record that breaks a shape rule."""


def name(value: str) -> str:
    """A name, trimmed: not blank and at most MAX_NAME characters."""
    text = value.strip()
    if not text or len(text) > MAX_NAME:
        raise RecordError(f"a name is 1 to {MAX_NAME} characters")
    return text


def choice(value: str, choices: Sequence[str], what: str) -> str:
    if value not in choices:
        raise RecordError(f"{what} is {'|'.join(choices)}")
    return value


def price(value: int | None) -> int | None:
    """A price in minor units. Never negative; None when the item has no price."""
    if value is not None and value < 0:
        raise RecordError("a price is 0 or more")
    return value
