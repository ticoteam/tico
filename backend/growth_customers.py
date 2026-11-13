# pyright: strict
"""Customer facts in: the rows Growth computes new customers, retention, LTV and payback from (backend/growth_computed.py).

A team gives Growth one row per customer, today as a CSV loaded in Growth (`POST /api/v2/growth/customers`, source
`upload`); a synced source's "customers" shape loads the same rows under its own source id. Each load replaces every row
of its source: a customer left out is deleted, which is how a team honours an erasure request.

The fields (and nothing else, refused by name):

    customer key      required  the team's own id for the customer, as given; never a name, an email or a phone number
    signed up         optional  date
    became customer   required  date the customer first paid
    channel           required  a Growth channel slug; anything else counts as Unknown
    segment, plan     optional  short labels ("agency", "pro")
    churned           optional  date the customer stopped paying; blank while they pay
    revenue per month optional  for this customer; blank uses the channel's month or the settings value

Privacy rules (the Security Owner's review, 2026-11-13): columns outside the list are refused; a load whose customer key
looks like an email address or a phone number is refused; a label is at most 40 characters, and a label column with more
than 50 distinct values or more than one per two rows is refused (a name went into it). Dates are stored as dates in the
team's time zone, with no time. Every refusal, error and event names the rule, the column and at most a row number,
never a value. The key is stored as given: hashing guessable keys protects nothing and breaks the hand check.

A customer who churned and came back is two rows with the same key: each "became customer" starts a spell, and spells
of one key may not overlap. A customer counts where they were when they first became a customer: a later load that
moves a spell to another channel, segment or plan keeps the first value seen.

Who may: the owner and admins load and delete (people acting on their own click, as for synced sources).
"""

import csv
import io
import math
import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, NoReturn, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request

from . import brand as _brand_untyped
from . import hubdb as _hubdb_untyped
from . import models as M
from .store import Problem

H: Any = _hubdb_untyped
admin = cast("Callable[[Any, Any], bool]", _brand_untyped.admin)

UPLOAD = "upload"  # the source of a CSV loaded in Growth
ROWS_MAX = 200_000
KEY_MAX = 100
LABEL_MAX = 40
LABEL_DISTINCT_MAX = 50
ROW_NUMBERS_SHOWN = 3
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# A phone number: 7 to 15 digits written the way people write them (see _looks_personal). A bare run of digits is a
# database id far more often than a phone number, so it passes.
PHONE_CHARS = re.compile(r"^\+?[\d\s().\-/]+$")

# (field, what a person calls it, required). The order is the order help and refusals list them in.
FIELDS = (
    ("customer_key", "customer key", True),
    ("signed_up", "signed up", False),
    ("became_customer", "became customer", True),
    ("channel", "channel", True),
    ("segment", "segment", False),
    ("plan", "plan", False),
    ("churned", "churned", False),
    ("revenue_month", "revenue per month", False),
)
NAMES = {field: name for field, name, _ in FIELDS}
ALIASES = {
    **{field: field for field, _, _ in FIELDS},
    **{name.replace(" ", "_"): field for field, name, _ in FIELDS},
    "became": "became_customer",
    "revenue": "revenue_month",
}
KEPT_ONLY = "Growth keeps only: " + ", ".join(name for _, name, _ in FIELDS) + "."


Cell = str | float | int | None
Table = list[list[Cell]]


@dataclass
class Spell:
    """One row of a load, checked: what is stored, and the row it came from (for refusals, never stored)."""

    row: int
    customer_key: str
    became_customer: date | None = None
    signed_up: date | None = None
    churned: date | None = None
    channel: str | None = None
    segment: str | None = None
    plan: str | None = None
    revenue_month: float | None = None


def refuse(rule: str, detail: str) -> NoReturn:
    """A refusal that names its rule; `detail` never holds a row's value."""
    raise Problem("customer_facts", detail, 422, extra={"rule": rule})


def _rows_said(rows: list[int]) -> str:
    shown = ", ".join(str(n) for n in rows[:ROW_NUMBERS_SHOWN])
    more = len(rows) - ROW_NUMBERS_SHOWN
    return ("row " if len(rows) == 1 else "rows ") + shown + (f" and {more} more" if more > 0 else "")


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _header(name: Cell) -> str:
    return re.sub(r"[\s\-]+", "_", str(name or "").strip().lower())


