"""KPIs: measures that stand on their own, and how each one is doing (docs/goals-and-kpis.md).

A KPI is a record with a name, a short definition, a unit, a direction (up, down or range), a
cadence (daily, weekly or monthly), one accountable owner, a source note and a `definition_version`
that goes up whenever the definition changes. A goal links to zero or more KPIs through `goal_kpis`,
and a KPI may serve several goals. The target belongs to that link: an improvement (a baseline, a
target and a deadline, paced in a straight line) or a maintenance range (a min, a max or both).

A reading is a fact. It keeps the business time it describes (`period_start`, `period_end`) apart
from `collected_at`, carries an evidence link or note, a quality (measured, estimate or partial) and
the definition version it used. Readings are never edited: a correction is a new reading that
`supersedes` the old one, and the old one stays in the history.

Colours are arithmetic, not opinion. `assess` turns a KPI, one link and its readings into
green, yellow, red or gray, and gray is what a KPI with no fresh data is: a missing reading is
never a zero. Nothing in this module writes a goal's colour; that is backend/goals.py.
"""

import math
import re
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from . import hubdb as H

CADENCES = {"daily": 1, "weekly": 7, "monthly": 30}      # days in a period
DIRECTIONS = ("up", "down", "range")
QUALITIES = ("measured", "estimate", "partial")
KINDS = ("none", "improve", "maintain")
COLOURS = ("red", "yellow", "green", "gray")
TOLERANCE = 0.10        # yellow: behind the needed pace by no more than this share of it
FLOOR = 0.02            # ...and never tighter than this share of the whole climb, so day one is not red
GRACE = 0.25            # a reading is due a quarter period late before it counts as missed
AUTO = "auto:"          # the id prefix of a KPI Tico computes itself (backend/botkpis.py)
SPARK = 24              # readings behind the sparkline


# ----------------------------------------------------------------------------- time and words
def utc(value, end=False):
    """A date, a date-time or a timestamp as `...Z` text. A bare date is the start of that day, or the
    end of it for `end` (a period that ends on the 20th ends at the close of the 20th)."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        text += "T23:59:59.999999Z" if end else "T00:00:00.000000Z"
    parsed = H.parse_ts(text)
    if not parsed:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _dt(value):
    parsed = H.parse_ts(value)
    if parsed and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def now():
    """The hub's clock (`hubdb.now`), as a datetime: what "today" is for freshness and pace."""
    return _dt(H.now())


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:60].strip("-")
    return slug or "kpi"


SYMBOLS = ("%", "pp", "$", "€", "£")    # written against the number: 52%, $1,200
# A word unit that measures time or a score keeps one decimal (4.5 months); any other word unit counts things
# (studios, demos, tickets), so its numbers are whole: 134.5 studios is 135.
MEASURES = {"year", "years", "yr", "yrs", "month", "months", "mo", "mos", "week", "weeks", "wk", "wks", "day", "days",
            "hour", "hours", "hr", "hrs", "h", "minute", "minutes", "min", "mins", "second", "seconds", "sec", "secs", "s",
            "ms", "x", "point", "points", "pts", "score", "nps", "stars", "rating"}


def counts(unit):
    """True for a unit that counts things: a word that is not a symbol and not a measure of time or score."""
    unit = str(unit or "").strip()
    return bool(unit) and unit not in SYMBOLS and unit.lower() not in MEASURES


def number(value, whole=False):
    """One decimal, half up, and none when it is zero: 52 -> '52', 52.25 -> '52.3', 12000 -> '12,000'. `whole` rounds
    to an integer (134.5 -> '135')."""
    if value is None:
        return ""
    value = float(value)
    if not math.isfinite(value):
        return str(value)
    rounded = Decimal(repr(value)).quantize(Decimal(1) if whole else Decimal("0.1"), rounding=ROUND_HALF_UP)
    if rounded == rounded.to_integral_value():
        return f"{int(rounded):,}"
    return f"{rounded:,.1f}"


