# pyright: strict
"""The Growth line: one month of Growth read left to right, source to KPI, joined (docs/growth.md "The Growth line").

`line(c, month, hidden)` is what the Growth page draws above its cards and what `GET /api/v2/growth/numbers` returns as
`line`, so an agent quotes what a person sees:

- `sources`: each live source (name, kind, health, the date of its last good sync, what it fills on this line), then
  "Typed by hand" when any number on the line was typed, and "Upload in Growth" when one was computed from customers
  loaded as a CSV. A source fills a channel's number when that number's origin names it (growth_origins); nothing else
  stores which channels a source feeds.
- `channels`: each live channel in its sort order, then Unknown when it has numbers, then All channels. Each carries
  its month numbers (spend against its budget, bot cost, new customers, kept day 30), each with its value, where it came
  from (synced, typed, computed), its quality, when, and whether it is stale; its unit economics (CAC, LTV:CAC, payback),
  each with its formula, the numbers it is made from and one sentence that says how it was made with today's values; the
  input KPI it moves and the guardrail it is held to.
- `kpis`: the North Star, then each input KPI and guardrail a channel links to: value, target, which channels move it or
  are held to it, and whether a guardrail is breached and by how much.
- `headline`: the month in one sentence, with the values it is made of.

A number with no value is null with a `status` where there is a reason: `not_yet` (a retention window that has not come
due), `too_few` (a cohort under the minimum). Never 0. The maths is growth.py's (economics_row, economics_total): this
module explains it and changes none of it. None when there is no channel: Get started has the page.
"""

from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from typing import Any, cast

from . import growth as G
from . import hubdb as H
from . import kpis as K

Row = dict[str, Any]
# growth.py is not strictly typed yet: its reads are taken as the shapes this module names (Row, list[Row]).
_G: Any = G
_K: Any = K

