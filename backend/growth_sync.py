# pyright: strict
"""Growth fills its own numbers: synced sources (spec "Growth fills its own numbers", task #304).

A source (`gtm_sources`) with a `sync_kind` fetches numbers instead of only naming a system. Each run hands this module
rows of month, channel and one column per number; `run` maps them to channels and months and writes them into the
cohort rows (`growth_cohorts`), recording where each number came from (`growth_origins`). Part 1 brings the `file` kind
(a CSV upload, or rows posted by a script or a bot); the database query, Finance ad spend and bot cost kinds post into
the same `run`.

Rules, from the spec:
- Mapping: `sync_map` names the month column, the channel column and, under `fields`, the column for each number it
  fills. A channel is a channel's slug or name (any case); blank or "unknown" is the Unknown row. A row naming a channel
  Growth does not have is not written; the run lists it.
- Months: the first good run writes up to `FIRST_MONTHS` months back; later runs rewrite only the current month and the
  `LATER_MONTHS` before it. A month in the future is never written.
- Each number knows its origin: synced (source, run, time) or typed (who, when), actual or estimate.
- Who wins: a number a live synced source filled belongs to it, so typing it is refused, naming the source
  (`refuse_typed`). A sync never overwrites a number someone typed as actual: when they differ it holds the source's
  value beside it (`held_*`) and the source's owner chooses (`resolve`); a value the owner declined is not held again.
  A typed estimate is replaced by the synced actual. A run that fails changes no number.
- Two live synced sources never fill the same number: connecting or remapping one that would is refused.
- Database query (`db_query`, task #307): `sync_map` also names the `database`, a named `query` in that database's
  catalog (integrations/queries/<database>.yaml) and the `bot` granted that database. Connecting saves a daily schedule
  automation (`automation` in the map) whose bot turn runs `tico growth source run <id>` on that bot's computer: the
  query runs there through `tico db` and its rows, or the error it ended with (wrong column, timeout, login refused),
  are posted to the same `run`. The server never holds the connection string.
- Customers: a source whose mapping has `shape: customers` posts customer rows, not numbers. Each run is a load of
  backend/growth_customers.py under the source's own id (`growth_customers.source_key`), so it replaces only that
  source's customers; a load the privacy rules refuse fails the run, naming the rule and column. It fills no typed
  number, so any number of customers sources may be live. Retiring it, stopping its sync or remapping it to numbers
  deletes its customers and its stored file at once (`forget_customers`).
- Health: `fresh`, `stale` (no good run in twice `refresh_hours`), `failing` (its last run failed, with the reason) or
  `waiting` (connected, never run). `not_synced` for a source that only names a system. The source's owner gets one
  notice when it starts failing and one when it recovers.
"""

import csv
import hashlib
import hmac
import io
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from . import growth_finance as GF
from . import growth_customers as GC
from .store import H as _H
from .store import Problem

H: Any = _H

FIELDS = (
    "spend",
    "bot_cost",
    "new_customers",
    "kept_d30",
    "kept_d90",
    "kept_d180",
    "revenue_month",
    "margin_pct",
)
PERCENT = ("kept_d30", "kept_d90", "kept_d180", "margin_pct")
KINDS = ("file", "db_query", "finance_ad_spend", "bot_cost")
REFRESH_HOURS = {"file": 168, "db_query": 24, "finance_ad_spend": 24, "bot_cost": 24}
FIRST_MONTHS = 12  # the first good run fills this many months back from the current one
LATER_MONTHS = 2  # later runs rewrite the current month and this many before it
PAYLOAD_MAX = 1_000_000  # characters of CSV one run takes
ROWS_MAX = 5000
NOT_WRITTEN_MAX = 50  # rows listed per run; `not_written_count` has them all
ERROR_CHARS = 300
UNKNOWN = "unknown"
DB_KEYS = ("database", "query", "bot")  # what a db_query source's map names besides its columns
IN_PATH = "/api/v2/growth/in/"  # a file source's signed address: IN_PATH + the source's id
DB_CRON = "0 6 * * *"  # the daily run, in UTC


# ----------------------------------------------------------------------------- months
def _index(month: str) -> int:
    return int(month[:4]) * 12 + int(month[5:7]) - 1


