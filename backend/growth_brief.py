# pyright: strict
"""One read per channel and per segment, and the alert that tells a channel's owner when it crosses a line (task #288).

A growth agent starts each run asking how its channel is doing against its target, what changed and whether its test
works. `channel` answers it in one read, in this order:

1. `kpi`: the KPI the channel moves (`growth_channels.input_kpi_id`) with its latest value and date, the target and colour of the
   goal it is linked to, and its last 12 readings. Growth keeps no target of its own: targets stay on goals.
2. `months`: the last 6 months of spend, new customers, CAC, kept at 30 days and LTV:CAC, each month with its quality.
3. `changed`: the newest month against the month before for each figure, and the dated events since the start of the
   month before (tests started and finished, stage and budget changes, crossings). Each figure that changed carries the
   events that bear on it (`beside`): a test or a stage change bears on every figure, a budget change on spend, CAC and
   LTV:CAC; a figure whose source was failing or stale says `data_gap`; a change nothing recorded lines up with says
   so (`unexplained`). Tico never states a cause: with monthly cohorts and no control, a cause would be a guess.
4. `tests`: open tests on the channel with their stage and start date, and tests finished in the last 90 days with kept
   or dropped and their `result:` tag. The reader sees only tasks it may see.
5. `sources`: each source behind the channel's figures, its health and last good update; `stale` when the source is
   failing or stale, or the newest month is more than 45 days old.

`segment` gives a segment's last 6 months from customer facts (backend/growth_computed.py, cut by segment): new
customers, kept at 30 and 90 days, LTV, the change against the month before, and "spend is not split by segment". A
cohort under the team's minimum shows counts only.

Alerts. The lines are ones the team already set: the channel's KPI turning red against its goal (`kpi`), its guardrail
KPI going over its max (`guardrail`), and its source failing (`source`). A KPI reading (backend/kpis.py add_reading)
and a source run (backend/growth_sync.py run) call `check`. Entering the bad state files one task for the channel's
owner, filed by the owner itself so it may close it, labelled `channel:<slug>` and `growth:alert`, in no pipeline;
staying bad comments on that task at most once a day while it is open; once it is closed or done the next crossing
files a new task. Leaving the bad state comments once and asks the owner to close the task: Tico never closes it. A
channel with no owner tells nobody, and its read says so. Every change of state emits `growth.channel.crossed` (ids
only) for routines.
"""

import json
import sqlite3
from collections.abc import Callable, Collection
from datetime import date, datetime, timedelta, timezone
from typing import Any

from . import growth as _growth
from . import growth_sync as GS
from . import hubdb as _hubdb
from . import kpis as _kpis
from . import marketing_tasks as MT
from .hubdb_core import TICO_ACTOR
from .store import Problem

G: Any = _growth
H: Any = _hubdb
K: Any = _kpis
Row = dict[str, Any]
Conn = sqlite3.Connection

MONTHS = 6
READINGS = 12
TESTS_DAYS = 90
STALE_DAYS = 45  # the newest month is stale this many days after it ended
FIGURES = ("spend", "new_customers", "cac", "kept_d30", "ltv_cac")
FIGURE_NAMES = {
    "spend": "Spend",
    "new_customers": "New customers",
    "cac": "CAC",
    "kept_d30": "Kept at 30 days",
    "ltv_cac": "LTV:CAC",
    "kept_d90": "Kept at 90 days",
    "ltv": "LTV",
}
# What a budget change bears on; a test or a stage change bears on every figure.
BUDGET_BEARS = ("spend", "cac", "ltv_cac")
# The cohort fields a figure is made from, for which source it came from.
MADE_FROM = {
    "spend": ("spend",),
    "new_customers": ("new_customers",),
    "cac": ("spend", "bot_cost", "new_customers"),
    "kept_d30": ("kept_d30",),
    "ltv_cac": ("spend", "bot_cost", "new_customers", "kept_d30", "kept_d90", "kept_d180", "revenue_month"),
}
UNEXPLAINED = "nothing recorded on this channel explains it"
DATA_GAP = "may be a data gap"
KINDS = ("kpi", "guardrail", "source")
BAD = {"kpi": "red", "guardrail": "over", "source": "failing"}
EVENT = "growth.channel.crossed"
ALERT_LABEL = "growth:alert"
WHO_MAY_LINK = "the bot that owns the channel, the owner or an admin"
SEGMENT_FIGURES = ("new_customers", "kept_d30", "kept_d90", "ltv")