TYPED = "typed"  # the line's source for numbers typed by hand
UPLOAD = "upload"  # the line's source for customers loaded as a CSV in Growth
ALL = "all"
UNKNOWN = cast(str, _G.UNKNOWN)
UPLOAD_NAME = cast(str, _G.UPLOAD_NAME)
NUMBERS = ("spend", "bot_cost", "new_customers", "kept_d30")
ECONOMICS = ("cac", "ltv_cac", "payback_months")
BAD = ("stale", "failing")
USES = ("trace", "source")
WORDS = {
    "spend": "spend",
    "bot_cost": "bot cost",
    "new_customers": "new customers",
    "kept_d30": "kept to day 30",
    "cac": "CAC",
    "ltv_cac": "LTV:CAC",
    "payback_months": "payback",
}
SPELLED = ("Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


# ----------------------------------------------------------------------------- words
def _num(v: float, digits: int = 1) -> str:
    """A count or ratio as people write it: 1,234 or 2.5 (no trailing .0)."""
    text = f"{round(float(v), digits):,.{digits}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def money(v: float | None) -> str:
    v = float(v or 0)
    return ("-$" if v < 0 else "$") + _num(abs(v), 2 if abs(v) < 10 else 0)


def _fmt(field: str, v: float | None) -> str | None:
    if v is None:
        return None
    if field in ("spend", "bot_cost", "cac"):
        return money(v)
    if field == "kept_d30":
        return f"{_num(v)}%"
    if field == "ltv_cac":
        return f"{_num(v)}x"
    if field == "payback_months":
        return f"{_num(v)} months"
    return _num(v, 0)


def kpi_value(v: float | None, unit: str | None) -> str | None:
    """A KPI's value in its own unit: money with its sign, a share with %, else the number then the unit."""
    if v is None:
        return None
    unit = (unit or "").strip()
    if unit in ("$", "usd", "USD"):
        return money(v)
    if unit == "%":
        return f"{_num(v)}%"
    return f"{_num(v)} {unit}".strip()


def day(at: object) -> str | None:
    """An ISO time or date as "11 Oct" (the line's one way to say when)."""
    if not at:
        return None
    d = datetime.fromisoformat(str(at)[:19].rstrip("Z")) if len(str(at)) > 10 else datetime.fromisoformat(str(at))
    return f"{d.day} {MONTHS[d.month - 1]}"


def counted(n: int, one: str, many: str) -> str:
    """A count that may open a sentence: spelled out up to ten ("One channel"), else written ("12 channels")."""
    word = SPELLED[n] if n <= 10 else str(n)
    return f"{word} {one if n == 1 else many}"


def _month_name(month: str) -> str:
    return MONTH_NAMES[int(month[5:7]) - 1]


def _prev_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _not_yet(month: str, today: date | None = None) -> bool:
    """A month's day-30 window has not come due while its last customers are under 30 days old."""
    y, m = int(month[:4]), int(month[5:7])
    last = (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)
    return (today or datetime.now(timezone.utc).date()) < last + timedelta(days=30)


# ----------------------------------------------------------------------------- the stations
def _sources(c: Any) -> dict[str, Row]:
    """{id: the line's view of a live source}, in growth.sources' order."""
    out: dict[str, Row] = {}
    listed = cast(list[Row], _G.sources(c)["sources"])
    for s in listed:
        if s.get("retired_at"):
            continue
        bad = s.get("health") in BAD
        out[s["id"]] = {
            "id": s["id"],
            "name": s.get("name") or s.get("system"),
            "truth_for": s.get("truth_for"),
            "kind": s.get("kind"),
            "health": s.get("health"),
            # One date for every "since" on the line: the last good sync (or, never synced, when it began failing).
            "since": (s.get("last_good_run") or s.get("health_since")) if bad else None,
            "last_good": s.get("last_good_run"),
            "error": s.get("last_error") if s.get("health") == "failing" else None,
            "fills": [],
        }
    return out


def _cell(row: Row, field: str, sources: dict[str, Row], month: str) -> Row:
    """One month number of a channel: its value and where it came from."""
    value = row.get(field)
    origins: Row = row.get("origins") or {}
    origin: Row = origins.get(field) or {}
    kind = origin.get("origin") or (TYPED if value is not None else None)
    refs: list[str] = []
    names: list[str] = []
    at = origin.get("at")
    if kind == "synced" and origin.get("source_id"):
        refs.append(origin["source_id"])
    elif kind == "computed":
        computed: list[Row] = origin.get("sources") or []
        for s in computed:
            refs.append(s.get("source_id") or UPLOAD)
    elif kind == TYPED:
        refs.append(TYPED)
    for ref in refs:
        names.append(
            "Typed by hand"
            if ref == TYPED
            else UPLOAD_NAME
            if ref == UPLOAD
            else (sources.get(ref) or {}).get("name") or "a retired source"
        )
    stale = any((sources.get(r) or {}).get("health") in BAD for r in refs)
    statuses: Row = row.get("status") or {}
    status = statuses.get(field)
    if value is None and status is None and field == "kept_d30" and _not_yet(month):
        status = "not_yet"
    return {
        "value": value,
        "says": _fmt(field, value),
        "origin": kind if value is not None else None,
        "quality": (origin.get("quality") or row.get("quality") or "actual") if value is not None else None,
        "sources": refs if value is not None else [],
        "source_names": names if value is not None else [],
        "synced_at": at if kind in ("synced", "computed") and value is not None else None,
        "typed_at": at if kind == TYPED and value is not None else None,
        "stale": stale and value is not None,
        "status": status,
        "computed_from": origin.get("computed_from") if kind == "computed" else None,
        "conflict": bool(origin.get("conflict") or origin.get("differs")),
    }


def _from(cell: Row, word: str) -> str:
    """A number as it enters a sentence: its value, its name and where it came from."""
    where = ", ".join(cell["source_names"]) or "no source"
    when = ""
    if cell["origin"] == "synced" and cell["synced_at"]:
        when = f", synced {day(cell['synced_at'])}"
    elif cell["origin"] == "computed":
        when = f", computed from {_num(cell['computed_from'] or 0, 0)} customers"
    stale = ", stale" if cell["stale"] else ""
    return f"{cell['says']} {word} ({where}{when}{stale})"


def _economics(row: Row, numbers: dict[str, Row], guard: Row | None) -> dict[str, Row]:
    """CAC, LTV:CAC and payback for one row, each with how it was made."""
    new, kept = numbers["new_customers"], numbers["kept_d30"]
    inputs_cac = [k for k in ("spend", "bot_cost") if numbers[k]["value"] is not None]
    cac = row.get("cac")
    cac_said = None
    if cac is not None:
        parts = " + ".join(_from(numbers[k], WORDS[k]) for k in inputs_cac)
        cac_said = f"{parts} ÷ {_from(new, 'new customers')} = {money(cac)} CAC"
    ltv, ltv_cac, payback = row.get("ltv"), row.get("ltv_cac"), row.get("payback_months")
    months = row.get("expected_months")
    ltv_said = payback_said = None
    if ltv_cac is not None:
        ltv_said = (
            f"LTV {money(ltv)} ({_num(months or 0)} expected months from {_from(kept, 'kept to day 30')}) "
            f"÷ CAC {money(cac)} = {_num(ltv_cac)}x"
        )
    if payback is not None:
        per_month = cac / payback if payback else None
        payback_said = f"CAC {money(cac)} ÷ {money(per_month)} profit per customer per month = {_num(payback)} months"
    not_yet = "not_yet" if kept["status"] == "not_yet" else None
    stale_cac = any(numbers[k]["stale"] for k in (*inputs_cac, "new_customers"))
    out: dict[str, Row] = {
        "cac": {
            "value": cac,
            "says": _fmt("cac", cac),
            "formula": "(spend + bot cost) ÷ new customers",
            "from": [*inputs_cac, "new_customers"],
            "trace": cac_said,
            "stale": stale_cac and cac is not None,
            "status": None,
        },
        "ltv_cac": {
            "value": ltv_cac,
            "says": _fmt("ltv_cac", ltv_cac),
            "formula": "LTV ÷ CAC; LTV = profit per customer per month × expected months from retention",
            "from": ["kept_d30", "new_customers", *inputs_cac],
            "ltv": ltv,
            "expected_months": months,
            "trace": ltv_said,
            "stale": (stale_cac or kept["stale"]) and ltv_cac is not None,
            "status": not_yet if ltv_cac is None else None,
        },
        "payback_months": {
            "value": payback,
            "says": _fmt("payback_months", payback),
            "formula": "CAC ÷ profit per customer per month",
            "from": [*inputs_cac, "new_customers"],
            "trace": payback_said,
            "stale": stale_cac and payback is not None,
            "status": None,
        },
    }
    if guard:
        held = f"held to {guard['kpi_name']} at most {kpi_value(guard['max'], guard['unit'])}"
        if guard["over"] is not None:
            held += f": {kpi_value(guard['value'], guard['unit'])}, {kpi_value(guard['over'], guard['unit'])} over guardrail"
        if out["cac"]["trace"]:
            out["cac"]["trace"] += f"; {held}"
    return out


def _guard(g: Row | None, units: dict[str, str]) -> Row | None:
    if not g:
        return None
    over = round(g["value"] - g["max"], 2) if g["breached"] else None
    unit = units.get(g["kpi_id"], "")
    return {
        "kpi_id": g["kpi_id"],
        "kpi_name": g["kpi_name"],
        "value": g["value"],
        "max": g["max"],
        "unit": unit,
        "breached": g["breached"],
        "over": over,
        "says": f"{kpi_value(over, unit)} over guardrail" if over is not None else None,
    }


def _channel(
    slug: str,
    name: str,
    ch: Row | None,
    row: Row,
    sources: dict[str, Row],
    month: str,
    guard: Row | None,
    kpis_by_id: dict[str, Row],
) -> Row:
    numbers = {k: _cell(row, k, sources, month) for k in NUMBERS}
    budget = ch.get("budget_month") if ch else None
    spend = numbers["spend"]
    spend["budget"] = budget
    over = round(spend["value"] - budget, 2) if spend["value"] is not None and budget is not None else None
    spend["over_budget"] = over if over is not None and over > 0 else None
    spend["budget_says"] = f"{money(over)} over budget" if spend["over_budget"] else None
    input_kpi = (ch or {}).get("input_kpi_id")
    if input_kpi and input_kpi not in kpis_by_id:
        input_kpi = None  # a KPI the reader may not see, or archived: not linked on their line
    refs: list[str] = list(dict.fromkeys(r for cell in numbers.values() for r in cast(list[str], cell["sources"])))
    return {
        "slug": slug,
        "name": name,
        "owner_ref": (ch or {}).get("owner_ref"),
        "track": (ch or {}).get("track"),
        "stage": (ch or {}).get("stage"),
        "quality": row.get("quality"),
        "sources": refs,
        "numbers": numbers,
        "economics": _economics(row, numbers, guard),
        "input_kpi": input_kpi,
        "guardrail": guard,
    }


def _headline(month: str, total: Row, prev: Row | None, channels: list[Row], sources: list[Row], conf: Row) -> Row:
    """The month in one sentence, from the All channels row, the guardrails breached and the stale sources."""
    new, cac, payback = total["new_customers"], total["cac"], total["payback_months"]
    change = None
    # A month still in progress is half a month: no change against a whole one.
    running = month >= datetime.now(timezone.utc).strftime("%Y-%m")
    if new is not None and prev is not None and prev.get("new_customers") and not running:
        change = round((new - prev["new_customers"]) / prev["new_customers"] * 100)
    over = sorted({ch["slug"] for ch in channels if ch.get("guardrail") and ch["guardrail"]["over"] is not None})
    bad = [s for s in sources if s["health"] in BAD]
    stale_numbers = sum(
        1
        for ch in channels
        if ch["slug"] != ALL
        for part in ("numbers", "economics")
        for cell in ch[part].values()
        if cell["stale"]
    )
    since = min((s["since"] for s in bad if s["since"]), default=None)
    said: list[str] = []
    if new is not None:
        unit = conf.get("unit") or "customers"
        first = f"{_month_name(month)} brought {_num(new, 0)} new {unit}"
        if change is not None:
            first += ", flat" if change == 0 else f", {'up' if change > 0 else 'down'} {abs(change)}%"
        said.append(first + ".")
    if cac is not None:
        said.append(
            f"Each cost {money(cac)} to win"
            + (f" and pays back in {_num(payback)} months." if payback is not None else ".")
        )
    if over:
        said.append(
            f"{counted(len(over), 'channel is', 'channels are')} over {'its guardrail' if len(over) == 1 else 'their guardrails'}."
        )
    if bad:
        has = "has" if len(bad) == 1 else "have"
        if since:
            tail = f", so {_num(stale_numbers, 0)} numbers below are from then." if stale_numbers else "."
            said.append(f"{counted(len(bad), 'source', 'sources')} {has} not synced since {day(since)}{tail}")
        else:
            said.append(f"{counted(len(bad), 'source', 'sources')} {has} never synced.")
    return {
        "month": month,
        "new_customers": new,
        "change_pct": change,
        "cac": cac,
        "payback_months": payback,
        "over_guardrail": over,
        "stale_sources": [s["id"] for s in bad],
        "stale_since": since,
        "stale_numbers": stale_numbers,
        "sentence": " ".join(said),
    }


def _as_of(c: Any, tree: list[Row], month: str) -> list[Row]:
    """Each KPI as of the end of `month`: its newest reading up to then (the Funnel's kpi_upto, from the readings), so
    an earlier month's line never shows today's value or today's guardrail breach."""
    readings = cast(dict[str, list[Row]], _K.effective_many(c, [k["id"] for k in tree]))
    out: list[Row] = []
    for k in tree:
        upto = [
            r for r in readings.get(k["id"], []) if str(r.get("period_end") or r.get("collected_at") or "")[:7] <= month
        ]
        newest = cast(Row | None, _K.latest(upto))
        out.append({**k, "latest": newest["value"] if newest else None})
    return out


def _held_as_of(g: Row, values: dict[str, Any]) -> Row:
    """A guardrail with its KPI's value as of the line's month (growth.guardrails reads the newest)."""
    value = values.get(g["kpi_id"])
    return {**g, "value": value, "breached": value is not None and value > g["max"]}


def line(c: Any, month: str | None = None, hidden: Iterable[str] = ()) -> Row | None:
    """The Growth line for `month` (YYYY-MM; default the newest with numbers, else this month), as the reader may see it."""
    live = [ch for ch in cast(list[Row], _G.channels(c)) if not ch.get("retired_at")]
    if not live:
        return None
    months = cast(list[str], _G.cohort_months(c))
    month = month or (months[0] if months else datetime.now(timezone.utc).strftime("%Y-%m"))
    conf = cast(Row, _G.settings(c))
    econ = cast(Row, _G.economics(c, month, hidden))
    prev_month = _prev_month(month)
    prev = cast(Row, _G.economics(c, prev_month, hidden)["total"]) if prev_month in months else None
    tree = _as_of(c, cast(list[Row], _G.kpis(c, hidden)["kpis"]), month)
    kpis_by_id = {k["id"]: k for k in tree}
    units: dict[str, str] = {k["id"]: k["unit"] for k in tree}
    values: dict[str, Any] = {k["id"]: k["latest"] for k in tree}
    guards: dict[str, Row | None] = {
        g["channel"]: _guard(_held_as_of(g, values), units) for g in cast(list[Row], econ["guardrails"])
    }
    rows: dict[str, Row] = {r["channel"]: r for r in cast(list[Row], econ["rows"])}
    sources = _sources(c)
    blank: Row = {"origins": {}, "status": {}}
    channels: list[Row] = []
    for ch in live:
        channels.append(
            _channel(
                ch["slug"],
                ch["name"],
                ch,
                rows.get(ch["slug"]) or blank,
                sources,
                month,
                guards.get(ch["slug"]),
                kpis_by_id,
            )
        )
    unknown = rows.get(UNKNOWN)
    if unknown and unknown.get("new_customers") is not None:
        channels.append(_channel(UNKNOWN, "Unknown", None, unknown, sources, month, None, kpis_by_id))
    total = cast(Row, econ["total"])
    every = _channel(ALL, "All channels", None, {**total, "origins": {}}, sources, month, None, kpis_by_id)
    for field in NUMBERS:  # every channel together: from the channels above, so no source of its own
        every["numbers"][field].update(origin=None, sources=[], source_names=[], synced_at=None, typed_at=None)
    every["sources"] = []
    if total.get("cac") is not None:
        from_unknown = (unknown or {}).get("new_customers") or 0
        every["economics"]["cac"]["trace"] = (
            f"{money((total.get('spend') or 0) + (total.get('bot_cost') or 0))} spend and bot cost from named channels "
            f"only ÷ all {_num(total['new_customers'], 0)} new customers, {_num(from_unknown, 0)} of them from Unknown "
            f"= {money(total['cac'])} CAC"
        )
    channels.append(every)
    # What each source fills on this line, and the two sources that are not systems: typed, and a CSV upload.
    extra: dict[str, Row] = {}
    for ch in channels:
        if ch["slug"] == ALL:
            continue
        for field, cell in ch["numbers"].items():
            for ref in cell["sources"]:
                if ref in (TYPED, UPLOAD) and ref not in extra:
                    extra[ref] = {
                        "id": ref,
                        "name": "Typed by hand" if ref == TYPED else UPLOAD_NAME,
                        "truth_for": None,
                        "kind": ref,
                        "health": None,
                        "since": None,
                        "last_good": None,
                        "error": None,
                        "fills": [],
                    }
                target = sources.get(ref) or extra.get(ref)
                if target is not None:
                    target["fills"].append({"channel": ch["slug"], "number": field})
    if TYPED not in extra and UPLOAD not in extra and not any(s["fills"] for s in sources.values()):
        # Channels and nothing filled yet: every number will be typed until a source fills it.
        extra[TYPED] = {
            "id": TYPED,
            "name": "Typed by hand",
            "truth_for": None,
            "kind": TYPED,
            "health": None,
            "since": None,
            "last_good": None,
            "error": None,
            "fills": [],
        }
    listed = [*sources.values(), *(extra[k] for k in (TYPED, UPLOAD) if k in extra)]
    # Station 5: the North Star, then each input KPI and guardrail a channel links to.
    moved: dict[str, list[str]] = {}
    held: dict[str, list[str]] = {}
    for ch in channels:
        if ch["input_kpi"]:
            moved.setdefault(ch["input_kpi"], []).append(ch["slug"])
        if ch["guardrail"]:
            held.setdefault(ch["guardrail"]["kpi_id"], []).append(ch["slug"])
    kpis: list[Row] = []
    for k in tree:
        role = k.get("growth_role")
        if role != "north_star" and k["id"] not in moved and k["id"] not in held:
            continue
        breaches = [g for g in guards.values() if g and g["kpi_id"] == k["id"] and g["over"] is not None]
        kpis.append(
            {
                "id": k["id"],
                "name": k["name"],
                "unit": k["unit"],
                "role": role,
                "value": k["latest"],
                "says": kpi_value(k["latest"], k["unit"]),
                "target": k["target"],
                "moved_by": moved.get(k["id"], []),
                "held_by": held.get(k["id"], []),
                "breached": bool(breaches),
                "over": max((g["over"] for g in breaches), default=None),
            }
        )
    kpis.sort(key=lambda k: (k["role"] != "north_star", k["role"] != "input", k["name"].lower()))
    return {
        "month": month,
        "sources": listed,
        "channels": channels,
        "kpis": kpis,
        "headline": _headline(month, total, prev, channels, listed, conf),
    }


def used(c: Any, actor: str, use: str, at: str | None = None) -> None:
    """A person or bot opened a number's trace or selected a source on the line: counted once per day each."""
    at = at or H.now()
    c.execute("INSERT OR IGNORE INTO growth_line_uses (day, actor, use) VALUES (?,?,?)", (at[:10], actor, use))
