# pyright: strict
"""Calendar occurrences that come from tables Tico already keeps: bot runs, scheduled automations and due dates
(docs/dev/calendar-contract.md, `backend/calendar_derived.py`; spec section 9).

Read-only. Nothing here writes a row, and none of it is stored as a calendar event. Every occurrence comes from
`_occurrence`, so each one has every key the contract lists. Times are UTC with a Z, windows are half-open [start, end).
`visibility` says whether the source is private (a private task); the routes hide such a title from viewers who may
not read it, as they do for Tico events.
"""

import json
import re
import sqlite3
import statistics
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime, time, timedelta, timezone
from typing import NamedTuple, cast
from zoneinfo import ZoneInfo

from croniter import CroniterError, croniter

from . import bot_hours, scheduler
from .store import H

# The hub's helpers below are untyped, so pyright sees them as Unknown. Each cast gives the helper the signature the
# calls here rely on; a cast changes only the static type, so each name is the helper itself, which a test checks.
_stamp = cast("Callable[[datetime], str]", bot_hours.stamp)
_next_due = cast("Callable[[str, datetime, str], datetime]", scheduler.next_due)
_bot = cast("Callable[[sqlite3.Connection, str], dict[str, object] | None]", H.bot)
_resolve_bot = cast("Callable[[sqlite3.Connection, str], dict[str, object] | None]", H.resolve_bot)
_person_name = cast("Callable[[sqlite3.Connection, str], object]", H.person_name)

ZONE = bot_hours.ZONE  # a day in a month count is a company day, as bot_hours' weeks are
AUTOMATION = "automation:"  # a run's trigger_source when an automation's step or wake started it
GROUP_MINUTES = 10  # a run shorter than this may join its neighbours in one block (group_runs)
MIN_LENGTH = timedelta(minutes=15)  # the shortest planned length of a scheduled run
MEDIAN_OF = 10  # the finished runs whose median sets a scheduled run's length
HOURLY = timedelta(hours=1)
GAP_LOOK = 100  # firings read ahead to tell whether a schedule fires more often than hourly
FIRING_BUDGET = 40_000  # firings read per schedule per call: a cron firing every minute over 62 days is cut here
SHORT_TITLE = 80
# The attempts that have a run record: bot_hours.Counter.count reads runs the same way.
RUN_FILTER = "(a.job_id IS NULL OR a.trigger IS NOT NULL)"
RUN_SELECT = (
    "SELECT a.id, a.bot_id, a.created_at, a.started_at, a.finished_at, a.settled_at, a.thread_id, a.task_id, "
    "a.trigger_source, a.summary, m.refs AS message_refs, t.title AS task_title, t.private AS task_private "
    "FROM attempts a LEFT JOIN messages m ON m.id = a.message_id LEFT JOIN tasks t ON t.id = a.task_id "
)
NO_LINKS: dict[str, object] = {"thread": None, "meeting": None, "task": None, "automation": None, "attempt": None}
DATE_PREFIX = re.compile(r"\d{4}-\d{2}-\d{2}")


class Run(NamedTuple):
    id: str
    bot_id: str
    start: datetime
    end: datetime
    thread_id: str | None
    task_id: str | None
    task_title: str | None
    task_private: bool
    automation_id: str | None
    summary: str | None


def _utc(at: datetime) -> datetime:
    return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)