# ----------------------------------------------------------------------------- small helpers
def _month_end(month: str) -> date:
    y, m = int(month[:4]), int(month[5:7])
    return date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)


def _month_before(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y - (m == 1)}-{(m - 2) % 12 + 1:02d}"


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _change(new: Any, old: Any) -> Row:
    """{value, before, diff, pct}: never a change from or to a figure not recorded."""
    out: Row = {"value": new, "before": old, "diff": None, "pct": None, "recorded": new is not None and old is not None}
    if new is not None and old is not None:
        out["diff"] = round(new - old, 2)
        out["pct"] = round((new - old) / abs(old) * 100, 1) if old else None
    return out


def _origins(month: Row) -> dict[str, Row]:
    found: dict[str, Row] = month.get("origins") or {}
    return found


def _link(c: Conn, kpi_id: str) -> Row | None:
    return H._one(
        c,
        "SELECT * FROM goal_kpis WHERE kpi_id=? ORDER BY target IS NULL AND min IS NULL AND max IS NULL, created_at "
        "LIMIT 1",
        (kpi_id,),
    )


# ----------------------------------------------------------------------------- the channel read
def kpi_block(c: Conn, row: Row, hidden: Collection[str] = ()) -> Row:
    """The channel's KPI against its goal's target, or what to do when there is none to show."""
    slug = row["slug"]
    if not row.get("input_kpi_id"):
        return {
            "linked": False,
            "text": "No KPI is linked to this channel.",
            "fix": f"tico growth channel update {slug} --input-kpi <id>",
            "who_may": WHO_MAY_LINK,
        }
    kpi = K.kpi(c, row["input_kpi_id"])
    if not kpi:
        return {
            "linked": False,
            "text": "Its KPI no longer exists.",
            "fix": f"tico growth channel update {slug} --input-kpi <id>",
            "who_may": WHO_MAY_LINK,
        }
    if kpi.get("owner_id") in hidden:
        return {"linked": True, "id": kpi["id"], "hidden": True, "text": "Its KPI belongs to a bot you may not read."}
    readings = K.readings(c, kpi["id"], effective=True)
    link = _link(c, kpi["id"])
    state = K.assess(kpi, link, readings)
    newest = K.latest(readings)
    target = K.target_label(kpi, link) if link else ""
    out = {
        "linked": True,
        "id": kpi["id"],
        "name": kpi["name"],
        "unit": kpi.get("unit") or "",
        "value": newest["value"] if newest else None,
        "at": (newest.get("period_end") or newest.get("collected_at")) if newest else None,
        "goal_id": link["goal_id"] if link else None,
        "target": target or None,
        "colour": state["status"] if target else None,
        "reason": state["reason"],
        "readings": [{"value": r["value"], "at": r["period_end"]} for r in readings if r.get("quality") != "partial"][
            -READINGS:
        ],
    }
    if not target:
        out["text"] = "no target: set on the goal by the Strategy Owner"
    return out


def _guardrail(c: Conn, row: Row, hidden: Collection[str] = ()) -> Row | None:
    if not (row.get("guardrail_kpi_id") and row.get("guardrail_max") is not None):
        return None
    kpi: Row = K.kpi(c, row["guardrail_kpi_id"]) or {}
    if kpi.get("owner_id") in hidden:
        return {"kpi_id": row["guardrail_kpi_id"], "hidden": True}
    newest = K.latest(K.readings(c, row["guardrail_kpi_id"], effective=True))
    value = newest["value"] if newest else None
    return {
        "kpi_id": row["guardrail_kpi_id"],
        "name": kpi.get("name"),
        "value": value,
        "max": row["guardrail_max"],
        "breached": value is not None and value > row["guardrail_max"],
    }