def amount(value, unit=""):
    """A number with its unit: '52%', '$1,200', '148 studios' (whole, as a count), '4.5 months'."""
    unit = str(unit or "").strip()
    text = number(value, counts(unit))
    if not unit:
        return text
    if unit in ("%", "pp"):
        return text + unit
    if unit in ("$", "€", "£"):
        return unit + text
    return f"{text} {unit}"


def versus(value, other, unit=""):
    """Two amounts side by side with a word unit said once, on the second: ('148', '135 studios'). A symbol stays on
    both ('52%', '58%'), as it reads."""
    unit = str(unit or "").strip()
    if unit and unit not in SYMBOLS:
        return number(value, counts(unit)), amount(other, unit)
    return amount(value, unit), amount(other, unit)


def day(value):
    """`2026-12-31` -> 'Dec 31' (with the year when it is not this one)."""
    parsed = _dt(utc(value))
    if not parsed:
        return ""
    current = now()
    return parsed.strftime("%b ") + str(parsed.day) + ("" if parsed.year == current.year else f", {parsed.year}")


# ----------------------------------------------------------------------------- freshness
def freshness(cadence, last_period_end, at=None):
    """`fresh`, `stale` (one period missed) or `missing` (never read, or two or more periods missed),
    with the age in days. A reading is due one period after the one before it, plus a grace of a
    quarter period for the collector to run."""
    at = at or now()
    ended = _dt(last_period_end)
    if not ended:
        return "missing", None
    period = CADENCES.get(cadence, 7)
    age = max(0.0, (at - ended).total_seconds() / 86400)
    if age <= period * (1 + GRACE):
        return "fresh", age
    if age <= period * (2 + GRACE):
        return "stale", age
    return "missing", age


def age_words(days):
    if days is None:
        return ""
    return f"{int(days)}d" if days >= 1 else "today"


# ----------------------------------------------------------------------------- the arithmetic
def pace(baseline, baseline_at, target, deadline, value, at, direction="up"):
    """Improvement against a straight line from the baseline to the target by the deadline.
    Returns (colour, expected value now). Green: on pace or past it. Yellow: behind, but by no more than
    10% of the pace the line needs so far (and never tighter than 2% of the whole climb). Red: further behind."""
    span = target - baseline
    sign = 1 if span > 0 else -1 if span < 0 else (-1 if direction == "down" else 1)
    total = (deadline - baseline_at).total_seconds()
    fraction = 1.0 if total <= 0 else min(1.0, max(0.0, (at - baseline_at).total_seconds() / total))
    expected = baseline + span * fraction
    if sign * (value - target) >= 0:
        return "green", expected
    needed = abs(span) * fraction
    behind = needed - sign * (value - baseline)
    if behind <= 1e-9:
        return "green", expected
    tolerance = max(TOLERANCE * needed, FLOOR * abs(span))
    return ("yellow" if behind <= tolerance else "red"), expected


def within(value, low, high):
    """Maintenance: green inside the range, yellow within a tenth of it of an edge, red outside."""
    if (low is not None and value < low) or (high is not None and value > high):
        return "red"
    if low is not None and high is not None:
        band = TOLERANCE * (high - low)
    else:
        band = TOLERANCE * abs(low if low is not None else high)
    near = (low is not None and value < low + band) or (high is not None and value > high - band)
    return "yellow" if near else "green"


def range_label(low, high, unit=""):
    tail = "%" if unit == "%" else ""
    if low is not None and high is not None:
        return f"range {number(low)}–{number(high)}{tail}"
    if low is not None:
        return "min " + amount(low, unit)
    if high is not None:
        return "max " + amount(high, unit)
    return ""


def target_label(kpi, link):
    """'→ 65% by Dec 31' or 'range 40–60', or '' when the link sets none."""
    if not link or link.get("kind") == "none":
        return ""
    unit = kpi.get("unit") or ""
    if link["kind"] == "maintain":
        return range_label(link.get("min"), link.get("max"), unit)
    if link.get("target") is None:
        return ""
    return "→ " + amount(link["target"], unit) + (" by " + day(link["deadline"]) if link.get("deadline") else "")


def latest(readings, usable=False):
    """The newest effective reading by business time, then by when it was collected. `usable` skips a
    partial one: half a period is not a value to judge."""
    pool = [r for r in readings if not (usable and r.get("quality") == "partial")]
    return max(pool, key=lambda r: (r["period_end"] or "", r["collected_at"] or "", r["created"] or ""), default=None)