def _month(index: int) -> str:
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def this_month(at: datetime | None = None) -> str:
    at = at or datetime.now(timezone.utc)
    return f"{at.year:04d}-{at.month:02d}"


def window(first: bool, at: datetime | None = None) -> tuple[str, str]:
    """The oldest and newest month a run writes."""
    now = _index(this_month(at))
    return _month(now - (FIRST_MONTHS if first else LATER_MONTHS)), _month(now)


# ----------------------------------------------------------------------------- the source
def sealed_as(sid: str) -> str:
    return "growth_sources:" + sid


def signed(secret: str, body: bytes, header: str) -> bool:
    """Whether `header` is `sha256=` and the HMAC-SHA256 of `body` under `secret`."""
    want = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.strip().encode(), want.encode())


def sync_map(row: dict[str, Any]) -> dict[str, Any]:
    raw: Any = json.loads(row.get("sync_map") or "{}")
    return cast(dict[str, Any], raw) if isinstance(raw, dict) else {}


def is_customers(row: dict[str, Any]) -> bool:
    """A synced source that posts customer rows."""
    return bool(row.get("sync_kind")) and sync_map(row).get("shape") == "customers"


def fills(row: dict[str, Any]) -> list[str]:
    fields: Any = sync_map(row).get("fields") or {}
    return [f for f in FIELDS if f in cast(dict[str, Any], fields)] if row.get("sync_kind") else []


def _parse_at(text: str) -> datetime:
    return datetime.fromisoformat(text.rstrip("Z")).replace(tzinfo=timezone.utc)


