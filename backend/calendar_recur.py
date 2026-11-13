# pyright: strict
"""Recurrence for Tico's calendar (docs/dev/calendar-contract.md, RFC 5545): read a rule, expand a series. Pure: no DB.

A series is stored as its rule and its first instance in wall time plus an IANA zone; instances are worked out per
read, in the caller's window, and never stored. Each instance keeps its wall start across clock changes: a time that
does not exist on a spring-forward day moves forward by the gap, and a time that happens twice on a fall-back day takes
the first (fold=0, which is what zoneinfo does for both). Each lasts exactly as long as the first. All-day series are
dates, with no zone.

The rule set is deliberately small (spec section 7 rule 9): FREQ DAILY|WEEKLY|MONTHLY|YEARLY, INTERVAL, BYDAY (with an
ordinal such as 2TU in a monthly or yearly rule), BYMONTHDAY, and COUNT or UNTIL. Anything else is refused with the
reason, so nothing is stored that the expander would read differently from Google.

UNTIL comes in three forms: a date (YYYYMMDD, through the end of that local day), a UTC time (YYYYMMDDTHHMMSSZ, the
form "following" edits write), and a floating local time (YYYYMMDDTHHMMSS).
"""

import re
from collections import deque
from collections.abc import Iterable, Iterator
from datetime import date, datetime, time, timedelta, timezone
from itertools import islice
from typing import Any, NoReturn
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil import rrule as _rrule

from .store import Problem

R: Any = _rrule  # its stubs type rrule's freq as a Literal that the FREQS table cannot carry
Row = dict[str, Any]

CAP = 750  # instances per series per call (spec section 7 rule 8)
COUNT_MAX = 5000  # a COUNT past this is refused: a series that long wants an UNTIL
INTERVAL_MAX = 999
FREQS = {"DAILY": R.DAILY, "WEEKLY": R.WEEKLY, "MONTHLY": R.MONTHLY, "YEARLY": R.YEARLY}
DAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
WEEKDAYS = {
    "mo": "MO",
    "mon": "MO",
    "monday": "MO",
    "tu": "TU",
    "tue": "TU",
    "tues": "TU",
    "tuesday": "TU",
    "we": "WE",
    "wed": "WE",
    "wednesday": "WE",
    "th": "TH",
    "thu": "TH",
    "thur": "TH",
    "thurs": "TH",
    "thursday": "TH",
    "fr": "FR",
    "fri": "FR",
    "friday": "FR",
    "sa": "SA",
    "sat": "SA",
    "saturday": "SA",
    "su": "SU",
    "sun": "SU",
    "sunday": "SU",
}
ORDINALS = {
    "1st": 1,
    "first": 1,
    "2nd": 2,
    "second": 2,
    "3rd": 3,
    "third": 3,
    "4th": 4,
    "fourth": 4,
    "5th": 5,
    "fifth": 5,
    "last": -1,
}
UNITS = {"day": "DAILY", "week": "WEEKLY", "month": "MONTHLY", "year": "YEARLY"}
WORD_FREQS = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY", "yearly": "YEARLY", "annually": "YEARLY"}
BYDAY_ITEM = re.compile(r"([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)")
UNTIL_FORMS = re.compile(r"\d{8}(T\d{6}Z?)?")


def _refuse(why: str) -> NoReturn:
    raise Problem("repeat", why, 422)


# ----------------------------------------------------------------------------- parse
def parse_repeat(text: object) -> str:
    """`text` as a normalized RRULE (no "RRULE:" prefix): an RRULE, or words ("weekly on mon,wed", "every 2 weeks on
    fri", "monthly on the 2nd tue", "monthly on day 15", "yearly", "daily until 2026-12-31", "weekly for 10 times")."""
    text = str(text or "").strip()
    if not text:
        _refuse("Say how it repeats")
    if len(text) > 300:
        _refuse("Keep the rule under 300 characters")
    body = text[6:] if text.upper().startswith("RRULE:") else text
    if "=" in body:
        return _normalize(_rule_parts(body))
    return _normalize(_words(text))