def _date(value: Cell, tz: ZoneInfo) -> date | None:
    """A date (YYYY-MM-DD) from a date or a timestamp. A timestamp with an offset is read in the team's time zone; one
    without is taken as already in it. None for blank; ValueError for anything else."""
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text)
    at = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1))
    if at.tzinfo is not None:
        at = at.astimezone(tz)
    return at.date()


def _looks_personal(key: str) -> bool:
    if "@" in key and EMAIL.match(key):
        return True
    if PHONE_CHARS.match(key):
        # A + prefix, a space, brackets or two separators is how people write a phone number; one dash in a run of
        # digits (1234-5678) is how systems write an id, so it passes.
        digits = sum(ch.isdigit() for ch in key)
        written = key.startswith("+") or any(ch in key for ch in " ()") or sum(key.count(ch) for ch in "-./") >= 2
        return 7 <= digits <= 15 and written
    return False


def parse_csv(text: str) -> tuple[list[Cell], Table]:
    """CSV text -> (headers, rows as lists). Refuses text that is not a CSV with a header."""
    try:
        table = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    except csv.Error:
        refuse("csv", "That is not a CSV file Growth can read: check the quoting.")
    kept: Table = [list(row) for row in table if any(cell.strip() for cell in row)]
    if not kept:
        refuse("empty", "The file has no header and no customers.")
    return kept[0], kept[1:]


def _cell(row: Sequence[Cell], i: int) -> Cell:
    if i >= len(row):
        return None
    value = row[i]
    return value.strip() if isinstance(value, str) else value


def _fields(headers: Sequence[Cell]) -> list[str | None]:
    """Each header's field. Refuses any column outside the list, by name, and a load missing a required one."""
    fields: list[str | None] = []
    refused: list[str] = []
    for raw in headers:
        field = ALIASES.get(_header(raw))
        if field is None or field in fields:
            refused.append(str(raw or "").strip()[:LABEL_MAX] or "(blank)")
        fields.append(field)
    if refused:
        refuse("columns", f"Nothing loaded. {KEPT_ONLY} Remove these columns and load again: {', '.join(refused)}.")
    missing = [NAMES[f] for f, _, required in FIELDS if required and f not in fields]
    if missing:
        refuse("columns", f"Nothing loaded. These columns are required: {', '.join(missing)}.")
    return fields


def check(headers: Sequence[Cell], rows: Sequence[Sequence[Cell]], tz: ZoneInfo) -> list[Spell]:
    """Validate a load and return its spells. Refuses the whole load on any rule."""
    fields = _fields(headers)
    if not rows:
        refuse("empty", "Nothing loaded: the file has no customers. To remove them all, delete the customer facts.")
    if len(rows) > ROWS_MAX:
        refuse("too_many", f"Nothing loaded: a load holds at most {ROWS_MAX:,} customers.")

    problems: dict[tuple[str, str], list[int]] = {}  # (rule, column) -> row numbers; reported together, values never

    def bad(rule: str, field: str, n: int) -> None:
        problems.setdefault((rule, field), []).append(n)

    spells: list[Spell] = []
    for n, row in enumerate(rows, start=2):  # row 1 is the header
        cells = {f: _cell(row, i) for i, f in enumerate(fields) if f}
        key = str(cells.get("customer_key") or "").strip()
        if not key:
            bad("blank", "customer_key", n)
            continue
        if len(key) > KEY_MAX:
            bad("long_key", "customer_key", n)
        elif _looks_personal(key):
            bad("personal_key", "customer_key", n)
        spell = Spell(row=n, customer_key=key)
        dates: dict[str, date | None] = {}
        for field in ("signed_up", "became_customer", "churned"):
            try:
                dates[field] = _date(cells.get(field), tz)
            except (TypeError, ValueError):
                bad("date", field, n)
                dates[field] = None
        spell.signed_up, spell.became_customer, spell.churned = (
            dates["signed_up"],
            dates["became_customer"],
            dates["churned"],
        )
        if spell.became_customer is None and n not in problems.get(("date", "became_customer"), []):
            bad("blank", "became_customer", n)
        if spell.became_customer and spell.churned and spell.churned < spell.became_customer:
            bad("churned_before", "churned", n)
        channel = str(cells.get("channel") or "").strip().lower()
        spell.channel = channel if SLUG.match(channel) else None
        labels: dict[str, str | None] = {}
        for field in ("segment", "plan"):
            label = str(cells.get(field) or "").strip()
            if len(label) > LABEL_MAX:
                bad("long_label", field, n)
            labels[field] = label or None
        spell.segment, spell.plan = labels["segment"], labels["plan"]
        revenue = cells.get("revenue_month")
        if revenue not in (None, ""):
            try:
                amount = float(cast(str | float, revenue))
                if amount < 0 or not math.isfinite(amount):
                    raise ValueError
                spell.revenue_month = amount
            except (TypeError, ValueError):
                bad("number", "revenue_month", n)
        spells.append(spell)

    if problems:
        refuse(*_first_problem(problems))
    for field, values in (("segment", [s.segment for s in spells]), ("plan", [s.plan for s in spells])):
        distinct = {v for v in values if v}
        if len(distinct) > LABEL_DISTINCT_MAX or len(distinct) > len(spells) / 2:
            refuse(
                "identifier_label",
                f"Nothing loaded: the {NAMES[field]} column looks like an identifier, not a label. A label column has "
                f"at most {LABEL_DISTINCT_MAX} different values and no more than one per two rows; remove the column "
                "or load the labels the team groups customers by.",
            )
    overlap = _overlaps(spells)
    if overlap:
        refuse(
            "overlapping_spells",
            f"Nothing loaded: one customer key has spells that overlap ({_rows_said(overlap)}). A customer who came "
            "back needs a churned date on the earlier row before the later became customer date.",
        )
    return spells