def health(row: dict[str, Any], at: datetime | None = None) -> dict[str, Any]:
    """The source's health and since when: fresh, stale, failing, waiting or not_synced."""
    if not row.get("sync_kind"):
        return {"health": "not_synced", "since": None}
    if row.get("last_error"):
        return {"health": "failing", "since": row.get("failing_since")}
    good = row.get("last_good_run_at")
    if not good:
        return {"health": "waiting", "since": None}
    hours = int(row.get("refresh_hours") or REFRESH_HOURS.get(str(row["sync_kind"]), 24))
    stale_at = _parse_at(str(good)) + timedelta(hours=2 * hours)
    if (at or datetime.now(timezone.utc)) > stale_at:
        return {"health": "stale", "since": stale_at.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"}
    return {"health": "fresh", "since": None}


def listed(c: Any, alias: str = "") -> str:
    """The gtm_sources columns to select for a list: every column but the stored file, which can be 1 MB a source, with
    `has_payload` saying whether one is kept."""
    p = f"{alias}." if alias else ""
    names = [str(r[1]) for r in c.execute("PRAGMA table_info(gtm_sources)") if r[1] != "last_payload"]
    return ", ".join(p + n for n in names) + f", length({p}last_payload) > 0 AS has_payload, "
        f"EXISTS (SELECT 1 FROM growth_source_secrets WHERE source_id={p}id) AS has_address"


def view(row: dict[str, Any]) -> dict[str, Any]:
    """What a source adds to its list row: its kind, what it fills, its mapping and its health."""
    kind = row.get("sync_kind")
    state = health(row)
    return {
        "name": row.get("system"),
        "kind": kind,
        "fills": fills(row),
        "map": (GF.public_map(sync_map(row)) if kind == GF.KIND else sync_map(row)) if kind else {},
        "refresh_hours": (row.get("refresh_hours") or REFRESH_HOURS.get(str(kind))) if kind else None,
        "health": state["health"],
        "health_since": state["since"],
        "last_run_at": row.get("last_run_at"),
        "last_good_run": row.get("last_good_run_at"),
        "last_error": row.get("last_error"),
        "has_address": bool(row.get("has_address")),
        "has_file": bool(row.get("has_payload") or row.get("last_payload")),
    }


def _db_keys(kind: str | None, given: dict[str, Any]) -> dict[str, str]:
    """A database query source's database, query and bot, each required; none for another kind."""
    if kind != "db_query":
        return {}
    out: dict[str, str] = {}
    for key in DB_KEYS:
        value = str(given.get(key) or "").strip()
        if not value:
            raise Problem(
                "validation", "A database query source names its database, query and bot (the one granted it)", 422
            )
        out[key] = value.removeprefix("bot:") if key == "bot" else value
    return out


def check_map(c: Any, kind: str | None, mapping: dict[str, Any] | None, sid: str | None) -> dict[str, Any]:
    """The mapping a source will keep: month and channel columns (default `month`, `channel`) and `fields`, the column
    for each number (default the number's own name). Refused when a number is unknown, a column is blank, or another
    live synced source already fills one of its numbers."""
    if not kind:
        return {}
    given: dict[str, Any] = dict(mapping or {})
    if kind == GF.KIND:
        given = GF.check_map(given)
    elif kind == "bot_cost":
        given["fields"] = {"bot_cost": "bot_cost"}  # the only number it fills, from the bots' own run cost
    if given.get("shape") == "customers":
        if given.get("fields") or given.get("month") or given.get("channel"):
            raise Problem("validation", "A customers source maps no numbers: its rows are customers", 422)
        return {"shape": "customers", **_db_keys(kind, given)}
    raw: Any = given.get("fields") or {}
    if not isinstance(raw, dict) or not raw:
        raise Problem("validation", "A synced source fills at least one number: map FIELD=COLUMN", 422)
    fields = cast(dict[str, Any], raw)
    unknown = sorted(set(fields) - set(FIELDS))
    if unknown:
        raise Problem("validation", f"Unknown number {', '.join(unknown)}; one of {', '.join(FIELDS)}", 422)
    out: dict[str, Any] = {
        "month": str(given.get("month") or "month").strip(),
        "channel": str(given.get("channel") or "channel").strip(),
        "fields": {f: str(fields[f] or f).strip() for f in FIELDS if f in fields},
    }
    out.update(_db_keys(kind, given))
    if kind == GF.KIND:
        out["campaigns"] = given["campaigns"]
    for live in H._rows(
        c.execute(
            "SELECT id, system, sync_map, sync_kind FROM gtm_sources WHERE sync_kind IS NOT NULL "
            "AND retired_at IS NULL AND id<>?",
            (sid or "",),
        )
    ):
        taken = sorted(set(fills(cast(dict[str, Any], live))) & set(out["fields"]))
        if taken:
            raise Problem(
                "conflict", f"{', '.join(taken)} already comes from {live['system']}; retire or remap that one", 409
            )
    return out


# ----------------------------------------------------------------------------- reading rows
class RunFailed(Exception):
    """The run cannot write anything: the reason is shown as the source's last error."""


def _number(text: Any, field: str, where: str) -> float | int | None:
    if text is None:
        return None
    if isinstance(text, bool):
        raise RunFailed(f"{where}: {field} {text!r} is not a number")
    if isinstance(text, (int, float)):
        value = float(text)
    else:
        cleaned = str(text).strip().replace(",", "").replace("$", "").rstrip("%").strip()
        if not cleaned:
            return None
        try:
            value = float(cleaned)
        except ValueError:
            raise RunFailed(f"{where}: {field} {str(text)[:40]!r} is not a number") from None
    if math.isnan(value) or math.isinf(value) or value < 0:
        raise RunFailed(f"{where}: {field} {value} is not a number of 0 or more")
    if field in PERCENT and value > 100:
        raise RunFailed(f"{where}: {field} {value} is over 100%")
    if field == "new_customers":
        if value != int(value):
            raise RunFailed(f"{where}: new_customers {value} is not a whole number")
        return int(value)
    return value


def _month_of(text: Any, where: str) -> str:
    value = str(text or "").strip()[:7]
    if len(value) != 7 or value[4] != "-" or not (value[:4] + value[5:]).isdigit() or not 1 <= int(value[5:]) <= 12:
        raise RunFailed(f"{where}: month {str(text)[:20]!r} is not YYYY-MM")
    return value


def read_csv(text: str) -> list[dict[str, Any]]:
    """A CSV's rows, keyed by its header (trimmed, any case kept as written)."""
    if len(text) > PAYLOAD_MAX:
        raise RunFailed(f"The file is over {PAYLOAD_MAX // 1000} KB")
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames:
        raise RunFailed("The file is empty")
    rows: list[dict[str, Any]] = []
    for raw in reader:
        rows.append({str(k or "").strip(): v for k, v in raw.items()})
        if len(rows) > ROWS_MAX:
            raise RunFailed(f"The file has over {ROWS_MAX} rows")
    return rows


def _column(rows: list[dict[str, Any]], name: str) -> str:
    """The key in `rows` for column `name`, matched without case."""
    keys = {str(k).strip().lower(): str(k) for row in rows[:1] for k in row}
    found = keys.get(name.strip().lower())
    if found is None:
        raise RunFailed(f'column "{name}" not found')
    return found


# ----------------------------------------------------------------------------- the run
def _channels(c: Any) -> dict[str, str]:
    """Every live channel's slug and name, lowercased, to its id."""
    out: dict[str, str] = {}
    for r in c.execute("SELECT id, slug, name FROM growth_channels WHERE retired_at IS NULL"):
        out[str(r[1]).lower()] = str(r[0])
        out.setdefault(str(r[2]).strip().lower(), str(r[0]))
    return out


def _source(c: Any, sid: str) -> dict[str, Any]:
    row = H._one(c, "SELECT * FROM gtm_sources WHERE id=?", (sid,))
    if not row:
        raise Problem("not_found", f"No source {sid}", 404)
    return cast(dict[str, Any], row)


def run_bot(row: dict[str, Any]) -> str | None:
    """`bot:<slug>` of the bot that runs a database query source, else None."""
    bot = sync_map(row).get("bot") if row.get("sync_kind") == "db_query" else None
    return f"bot:{bot}" if bot else None


def may_run(c: Any, sid: str, actor: str, is_admin: bool) -> dict[str, Any]:
    """The source, when `actor` may run it: an owner or admin, the source's own owner (a bot posting its rows), or the
    bot that runs its database query."""
    row = _source(c, sid)
    if not is_admin and actor not in (row.get("owner_ref"), run_bot(row)):
        raise Problem("forbidden", "Only the owner, an admin, the source's owner or its bot runs a source", 403)
    if not row.get("sync_kind"):
        raise Problem("conflict", f"{row['system']} does not sync: connect it first (tico growth source update)", 409)
    if row.get("retired_at"):
        raise Problem("conflict", f"{row['system']} is retired", 409)
    return row


def _plan(c: Any, source: dict[str, Any], rows: list[dict[str, Any]], at: datetime) -> dict[str, Any]:
    """What a run would write: {(channel id or '', month): {field: value}} and the rows it will not write. Raises
    RunFailed when nothing may be written."""
    mapping = sync_map(source)
    fields: dict[str, str] = mapping.get("fields") or {}
    if not rows:
        return {"cells": {}, "not_written": []}
    month_key = _column(rows, mapping.get("month") or "month")
    channel_key = _column(rows, mapping.get("channel") or "channel")
    columns = {f: _column(rows, col) for f, col in fields.items()}
    known = _channels(c)
    oldest, newest = window(not source.get("last_good_run_at"), at)
    cells: dict[tuple[str, str], dict[str, Any]] = {}
    seen: dict[tuple[str, str], int] = {}
    not_written: list[dict[str, Any]] = []
    for n, row in enumerate(rows, start=2):  # row 1 is the header
        where = f"row {n}"
        month = _month_of(row.get(month_key), where)
        name = str(row.get(channel_key) or "").strip()
        values = {f: _number(row.get(col), f, where) for f, col in columns.items()}
        if name.lower() in ("", UNKNOWN):
            channel_id = ""
        elif name.lower() in known:
            channel_id = known[name.lower()]
        else:
            not_written.append({"row": n, "channel": name, "month": month, "reason": "no such channel"})
            continue
        key = (channel_id, month)
        if key in seen:
            raise RunFailed(f"{where} repeats {name or 'Unknown'} {month} (row {seen[key]})")
        seen[key] = n
        if month > newest:
            not_written.append({"row": n, "channel": name, "month": month, "reason": "a future month"})
            continue
        if month < oldest:
            not_written.append({"row": n, "channel": name, "month": month, "reason": "before the months a run writes"})
            continue
        cells[key] = {f: v for f, v in values.items() if v is not None}
    return {"cells": cells, "not_written": not_written}


def _cohort(c: Any, channel_id: str, month: str, actor: str) -> dict[str, Any]:
    row = H._one(c, "SELECT * FROM growth_cohorts WHERE coalesce(channel_id,'')=? AND month=?", (channel_id, month))
    if row:
        return cast(dict[str, Any], row)
    cid = H.new_id()
    c.execute(
        "INSERT INTO growth_cohorts (id, channel_id, month, quality, sources, updated_by, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (cid, channel_id or None, month, "actual", "{}", actor, H.now()),
    )
    return cast(dict[str, Any], H._one(c, "SELECT * FROM growth_cohorts WHERE id=?", (cid,)))


def _origins(c: Any, cid: str) -> dict[str, dict[str, Any]]:
    return {
        str(r["field"]): cast(dict[str, Any], r)
        for r in H._rows(c.execute("SELECT * FROM growth_origins WHERE cohort_id=?", (cid,)))
    }


def _same(a: Any, b: Any) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) < 1e-9