def _iso(at: datetime) -> str:
    return _utc(at).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: object) -> datetime | None:
    """A stored timestamp as an aware UTC datetime; None when it is empty or cannot be read."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _day(value: object) -> date | None:
    """The calendar date a stored due time or deadline names (its first ten characters), or None."""
    if not isinstance(value, str):
        return None
    match = DATE_PREFIX.match(value)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(0))
    except ValueError:
        return None


def _day_overlaps(day: date, lo: datetime, hi: datetime) -> bool:
    start = datetime.combine(day, time(), tzinfo=timezone.utc)
    return start < hi and start + timedelta(days=1) > lo


def _ids(values: Iterable[str]) -> list[str]:
    return sorted({str(value).removeprefix("bot:") for value in values if value})


def _marks(values: Sequence[object]) -> str:
    return ",".join("?" * len(values))


def _rows(c: sqlite3.Connection, sql: str, args: Sequence[object] = ()) -> list[dict[str, object]]:
    return [dict(row) for row in c.execute(sql, tuple(args)).fetchall()]


def _short(text: str | None) -> str:
    return " ".join((text or "").split())[:SHORT_TITLE]


def _dict_of(value: object) -> dict[str, object] | None:
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def _json_dict(text: object) -> dict[str, object]:
    """A stored JSON object (a message's refs, an automation's flow) as a dict; empty when it is missing or unreadable."""
    if not isinstance(text, str) or not text:
        return {}
    try:
        value: object = json.loads(text)
    except ValueError:
        return {}
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _automation_of(source: object, refs_text: object) -> str | None:
    """The automation that started a run: named by its trigger source, or by the message that woke it."""
    if isinstance(source, str) and source.startswith(AUTOMATION):
        return source[len(AUTOMATION) :] or None
    found = _json_dict(refs_text).get("automation")
    return found if isinstance(found, str) and found else None


def _bot_name(c: sqlite3.Connection, bot_id: str) -> str:
    """A bot's display name, the way the rest of the hub reads it; its slug, then its id, when it has none."""
    row = _bot(c, bot_id)
    if not row:
        return bot_id
    for key in ("display_name", "slug"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return bot_id


def _name_of(c: sqlite3.Connection, actor: str) -> str:
    kind, _, ident = actor.partition(":")
    return str(_person_name(c, ident)) if kind == "human" else _bot_name(c, ident)


def _calendar(kind: str, ident: str, name: str) -> dict[str, object]:
    return {"ref": f"{kind}:{ident}", "type": kind, "id": ident, "name": name}


def _occurrence(
    occurrence_id: str,
    kind: str,
    calendar: dict[str, object],
    *,
    start: str,
    end: str,
    title: str,
    all_day: bool = False,
    time_zone: str | None = None,
    planned: bool = False,
    recurring: bool = False,
    visibility: str = "public",
    links: dict[str, object] | None = None,
) -> dict[str, object]:
    """One derived occurrence in the contract's shape. Viewer-specific keys (`response`, `can`) are left for the route."""
    return {
        "occurrence_id": occurrence_id,
        "event_id": None,
        "calendar": calendar,
        "home": "derived",
        "kind": kind,
        "title": title,
        "start": start,
        "end": end,
        "all_day": all_day,
        "time_zone": time_zone,
        "attendees": [],
        "response": None,
        "busy_only": False,
        "recurring": recurring,
        "status": "confirmed",
        "meeting_url": None,
        "open_url": None,
        "links": {**NO_LINKS, **(links or {})},
        "can": {"join": False, "edit": False},
        "planned": planned,
        "visibility": visibility,
        "sequence": None,
    }


def _run_rows(c: sqlite3.Connection, bot_ids: list[str], lo: datetime, hi: datetime, now: datetime) -> list[Run]:
    """The runs of `bot_ids` that overlap [lo, hi). A run runs from its start to its finish, or to `now` while it has
    none. The two queries are the ones bot_hours reads: runs that closed after `lo`, and runs still open."""
    marks = _marks(bot_ids)
    closed = _rows(
        c,
        RUN_SELECT + f"WHERE a.bot_id IN ({marks}) AND {RUN_FILTER} AND a.settled_at > ? AND a.created_at < ?",
        (*bot_ids, _stamp(lo), _stamp(hi)),
    )
    still_open = _rows(
        c,
        RUN_SELECT + f"WHERE a.bot_id IN ({marks}) AND {RUN_FILTER} AND a.settled_at IS NULL AND a.created_at < ?",
        (*bot_ids, _stamp(hi)),
    )
    out: list[Run] = []
    for row in closed + still_open:
        start = _parse(row["started_at"]) or _parse(row["created_at"])
        if start is None:
            continue
        end = max(_parse(row["finished_at"]) or _parse(row["settled_at"]) or now, start)
        if start >= hi or end <= lo:
            continue
        title = " ".join(str(row["task_title"] or "").split()) or None
        out.append(
            Run(
                id=str(row["id"]),
                bot_id=str(row["bot_id"]),
                start=start,
                end=end,
                thread_id=str(row["thread_id"]) if row["thread_id"] else None,
                task_id=str(row["task_id"]) if row["task_id"] else None,
                task_title=title,
                task_private=row["task_private"] == 1,
                automation_id=_automation_of(row["trigger_source"], row["message_refs"]),
                summary=str(row["summary"]) if row["summary"] else None,
            )
        )
    return out


def _automation_names(c: sqlite3.Connection, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    ordered = sorted(ids)
    rows = _rows(c, f"SELECT id, name FROM automations WHERE id IN ({_marks(ordered)})", ordered)
    return {str(row["id"]): str(row["name"]) for row in rows if row.get("name")}


def runs(
    c: sqlite3.Connection, bot_ids: list[str], start_utc: datetime, end_utc: datetime, now: datetime
) -> list[dict[str, object]]:
    """One occurrence per run of `bot_ids` overlapping the window. The title is the automation's name when a schedule or
    a wake of an automation started the run, else the task's title, else the run's summary, else "Run"."""
    ids = _ids(bot_ids)
    lo, hi = _utc(start_utc), _utc(end_utc)
    if not ids or lo >= hi:
        return []
    found = sorted(_run_rows(c, ids, lo, hi, _utc(now)), key=lambda run: (run.start, run.id))
    names = _automation_names(c, {run.automation_id for run in found if run.automation_id})
    bots = {bot: _bot_name(c, bot) for bot in {run.bot_id for run in found}}
    out: list[dict[str, object]] = []
    for run in found:
        automation = names.get(run.automation_id) if run.automation_id else None
        out.append(
            _occurrence(
                f"run:{run.id}",
                "run",
                _calendar("bot", run.bot_id, bots[run.bot_id]),
                start=_iso(run.start),
                end=_iso(run.end),
                title=automation or run.task_title or _short(run.summary) or "Run",
                visibility="private" if run.task_private else "public",
                links={
                    "thread": run.thread_id,
                    "task": run.task_id,
                    "automation": run.automation_id,
                    "attempt": run.id,
                },
            )
        )
    return out


def _runs_as(c: sqlite3.Connection, flow: dict[str, object], owner_id: str) -> str | None:
    """The bot a scheduled automation runs as: its owner when that is a bot, else the bot of its first bot step."""
    owner = flow.get("owner")
    if not isinstance(owner, str) or not owner:
        owner = owner_id
    if owner.startswith("bot:"):
        return owner[len("bot:") :]
    steps = flow.get("steps")
    for raw in cast("list[object]", steps) if isinstance(steps, list) else []:
        step = _dict_of(raw)
        if step is None or step.get("type") != "bot_turn":
            continue
        named = step.get("bot")
        if not isinstance(named, str) or not named:
            return None
        found = _resolve_bot(c, named)
        return str(found["id"]) if found else None
    return None


def _planned_length(c: sqlite3.Connection, automation_id: str) -> timedelta:
    """The median of the last finished runs' lengths, at least MIN_LENGTH; MIN_LENGTH when it has none."""
    spans: list[float] = []
    for row in _rows(
        c,
        "SELECT started_at, finished_at FROM automation_runs WHERE automation_id=? AND finished_at IS NOT NULL "
        "AND status <> 'skipped' ORDER BY started_at DESC LIMIT ?",
        (automation_id, MEDIAN_OF),
    ):
        start, end = _parse(row["started_at"]), _parse(row["finished_at"])
        if start is not None and end is not None and end >= start:
            spans.append((end - start).total_seconds())
    if not spans:
        return MIN_LENGTH
    return max(MIN_LENGTH, timedelta(seconds=statistics.median(spans)))


def _cron(expr: str, zone: str, after: datetime) -> croniter:
    """Firings of `expr` in `zone` after `after`, built with the arguments scheduler.next_due passes. A count reads
    many firings, and rebuilding the schedule for each one costs far more."""
    return croniter(expr, after.astimezone(ZoneInfo(zone)), day_or=False)


def _more_than_hourly(expr: str, zone: str, lo: datetime) -> bool:
    it = _cron(expr, zone, lo)
    previous = it.get_next(datetime)
    for _ in range(GAP_LOOK):
        following = it.get_next(datetime)
        if following.astimezone(timezone.utc) - previous.astimezone(timezone.utc) < HOURLY:
            return True
        previous = following
    return False


def _firings(expr: str, zone: str, lo: datetime, hi: datetime) -> list[datetime]:
    """Each firing in [lo, hi), in UTC, through scheduler.next_due."""
    out: list[datetime] = []
    at = lo - timedelta(seconds=1)  # next_due returns firings strictly after `at`; this keeps one at `lo`
    while len(out) < FIRING_BUDGET:
        at = _next_due(expr, at, zone)
        if at.astimezone(timezone.utc) >= hi:
            break
        out.append(at.astimezone(timezone.utc))
    return out


def scheduled(
    c: sqlite3.Connection, bot_ids: list[str], start_utc: datetime, end_utc: datetime, now: datetime
) -> list[dict[str, object]]:
    """Planned runs of enabled schedules from the later of `now` and the window start. A bot that is not active is
    skipped, as the scheduler skips it. A schedule firing more often than hourly becomes one all-day pill per day, titled
    "<name> ×N". Otherwise each firing is a timed occurrence as long as the median of its last finished runs."""
    ids = set(_ids(bot_ids))
    lo = max(_utc(start_utc), _utc(now))
    hi = _utc(end_utc)
    if not ids or lo >= hi:
        return []
    out: list[dict[str, object]] = []
    for row in _rows(
        c,
        "SELECT id, name, owner_id, flow FROM automations "
        "WHERE deleted_at IS NULL AND enabled=1 AND trigger_type='schedule' ORDER BY id",
    ):
        automation_id, name = str(row["id"]), str(row["name"])
        flow = _json_dict(row["flow"])
        bot = _runs_as(c, flow, str(row["owner_id"]))
        if bot is None or bot not in ids:
            continue
        state = _bot(c, bot)
        if not state or state.get("state") != "active":
            continue
        trigger = _dict_of(flow.get("trigger"))
        if trigger is None:
            continue
        expr, zone = trigger.get("cron"), trigger.get("tz") or "UTC"
        if not isinstance(expr, str) or not isinstance(zone, str):
            continue
        try:
            if _more_than_hourly(expr, zone, lo):
                out.extend(_day_pills(automation_id, name, bot, _bot_name(c, bot), expr, zone, lo, hi))
                continue
            length = _planned_length(c, automation_id)
            calendar = _calendar("bot", bot, _bot_name(c, bot))
            for at in _firings(expr, zone, lo, hi):
                out.append(
                    _occurrence(
                        f"auto:{automation_id}:{_iso(at)}",
                        "run",
                        calendar,
                        start=_iso(at),
                        end=_iso(at + length),
                        title=name,
                        time_zone=zone,
                        planned=True,
                        recurring=True,
                        links={"automation": automation_id},
                    )
                )
        except (CroniterError, ValueError, KeyError):
            continue  # a schedule the scheduler could not read is passed over, as it is there
    return sorted(out, key=lambda occ: (str(occ["start"]), str(occ["occurrence_id"])))


def _day_pills(
    automation_id: str, name: str, bot: str, bot_name: str, expr: str, zone: str, lo: datetime, hi: datetime
) -> list[dict[str, object]]:
    """One all-day pill per local day that has firings in [lo, hi), titled "<name> ×N" with the count."""
    it = _cron(expr, zone, lo - timedelta(seconds=1))
    counts: dict[date, int] = {}
    for _ in range(FIRING_BUDGET):
        at = it.get_next(datetime)
        if at.astimezone(timezone.utc) >= hi:
            break
        counts[at.date()] = counts.get(at.date(), 0) + 1
    calendar = _calendar("bot", bot, bot_name)
    return [
        _occurrence(
            f"auto:{automation_id}:{day.isoformat()}",
            "run",
            calendar,
            start=day.isoformat(),
            end=(day + timedelta(days=1)).isoformat(),
            title=f"{name} ×{count}",
            all_day=True,
            time_zone=zone,
            planned=True,
            recurring=True,
            links={"automation": automation_id},
        )
        for day, count in sorted(counts.items())
    ]


def due(
    c: sqlite3.Connection, owners: list[tuple[str, str]], start_utc: datetime, end_utc: datetime
) -> list[dict[str, object]]:
    """All-day pills for open tasks due on a day of the window, owned by one of `owners` (("human"|"bot", id) pairs),
    and for goal KPIs whose deadline falls there when the KPI's owner is one of them. A KPI with no owner is not shown:
    company KPIs have no calendar. Closed tasks, and KPIs of a done or dropped goal, are left out."""
    actors = sorted({f"{kind}:{ident}" for kind, ident in owners if kind in ("human", "bot") and ident})
    lo, hi = _utc(start_utc), _utc(end_utc)
    if not actors or lo >= hi:
        return []
    first, last = (lo.date() - timedelta(days=1)).isoformat(), hi.date().isoformat()
    names: dict[str, str] = {}
    out: list[dict[str, object]] = []

    def calendar_for(actor: str) -> dict[str, object]:
        if actor not in names:
            names[actor] = _name_of(c, actor)
        kind, _, ident = actor.partition(":")
        return _calendar(kind, ident, names[actor])

    for row in _rows(
        c,
        f"SELECT id, title, owner_id, due_at, private FROM tasks WHERE owner_id IN ({_marks(actors)}) "
        "AND status='open' AND due_at IS NOT NULL AND substr(due_at,1,10) BETWEEN ? AND ?",
        (*actors, first, last),
    ):
        day = _day(row["due_at"])
        if day is None or not _day_overlaps(day, lo, hi):
            continue
        task_id = str(row["id"])
        out.append(
            _occurrence(
                f"due:task:{task_id}",
                "due",
                calendar_for(str(row["owner_id"])),
                start=day.isoformat(),
                end=(day + timedelta(days=1)).isoformat(),
                title=" ".join(str(row["title"] or "").split()) or "Task",
                all_day=True,
                visibility="private" if row["private"] == 1 else "public",
                links={"task": task_id},
            )
        )
    for row in _rows(
        c,
        f"SELECT gk.id, gk.deadline, k.name, k.owner_id FROM goal_kpis gk JOIN kpis k ON k.id = gk.kpi_id "
        "LEFT JOIN goals g ON g.id = gk.goal_id "
        f"WHERE k.owner_id IN ({_marks(actors)}) AND k.archived_at IS NULL AND gk.deadline IS NOT NULL "
        "AND substr(gk.deadline,1,10) BETWEEN ? AND ? AND COALESCE(g.status,'') NOT IN ('done','dropped')",
        (*actors, first, last),
    ):
        day = _day(row["deadline"])
        if day is None or not _day_overlaps(day, lo, hi):
            continue
        out.append(
            _occurrence(
                f"due:kpi:{row['id']}",
                "due",
                calendar_for(str(row["owner_id"])),
                start=day.isoformat(),
                end=(day + timedelta(days=1)).isoformat(),
                title=" ".join(str(row["name"] or "").split()) or "KPI",
                all_day=True,
            )
        )
    return sorted(out, key=lambda occ: (str(occ["start"]), str(occ["occurrence_id"])))


def _span(occ: dict[str, object]) -> tuple[datetime, datetime] | None:
    start, end = _parse(occ.get("start")), _parse(occ.get("end"))
    return (start, end) if start is not None and end is not None else None


def _groupable(occ: dict[str, object]) -> bool:
    return (
        occ.get("kind") == "run"
        and occ.get("planned") is not True
        and str(occ.get("occurrence_id", "")).startswith("run:")
    )


def _block(cluster: list[dict[str, object]]) -> dict[str, object]:
    """One block over `cluster`, the runs of one bot that touch or overlap, in the contract's `runs:` form."""
    spans = [span for span in (_span(occ) for occ in cluster) if span is not None]
    start = min(span[0] for span in spans)
    end = max(span[1] for span in spans)
    calendar = cast(dict[str, object], cluster[0]["calendar"])
    block = _occurrence(
        f"runs:{calendar['id']}:{_iso(start)}",
        "run",
        calendar,
        start=_iso(start),
        end=_iso(end),
        title=f"{len(cluster)} runs",
    )
    block["count"] = len(cluster)
    block["children"] = [str(occ["occurrence_id"]) for occ in cluster]
    return block


def _settle(cluster: list[dict[str, object]]) -> list[dict[str, object]]:
    """A cluster of one run stays a run; two or more become one block."""
    if not cluster:
        return []
    return cluster if len(cluster) == 1 else [_block(cluster)]


def group_runs(occurrences: list[dict[str, object]], min_minutes: int = GROUP_MINUTES) -> list[dict[str, object]]:
    """Runs of `min_minutes` or more stay as they are. Shorter runs of one bot that touch or overlap become one `runs:`
    block with `count` and `children`; a short run that touches no other stays as it is. Everything else passes through.
    Pure: nothing is read or written."""
    keep: list[dict[str, object]] = []
    short: dict[str, list[dict[str, object]]] = {}
    for occ in occurrences:
        span = _span(occ)
        if not _groupable(occ) or span is None or span[1] - span[0] >= timedelta(minutes=min_minutes):
            keep.append(occ)
            continue
        ref = str(cast(dict[str, object], occ["calendar"])["ref"])
        short.setdefault(ref, []).append(occ)
    for runs_of_bot in short.values():
        runs_of_bot.sort(key=lambda occ: str(occ["start"]))
        cluster: list[dict[str, object]] = []
        reach: datetime | None = None
        for occ in runs_of_bot:
            span = _span(occ)
            if span is None:
                continue
            if cluster and reach is not None and span[0] <= reach:
                cluster.append(occ)
                reach = max(reach, span[1])
            else:
                keep.extend(_settle(cluster))
                cluster, reach = [occ], span[1]
        keep.extend(_settle(cluster))
    return sorted(keep, key=lambda occ: (str(occ["start"]), str(occ["occurrence_id"])))


def month_counts(
    c: sqlite3.Connection,
    bot_ids: list[str],
    start_utc: datetime,
    end_utc: datetime,
    zone: ZoneInfo | str | None = None,
) -> list[dict[str, object]]:
    """How many runs each bot had on each day of the window, a day in `zone` (the viewer's; default the company's ZONE,
    whose Monday-to-Sunday weeks bot_hours keeps). A run is counted on the first window day it is in, so every count
    falls inside the window."""
    day_zone = ZoneInfo(zone) if isinstance(zone, str) else zone or ZONE
    ids = _ids(bot_ids)
    lo, hi = _utc(start_utc), _utc(end_utc)
    if not ids or lo >= hi:
        return []
    counts: dict[tuple[str, date], int] = {}
    for run in _run_rows(c, ids, lo, hi, datetime.now(timezone.utc)):
        key = (run.bot_id, max(run.start, lo).astimezone(day_zone).date())
        counts[key] = counts.get(key, 0) + 1
    return [
        {"bot_id": bot, "date": day.isoformat(), "count": count}
        for (bot, day), count in sorted(counts.items(), key=lambda item: (item[0][1], item[0][0]))
    ]