def _first_problem(problems: dict[tuple[str, str], list[int]]) -> tuple[str, str]:
    """The first rule broken, as (rule, detail), in the order a person fixes them."""
    order = ("personal_key", "long_key", "blank", "date", "churned_before", "long_label", "number")
    (rule, field), rows = min(problems.items(), key=lambda item: (order.index(item[0][0]), item[1][0]))
    name = NAMES[field]
    where = _rows_said(rows)
    detail = {
        "personal_key": "Nothing loaded: customer keys must not be email addresses or phone numbers "
        f"(column {name}, {where}). Load the team's own customer id instead; a team may tokenise keys first.",
        "long_key": f"Nothing loaded: a customer key is at most {KEY_MAX} characters (column {name}, {where}).",
        "blank": f"Nothing loaded: {name} is blank ({where}).",
        "date": f"Nothing loaded: {name} is not a date like 2026-09-30 ({where}).",
        "churned_before": f"Nothing loaded: churned is before became customer ({where}).",
        "long_label": f"Nothing loaded: a {name} label is at most {LABEL_MAX} characters ({where}).",
        "number": f"Nothing loaded: {name} is not a number of zero or more ({where}).",
    }[rule]
    return rule, detail


def _overlaps(spells: list[Spell]) -> list[int]:
    """Row numbers of the first key whose spells overlap (a later spell starts before the earlier one churned)."""
    by_key: dict[str, list[Spell]] = {}
    for s in spells:
        by_key.setdefault(s.customer_key, []).append(s)
    for group in by_key.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda s: s.became_customer or date.min)
        for earlier, later in zip(group, group[1:], strict=False):
            if earlier.churned is None or earlier.churned > (later.became_customer or date.min):
                return [earlier.row, later.row]
    return []


def rows_from_body(body: M.GrowthCustomersLoad) -> tuple[list[Cell], Table]:
    """(headers, rows) from a load: CSV text, or rows as objects (a synced source's shape)."""
    if body.csv is not None:
        return parse_csv(body.csv)
    objects = body.rows or []
    headers: list[str] = list(dict.fromkeys(k for row in objects for k in row))
    return list[Cell](headers), [[row.get(h) for h in headers] for row in objects]