def _settle_quality(c: Any, cohort: dict[str, Any]) -> None:
    """The row's quality after a run: estimate while any number it holds is still an estimate. A number with no origin
    row (typed before origins were kept) has the row's own quality."""
    fresh = cast(dict[str, Any], H._one(c, "SELECT * FROM growth_cohorts WHERE id=?", (cohort["id"],)))
    origins = _origins(c, fresh["id"])
    estimate = any(
        fresh.get(f) is not None and (origins[f]["quality"] if f in origins else fresh.get("quality")) == "estimate"
        for f in FIELDS
    )
    quality = "estimate" if estimate else "actual"
    if quality != fresh.get("quality"):
        c.execute("UPDATE growth_cohorts SET quality=? WHERE id=?", (quality, fresh["id"]))


def _write(
    c: Any, source: dict[str, Any], run_id: str, actor: str, cells: dict[tuple[str, str], dict[str, Any]]
) -> tuple[int, int]:
    """Write the planned numbers by the who-wins rules. Returns (numbers written, numbers held)."""
    written = held = 0
    at = H.now()
    sid = str(source["id"])
    for (channel_id, month), values in sorted(cells.items()):
        if not values:
            continue
        cohort = _cohort(c, channel_id, month, actor)
        origins = _origins(c, cohort["id"])
        changed: dict[str, Any] = {}
        for field, value in values.items():
            current = cohort.get(field)
            origin = origins.get(field)
            typed_actual = (
                origin["origin"] == "typed" and origin["quality"] == "actual"
                if origin
                else current is not None and cohort.get("quality") != "estimate"
            )
            if typed_actual and current is not None and not _same(current, value):
                if origin and origin.get("declined_value") is not None and _same(origin["declined_value"], value):
                    continue  # the owner already chose the typed number over this value
                if origin and origin.get("held_source_id") == sid and _same(origin.get("held_value"), value):
                    continue  # already held
                c.execute(
                    "INSERT INTO growth_origins (cohort_id, field, origin, quality, by, at, held_value, held_source_id, "
                    "held_run_id, held_at) VALUES (?,?,'typed','actual',?,?,?,?,?,?) ON CONFLICT(cohort_id, field) DO "
                    "UPDATE SET held_value=excluded.held_value, held_source_id=excluded.held_source_id, "
                    "held_run_id=excluded.held_run_id, held_at=excluded.held_at",
                    (cohort["id"], field, cohort.get("updated_by"), cohort["updated_at"], value, sid, run_id, at),
                )
                H.event(
                    c,
                    actor,
                    "growth.cohort.conflict",
                    cohort["id"],
                    {"field": field, "month": month, "typed": current, "source": sid, "source_value": value},
                )
                held += 1
                continue
            same_source = origin is not None and origin["origin"] == "synced" and origin.get("source_id") == sid
            if same_source and _same(current, value):
                c.execute(
                    "UPDATE growth_origins SET run_id=?, at=? WHERE cohort_id=? AND field=?",
                    (run_id, at, cohort["id"], field),
                )
                continue
            if not _same(current, value):
                changed[field] = value
            c.execute(
                "INSERT INTO growth_origins (cohort_id, field, origin, quality, source_id, run_id, at) "
                "VALUES (?,?,'synced','actual',?,?,?) ON CONFLICT(cohort_id, field) DO UPDATE SET origin='synced', "
                "quality='actual', source_id=excluded.source_id, run_id=excluded.run_id, at=excluded.at, by=NULL, "
                "held_value=NULL, held_source_id=NULL, held_run_id=NULL, held_at=NULL, declined_value=NULL",
                (cohort["id"], field, sid, run_id, at),
            )
        if changed:
            c.execute(
                f"UPDATE growth_cohorts SET {', '.join(k + '=?' for k in changed)}, updated_by=?, updated_at=? "
                "WHERE id=?",
                [*changed.values(), actor, at, cohort["id"]],
            )
            written += len(changed)
        _settle_quality(c, cohort)
    return written, held


