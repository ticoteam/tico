"""Importing shared records from a CSV file (API v2 `POST /api/v2/imports/preview` and `/commit`).

A row becomes one Finance action, through the same path as hand entry, so rights, gates and the audit row apply to
each one. Every row is checked by a dry run first, so a row that would be refused is never stored under its key, and
a fixed file can be imported again to finish what failed.

A row's idempotency key is its content: a file imported again creates nothing and says "already there". A row that
matches a record already held by a natural key (the same normalized address, SKU or serial) or an earlier line of the
same file is a possible duplicate. A person decides, so it is skipped unless its line is named to import anyway. An
import creates records; it never changes one that exists."""
import json
import sqlite3
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..records import csvimport as CI
from ..records import reads as RR
from . import actions as finance
from .context import Ctx, load
from .core import kinds as K
from .core import rights as R
from .core.errors import DENIED, INVALID, NOT_AVAILABLE, NOT_FOUND, FinanceError
from .store import actions as SX
from .store import ap as SAP

ASSIGNABLE = ("location_id", "customer_id")
SUBJECTS = ("location", "equipment", "contact")
WHAT = {"location": "location", "customer": "customer", "contact": "contact", "equipment": "equipment"}
REPORT_HEADER = ("line", "name", "status", "reason", "matches")


@dataclass(frozen=True)
class RecordType:
    """What one kind of record file holds: the Finance action each row becomes, the columns it may use, the columns a
    row must fill, and which bulk-assigned field fills which payload field."""
    kind: str
    columns: tuple[str, ...]
    required: tuple[str, ...]
    assign: Mapping[str, str]


Cells = Mapping[str, str]


def _location(cells: Cells, names: "Names") -> dict[str, object]:
    address = {part: cells[part] for part in ("street", "city", "state", "postal_code") if cells[part]}
    payload: dict[str, object] = {"name": cells["name"], "address": address}
    if cells["kind"]:
        payload["kind"] = cells["kind"]
    if cells["in_books"]:
        payload["in_books"] = CI.flag(cells["in_books"], "in_books")
    return payload


def _item(cells: Cells, names: "Names") -> dict[str, object]:
    payload: dict[str, object] = {"name": cells["name"], "kind": cells["kind"]}
    for column in ("sku", "unit"):
        if cells[column]:
            payload[column] = cells[column]
    if cells["price"]:
        payload["price"] = CI.money(cells["price"], "price")
    return payload


def _equipment(cells: Cells, names: "Names") -> dict[str, object]:
    payload: dict[str, object] = {"name": cells["name"], "kind": cells["kind"]}
    if cells["location"]:
        payload["location_id"] = names.find("location", cells["location"])
    if cells["customer"]:
        payload["customer_id"] = names.find("customer", cells["customer"])
    for column in ("make", "model", "serial"):
        if cells[column]:
            payload[column] = cells[column]
    for column in ("installed_on", "warranty_ends_on"):
        if cells[column]:
            payload[column] = CI.day(cells[column], column)
    return payload


def _due_date(cells: Cells, names: "Names") -> dict[str, object]:
    subject_type = cells["subject_type"]
    if subject_type not in SUBJECTS:
        raise CI.RowError("subject_type is location, equipment or contact")
    payload: dict[str, object] = {
        "name": cells["name"], "subject_type": subject_type, "subject_id": names.find(subject_type, cells["subject"]),
        "repeats": cells["repeats"], "starts_on": CI.day(cells["starts_on"], "starts_on")}
    if cells["every_count"]:
        payload["every_count"] = CI.whole(cells["every_count"], "every_count")
    if cells["lead_days"]:
        payload["lead_days"] = CI.whole(cells["lead_days"], "lead_days", low=0, high=365)
    return payload