def _months(c: Conn, slug: str) -> list[Row]:
    conf = G.settings(c)
    rows: list[Row] = G.cohorts(c, channel_slug=slug, limit=MONTHS + 1)
    out: list[Row] = []
    for r in rows:
        econ = G.economics_row(r, conf)
        out.append(
            {
                "month": r["month"],
                **{k: econ.get(k) for k in FIGURES},
                "quality": econ.get("quality"),
                "origins": r.get("origins") or {},
            }
        )
    return out


def _source_rows(c: Conn, row: Row, months: list[Row]) -> list[Row]:
    """Each source behind the channel: its own source, and every source a figure of its months came from."""
    ids: list[str] = []
    if row.get("source_id"):
        ids.append(row["source_id"])
    for m in months:
        origins: dict[str, Row] = m.get("origins") or {}
        for origin in origins.values():
            if origin.get("source_id") and origin["source_id"] not in ids:
                ids.append(origin["source_id"])
    if not ids:
        return []
    found: dict[str, Row] = {
        r["id"]: r
        for r in H._rows(
            c.execute(f"SELECT {GS.listed(c)} FROM gtm_sources WHERE id IN ({','.join('?' * len(ids))})", ids)
        )
    }
    return [found[i] for i in ids if i in found]


def _sources(c: Conn, row: Row, months: list[Row]) -> Row:
    today = _today()
    newest = months[0]["month"] if months else None
    old = newest is not None and today > _month_end(newest) + timedelta(days=STALE_DAYS)
    out: list[Row] = []
    for src in _source_rows(c, row, months):
        state = GS.health(src)
        used = {f for m in months for f, o in _origins(m).items() if o.get("source_id") == src["id"]}
        fills = sorted(used | set(GS.fills(src)))
        out.append(
            {
                "id": src["id"],
                "name": src["system"],
                "truth_for": src["truth_for"],
                "fills": fills,
                "health": state["health"],
                "since": state["since"],
                "last_good": src.get("last_good_run_at"),
                "last_error": src.get("last_error"),
                "stale": state["health"] in ("failing", "stale"),
            }
        )
    return {"newest_month": newest, "newest_month_stale": old, "sources": out}


def _stale_fields(months: list[Row], sources: list[Row]) -> dict[str, Row]:
    """Cohort fields whose source is failing or stale in the newest month."""
    bad = {s["id"]: s for s in sources if s["stale"]}
    if not months or not bad:
        return {}
    out: dict[str, Row] = {}
    for field, origin in _origins(months[0]).items():
        if origin.get("source_id") in bad:
            out[field] = bad[origin["source_id"]]
    return out


def _channel_events(c: Conn, row: Row, since: str) -> list[Row]:
    """Stage and budget changes from the audit trail, newest first."""
    out: list[Row] = []
    for e in H._rows(
        c.execute(
            "SELECT created_at, actor, detail FROM events WHERE action='growth.channel.update' AND target=? "
            "AND created_at>=? ORDER BY created_at DESC LIMIT 100",
            (row["id"], since),
        )
    ):
        detail = json.loads(e["detail"] or "{}")
        for field, kind in (("stage", "stage"), ("budget_month", "budget")):
            if field in detail:
                out.append(
                    {
                        "kind": kind,
                        "at": e["created_at"],
                        "by": e["actor"],
                        "to": detail[field],
                        "text": f"{'stage' if kind == 'stage' else 'budget'} set to {detail[field]}",
                    }
                )
    return out