def _load_customers(
    c: Any, actor: str, rows: list[dict[str, Any]] | None, csv_text: str | None, source: dict[str, Any]
) -> int:
    """One customers run: a load under the source's own id. Returns the customers it holds now."""
    try:
        if csv_text is not None:
            headers, table = GC.parse_csv(csv_text)
        elif rows is None:
            raise RunFailed("No rows were given")
        else:
            names: list[str] = list(dict.fromkeys(k for row in rows for k in row))
            headers, table = list[GC.Cell](names), [[row.get(h) for h in names] for row in rows]
        loaded = GC.load(c, actor, headers, table, source=GC.source_key(str(source["id"])))
    except Problem as refused:
        raise RunFailed(str(refused)) from None
    return int(loaded["customers"])


def forget_customers(c: Any, actor: str, source: dict[str, Any]) -> None:
    """A customers source that is retired, stops syncing or maps numbers instead: its customers and its stored file go
    at once (spec privacy rules)."""
    GC.delete_all(c, actor, GC.source_key(str(source["id"])))
    c.execute("UPDATE gtm_sources SET last_payload=NULL WHERE id=?", (source["id"],))


def _notify(c: Any, source: dict[str, Any], text: str) -> None:
    owner = source.get("owner_ref")
    if owner:
        H.notice(c, H.KEEPER, owner, text, refs={"growth_source": source["id"]})