TYPES: dict[str, RecordType] = {
    "locations": RecordType("location_create", ("name", "kind", "street", "city", "state", "postal_code", "in_books"),
                            ("name",), {"location_id": "parent_id"}),
    "items": RecordType("item_create", ("name", "sku", "kind", "unit", "price"), ("name", "kind"), {}),
    "equipment": RecordType("equipment_create", ("name", "kind", "location", "customer", "make", "model", "serial",
                                                 "installed_on", "warranty_ends_on"), ("name", "kind"),
                            {"location_id": "location_id", "customer_id": "customer_id"}),
    "due_dates": RecordType("due_date_create", ("name", "subject_type", "subject", "repeats", "every_count",
                                                "starts_on", "lead_days"),
                            ("name", "subject_type", "subject", "repeats", "starts_on"), {}),
}
CONVERT: dict[str, Callable[[Cells, "Names"], dict[str, object]]] = {
    "locations": _location, "items": _item, "equipment": _equipment, "due_dates": _due_date}


class Names:
    """What a row may name: a location, a customer, a contact or a piece of equipment, by its name. A name that two
    records hold is refused, since the row would not say which one it means. Each list is read once per import."""

    def __init__(self, c: sqlite3.Connection, book_id: str) -> None:
        self.c, self.book_id = c, book_id
        self._by_name: dict[str, dict[str, list[str]]] = {}

    def _table(self, what: str) -> dict[str, list[str]]:
        if what not in self._by_name:
            if what == "location":
                pairs = [(str(r[0]), str(r[1])) for r in RR.active_rows(self.c, "locations", ("name",))]
            elif what == "equipment":
                pairs = [(str(r[0]), str(r[1])) for r in RR.active_equipment(self.c)]
            else:
                kinds = ("customer", "both") if what == "customer" else ("vendor", "customer", "both")
                pairs = [(str(r[0]), str(r[1])) for r in SAP.active_contacts(self.c, self.book_id, kinds)]
            table: dict[str, list[str]] = {}
            for record_id, name in pairs:
                table.setdefault(CI.match_key(name), []).append(record_id)
            self._by_name[what] = table
        return self._by_name[what]

    def find(self, what: str, text: str) -> str:
        hits = self._table(what).get(CI.match_key(text), [])
        if not hits:
            raise CI.RowError(f"no {WHAT[what]} named {text}")
        if len(hits) > 1:
            raise CI.RowError(f"more than one {WHAT[what]} is named {text}: rename one of them first")
        return hits[0]

    def has(self, what: str, record_id: str) -> bool:
        return any(record_id in ids for ids in self._table(what).values())


def natural_key(record_type: str, payload: Mapping[str, object]) -> str:
    """The key two rows share when they are the same record: an address, a SKU or a serial; "" when there is none."""
    if record_type == "locations":
        address = payload.get("address")
        return CI.address_key(address) if isinstance(address, dict) else ""
    if record_type == "items":
        return CI.match_key(str(payload.get("sku") or ""))
    if record_type == "equipment":
        return CI.match_key(str(payload.get("serial") or ""))
    return ""


class Keys:
    """The natural keys a book already holds, and the ones earlier lines of this file took."""

    def __init__(self, c: sqlite3.Connection, record_type: str) -> None:
        self.held: dict[str, tuple[str, str]] = {}
        self.taken: dict[str, int] = {}
        if record_type == "locations":
            for record_id, name, address in RR.active_rows(c, "locations", ("name", "address")):
                self._hold(CI.address_key(json.loads(str(address or "{}"))), str(record_id), str(name))
        elif record_type == "items":
            for record_id, name, sku in RR.active_rows(c, "items", ("name", "sku")):
                self._hold(CI.match_key(str(sku or "")), str(record_id), str(name))
        elif record_type == "equipment":
            for record_id, name, serial in RR.active_equipment(c):
                self._hold(CI.match_key(str(serial or "")), str(record_id), str(name))

    def _hold(self, key: str, record_id: str, name: str) -> None:
        if key:
            self.held.setdefault(key, (record_id, name))

    def match(self, key: str) -> dict[str, str] | None:
        if not key:
            return None
        if key in self.held:
            record_id, name = self.held[key]
            return {"record_id": record_id, "name": name}
        if key in self.taken:
            return {"line": str(self.taken[key])}
        return None

    def take(self, key: str, line: int) -> None:
        if key:
            self.taken.setdefault(key, line)