def _rule_parts(body: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for piece in body.strip().strip(";").split(";"):
        key, sep, value = piece.partition("=")
        key, value = key.strip().upper(), value.strip().upper()
        if not sep or not key or not value:
            _refuse(f"{piece!r} is not KEY=VALUE")
        if key in parts:
            _refuse(f"{key} appears twice")
        parts[key] = value
    return parts


def _words(text: str) -> dict[str, str]:
    words = re.sub(r"[,]+", " , ", text.lower()).split()
    parts: dict[str, str] = {}
    end = _take_end(words, parts)
    words = words[:end]
    if not words:
        _refuse("Say how often it repeats")
    rest: list[str] = []
    if words[0] in WORD_FREQS:
        parts["FREQ"], rest = WORD_FREQS[words[0]], words[1:]
    elif words[:2] == ["every", "weekday"] or words[0] == "weekdays":
        parts["FREQ"], parts["BYDAY"] = "WEEKLY", "MO,TU,WE,TH,FR"
        rest = words[2:] if words[0] == "every" else words[1:]
    elif words[0] == "every":
        count, unit_at = 1, 1
        if len(words) > 1 and words[1].isdigit():
            count, unit_at = int(words[1]), 2
        unit = words[unit_at].rstrip("s") if len(words) > unit_at else ""
        if unit in UNITS:
            parts["FREQ"] = UNITS[unit]
            if count != 1:
                parts["INTERVAL"] = str(count)
            rest = words[unit_at + 1 :]
        elif unit in WEEKDAYS or words[unit_at] in WEEKDAYS:  # "every monday", "every mon and thu"
            parts["FREQ"], rest = "WEEKLY", ["on", *words[unit_at:]]
            if count != 1:
                parts["INTERVAL"] = str(count)
        else:
            _refuse(f"{text!r}: say daily, weekly, monthly or yearly, or every N days/weeks/months/years")
    else:
        _refuse(f"{text!r}: say daily, weekly, monthly or yearly, or give an RRULE")
    if rest:
        _take_on(rest, parts, text)
    return parts


def _take_end(words: list[str], parts: dict[str, str]) -> int:
    """Pull "until YYYY-MM-DD" or "for N times" / "N times" off the end; returns where the rest ends."""
    end = len(words)
    for at, word in enumerate(words):
        if word == "until" and at + 1 < len(words):
            day = _date(words[at + 1])
            if day is None:
                _refuse(f"{words[at + 1]!r} is not a date (YYYY-MM-DD)")
            parts["UNTIL"] = day.strftime("%Y%m%d")
            end = min(end, at)
        elif word in ("time", "times") and at >= 1 and words[at - 1].isdigit():
            parts["COUNT"] = words[at - 1]
            end = min(end, at - 2 if at >= 2 and words[at - 2] == "for" else at - 1)
    return end


def _take_on(rest: list[str], parts: dict[str, str], text: str) -> None:
    words = [w for w in rest if w not in (",", "and", "the")]
    if words and words[0] == "on":
        words = words[1:]
    if not words:
        _refuse(f"{text!r}: say which days after 'on'")
    if words[0] == "day" and len(words) == 2:  # "monthly on day 15"
        parts["BYMONTHDAY"] = words[1]
        return
    if len(words) == 1 and re.fullmatch(r"\d{1,2}(st|nd|rd|th)?", words[0]):  # "monthly on the 15th"
        parts["BYMONTHDAY"] = re.sub(r"\D", "", words[0])
        return
    days: list[str] = []
    ordinal: int | None = None
    for word in words:
        if word in ORDINALS:
            ordinal = ORDINALS[word]
        elif word in WEEKDAYS:
            days.append(("" if ordinal is None else str(ordinal)) + WEEKDAYS[word])
            ordinal = None
        else:
            _refuse(f"{text!r}: {word!r} is not a day")
    parts["BYDAY"] = ",".join(days)


def _date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _normalize(parts: dict[str, str]) -> str:
    """Check `parts` against the supported subset and write them in one order, so one rule has one spelling."""
    unknown = sorted(set(parts) - {"FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "COUNT", "UNTIL", "WKST"})
    if unknown:
        _refuse(f"{', '.join(unknown)} is not supported; use FREQ, INTERVAL, BYDAY, BYMONTHDAY, COUNT or UNTIL")
    freq = parts.get("FREQ")
    if freq not in FREQS:
        _refuse("FREQ must be DAILY, WEEKLY, MONTHLY or YEARLY")
    if parts.get("WKST", "MO") != "MO":
        _refuse("Only WKST=MO is supported")
    out = [f"FREQ={freq}"]
    interval = _int(parts.get("INTERVAL", "1"), "INTERVAL", 1, INTERVAL_MAX)
    if interval != 1:
        out.append(f"INTERVAL={interval}")
    if "BYDAY" in parts:
        items: list[str] = []
        for item in parts["BYDAY"].split(","):
            match = BYDAY_ITEM.fullmatch(item.strip())
            if not match:
                _refuse(f"BYDAY {item!r} is not a day such as MO or 2TU")
            if match.group(1):
                n = int(match.group(1))
                if freq not in ("MONTHLY", "YEARLY"):
                    _refuse("An ordinal day such as 2TU needs a monthly or yearly rule")
                if not (1 <= abs(n) <= 5):
                    _refuse("An ordinal day must be 1 to 5, or -1 to -5 from the end")
                items.append(f"{n}{match.group(2)}")
            else:
                items.append(match.group(2))
        if len(set(items)) != len(items):
            _refuse("BYDAY repeats a day")
        out.append("BYDAY=" + ",".join(items))
    if "BYMONTHDAY" in parts:
        if freq not in ("MONTHLY", "YEARLY"):
            _refuse("BYMONTHDAY needs a monthly or yearly rule")
        if "BYDAY" in parts:
            _refuse("Use BYDAY or BYMONTHDAY, not both")
        days = [_int(d, "BYMONTHDAY", -31, 31) for d in parts["BYMONTHDAY"].split(",")]
        if 0 in days:
            _refuse("BYMONTHDAY cannot be 0")
        out.append("BYMONTHDAY=" + ",".join(str(d) for d in days))
    if "COUNT" in parts and "UNTIL" in parts:
        _refuse("Use COUNT or UNTIL, not both")
    if "COUNT" in parts:
        out.append(f"COUNT={_int(parts['COUNT'], 'COUNT', 1, COUNT_MAX)}")
    if "UNTIL" in parts:
        until = parts["UNTIL"]
        if not UNTIL_FORMS.fullmatch(until) or _until_value(until) is None:
            _refuse("UNTIL must be YYYYMMDD or YYYYMMDDTHHMMSSZ")
        out.append(f"UNTIL={until}")
    return ";".join(out)


def _int(text: object, name: str, low: int, high: int) -> int:
    try:
        value = int(str(text).strip())
    except ValueError:
        _refuse(f"{name} must be a whole number")
    if not (low <= value <= high):
        _refuse(f"{name} must be from {low} to {high}")
    return value


def _until_value(text: str) -> datetime | None:
    try:
        if len(text) == 8:
            return datetime.strptime(text, "%Y%m%d")  # noqa: DTZ007 - a date or floating time is wall time, zoneless
        if text.endswith("Z"):
            return datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return datetime.strptime(text, "%Y%m%dT%H%M%S")  # noqa: DTZ007 - see above
    except ValueError:
        return None


def rule_parts(rrule_text: str) -> dict[str, str]:
    """A stored rule as {KEY: VALUE}."""
    return _rule_parts(rrule_text[6:] if rrule_text.upper().startswith("RRULE:") else rrule_text)


def with_parts(rrule_text: str, **changes: object) -> str:
    """The stored rule with keys set (a value) or removed (None), normalized again."""
    parts = rule_parts(rrule_text)
    for key, value in changes.items():
        if value is None:
            parts.pop(key, None)
        else:
            parts[key] = str(value)
    return _normalize(parts)


# ----------------------------------------------------------------------------- time helpers
def zone(name: object) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        raise Problem("time_zone", f"{name!r} is not an IANA time zone", 422) from None


def z(at: datetime) -> str:
    """A UTC instant as the calendar's one stored and returned form: YYYY-MM-DDTHH:MM:SSZ."""
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_z(text: object) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(timezone.utc)


def to_utc(local: datetime, tz: ZoneInfo | None) -> datetime:
    """A naive wall time in `tz` as UTC. A nonexistent time (spring forward) lands after the gap and an ambiguous one
    (fall back) on its first occurrence: zoneinfo reads both with fold=0 that way."""
    return local.replace(tzinfo=tz, fold=0).astimezone(timezone.utc)


def local_naive(text: object) -> datetime:
    return datetime.fromisoformat(str(text)).replace(tzinfo=None, microsecond=0)


def _aware(at: datetime) -> datetime:
    return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)


def _day(value: object) -> date:
    return value if isinstance(value, date) and not isinstance(value, datetime) else date.fromisoformat(str(value)[:10])


# ----------------------------------------------------------------------------- expand
def _rule(rrule_text: str, dtstart: datetime, tz: ZoneInfo | None, all_day: bool) -> Any:
    parts = rule_parts(rrule_text)
    kwargs: dict[str, Any] = {"dtstart": dtstart, "interval": int(parts.get("INTERVAL", "1")), "wkst": R.MO}
    kwargs["cache"] = False
    if "BYDAY" in parts:
        days: list[Any] = []
        for item in parts["BYDAY"].split(","):
            match = BYDAY_ITEM.fullmatch(item)
            if match is None:
                _refuse(f"BYDAY {item!r} is not a day such as MO or 2TU")
            weekday = R.weekdays[DAYS.index(match.group(2))]
            days.append(weekday(int(match.group(1))) if match.group(1) else weekday)
        kwargs["byweekday"] = days
    if "BYMONTHDAY" in parts:
        kwargs["bymonthday"] = [int(d) for d in parts["BYMONTHDAY"].split(",")]
    if "COUNT" in parts:
        kwargs["count"] = int(parts["COUNT"])
    if "UNTIL" in parts:
        until = _until_value(parts["UNTIL"])
        if until is None:
            _refuse("UNTIL must be YYYYMMDD or YYYYMMDDTHHMMSSZ")
        if len(parts["UNTIL"]) == 8:
            until = datetime.combine(until.date(), time(23, 59, 59))  # through the end of that day
        elif until.tzinfo is not None and all_day:
            until = datetime.combine(until.date(), time(23, 59, 59))
        elif until.tzinfo is not None:  # a UTC UNTIL, compared in the series' wall time
            until = until.astimezone(tz).replace(tzinfo=None)
        kwargs["until"] = until
    return R.rrule(FREQS[parts["FREQ"]], **kwargs)


def _shape(event: Row) -> tuple[bool, ZoneInfo | None, datetime, timedelta]:
    """(all_day, tz, first start as naive wall time / midnight, wall duration)."""
    if event.get("all_day"):
        start, end = _day(event["starts_on"]), _day(event["ends_on"])
        return True, None, datetime.combine(start, time()), end - start
    tz = zone(event.get("time_zone"))
    start, end = local_naive(event["starts_at_local"]), local_naive(event["ends_at_local"])
    return False, tz, start, end - start


def _instance(all_day: bool, tz: ZoneInfo | None, local_start: datetime, length: timedelta) -> Row:
    """One instance from its wall start: original keys and UTC (or dates) for its start and end."""
    if all_day:
        day = local_start.date()
        text = day.isoformat()
        return {
            "original_start_local": text,
            "original_starts_at": text,
            "starts_at": text,
            "ends_at": (day + length).isoformat(),
        }
    start = to_utc(local_start, tz)
    end = start + length  # RFC 5545: an instance lasts exactly as long as the first, even across a clock change
    return {
        "original_start_local": local_start.isoformat(),
        "original_starts_at": z(start),
        "starts_at": z(start),
        "ends_at": z(end),
    }


def _overlaps(inst: Row, start_utc: datetime, end_utc: datetime, all_day: bool) -> bool:
    if all_day:
        first, last = start_utc.astimezone(timezone.utc).date(), (end_utc - timedelta(microseconds=1)).date()
        return _day(inst["starts_at"]) <= last and _day(inst["ends_at"]) > first
    s, e = parse_z(inst["starts_at"]), parse_z(inst["ends_at"])
    if s == e:
        return start_utc <= s < end_utc
    return s < end_utc and e > start_utc


def _moved(exception: Row, all_day: bool) -> tuple[str, str]:
    if exception.get("all_day") or (all_day and exception.get("starts_on")):
        return str(exception["starts_on"]), str(exception["ends_on"])
    return str(exception["starts_at"]), str(exception["ends_at"])


def series_starts(event: Row, after_local: datetime | None = None) -> Iterator[datetime]:
    """The series' wall starts in order (an iterator), from `after_local` on when given; one for a single event."""
    all_day, tz, first, _length = _shape(event)
    if not event.get("rrule"):
        return iter([first] if after_local is None or first >= after_local else [])
    rule = _rule(event["rrule"], first, tz, all_day)
    return iter(rule) if after_local is None else rule.xafter(after_local, inc=True)


def instance_at(event: Row, original_starts_at: str | None) -> Row | None:
    """The instance of `event` whose original start is `original_starts_at`, or None when the rule has none there."""
    all_day, tz, _first, length = _shape(event)
    try:
        if all_day:
            local = datetime.combine(_day(original_starts_at), time())
        else:
            local = parse_z(original_starts_at).astimezone(tz).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None
    for candidate in islice(series_starts(event, local - timedelta(hours=3)), 4):
        inst = _instance(all_day, tz, candidate, length)
        if inst["original_starts_at"] == str(original_starts_at):
            return inst
        if candidate > local + timedelta(hours=3):
            break
    return None


def expand(
    event: Row, exceptions: Iterable[Row] | None, start_utc: datetime, end_utc: datetime, cap: int = CAP
) -> list[Row]:
    """The instances of `event` that overlap [start_utc, end_utc), in start order, at most `cap`: each
    {original_start_local, original_starts_at, starts_at, ends_at} (UTC "Z", or dates for all-day), with `exception`
    set to the exception row that moved it. A cancelled exception removes its instance; a moved one is placed at its
    new time, inside the window even when its original start is not. An exception whose original start the rule no
    longer has is ignored."""
    start_utc, end_utc = _aware(start_utc), _aware(end_utc)
    all_day, tz, _first, length = _shape(event)
    by_original = {str(x["original_starts_at"]): x for x in exceptions or ()}
    found: dict[str, Row] = {}
    # Wall-time bounds a day wider than the window either side cover any zone offset and the instance's own length.
    lo = start_utc.astimezone(tz or timezone.utc).replace(tzinfo=None) - length - timedelta(days=1)
    hi = end_utc.astimezone(tz or timezone.utc).replace(tzinfo=None) + timedelta(days=1)
    for local in series_starts(event, lo):
        if local >= hi or len(found) >= cap:
            break
        inst = _instance(all_day, tz, local, length)
        exception = by_original.get(inst["original_starts_at"])
        if exception is not None:
            continue  # placed below, at its own time
        if _overlaps(inst, start_utc, end_utc, all_day):
            found[inst["original_starts_at"]] = inst
    for original, exception in by_original.items():
        if exception.get("status") == "cancelled" or original in found:
            continue
        starts, ends = _moved(exception, all_day)
        inst = instance_at(event, original)
        if inst is None:
            continue
        moved = {**inst, "starts_at": starts, "ends_at": ends, "exception": exception}
        if _overlaps(moved, start_utc, end_utc, all_day or bool(exception.get("all_day"))):
            found[original] = moved
    ordered = sorted(found.values(), key=lambda i: (i["starts_at"], i["original_starts_at"]))
    return ordered[:cap]


def last_end(event: Row) -> str | None:
    """The UTC end of a series' last instance (dates as midnight UTC for all-day), or None when it never ends.
    Bounded: COUNT is capped at COUNT_MAX and an UNTIL stops the walk."""
    if not event.get("rrule"):
        return None
    parts = rule_parts(event["rrule"])
    if "COUNT" not in parts and "UNTIL" not in parts:
        return None
    all_day, tz, _first, length = _shape(event)
    tail = deque(series_starts(event), maxlen=1)
    if not tail:
        return None
    inst = _instance(all_day, tz, tail[0], length)
    if all_day:
        return z(datetime.combine(_day(inst["ends_at"]), time(), timezone.utc))
    return str(inst["ends_at"])


def count_before(event: Row, local_start: datetime) -> int:
    """How many instances the series has before the wall time `local_start` (bounded by COUNT)."""
    n = 0
    for local in series_starts(event):
        if local >= local_start:
            break
        n += 1
    return n