def load(
    c: sqlite3.Connection,
    actor: str,
    headers: Sequence[Cell],
    rows: Sequence[Sequence[Cell]],
    source: str = UPLOAD,
) -> dict[str, Any]:
    """Replace every customer row of `source` with this load. Returns counts only."""
    from . import growth as _growth_untyped

    G: Any = _growth_untyped
    settings: dict[str, Any] = G.settings(c)
    spells = check(headers, rows, zone(settings.get("time_zone")))
    first_seen: dict[tuple[str, str], tuple[str | None, str | None, str | None]] = {
        (r[0], r[1]): (r[2], r[3], r[4])
        for r in c.execute(
            "SELECT customer_key, became, channel, segment, plan FROM growth_customers WHERE source=?", (source,)
        )
    }
    at: str = H.now()
    c.execute("DELETE FROM growth_customers WHERE source=?", (source,))
    values: list[tuple[Any, ...]] = []
    for s in spells:
        became = s.became_customer.isoformat() if s.became_customer else ""
        channel, segment, plan = first_seen.get((s.customer_key, became), (s.channel, s.segment, s.plan))
        values.append(
            (
                source,
                s.customer_key,
                became,
                s.signed_up.isoformat() if s.signed_up else None,
                channel,
                segment,
                plan,
                s.churned.isoformat() if s.churned else None,
                s.revenue_month,
                at,
            )
        )
    c.executemany(
        "INSERT INTO growth_customers (source, customer_key, became, signed_up, channel, segment, plan, churned, "
        "revenue_month, loaded_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        values,
    )
    keys = {s.customer_key for s in spells}
    customers = len(keys)
    removed = len({key for key, _ in first_seen} - keys)  # customers this source no longer gives: their rows are gone
    load_id: str = H.new_id()
    c.execute(
        "INSERT INTO growth_customer_loads (id, source, customers, removed, loaded_by, loaded_at) VALUES (?,?,?,?,?,?)",
        (load_id, source, customers, removed, actor, at),
    )
    H.event(c, actor, "growth.customers.load", load_id, {"source": source, "customers": customers, "removed": removed})
    channels: set[str] = {r[0] for r in c.execute("SELECT slug FROM growth_channels")}
    unknown = sum(1 for v in values if v[4] not in channels)
    months: dict[tuple[str, str], int] = {}
    for v in values:
        slug = cast(str, v[4]) if v[4] in channels else cast(str, G.UNKNOWN)
        months[(cast(str, v[2])[:7], slug)] = months.get((cast(str, v[2])[:7], slug), 0) + 1
    return {
        "source": source,
        "customers": customers,
        "spells": len(values),
        "removed": removed,
        "unknown_channel": unknown,
        "loaded_at": at,
        "new_by_month": [
            {"month": m, "channel": slug, "new_customers": n} for (m, slug), n in sorted(months.items(), reverse=True)
        ],
    }


def delete_all(c: sqlite3.Connection, actor: str, source: str | None = None) -> dict[str, Any]:
    """Every customer row (or one source's)."""
    where, args = (" WHERE source=?", (source,)) if source else ("", ())
    found = c.execute(f"SELECT count(DISTINCT source || ':' || customer_key) FROM growth_customers{where}", args)
    customers = int(found.fetchone()[0])
    c.execute(f"DELETE FROM growth_customers{where}", args)
    H.event(c, actor, "growth.customers.delete", source or "all", {"customers": customers})
    return {"deleted": customers, "source": source}


def loads(c: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    """The newest loads: source, customers, removed, who and when. Two loads at once: the last one wins, both listed."""
    found: list[dict[str, Any]] = H._rows(
        c.execute(
            "SELECT source, customers, removed, loaded_by, loaded_at FROM growth_customer_loads "
            "ORDER BY loaded_at DESC, rowid DESC LIMIT ?",
            (limit,),
        )
    )
    return found


def install(app: Any, store: Any, auth: Any, mutate: Callable[..., Any]) -> None:
    del store  # the writes go through mutate, which holds the store

    def editor(who: Any, what: str) -> None:
        auth.domain(who)
        if not admin(auth, who):
            raise Problem("forbidden", f"Only the owner or an admin {what}", 403)

    @app.post("/api/v2/growth/customers")
    def growth_customers_load(request: Request, body: M.GrowthCustomersLoad) -> Any:  # pyright: ignore[reportUnusedFunction]
        """Owner or admin: load customer facts, as CSV text (`csv`) or rows (`rows`). The load replaces every customer
        this source gave before; nothing is loaded when any rule refuses it, and the refusal names the rule and the
        column, never a value."""
        who = request.state.identity

        def work(c: sqlite3.Connection) -> dict[str, Any]:
            editor(who, "loads customer facts")
            headers, rows = rows_from_body(body)
            return load(c, who.actor, headers, rows)

        return mutate(request, body, work)

    @app.post("/api/v2/growth/customers/delete")
    def growth_customers_delete(request: Request, body: M.Empty) -> Any:  # pyright: ignore[reportUnusedFunction]
        """Owner or admin: delete every customer fact at once; the figures fall back to typed values."""
        who = request.state.identity

        def work(c: sqlite3.Connection) -> dict[str, Any]:
            editor(who, "deletes customer facts")
            return delete_all(c, who.actor)

        return mutate(request, body, work)