@dataclass
class Line:
    """One row's answer. `payload` and `key` are what a commit sends; they are not reported."""
    line: int
    name: str
    status: str
    reason: str = ""
    record_id: str = ""
    matches: dict[str, str] | None = None
    approval_id: str = ""
    payload: dict[str, object] | None = None
    key: str = ""

    def out(self) -> dict[str, object]:
        return {"line": self.line, "name": self.name, "status": self.status, "reason": self.reason,
                "record_id": self.record_id, "matches": self.matches, "approval_id": self.approval_id}


def _id_in(result: Mapping[str, Any]) -> str:
    """The id of the record a result names, not the outbox row that carries it to a provider."""
    return next((str(v) for k, v in result.items() if k.endswith("_id") and k != "outbox_id"), "")


def _available(ctx: Ctx, actor: str, record_type: str, kind: str) -> None:
    """Refuses the whole file when its action is not on this install or the teammate lacks the records right (plan §3)
    or the action's own right."""
    if kind not in K.LIVE:
        raise FinanceError(NOT_AVAILABLE, f"importing {record_type} is not available on this install yet")
    if not R.has(ctx.org, actor, R.RECORDS, ctx.rights(actor)):
        raise FinanceError(DENIED, "importing records needs finance.records")
    gate = K.authority(ctx.org, actor, kind, ctx.rights(actor))
    if gate is not None and gate.outcome == "deny":
        raise FinanceError(DENIED, f"importing {record_type} needs {gate.rule.removeprefix('right:')}")


def _assignment(record_type: str, spec: RecordType, names: Names, assign: Mapping[str, str]) -> dict[str, str]:
    """The bulk-assigned values, by the payload field each one fills. A value that names no record is refused."""
    fill: dict[str, str] = {}
    for field_name, value in assign.items():
        if field_name not in ASSIGNABLE:
            raise FinanceError(INVALID, "bulk assign takes location_id or customer_id")
        if field_name not in spec.assign:
            raise FinanceError(INVALID, f"bulk assign {field_name} does not apply to {record_type}")
        if not value:
            continue
        what = "location" if field_name == "location_id" else "customer"
        if not names.has(what, value):
            raise FinanceError(NOT_FOUND, f"no such {WHAT[what]}")
        fill[spec.assign[field_name]] = value
    return fill


def _stored(number: int, name: str, prior: Any, key: str) -> Line:
    """A row whose key an earlier import (or action) already stored: the answer that stored."""
    status = str(prior["status"])
    if status == "committed":
        return Line(number, name, "already_there", record_id=_id_in(json.loads(str(prior["result_json"] or "{}"))),
                    key=key)
    if status == "needs_approval":
        return Line(number, name, "needs_approval", approval_id=str(prior["approval_id"] or ""), key=key)
    return Line(number, name, "error", str(prior["reason"] or "refused before"), key=key)


def _line(c: sqlite3.Connection, actor: str, record_type: str, spec: RecordType, row: CI.Row, names: Names,
          keys: Keys, fill: Mapping[str, str]) -> Line:
    name = row.cells.get("name", "")
    if row.problems:
        return Line(row.line, name, "error", "; ".join(row.problems))
    try:
        for column in spec.required:
            if not row.cells.get(column):
                raise CI.RowError(f"{column} is blank")
        payload = CONVERT[record_type](row.cells, names)
    except CI.RowError as exc:
        return Line(row.line, name, "error", str(exc))
    payload.update({field_name: value for field_name, value in fill.items() if not payload.get(field_name)})
    key = CI.content_key(record_type, payload)
    prior = SX.by_key(c, key)
    if prior is not None:
        return _stored(row.line, name, prior, key)
    checked = finance.act(c, actor, spec.kind, payload, key, dry_run=True)
    if checked.status in ("denied", "failed"):
        return Line(row.line, name, "denied" if checked.status == "denied" else "error", checked.reason)
    natural = natural_key(record_type, payload)
    matches = keys.match(natural)
    if matches is not None:
        return Line(row.line, name, "possible_duplicate",
                    "it matches a record already here or an earlier line: a person decides", matches=matches,
                    payload=payload, key=key)
    keys.take(natural, row.line)
    return Line(row.line, name, "new", payload=payload, key=key)