def _crossings(c: Conn, row: Row, since: str) -> list[Row]:
    return [
        {
            "kind": "crossing",
            "crossing": r["kind"],
            "state": r["state"],
            "at": r["at"],
            "by": r["actor"],
            "value": r["value"],
            "line": r["line"],
            "task_id": r["task_id"],
            "told": r["told"],
            "text": r["text"] + ("" if r["told"] != "none" else " (no owner: nobody was told)"),
        }
        for r in H._rows(
            c.execute(
                "SELECT * FROM growth_crossings WHERE channel_id=? AND at>=? ORDER BY at DESC LIMIT 100",
                (row["id"], since),
            )
        )
    ]


def _markers(c: Conn, auth: Any, who: Any, slug: str, since: str) -> list[Row]:
    from . import growth_numbers

    gn: Any = growth_numbers
    found: list[Row] = gn._markers(c, auth, who, since)  # pyright: ignore[reportPrivateUsage]
    return [m for m in found if slug in (m.get("channels") or [])]


def _test_events(markers: list[Row]) -> list[Row]:
    out: list[Row] = []
    for m in markers:
        if m["kind"] == "started":
            text = f'test "{m["title"]}" started'
        else:
            text = f'test "{m["title"]}" {m["outcome"]}' + (f", result:{m['result']}" if m.get("result") else "")
        out.append({"kind": "test", "at": m["at"], "task_id": m["task_id"], "text": text})
    return out


def _changed(c: Conn, row: Row, months: list[Row], sources: list[Row], markers: list[Row]) -> Row:
    if not months:
        return {"month": None, "before": None, "figures": [], "events": []}
    newest = months[0]["month"]
    before = _month_before(newest)
    empty: Row = {}
    prev: Row = next((m for m in months if m["month"] == before), empty)
    since = before + "-01"
    events = sorted(
        _test_events([m for m in markers if m["at"] >= since])
        + _channel_events(c, row, since)
        + _crossings(c, row, since),
        key=lambda e: e["at"],
        reverse=True,
    )
    stale = _stale_fields(months, sources)
    figures: list[Row] = []
    for f in FIGURES:
        ch = _change(months[0].get(f), prev.get(f))
        line = {"figure": f, "name": FIGURE_NAMES[f], **ch}
        if ch["recorded"] and ch["diff"]:
            beside = [
                e for e in events if e["kind"] in ("test", "stage") or (e["kind"] == "budget" and f in BUDGET_BEARS)
            ]
            gaps = sorted({stale[k]["name"] for k in MADE_FROM[f] if k in stale})
            line["beside"] = beside
            line["data_gap"] = [f"{name} is stale: {DATA_GAP}" for name in gaps]
            line["unexplained"] = not beside and not gaps
            if line["unexplained"]:
                line["note"] = UNEXPLAINED
        elif not ch["recorded"]:
            line["note"] = "not recorded"
        figures.append(line)
    return {"month": newest, "before": before, "figures": figures, "events": events}