def assess(kpi, link, readings, at=None):
    """How one KPI is doing against one link (or none): {status, reason, freshness, value, expected,
    basis, age_days}. `readings` are the effective ones (nothing superseded). gray is stale or
    missing data; `none` is a fresh KPI with no target to judge it by."""
    at = at or now()
    name, unit = kpi["name"], kpi.get("unit") or ""
    newest = latest(readings)
    used = latest(readings, usable=True)
    state, age = freshness(kpi.get("cadence") or "weekly", used and used["period_end"], at)
    out = {"status": "gray", "reason": "", "freshness": state, "value": None, "expected": None, "basis": None,
           "age_days": None if age is None else round(age, 1), "reading_id": used and used.get("id")}
    if newest and not used:
        out["reason"] = f"{name} has only partial data"
        return out
    if state == "missing" and not newest:
        out["reason"] = f"{name} has no reading yet"
        return out
    if state == "missing":
        out["reason"] = f"{name} is missing: last read {age_words(age)} ago ({kpi.get('cadence')})"
        return out
    if state == "stale":
        out["reason"] = f"{name} is stale: last read {age_words(age)} ago ({kpi.get('cadence')})"
        return out
    value = used["value"]
    out.update(value=value, basis=used.get("quality"))
    note = " (estimate)" if used.get("quality") == "estimate" else ""
    current = amount(value, unit)
    if not link or link.get("kind") in (None, "none") or (
            link["kind"] == "improve" and link.get("target") is None):
        out.update(status="none", reason=f"{name} {current}{note}")
        return out
    if link["kind"] == "maintain":
        colour = within(value, link.get("min"), link.get("max"))
        span = range_label(link.get("min"), link.get("max"), unit)
        low, high = link.get("min"), link.get("max")
        where = "below" if low is not None and value < low else "above" if high is not None and value > high else "in"
        if span.startswith(("min ", "max ")):            # the bound carries the unit: say it once
            current = versus(value, low if low is not None else high, unit)[0]
        out.update(status=colour, reason=f"{name} {current} {where} {span}{note}")
        return out
    target = link["target"]
    falling = (target < link["baseline"]) if link.get("baseline") is not None else kpi.get("direction") == "down"
    reached = (value <= target) if falling else (value >= target)
    mine, goal = versus(value, target, unit)
    if not link.get("deadline"):
        out.update(status="green" if reached else "gray",
                   reason=f"{name} {mine} reached {goal}{note}" if reached
                   else f"{name} {mine}, target {goal} has no deadline{note}")
        return out
    start = link.get("baseline")
    started = _dt(link.get("baseline_at"))
    if start is None:
        # No baseline set: the first usable reading is where the line starts.
        first = min((r for r in readings if r.get("quality") != "partial"),
                    key=lambda r: (r["period_end"] or "", r["collected_at"] or ""), default=used)
        start, started = first["value"], _dt(first["period_end"])
    started = started or _dt(link.get("created")) or at
    deadline = _dt(utc(link["deadline"], end=True))
    colour, expected = pace(start, started, target, deadline, value, _dt(used["period_end"]) or at, kpi.get("direction") or "up")
    out["expected"] = expected
    if reached:
        reason = f"{name} {mine} reached {goal}{note}"
    elif at > deadline:
        reason = f"{name} {mine} missed {goal} by {day(link['deadline'])}{note}"
    else:
        mine, needed = versus(value, expected, unit)
        reason = f"{name} {mine} vs {needed} needed on pace{note}"
    out.update(status=colour, reason=reason)
    return out