def run(
    c: Any,
    actor: str,
    source: dict[str, Any],
    *,
    rows: list[dict[str, Any]] | None = None,
    csv_text: str | None = None,
    error: str | None = None,
    at: datetime | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """One run of `source`: rows (or a CSV) to write, or the `error` its fetch ended with. Never raises for a bad
    payload: a failed run is recorded, the source shows Failing with the reason and nothing it wrote changes."""
    at = at or datetime.now(timezone.utc)
    run_id = H.new_id()
    started = H.now()
    result: dict[str, Any] = {
        "run": run_id,
        "source": source["id"],
        "ok": False,
        "error": None,
        "written": 0,
        "held": 0,
        "not_written": [],
        "not_written_count": 0,
    }
    if notes:
        result["notes"] = notes
    try:
        if error:
            raise RunFailed(" ".join(error.split())[:ERROR_CHARS])
        if is_customers(source):  # its file keeps up to growth_customers.ROWS_MAX customers, not this module's cap
            result.update(ok=True, written=_load_customers(c, actor, rows, csv_text, source))
        else:
            if csv_text is not None:
                rows = read_csv(csv_text)
            if rows is None:
                raise RunFailed("No rows were given")
            if len(rows) > ROWS_MAX:
                raise RunFailed(f"Over {ROWS_MAX} rows")
            plan = _plan(c, source, rows, at)
            written, held = _write(c, source, run_id, actor, plan["cells"])
            result.update(
                ok=True,
                written=written,
                held=held,
                not_written=plan["not_written"][:NOT_WRITTEN_MAX],
                not_written_count=len(plan["not_written"]),
            )
    except RunFailed as failed:
        result["error"] = str(failed)[:ERROR_CHARS]
    c.execute(
        "INSERT INTO growth_sync_runs (id, source_id, actor, started_at, ok, error, written, held, not_written) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            source["id"],
            actor,
            started,
            1 if result["ok"] else 0,
            result["error"],
            result["written"],
            result["held"],
            json.dumps(result["not_written"]),
        ),
    )
    payload = csv_text if csv_text is not None and len(csv_text) <= PAYLOAD_MAX else source.get("last_payload")
    if result["ok"]:
        c.execute(
            "UPDATE gtm_sources SET last_run_at=?, last_good_run_at=?, last_error=NULL, failing_since=NULL, "
            "last_payload=? WHERE id=?",
            (started, started, payload, source["id"]),
        )
        if source.get("last_error"):
            _notify(c, source, f"{source['system']} syncs again: its last run wrote {result['written']} numbers.")
    else:
        c.execute(
            "UPDATE gtm_sources SET last_run_at=?, last_error=?, failing_since=coalesce(failing_since, ?) WHERE id=?",
            (started, result["error"], started, source["id"]),
        )
        if not source.get("last_error"):
            _notify(c, source, f"{source['system']} stopped syncing: {result['error']}. Nothing it wrote changed.")
    H.event(
        c,
        actor,
        "growth.source.run",
        source["id"],
        {k: result[k] for k in ("run", "ok", "error", "written", "held", "not_written_count")},
    )
    return result