def _tests(c: Conn, auth: Any, who: Any, slug: str, markers: list[Row]) -> Row:
    key = H._labels([f"channel:{slug}"])[0]
    rows = H._rows(
        c.execute(
            "SELECT t.id, t.title, t.stage_id, t.pipeline_id, t.created_at, p.preset FROM tasks t "
            "LEFT JOIN pipelines p ON p.id=t.pipeline_id WHERE t.status='open' "
            f"AND t.id IN (SELECT id FROM tasks WHERE {auth.task_sql(c, who)}) "
            "AND t.id IN (SELECT tt.task_id FROM task_tags tt JOIN tags tg ON tg.id=tt.tag_id WHERE tg.key=?) "
            "ORDER BY t.created_at DESC LIMIT 100",
            (key,),
        )
    )
    labels: dict[str, set[str]] = {}
    if rows:
        ids = [r["id"] for r in rows]
        for tid, k in c.execute(
            "SELECT tt.task_id, tg.key FROM task_tags tt JOIN tags tg ON tg.id=tt.tag_id "
            f"WHERE tt.task_id IN ({','.join('?' * len(ids))})",
            ids,
        ):
            labels.setdefault(tid, set()).add(k)
    started: dict[str, str] = {}
    for m in markers:
        if m["kind"] == "started":
            started.setdefault(m["task_id"], m["at"])  # newest first
    running: list[Row] = []
    mt: Any = MT
    for r in rows:
        stage: str | None
        if r["preset"] in (MT.EXPERIMENTS,):
            stage = mt.pipeline_stage(r["preset"], r["pipeline_id"], r["stage_id"])
        else:
            stage = next((MT.LABELS[k] for k in sorted(labels.get(r["id"], set())) if k in MT.LABELS), None)
        if not stage:
            continue
        running.append(
            {"task_id": r["id"], "title": r["title"], "stage": stage, "started": started.get(r["id"], r["created_at"])}
        )
    cutoff = (_today() - timedelta(days=TESTS_DAYS)).isoformat()
    finished = [
        {
            "task_id": m["task_id"],
            "title": m["title"],
            "outcome": m["outcome"],
            "result": m.get("result"),
            "at": m["at"],
        }
        for m in markers
        if m["kind"] == "finished" and m["at"] >= cutoff
    ]
    return {"open": running, "finished": finished}


def channel(c: Conn, auth: Any, who: Any, row: Row, hidden: Collection[str] = ()) -> Row:
    """The whole channel read (see the module's doc)."""
    months = _months(c, row["slug"])
    src = _sources(c, row, months)
    since = (_today() - timedelta(days=400)).isoformat()
    markers = _markers(c, auth, who, row["slug"], since)
    return {
        "kpi": kpi_block(c, row, hidden),
        "guardrail": _guardrail(c, row, hidden),
        "months": [{k: v for k, v in m.items() if k != "origins"} for m in months[:MONTHS]],
        "changed": _changed(c, row, months, src["sources"], markers),
        "tests": _tests(c, auth, who, row["slug"], markers),
        "sources": src,
        "alerts": open_alerts(c, row),
    }


# ----------------------------------------------------------------------------- the segment read
def segment(c: Conn, auth: Any, who: Any, label: str) -> Row:
    from . import growth_computed as GX

    found = GX.computed(c, auth, who, cut="segment", grain="month", segment=label)
    cohorts = sorted((r for r in found["cohorts"] if r.get("value") == label), key=lambda r: r["period"], reverse=True)
    if not cohorts:
        raise Problem("not_found", f"No customers in segment {label}", 404)

    def months_row(r: Row) -> Row:
        if r["too_few"]:
            return {"month": r["period"], "too_few": True, "new_customers": r["new_customers"]["value"]}
        return {
            "month": r["period"],
            "too_few": False,
            **{f: r[f]["value"] for f in SEGMENT_FIGURES},
            "status": {f: r[f]["status"] for f in SEGMENT_FIGURES},
        }

    months = [months_row(r) for r in cohorts[:MONTHS]]
    figures: list[Row] = []
    if months:
        empty: Row = {}
        prev: Row = next((m for m in months if m["month"] == _month_before(months[0]["month"])), empty)
        for f in SEGMENT_FIGURES if not months[0]["too_few"] else ("new_customers",):
            ch = _change(months[0].get(f), prev.get(f))
            figures.append(
                {"figure": f, "name": FIGURE_NAMES[f], **ch, **({} if ch["recorded"] else {"note": "not recorded"})}
            )
    return {
        "segment": label,
        "min_cohort": found["min_cohort"],
        "counts_only": all(m["too_few"] for m in months),
        "months": months,
        "changed": {"month": months[0]["month"] if months else None, "figures": figures},
        "spend": found["cac_note"] or "spend is not split by segment",
    }