def validate_target(kpi, fields):
    """The link's target fields, cleaned, or a sentence saying what is wrong. Returns (values, error)."""
    kind = fields.get("kind") or "none"
    if kind not in KINDS:
        return None, f"a target is {'|'.join(KINDS)}, not {kind}"
    out = {"kind": kind, "baseline": None, "baseline_at": None, "target": None, "deadline": None,
           "min": None, "max": None}
    if kind == "improve":
        if fields.get("target") is None:
            return None, "an improvement needs a target value"
        if not fields.get("deadline"):
            return None, "an improvement needs a deadline: the date the target is due"
        deadline = utc(fields["deadline"], end=True)
        if not deadline:
            return None, "deadline must be a date like 2026-12-31"
        out.update(target=float(fields["target"]), deadline=str(fields["deadline"])[:10])
        if fields.get("baseline") is not None:
            baseline = float(fields["baseline"])
            if baseline == out["target"]:
                return None, "the target is the baseline: there is nothing to improve"
            direction = kpi.get("direction")
            if (direction == "up" and out["target"] < baseline) or (direction == "down" and out["target"] > baseline):
                return None, f"the target is on the wrong side of the baseline for a KPI that should go {direction}"
            out["baseline"] = baseline
            out["baseline_at"] = utc(fields.get("baseline_at"))  or H.now()
    elif kind == "maintain":
        low, high = fields.get("min"), fields.get("max")
        if low is None and high is None:
            return None, "a range needs a min, a max or both"
        if low is not None and high is not None and float(low) > float(high):
            return None, "the min is above the max"
        out.update(min=None if low is None else float(low), max=None if high is None else float(high))
    return out, None


# ----------------------------------------------------------------------------- reads
def auto(kpi_id):
    return str(kpi_id or "").startswith(AUTO)


def kpi(conn, kpi_id):
    if auto(kpi_id):
        from . import botkpis
        return botkpis.kpi(conn, kpi_id)
    return H._one(conn, "SELECT * FROM kpis WHERE id=?", (kpi_id,))


def readings(conn, kpi_id, effective=False, limit=500):
    """Every reading of a KPI, oldest business time first, each with `superseded_by`; `effective`
    leaves out the ones a correction replaced."""
    if auto(kpi_id):
        from . import botkpis
        return botkpis.readings(conn, kpi_id)
    rows = H._rows(conn.execute(
        "SELECT r.*, (SELECT s.id FROM kpi_readings s WHERE s.supersedes=r.id ORDER BY s.created LIMIT 1) AS superseded_by "
        "FROM kpi_readings r WHERE r.kpi_id=? ORDER BY r.period_end, r.collected_at, r.created", (kpi_id,)))
    if effective:
        rows = [r for r in rows if not r["superseded_by"]]
    return rows[-limit:]


def effective_many(conn, kpi_ids):
    """{kpi id: effective readings, oldest first}, two queries for any number of KPIs."""
    ids = [k for k in dict.fromkeys(kpi_ids) if not auto(k)]
    out = {k: [] for k in ids}
    for start in range(0, len(ids), 400):
        chunk = ids[start:start + 400]
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(
                f"SELECT * FROM kpi_readings r WHERE kpi_id IN ({marks}) AND NOT EXISTS "
                "(SELECT 1 FROM kpi_readings s WHERE s.supersedes=r.id) "
                "ORDER BY r.period_end, r.collected_at, r.created", chunk):
            out[row["kpi_id"]].append(dict(row))
    return out


def links_of(conn, goal_id=None, kpi_id=None):
    where, args = [], []
    if goal_id:
        where.append("goal_id=?")
        args.append(goal_id)
    if kpi_id:
        where.append("kpi_id=?")
        args.append(kpi_id)
    return H._rows(conn.execute("SELECT * FROM goal_kpis" + (" WHERE " + " AND ".join(where) if where else "")
                                + " ORDER BY created", args))


def link(conn, goal_id, kpi_id):
    return H._one(conn, "SELECT * FROM goal_kpis WHERE goal_id=? AND kpi_id=?", (goal_id, kpi_id))


def spark(rows):
    """The last values, oldest first, one per business period (a correction already replaced its original)."""
    by_period = {}
    for r in rows:
        if r.get("quality") != "partial":
            by_period[r["period_end"]] = r["value"]
    return [by_period[k] for k in sorted(by_period)][-SPARK:]


def reading_brief(r):
    if not r:
        return None
    keep = ("id", "value", "period_start", "period_end", "collected_at", "quality", "evidence", "note", "source",
            "actor", "definition_version", "supersedes")
    return {k: r.get(k) for k in keep}


