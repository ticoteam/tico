"""Calendar math for books: days are ISO `YYYY-MM-DD` strings, months `YYYY-MM`, periods inclusive at both ends."""
from datetime import date, timedelta

from .errors import invalid

BUCKETS = ("day", "week", "month", "quarter", "year")


def day(value: object) -> date:
    """An ISO day, or the day of an ISO timestamp (`2026-10-30T12:00:00Z` is 2026-10-30)."""
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise invalid(f"{value!r} is not a date (YYYY-MM-DD)") from None


def month(value: object) -> date:
    """The first day of a `YYYY-MM` month (a full date is accepted and cut to its month)."""
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text[:7] + "-01")
    except ValueError:
        raise invalid(f"{value!r} is not a month (YYYY-MM)") from None


def month_end(start: date) -> date:
    return add_months(start, 1) - timedelta(days=1)


def add_months(start: date, n: int) -> date:
    """The first of the month `n` months from `start`'s month."""
    index = start.year * 12 + start.month - 1 + n
    return date(index // 12, index % 12 + 1, 1)


def month_bounds(at: date) -> tuple[date, date]:
    start = at.replace(day=1)
    return start, month_end(start)


def fiscal_bounds(at: date, months: int, fiscal_start: int) -> tuple[date, date]:
    """The `months`-long fiscal period (3: quarter, 12: year) holding `at`, for a year that starts in `fiscal_start`."""
    offset = (at.month - fiscal_start) % 12
    first = add_months(at.replace(day=1), -(offset % months))
    return first, add_months(first, months) - timedelta(days=1)


def quarters_around(a: date, z: date, fiscal_start: int, horizon: date) -> tuple[date, date, date]:
    """For a read over private lines: the window's first day, its last day held to `horizon` (the later of today and the
    book's last posting), and the last day of the fiscal quarter holding that. An open-ended window (a once budget runs
    to 9999-12-31) reads no further than the book does, so no quarter is built past it."""
    end = min(z, max(horizon, a))
    return fiscal_bounds(a, 3, fiscal_start)[0], end, fiscal_bounds(end, 3, fiscal_start)[1]


def bucket_bounds(kind: str, at: date, fiscal_start: int = 1) -> tuple[date, date]:
    """The report bucket holding `at`: a day, an ISO week (Monday first), a month, a fiscal quarter or year."""
    if kind == "day":
        return at, at
    if kind == "week":
        start = at - timedelta(days=at.weekday())
        return start, start + timedelta(days=6)
    if kind == "month":
        return month_bounds(at)
    if kind == "quarter":
        return fiscal_bounds(at, 3, fiscal_start)
    if kind == "year":
        return fiscal_bounds(at, 12, fiscal_start)
    raise invalid(f"a period is {'|'.join(BUCKETS)}, not {kind}")


def buckets(kind: str, start: date, end: date, fiscal_start: int = 1, most: int = 400) -> list[tuple[date, date]]:
    """Every bucket from the one holding `start` to the one holding `end`, at most `most` of them."""
    if end < start:
        raise invalid("the end is before the start")
    out: list[tuple[date, date]] = []
    at = start
    while at <= end:
        if len(out) >= most:
            raise invalid(f"that is more than {most} {kind}s; choose a longer period")
        first, last = bucket_bounds(kind, at, fiscal_start)
        out.append((first, last))
        at = last + timedelta(days=1)
    return out


def days(start: date, end: date) -> int:
    """How many days from `start` to `end`, both counted; 0 when they do not overlap."""
    return max(0, (end - start).days + 1)


def overlap(a: tuple[date, date], b: tuple[date, date]) -> int:
    return days(max(a[0], b[0]), min(a[1], b[1]))


def label(start: date, kind: str) -> str:
    """A bucket's key: 2026-10-30, 2026-W44, 2026-10, 2026-Q4 (fiscal quarters count from the year's start), 2026."""
    if kind == "day":
        return start.isoformat()
    if kind == "week":
        iso = start.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if kind == "month":
        return start.isoformat()[:7]
    if kind == "quarter":
        return f"{start.year}-Q{(start.month - 1) // 3 + 1}"
    return str(start.year)