def _classify(c: sqlite3.Connection, actor: str, record_type: str, text: str,
              assign: Mapping[str, str]) -> tuple[RecordType, list[Line], list[str]]:
    """Every row's answer, with nothing written: the type's checks, the file, then each row."""
    spec = TYPES.get(record_type)
    if spec is None:
        raise FinanceError(INVALID, "a record type is locations, items, equipment or due_dates")
    ctx = load(c)
    _available(ctx, actor, record_type, spec.kind)
    names = Names(c, ctx.book.id)
    fill = _assignment(record_type, spec, names, assign)
    rows, ignored = CI.read(text, spec.columns, spec.required)
    keys = Keys(c, record_type)
    lines = [_line(c, actor, record_type, spec, row, names, keys, fill) for row in rows]
    return spec, lines, ignored


def _answer(record_type: str, kind: str, lines: list[Line], ignored: list[str]) -> dict[str, Any]:
    return {"record_type": record_type, "kind": kind, "rows": len(lines),
            "summary": dict(Counter(line.status for line in lines)),
            "lines": [line.out() for line in lines[:CI.REPORT_LINES]], "truncated": len(lines) > CI.REPORT_LINES,
            "ignored_columns": ignored}


def preview(c: sqlite3.Connection, actor: str, record_type: str, text: str, assign: Mapping[str, str], *,
            report: bool = False) -> dict[str, Any]:
    """What an import would do, line by line. Writes nothing. `report` adds the same lines as a CSV, safe to open."""
    spec, lines, ignored = _classify(c, actor, record_type, text, assign)
    out = _answer(record_type, spec.kind, lines, ignored)
    if report:
        out["report_csv"] = CI.report_csv(REPORT_HEADER, [_report_row(line) for line in lines])
    return out


def commit(c: sqlite3.Connection, actor: str, record_type: str, text: str, assign: Mapping[str, str],
           include: frozenset[int]) -> dict[str, Any]:
    """Import the file. Each new line, and each possible duplicate named in `include`, becomes one Finance action."""
    spec, lines, ignored = _classify(c, actor, record_type, text, assign)
    for line in lines:
        if line.status == "new" or (line.status == "possible_duplicate" and line.line in include):
            _commit(c, actor, spec, line)
    return _answer(record_type, spec.kind, lines, ignored)


def _commit(c: sqlite3.Connection, actor: str, spec: RecordType, line: Line) -> None:
    """Runs one row for real. A key stored by an earlier run answers with that run's record, not a second one."""
    done = finance.act(c, actor, spec.kind, line.payload or {}, line.key)
    if done.status == "committed" and done.replayed:
        line.status = "already_there"
        line.record_id = _id_in(json.loads(str(SX.by_key(c, line.key)["result_json"] or "{}")))
    elif done.status == "committed":
        line.status, line.record_id = "created", _id_in(done.result)
    elif done.status == "needs_approval":
        line.status, line.approval_id = "needs_approval", done.approval_id or ""
    else:
        line.status = "denied" if done.status == "denied" else "error"
        line.reason = done.reason


def _report_row(line: Line) -> tuple[object, ...]:
    matches = line.matches or {}
    found = f"record {matches['record_id']}" if "record_id" in matches else (
        f"line {matches['line']}" if "line" in matches else "")
    return (line.line, line.name, line.status, line.reason, found)