def view(record, rows, links=None, at=None):
    """One KPI as the page and the API show it: its fields, its latest reading, how fresh, its
    sparkline, and (for a goal's line) the link with its target and the status against it."""
    newest = latest(rows)
    state = assess(record, links, rows, at)
    base = {**{k: record.get(k) for k in ("id", "slug", "name", "definition", "unit", "direction", "cadence", "owner",
                                          "source_note", "definition_version", "created", "created_by", "updated",
                                          "archived_at", "archived_by")},
            "auto": auto(record["id"]), "latest": reading_brief(newest), "readings": len(rows),
            "freshness": state["freshness"], "spark": spark(rows)}
    if links is not None:
        base.update(link={k: links.get(k) for k in ("id", "goal_id", "kind", "baseline", "baseline_at", "target",
                                                    "deadline", "min", "max")},
                    target_label=target_label(record, links), status=state["status"], reason=state["reason"],
                    expected=state["expected"])
    else:
        base.update(status=state["status"], reason=state["reason"])
    return base


def goal_views(conn, goal_ids, at=None):
    """{goal id: [KPI views with their link and status]} for many goals in a few queries."""
    goal_ids = list(dict.fromkeys(goal_ids))
    out = {g: [] for g in goal_ids}
    rows = []
    for start in range(0, len(goal_ids), 400):
        chunk = goal_ids[start:start + 400]
        rows.extend(H._rows(conn.execute(
            f"SELECT * FROM goal_kpis WHERE goal_id IN ({','.join('?' * len(chunk))}) ORDER BY created", chunk)))
    if not rows:
        return out
    ids = list({r["kpi_id"] for r in rows})
    real = [i for i in ids if not auto(i)]
    records = {}
    for start in range(0, len(real), 400):
        chunk = real[start:start + 400]
        for row in conn.execute(f"SELECT * FROM kpis WHERE id IN ({','.join('?' * len(chunk))}) "
                                "AND archived_at IS NULL", chunk):
            records[row["id"]] = dict(row)
    data = effective_many(conn, real)
    for r in rows:
        if auto(r["kpi_id"]):
            from . import botkpis
            record = botkpis.kpi(conn, r["kpi_id"])
            series = botkpis.readings(conn, r["kpi_id"]) if record else []
        else:
            record, series = records.get(r["kpi_id"]), data.get(r["kpi_id"], [])
        if record:
            out[r["goal_id"]].append(view(record, series, r, at))
    return out


def goal_link_status(conn, goal_id, at=None):
    """The linked KPIs of one goal that carry a target, judged: what a goal's automatic colour is made of."""
    return [v for v in goal_views(conn, [goal_id], at)[goal_id]
            if (v.get("link") or {}).get("kind") in ("improve", "maintain")]


# ----------------------------------------------------------------------------- writes
def _unique_slug(conn, name):
    base, slug, n = slugify(name), None, 1
    slug = base
    while conn.execute("SELECT 1 FROM kpis WHERE slug=?", (slug,)).fetchone():
        n += 1
        slug = f"{base}-{n}"
    return slug


def _version(conn, row, actor, ts):
    conn.execute("INSERT OR IGNORE INTO kpi_definitions (id, kpi_id, version, ts, actor, name, definition, unit, "
                 "direction, cadence, source_note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (f"{row['id']}:{row['definition_version']}", row["id"], row["definition_version"], ts, actor,
                  row["name"], row["definition"], row["unit"], row["direction"], row["cadence"], row["source_note"]))


def clean(fields):
    """The descriptive fields of a KPI, checked. Refuses in words a person can act on."""
    out = {}
    if "name" in fields and fields["name"] is not None:
        out["name"] = str(fields["name"]).strip()
        if not out["name"]:
            raise ValueError("name the measure, in words: what is counted, per what")
    for key in ("definition", "unit", "source_note"):
        if fields.get(key) is not None:
            out[key] = str(fields[key]).strip()
    if fields.get("direction") is not None:
        if fields["direction"] not in DIRECTIONS:
            raise ValueError(f"direction is {'|'.join(DIRECTIONS)}, not {fields['direction']}")
        out["direction"] = fields["direction"]
    if fields.get("cadence") is not None:
        if fields["cadence"] not in CADENCES:
            raise ValueError(f"cadence is {'|'.join(CADENCES)}, not {fields['cadence']}")
        out["cadence"] = fields["cadence"]
    return out


