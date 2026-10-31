"""Reading an action's payload: every field is checked and typed here, so nothing downstream handles raw JSON."""
import re
from collections.abc import Mapping, Sequence
from typing import cast

from . import dates, money
from .errors import invalid

Payload = Mapping[str, object]
MAX_TEXT = 500
MAX_ITEMS = 500


# Field names a card number goes under. A card number is 13 to 19 digits that pass the Luhn check every card number
# passes, written as digit groups joined by whitespace or dashes (any kind), or as a JSON number. A group run is one
# only as a whole token: a digit next to a letter or a dash is part of an id, and a card number may carry a code after
# it (`4242 4242 4242 4242 123`), so every run of groups is tried.
CARD_NUMBER_KEYS = frozenset({"card_number", "cardnumber", "pan", "cvc", "cvv", "cvv2", "security_code"})
CARD_NUMBER_REFUSED = "a card number never goes into Finance: Tico does not take one (spec §8.3)"
_DIGITS = re.compile(r"\d+")
_DASHES = "\\-\u2010-\u2015\u2212"
_GAP = re.compile(rf"[\s\u00ad\u200b-\u200d\u2060\ufeff{_DASHES}]*")
_EDGE = re.compile(rf"[\w{_DASHES}]")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch) * (2 if i % 2 else 1)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def _card_int(n: int) -> bool:
    return 10**12 <= abs(n) < 10**19 and _luhn(str(abs(n)))


def _card_text(text: str) -> bool:
    spans = [m.span() for m in _DIGITS.finditer(text)]
    for i, (start, _) in enumerate(spans):
        if _EDGE.match(text[start - 1:start]):
            continue
        digits = ""
        for j in range(i, len(spans)):
            if j > i and _GAP.fullmatch(text, spans[j - 1][1], spans[j][0]) is None:
                break
            digits += text[spans[j][0]:spans[j][1]]
            if len(digits) > 19:
                break
            end = spans[j][1]
            if len(digits) >= 13 and not _EDGE.match(text[end:end + 1]) and _luhn(digits):
                return True
    return False


def has_card_number(value: object) -> bool:
    """Whether a payload (or part of one) holds a card number: a field named like one, or text or a JSON number with a
    Luhn-valid run of 13 to 19 digits standing as its own token (not inside an id). Tico never takes or keeps one (spec
    §8.3), so such a payload is refused before it is stored."""
    if isinstance(value, Mapping):
        items_ = cast(Mapping[object, object], value).items()
        return any(str(k).lower() in CARD_NUMBER_KEYS or has_card_number(v) for k, v in items_)
    if isinstance(value, (list, tuple)):
        return any(has_card_number(v) for v in cast(Sequence[object], value))
    if isinstance(value, str):
        return _card_text(value)
    if isinstance(value, bool):
        return False
    if isinstance(value, float):
        return value.is_integer() and _card_int(int(value))
    if isinstance(value, int):
        return _card_int(value)
    return False


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