# ----------------------------------------------------------------------------- reads counted
def count_read(c: Conn, actor: str, view: str) -> None:
    """One read of a channel or segment view by this caller and run, for the spec's first measure."""
    c.execute(
        "INSERT INTO growth_view_reads (day, actor, run_id, view, n) VALUES (?,?,?,?,1) "
        "ON CONFLICT(day, actor, run_id, view) DO UPDATE SET n=n+1",
        (H.now()[:10], str(actor), H.RUN.get() or "", view),
    )


def reads(c: Conn, days: int = 30) -> Row:
    since = (_today() - timedelta(days=days)).isoformat()
    rows = H._rows(
        c.execute(
            "SELECT actor, view, count(DISTINCT CASE WHEN run_id!='' THEN run_id END) AS runs, sum(n) AS reads "
            "FROM growth_view_reads WHERE day>=? GROUP BY actor, view ORDER BY actor, view",
            (since,),
        )
    )
    return {"since": since, "reads": rows}


# ----------------------------------------------------------------------------- alerts
def open_alerts(c: Conn, row: Row) -> list[Row]:
    """The channel's alert tasks still open, newest first."""
    return H._rows(
        c.execute(
            "SELECT DISTINCT x.kind, x.task_id, t.title, t.status FROM growth_crossings x JOIN tasks t ON t.id=x.task_id "
            "WHERE x.channel_id=? AND x.told='task' AND t.status='open' ORDER BY x.at DESC",
            (row["id"],),
        )
    )


def _open_task(c: Conn, channel_id: str, kind: str, owner: str) -> str | None:
    """The alert task for this channel and kind while it is open and still its owner's: a closed or done one never
    blocks a new alert, and after the channel changes owner the new owner gets a task of its own."""
    found = H._one(
        c,
        "SELECT x.task_id FROM growth_crossings x JOIN tasks t ON t.id=x.task_id "
        "WHERE x.channel_id=? AND x.kind=? AND x.told='task' AND t.status='open' AND t.owner_id=? "
        "ORDER BY x.at DESC LIMIT 1",
        (channel_id, kind, owner),
    )
    return found["task_id"] if found else None


def _told_today(c: Conn, channel_id: str, kind: str, task_id: str, over: bool) -> bool:
    """Whether the task already had today's note of this direction: one "over the line" (the task itself, or a comment)
    and one "back" a day, so a line crossed back and forth all day tells its owner at most twice."""
    bad = BAD[kind]
    return bool(
        H._one(
            c,
            "SELECT 1 FROM growth_crossings WHERE channel_id=? AND kind=? AND task_id=? AND told IN ('task','comment') "
            f"AND substr(at,1,10)=? AND (state=?) = {1 if over else 0} LIMIT 1",
            (channel_id, kind, task_id, H.now()[:10], bad),
        )
    )


def _crossed_today(c: Conn, channel_id: str, kind: str) -> bool:
    """A channel without an owner tells nobody; its event still goes out at most once a day per kind."""
    return bool(
        H._one(
            c,
            "SELECT 1 FROM growth_crossings WHERE channel_id=? AND kind=? AND substr(at,1,10)=? LIMIT 1",
            (channel_id, kind, H.now()[:10]),
        )
    )


def _state(c: Conn, row: Row, kind: str, source: Row | None = None) -> tuple[str, Any, str, str] | None:
    """(state, value, line, words) for one kind, or None when there is nothing to judge."""
    if kind == "kpi":
        if not row.get("input_kpi_id"):
            return None
        kpi = K.kpi(c, row["input_kpi_id"])
        if not kpi:
            return None
        link = _link(c, kpi["id"])
        readings = K.readings(c, kpi["id"], effective=True)
        if not link or not readings:
            return None
        st = K.assess(kpi, link, readings)
        target = K.target_label(kpi, link)
        if not target:
            return None
        return st["status"], st["value"], target, kpi["name"]
    if kind == "guardrail":
        g = _guardrail(c, row)
        if not g or g["value"] is None:
            return None
        return ("over" if g["breached"] else "under"), g["value"], f"max {K.number(g['max'])}", g["name"] or "guardrail"
    src = source or (
        H._one(
            c,
            "SELECT id, system, last_error, last_good_run_at, sync_kind FROM gtm_sources WHERE id=?",
            (row.get("source_id"),),
        )
        if row.get("source_id")
        else None
    )
    if not src or not src.get("sync_kind"):
        return None
    good = src.get("last_good_run_at")
    return ("failing" if src.get("last_error") else "ok"), None, f"last good {str(good or 'never')[:10]}", src["system"]