def create(conn, actor, owner, fields, kid=None):
    """A new standalone KPI, at definition version 1."""
    values = clean(fields)
    if not values.get("name"):
        raise ValueError("name the measure, in words: what is counted, per what")
    ts = H.now()
    row = {"id": kid or H.new_id(), "goal_id": "", "name": values["name"], "unit": values.get("unit", ""), "target": None,
           "created": ts, "created_by": actor, "slug": _unique_slug(conn, values["name"]),
           "definition": values.get("definition", ""), "direction": values.get("direction", "up"),
           "cadence": values.get("cadence", "weekly"), "owner": owner, "source_note": values.get("source_note", ""),
           "definition_version": 1, "updated": ts}
    conn.execute("INSERT INTO kpis (id, goal_id, name, unit, target, created, created_by, slug, definition, direction, "
                 "cadence, owner, source_note, definition_version, updated) VALUES (:id, :goal_id, :name, :unit, "
                 ":target, :created, :created_by, :slug, :definition, :direction, :cadence, :owner, :source_note, "
                 ":definition_version, :updated)", row)
    _version(conn, row, actor, ts)
    return kpi(conn, row["id"])


DEFINING = ("definition", "unit", "direction", "cadence", "source_note")


def update(conn, actor, kpi_id, fields, owner=None):
    """Change a KPI's fields. Any change to what it measures (the definition, unit, direction, cadence
    or source note) is a new definition version; a rename or a new owner is not. Returns (row, changes)."""
    row = kpi(conn, kpi_id)
    values = clean(fields)
    changes = {k: (row[k], v) for k, v in values.items() if row[k] != v}
    if owner is not None and owner != row["owner"]:
        changes["owner"] = (row["owner"], owner)
    if not changes:
        return row, {}
    ts = H.now()
    sets = {k: new for k, (_, new) in changes.items()}
    if any(k in DEFINING for k in changes):
        sets["definition_version"] = row["definition_version"] + 1
    sets["updated"] = ts
    conn.execute(f"UPDATE kpis SET {', '.join(k + '=:' + k for k in sets)} WHERE id=:id", {**sets, "id": kpi_id})
    after = kpi(conn, kpi_id)
    if "definition_version" in sets:
        _version(conn, after, actor, ts)
    return after, changes


def set_archived(conn, actor, kpi_id, archived):
    """Hide or restore a KPI without changing its definition, links, readings or history."""
    row = kpi(conn, kpi_id)
    if not row or auto(kpi_id):
        raise ValueError("only a stored KPI can be archived")
    archived = bool(archived)
    if bool(row.get("archived_at")) == archived:
        return row
    if archived:
        ts = H.now()
        conn.execute("UPDATE kpis SET archived_at=?, archived_by=?, updated=? WHERE id=?",
                     (ts, actor, ts, kpi_id))
        H.event(conn, actor, "kpi.archive", kpi_id)
    else:
        conn.execute("UPDATE kpis SET archived_at=NULL, archived_by=NULL, updated=? WHERE id=?",
                     (H.now(), kpi_id))
        H.event(conn, actor, "kpi.restore", kpi_id)
    return kpi(conn, kpi_id)


def definitions(conn, kpi_id):
    return H._rows(conn.execute("SELECT * FROM kpi_definitions WHERE kpi_id=? ORDER BY version DESC", (kpi_id,)))