# ----------------------------------------------------------------------------- bot cost
def bot_cost_rows(c: Any, at: datetime | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """The rows a bot cost run posts: each month's run cost (USD, two places) of the bot that owns a channel, on that
    channel. A bot owning several channels cannot be split between them, so its cost goes to Unknown, and a note says
    so. A bot that owns no channel is not a Growth cost."""
    from .usage_records import RUNS

    oldest, newest = window(False, at)
    first = f"{oldest}-01"
    last = f"{_month(_index(newest) + 1)}-01"
    owned: dict[str, list[str]] = {}
    for r in c.execute(
        "SELECT slug, owner_ref FROM growth_channels WHERE retired_at IS NULL AND owner_ref LIKE 'bot:%'"
    ):
        owned.setdefault(str(r[1]).removeprefix("bot:"), []).append(str(r[0]))
    cost: dict[tuple[str, str], float] = {}
    notes: list[str] = []
    for bot, month, total in c.execute(
        f"SELECT bot, substr(started_at,1,7), coalesce(sum(est_cost_usd),0) FROM {RUNS} "
        "WHERE started_at >= ? AND started_at < ? GROUP BY bot, substr(started_at,1,7)",
        (first, last),
    ).fetchall():
        slugs = owned.get(str(bot))
        if not slugs or not total:
            continue
        channel = slugs[0] if len(slugs) == 1 else "unknown"
        if len(slugs) > 1:
            note = f"{bot} owns {len(slugs)} channels ({', '.join(sorted(slugs))}): its cost is counted on Unknown"
            if note not in notes:
                notes.append(note)
        cost[(channel, str(month))] = cost.get((channel, str(month)), 0.0) + float(total)
    rows = [{"month": m, "channel": ch, "bot_cost": round(v, 2)} for (ch, m), v in sorted(cost.items())]
    return rows, sorted(notes)


# ----------------------------------------------------------------------------- typing and conflicts
def live_owner(c: Any, cohort_id: str, field: str) -> dict[str, Any] | None:
    """The live synced source that filled this number and still fills it, if any; a remap that drops the number
    hands it back to typing."""
    row = H._one(
        c,
        f"SELECT {listed(c, 's')} FROM growth_origins o JOIN gtm_sources s ON s.id=o.source_id WHERE o.cohort_id=? "
        "AND o.field=? "
        "AND o.origin='synced' AND s.sync_kind IS NOT NULL AND s.retired_at IS NULL",
        (cohort_id, field),
    )
    if not row or field not in fills(cast(dict[str, Any], row)):
        return None
    return cast(dict[str, Any], row)


def refuse_typed(c: Any, cohort: dict[str, Any] | None, fields: list[str], channel: str, month: str) -> None:
    """Typing a number a live synced source fills is refused, naming the source."""
    if not cohort:
        return
    for field in fields:
        owner = live_owner(c, cohort["id"], field)
        if owner:
            raise Problem(
                "conflict",
                f"{field} for {channel} in {month} comes from {owner['system']}, which syncs it; "
                "change it there, or retire that source to type it",
                409,
            )


def record_typed(c: Any, cohort_id: str, fields: dict[str, Any], quality: str, actor: str) -> None:
    """Each number typed now is typed by `actor`, with the row's quality. A cleared number keeps no origin."""
    at = H.now()
    for field, value in fields.items():
        if field not in FIELDS:
            continue
        if value is None:
            c.execute("DELETE FROM growth_origins WHERE cohort_id=? AND field=?", (cohort_id, field))
            continue
        c.execute(
            "INSERT INTO growth_origins (cohort_id, field, origin, quality, by, at) VALUES (?,?,'typed',?,?,?) "
            "ON CONFLICT(cohort_id, field) DO UPDATE SET origin='typed', quality=excluded.quality, by=excluded.by, "
            "at=excluded.at, source_id=NULL, run_id=NULL",
            (cohort_id, field, quality, actor, at),
        )


def origins_view(c: Any, cohort_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Each cohort's numbers' origins: {cohort id: {field: {origin, quality, source_id, source, at, by, conflict}}}."""
    if not cohort_ids:
        return {}
    names = {str(r[0]): str(r[1]) for r in c.execute("SELECT id, system FROM gtm_sources")}
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(cohort_ids), 500):
        part = cohort_ids[i : i + 500]
        for r in H._rows(
            c.execute(f"SELECT * FROM growth_origins WHERE cohort_id IN ({','.join('?' * len(part))})", part)
        ):
            held = r["held_source_id"]
            out.setdefault(str(r["cohort_id"]), {})[str(r["field"])] = {
                "origin": r["origin"],
                "quality": r["quality"],
                "source_id": r["source_id"],
                "source": names.get(r["source_id"]) if r["source_id"] else None,
                "at": r["at"],
                "by": r["by"],
                "conflict": {
                    "value": r["held_value"],
                    "source_id": held,
                    "source": names.get(held),
                    "at": r["held_at"],
                }
                if held
                else None,
            }
    return out


def resolve(c: Any, actor: str, is_admin: bool, cohort_id: str, field: str, keep: str) -> dict[str, Any]:
    """The source's owner (or an admin) settles a held value: keep the typed number, or use the source's."""
    origin = H._one(c, "SELECT * FROM growth_origins WHERE cohort_id=? AND field=?", (cohort_id, field))
    if not origin or not origin.get("held_source_id"):
        raise Problem("not_found", f"No conflict on {field} for that month", 404)
    source = _source(c, origin["held_source_id"])
    if not is_admin and source.get("owner_ref") != actor:
        raise Problem("forbidden", f"Only {source['system']}'s owner or an admin chooses", 403)
    cohort = cast(dict[str, Any], H._one(c, "SELECT * FROM growth_cohorts WHERE id=?", (cohort_id,)))
    typed, value = cohort.get(field), origin["held_value"]
    at = H.now()
    if keep == "source":
        stored: Any = int(value) if field == "new_customers" else value
        c.execute(
            f"UPDATE growth_cohorts SET {field}=?, updated_by=?, updated_at=? WHERE id=?",
            (stored, actor, at, cohort_id),
        )
        c.execute(
            "UPDATE growth_origins SET origin='synced', quality='actual', source_id=held_source_id, run_id=held_run_id, "
            "at=?, by=NULL, held_value=NULL, held_source_id=NULL, held_run_id=NULL, held_at=NULL, declined_value=NULL "
            "WHERE cohort_id=? AND field=?",
            (at, cohort_id, field),
        )
    elif keep == "typed":
        c.execute(
            "UPDATE growth_origins SET declined_value=held_value, held_value=NULL, held_source_id=NULL, "
            "held_run_id=NULL, held_at=NULL WHERE cohort_id=? AND field=?",
            (cohort_id, field),
        )
    else:
        raise Problem("validation", "keep is typed or source", 422)
    H.event(
        c,
        actor,
        "growth.cohort.conflict.resolve",
        cohort_id,
        {"field": field, "kept": keep, "typed": typed, "source": source["id"], "source_value": value},
    )
    return {"cohort": cohort_id, "field": field, "kept": keep, "value": value if keep == "source" else typed}