def _words(row: Row, kind: str, state: str, value: Any, line: str, name: str, unit: str = "") -> str:
    ch = row["name"]
    shown = K.amount(value, unit) if value is not None else ""
    if kind == "kpi":
        if state == "red":
            return f"{ch}: {name} went red: {shown} against {line.lstrip('→ ').strip()}"
        if state == "green":
            return f"{ch}: {name} is back to green: {shown} ({line})"
        return f"{ch}: {name} is {state}: {shown} ({line})"
    if kind == "guardrail":
        if state == "over":
            return f"{ch}: guardrail {name} went over its {line}: {shown}"
        return f"{ch}: guardrail {name} is back under its {line}: {shown}"
    if state == "failing":
        return f"{ch}: source {name} stopped syncing ({line})"
    return f"{ch}: source {name} syncs again"


def _record(
    c: Conn,
    row: Row,
    kind: str,
    state: str,
    value: Any,
    line: str,
    text: str,
    task_id: str | None,
    told: str,
    actor: str,
) -> str:
    cid = H.new_id()
    c.execute(
        "INSERT INTO growth_crossings (id, channel_id, kind, state, value, line, text, task_id, told, actor, at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (cid, row["id"], kind, state, value, line, text, task_id, told, actor, H.now()),
    )
    return cid


def _file(c: Conn, row: Row, text: str, slug: str) -> str:
    owner = row["owner_ref"]
    body = (
        f"{text}.\n\nThis is a Growth alert: the channel {row['name']} crossed a line the team set. "
        f"Read the channel with `tico growth channel show {slug}` (what changed, its tests, how fresh its sources are). "
        "Tico comments here at most once a day while it stays over the line, and once when it is back. "
        "Close this task when you have dealt with it; the next crossing after that files a new one."
    )
    made: Any = H.task_create(
        c,
        owner,
        text[:300],
        body,
        owner,
        deduplicate=False,
        labels=[f"channel:{slug}", ALERT_LABEL],
        lint=False,
    )
    found: Row | str = made
    return str(found["id"]) if isinstance(found, dict) else found


def _comment(c: Conn, task_id: str, text: str) -> None:
    H.task_comment(c, TICO_ACTOR, task_id, text)


def _hook(c: Conn, row: Row, kind: str, crossing_id: str, actor: str) -> None:
    kpi_id = row.get("input_kpi_id") if kind == "kpi" else row.get("guardrail_kpi_id") if kind == "guardrail" else None
    H.hook(
        c,
        EVENT,
        row["id"],
        lambda: {
            "channel_id": row["id"],
            "kind": kind,
            "kpi_id": kpi_id,
            "source_id": row.get("source_id") if kind == "source" else None,
            "crossing_id": crossing_id,
        },
        actor,
        revision=0,
    )


