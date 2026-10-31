"""Reading an action's payload: every field is checked and typed here, so nothing downstream handles raw JSON."""
from collections.abc import Mapping, Sequence
from typing import cast

from . import dates, money
from .errors import invalid

Payload = Mapping[str, object]
MAX_TEXT = 500
MAX_ITEMS = 500


def obj(value: object, what: str = "payload") -> Payload:
    if not isinstance(value, Mapping):
        raise invalid(f"{what} is an object")
    return cast(Payload, value)


def items(value: object, what: str, most: int = MAX_ITEMS) -> list[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise invalid(f"{what} is a list")
    out = list(cast(Sequence[object], value))
    if not out:
        raise invalid(f"{what} is empty")
    if len(out) > most:
        raise invalid(f"{what} has more than {most} items")
    return out


def text(p: Payload, key: str, most: int = MAX_TEXT) -> str:
    value = opt_text(p, key, most)
    if not value:
        raise invalid(f"{key} is required")
    return value


def opt_text(p: Payload, key: str, most: int = MAX_TEXT) -> str | None:
    value = p.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise invalid(f"{key} is text")
    value = value.strip()
    if len(value) > most:
        raise invalid(f"{key} is longer than {most} characters")
    return value or None


def amount(p: Payload, key: str = "amount") -> int:
    if key not in p:
        raise invalid(f"{key} is required")
    return money.minor(p.get(key), key)


def opt_amount(p: Payload, key: str = "amount") -> int | None:
    return None if p.get(key) is None else money.minor(p.get(key), key)


def integer(p: Payload, key: str, low: int, high: int, default: int | None = None) -> int:
    value = p.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise invalid(f"{key} is a whole number from {low} to {high}")
    return value


def choice(p: Payload, key: str, choices: Sequence[str], default: str | None = None) -> str:
    value = p.get(key, default)
    if not isinstance(value, str) or value not in choices:
        raise invalid(f"{key} is {'|'.join(choices)}")
    return value


def flag(p: Payload, key: str, default: bool = False) -> bool:
    value = p.get(key, default)
    if not isinstance(value, bool):
        raise invalid(f"{key} is true or false")
    return value


def day(p: Payload, key: str) -> str:
    if p.get(key) in (None, ""):
        raise invalid(f"{key} is required")
    return dates.day(p.get(key)).isoformat()


def opt_day(p: Payload, key: str) -> str | None:
    return None if p.get(key) in (None, "") else dates.day(p.get(key)).isoformat()


def month(p: Payload, key: str) -> str:
    if p.get(key) in (None, ""):
        raise invalid(f"{key} is required")
    return dates.month(p.get(key)).isoformat()[:7]


def texts(p: Payload, key: str, most: int = MAX_ITEMS) -> list[str]:
    out: list[str] = []
    for value in items(p.get(key), key, most):
        if not isinstance(value, str) or not value.strip():
            raise invalid(f"{key} holds text ids")
        if value.strip() not in out:
            out.append(value.strip())
    return out


def only(p: Payload, allowed: Sequence[str]) -> None:
    """Refuse a key the kind does not take: a typo must not be read as "leave unchanged"."""
    extra = sorted(set(p) - set(allowed))
    if extra:
        raise invalid(f"unknown field {', '.join(extra)}")
