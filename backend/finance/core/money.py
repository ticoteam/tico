"""Money is an integer count of minor units (cents) plus an ISO currency code; FX rates are Decimal. No floats."""
import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from .errors import invalid

MAX_MINOR = 2**63 - 1               # the 64-bit range both SQLite and Postgres BIGINT hold
CURRENCY = re.compile(r"[A-Z]{3}")
# Minor-unit exponents that are not 2. Anything else (USD, EUR, GBP, ...) has cents.
EXPONENTS = {"JPY": 0, "KRW": 0, "VND": 0, "CLP": 0, "ISK": 0, "BHD": 3, "KWD": 3, "JOD": 3, "OMR": 3, "TND": 3}


def currency(value: object) -> str:
    """An ISO 4217 code, upper case."""
    text = str(value or "").strip().upper()
    if not CURRENCY.fullmatch(text):
        raise invalid(f"a currency is a three-letter ISO code, not {value!r}")
    return text


def minor(value: object, what: str = "amount") -> int:
    """An amount in minor units: an int (never a float or a bool) inside the 64-bit range."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise invalid(f"{what} is an integer number of minor units (cents)")
    if abs(value) > MAX_MINOR:
        raise invalid(f"{what} is out of range")
    return value


def rate(value: object) -> Decimal:
    """A positive FX rate from a string, an int or a Decimal; a float would carry binary error into the books."""
    if isinstance(value, bool) or isinstance(value, float):
        raise invalid("an fx rate is a decimal string, not a float")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise invalid(f"{value!r} is not an fx rate") from None
    if not number.is_finite() or number <= 0:
        raise invalid("an fx rate is a positive number")
    return number


def to_base(amount: int, cur: str, base: str, fx: Decimal) -> int:
    """`amount` minor units of `cur` in minor units of `base`, at `fx` base units per one unit of `cur`, rounded half
    to even. The exponents line minor units up, so 1000 JPY at 0.0067 is 670 cents."""
    if cur == base:
        return amount
    shift = EXPONENTS.get(base, 2) - EXPONENTS.get(cur, 2)
    value = Decimal(amount) * fx * (Decimal(10) ** shift)
    out = int(value.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
    return minor(out)


def from_major(value: object, cur: str = "USD") -> int:
    """A major-unit amount from elsewhere in Tico (usage limits store dollars as REAL) as minor units. The number goes
    through its shortest decimal text, so 12.3 is 1230 cents and never 1229."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise invalid(f"{value!r} is not an amount") from None
    if not number.is_finite():
        raise invalid(f"{value!r} is not an amount")
    scaled = number * (Decimal(10) ** EXPONENTS.get(cur, 2))
    return minor(int(scaled.quantize(Decimal(1), rounding=ROUND_HALF_EVEN)))


def prorate(amount: int, part: int, whole: int) -> int:
    """`part`/`whole` of `amount`, rounded half to even: a monthly budget's share of a day or a week."""
    if whole <= 0 or part >= whole:
        return amount if part >= whole else 0
    if part <= 0:
        return 0
    value = Decimal(amount) * Decimal(part) / Decimal(whole)
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def percent(part: int, whole: int | None) -> Decimal | None:
    """`part` as a percent of `whole` to one decimal place; None when there is no whole to measure against."""
    if whole is None or whole <= 0:
        return None
    return (Decimal(part) * 100 / Decimal(whole)).quantize(Decimal("0.1"), rounding=ROUND_HALF_EVEN)