def check_channel(c: Conn, row: Row, kind: str, actor: str, source: Row | None = None) -> str | None:
    """Judge one channel for one kind and act on a crossing. Returns what it did: task, comment, none or None."""
    judged = _state(c, row, kind, source)
    if judged is None:
        return None
    state, value, line, name = judged
    last = H._one(c, "SELECT state FROM growth_alert_state WHERE channel_id=? AND kind=?", (row["id"], kind))
    before = last["state"] if last else None
    bad = BAD[kind]
    if kind == "kpi" and before == bad and state not in (bad, "green"):
        # Red to yellow, gray or no target is not back: only green ends a KPI alert, so the edge of a line is quiet.
        return None
    c.execute(
        "INSERT INTO growth_alert_state (channel_id, kind, state, at) VALUES (?,?,?,?) "
        "ON CONFLICT(channel_id, kind) DO UPDATE SET state=excluded.state, at=excluded.at",
        (row["id"], kind, state, H.now()),
    )
    if state != bad and before != bad:
        return None
    unit = ""
    if kind in ("kpi", "guardrail"):
        kpi: Row = K.kpi(c, row["input_kpi_id"] if kind == "kpi" else row["guardrail_kpi_id"]) or {}
        unit = str(kpi.get("unit") or "")
    text = _words(row, kind, state, value, line, name, unit)
    owner = row.get("owner_ref")
    if not owner:
        # Nobody to tell: the read shows the crossing; the event goes out once a day per kind.
        if before == state:
            return None
        quiet = _crossed_today(c, row["id"], kind)
        cid = _record(c, row, kind, state, value, line, text, None, "none", actor)
        if not quiet:
            _hook(c, row, kind, cid, actor)
        return None if quiet else "none"
    task_id = _open_task(c, row["id"], kind, owner)
    if state == bad and not task_id:
        # Over the line with no open task (first time, or the last one was closed): a new task, whatever the hour.
        cid = _record(c, row, kind, state, value, line, text, _file(c, row, text, row["slug"]), "task", actor)
        _hook(c, row, kind, cid, actor)
        return "task"
    if not task_id:
        return None  # back, but its task was closed already: nothing left to tell
    if _told_today(c, row["id"], kind, task_id, state == bad):
        # The spec's guard: at most one "over" note and one "back" note a day on an alert task, however often the
        # line is crossed; the crossing is still recorded, so the read shows it.
        if before != state:
            _record(c, row, kind, state, value, line, text, task_id, "none", actor)
        return None
    if state != bad:
        words = (
            f"{text}. Read the channel before you close it: it may have crossed again since. "
            "Close this task if nothing more is needed; Tico will not close it."
        )
    elif before == bad:
        words = f"Still over the line: {text}."
    else:
        words = text + "."
    _comment(c, task_id, words)
    cid = _record(c, row, kind, state, value, line, text, task_id, "comment", actor)
    if before != state:  # a crossing, not the daily "still over" note
        _hook(c, row, kind, cid, actor)
    return "comment"


def check_kpi(c: Conn, kpi_id: str, actor: str) -> None:
    """After a KPI reading: every live channel whose KPI or guardrail it is."""
    for row in H._rows(
        c.execute(
            "SELECT * FROM growth_channels WHERE retired_at IS NULL AND (input_kpi_id=? OR guardrail_kpi_id=?)",
            (kpi_id, kpi_id),
        )
    ):
        if row.get("input_kpi_id") == kpi_id:
            check_channel(c, row, "kpi", actor)
        if row.get("guardrail_kpi_id") == kpi_id:
            check_channel(c, row, "guardrail", actor)


def check_source(c: Conn, source_id: str, actor: str) -> None:
    """After a source run: every live channel whose source it is."""
    src = H._one(
        c, "SELECT id, system, last_error, last_good_run_at, sync_kind FROM gtm_sources WHERE id=?", (source_id,)
    )
    for row in H._rows(
        c.execute("SELECT * FROM growth_channels WHERE retired_at IS NULL AND source_id=?", (source_id,))
    ):
        check_channel(c, row, "source", actor, src)


def safely(fn: Callable[[Conn, str, str], None], c: Conn, subject: str, actor: str) -> None:
    """A check never undoes or refuses the write that caused it: a failure rolls back the check only, is logged and
    shows on Health (batch_work.isolated); the write stands."""
    from . import batch_work

    bw: Any = batch_work
    with bw.isolated(c, "growth_alert", subject):
        fn(c, subject, actor)