def add_reading(conn, actor, row, value, period_start=None, period_end=None, collected_at=None, evidence="",
                quality=None, source="", note="", definition_version=None, supersedes=None):
    """Append one reading. Returns it. Raises ValueError with what to fix."""
    if not math.isfinite(float(value)):
        raise ValueError("a reading is a number")
    source = str(source or "").strip()[:40]
    if quality is None:
        quality = source if source in ("measured", "estimate") else "measured"
        source = "" if source in ("measured", "estimate") else source
    if quality not in QUALITIES:
        raise ValueError(f"quality is {'|'.join(QUALITIES)}, not {quality}")
    now = H.now()
    end = utc(period_end, end=True) if period_end else now
    if not end:
        raise ValueError("period_end must be an ISO date or date-time")
    period = timedelta(days=CADENCES.get(row["cadence"], 7))
    start = utc(period_start) if period_start else H.shift(end, seconds=-period.total_seconds())
    if not start:
        raise ValueError("period_start must be an ISO date or date-time")
    if start > end:
        raise ValueError("the period starts after it ends")
    if end > H.shift(now, seconds=60):
        raise ValueError("a reading describes a period that has ended, not one still to come")
    collected = utc(collected_at) if collected_at else now
    if not collected:
        raise ValueError("collected_at must be an ISO date-time")
    version = int(definition_version) if definition_version is not None else row["definition_version"]
    if version > row["definition_version"] or version < 1:
        raise ValueError(f"definition_version {version} does not exist; the KPI is at {row['definition_version']}")
    note = str(note or "").strip()
    if supersedes:
        old = H._one(conn, "SELECT * FROM kpi_readings WHERE id=? AND kpi_id=?", (supersedes, row["id"]))
        if not old:
            raise ValueError(f"no reading {supersedes} on this KPI to correct")
        newer = conn.execute("SELECT id FROM kpi_readings WHERE supersedes=?", (supersedes,)).fetchone()
        if newer:
            raise ValueError(f"reading {supersedes} was already corrected by {newer[0]}; correct that one")
        if not (note or str(evidence or "").strip()):
            raise ValueError("say what the correction changes, in the note or the evidence")
    reading = {"id": H.new_id(), "kpi_id": row["id"], "ts": end, "value": float(value), "actor": actor,
               "source": source, "note": note, "created": now, "period_start": start, "period_end": end,
               "collected_at": collected, "evidence": str(evidence or "").strip()[:2000], "quality": quality,
               "definition_version": version, "supersedes": supersedes or None}
    conn.execute("INSERT INTO kpi_readings (id, kpi_id, ts, value, actor, source, note, created, period_start, "
                 "period_end, collected_at, evidence, quality, definition_version, supersedes) VALUES (:id, :kpi_id, "
                 ":ts, :value, :actor, :source, :note, :created, :period_start, :period_end, :collected_at, "
                 ":evidence, :quality, :definition_version, :supersedes)", reading)
    return reading


def set_link(conn, actor, goal_id, kpi_id, values):
    """Create or replace the link between a goal and a KPI, with its (already validated) target fields.
    Returns (link, old link or None)."""
    old = link(conn, goal_id, kpi_id)
    ts = H.now()
    if old:
        conn.execute("UPDATE goal_kpis SET kind=:kind, baseline=:baseline, baseline_at=:baseline_at, target=:target, "
                     "deadline=:deadline, min=:min, max=:max, updated=:ts, updated_by=:actor WHERE id=:id",
                     {**values, "ts": ts, "actor": actor, "id": old["id"]})
    else:
        conn.execute("INSERT INTO goal_kpis (id, goal_id, kpi_id, kind, baseline, baseline_at, target, deadline, min, max, "
                     "created, created_by, updated, updated_by) VALUES (:id, :goal_id, :kpi_id, :kind, :baseline, "
                     ":baseline_at, :target, :deadline, :min, :max, :ts, :actor, :ts, :actor)",
                     {**values, "id": H.new_id(), "goal_id": goal_id, "kpi_id": kpi_id, "ts": ts, "actor": actor})
    return link(conn, goal_id, kpi_id), old


def unlink(conn, goal_id, kpi_id):
    conn.execute("DELETE FROM goal_kpis WHERE goal_id=? AND kpi_id=?", (goal_id, kpi_id))


def fill_slugs(conn):
    """Give every KPI a slug (the folder name in the Goal Manager's repository)."""
    for row in conn.execute("SELECT id, name FROM kpis WHERE slug IS NULL OR slug='' ORDER BY created").fetchall():
        conn.execute("UPDATE kpis SET slug=? WHERE id=?", (_unique_slug(conn, row["name"]), row["id"]))
