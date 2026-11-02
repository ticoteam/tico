"""When an edition comes due (plan §4). Periods are calendar months or quarters in the company's time zone. An audience's
due day is the Nth business day after its period ends (Monday to Friday, no holidays), or N days after its period ends. It
opens at 00:00 that day.

`tick` is a server job, called from the scheduler loop: nothing about when it runs goes through a model or a routine, and
the only model call is each edition's draft. It only ever looks at the latest period that has come due, so after any
downtime it catches up once and never builds a backlog; `editions_one_per_period` makes a repeat harmless. Once a day it
also refreshes the page's stats (release.refresh). While the page is Closed it reads one row and does nothing. `next_wake`
feeds sleep_idle.routine_times so a sleeping server wakes for an edition.

A rule or cadence this can't read stops that audience's schedule and is listed by `problems`. It never falls back to a
default: an edition drafted on the wrong day, for the wrong period, is worse than one that's visibly not scheduled.
"""
import json
import logging
import threading
from calendar import monthrange
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ..store import H

LOG = logging.getLogger("tico.company_page.schedule")
MOST = {"business_day": 23, "days_after": 365}
SPANS = {"monthly": 1, "quarterly": 3}      # months in one period
# audiences.get says {"kind": "business_day" | "days_after_period_end", "n": 5}; its row holds {"business_day": 5}.
VIEW_KINDS = {"business_day": "business_day", "days_after_period_end": "days_after"}
REFRESH_KEY = "company-page-refresh"        # registry_metadata: the company-zone day the stats last refreshed
_RUNNING = threading.Lock()


def company_zone():
    """The company's zone: updates.ZONE, the one automation_due.zone_of and the update ladder read. Tico has no per-company
    setting for it."""
    from ..updates import ZONE
    return ZONE


def _zone(zone):
    return zone if isinstance(zone, ZoneInfo) else ZoneInfo(str(zone))


def _aware(at):
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def transparency(c):
    """closed, private or open. No row, or anything else, is closed."""
    row = c.execute("SELECT transparency FROM company_page_settings WHERE id='company'").fetchone()
    return row["transparency"] if row and row["transparency"] in ("private", "open") else "closed"


def due_rule(audience):
    """("business_day", n) or ("days_after", n), from an audience row or audiences.get's view of one; None for anything
    else, an empty rule included."""
    raw = audience.get("due_rule")
    try:
        rule = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    if not isinstance(rule, dict):
        return None
    if set(rule) == {"kind", "n"}:
        key, n = VIEW_KINDS.get(str(rule["kind"])), rule["n"]
    elif len(rule) == 1:
        key, n = next(iter(rule.items()))
    else:
        return None
    return (key, n) if key in MOST and type(n) is int and 1 <= n <= MOST[key] else None


def _plan(audience):
    """(months in a period, due rule), or None when this audience can't be scheduled."""
    span, rule = SPANS.get(audience.get("cadence") or "monthly"), due_rule(audience)
    return (span, rule) if span and rule else None


def problem(audience):
    """Why this audience can't be scheduled, or None."""
    if not SPANS.get(audience.get("cadence") or "monthly"):
        return "cadence"
    return None if due_rule(audience) else "due_rule"


def problems(c):
    """The enabled audiences whose schedule is stopped, and why: for the owners' page."""
    return [{"audience_id": a["id"], "name": a["name"], "problem": problem(a)}
            for a in (dict(r) for r in c.execute("SELECT * FROM audiences WHERE enabled=1 ORDER BY created_at, id"))
            if problem(a)]


def _nth_business_day(year, month, n):
    day, left = date(year, month, 1), n
    while True:
        if day.weekday() < 5:
            left -= 1
            if not left:
                return day
        day += timedelta(days=1)


def _period(index, span):
    """The period `index` periods after year 0: (first day, last day)."""
    first_year, first_month = divmod(index * span, 12)
    last_year, last_month = divmod(index * span + span - 1, 12)
    return (date(first_year, first_month + 1, 1),
            date(last_year, last_month + 1, monthrange(last_year, last_month + 1)[1]))


def _due_day(plan, index):
    """The day the period `index` comes due."""
    span, (kind, n) = plan
    if kind == "days_after":
        return _period(index, span)[1] + timedelta(days=n)
    following = _period(index + 1, span)[0]
    return _nth_business_day(following.year, following.month, n)


def _moment(day, zone):
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc)


def _index_of(local, span):
    return (local.year * 12 + local.month - 1) // span


def next_due(audience, after, zone):
    """The first moment after `after` an edition of this audience comes due, as aware UTC. None when it can't be scheduled."""
    plan = _plan(audience)
    if not plan:
        return None
    zone, after = _zone(zone), _aware(after)
    # Back far enough that the first period tried can't already be past `after`: a long lag reaches over months.
    index = _index_of(after.astimezone(zone), plan[0]) - (plan[1][1] // 28 + 2 if plan[1][0] == "days_after" else 2)
    while True:
        moment = _moment(_due_day(plan, index), zone)
        if moment > after:
            return moment
        index += 1


def latest_due(audience, at, zone):
    """(the moment, period start, period end) of the latest period already due at `at`, or None."""
    plan = _plan(audience)
    if not plan:
        return None
    zone, at = _zone(zone), _aware(at)
    index = _index_of(at.astimezone(zone), plan[0])
    while True:
        moment = _moment(_due_day(plan, index), zone)
        if moment <= at:
            first, last = _period(index, plan[0])
            return moment, first.isoformat(), last.isoformat()
        index -= 1


def _audiences(c, shown):
    rows = [dict(r) for r in c.execute("SELECT * FROM audiences WHERE enabled=1 ORDER BY created_at, id")]
    # The Public audience exists only while the page is Open: while Private, it has no readers to write for.
    return [a for a in rows if a["kind"] != "public" or shown == "open"]


def _sources(c, audience_id):
    return [r["author"] for r in c.execute("SELECT author FROM audience_sources WHERE audience_id=? ORDER BY author",
                                           (audience_id,))]


def due_now(c, at):
    """(audience id, source, period start, period end) for each edition due and not opened. Only the latest period is ever
    looked at. A row in any state counts as opened, a retraction included; a source with nothing written in the period
    has nothing to draft."""
    from . import editions as E
    shown = transparency(c)
    if shown == "closed":
        return []
    zone, out = company_zone(), []
    stale = H.shift(H.now(), seconds=-E.DRAFT_STALE_S)
    for audience in _audiences(c, shown):
        found = latest_due(audience, at, zone)
        if not found:
            continue
        _, start, end = found
        for author in _sources(c, audience["id"]):
            row = c.execute("SELECT status, updated_at FROM editions WHERE audience_id=? AND author=? AND period_start=? "
                            "AND period_end=? ORDER BY version LIMIT 1", (audience["id"], author, start, end)).fetchone()
            if row and not (row["status"] == "drafting" and row["updated_at"] < stale):
                continue
            if E.has_material(c, author, start, end):
                out.append((audience["id"], author, start, end))
    return out


def _day_of(at):
    return _aware(at).astimezone(company_zone()).date().isoformat()


def _refresh_due(c, at):
    row = c.execute("SELECT value_json FROM registry_metadata WHERE key=?", (REFRESH_KEY,)).fetchone()
    return not row or row["value_json"] != json.dumps(_day_of(at))


def _refresh_stats(store, at):
    """The day's stats refresh, claimed first: one that fails tries again tomorrow rather than every minute."""
    with store.transaction() as c:
        if not _refresh_due(c, at):
            return
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES(?,?)", (REFRESH_KEY, json.dumps(_day_of(at))))
    try:
        from . import release
        release.refresh(store, _aware(at))
    except Exception as exc:  # noqa: BLE001 - nothing is being taken away, and the drafts don't depend on it
        LOG.warning("Company page refresh failed: %s", type(exc).__name__)


def tick(store, at=None, primary=None):
    """Refresh the stats if today's refresh hasn't run, and open every edition that's due; returns those drafted. One
    failing never stops the others."""
    from . import editions as E
    at = _aware(at or datetime.now(timezone.utc))
    with store.read() as c:
        if transparency(c) == "closed":
            return []
        due = due_now(c, at)
    _refresh_stats(store, at)
    done = []
    for audience_id, author, start, end in due:
        try:
            done.append(E.draft(store, audience_id, author, start, end, primary))
        except Exception as exc:  # noqa: BLE001 - the next tick sees this one again
            LOG.warning("Edition draft failed: %s", type(exc).__name__)
    return done


def start(store, at):
    """From the scheduler loop: one read, and when there's work, `tick` on its own thread, one at a time, so neither a
    model call nor a render holds the loop."""
    with store.read() as c:
        if transparency(c) == "closed" or not (_refresh_due(c, at) or due_now(c, _aware(at))):
            return False
    if not _RUNNING.acquire(blocking=False):
        return False

    def run():
        try:
            tick(store, at)
        finally:
            _RUNNING.release()
    try:
        threading.Thread(target=run, name="company-page-editions", daemon=True).start()
    except RuntimeError:
        _RUNNING.release()
        return False
    return True


def next_wake(c, at):
    """When the server next has an edition to open, or None: never while Closed, and always after `at`, so an edition it
    can't open (nothing written yet) never keeps a server awake."""
    shown = transparency(c)
    if shown == "closed":
        return None
    zone, at = company_zone(), _aware(at)
    times = [next_due(a, at, zone) for a in _audiences(c, shown) if _sources(c, a["id"])]
    return min((t for t in times if t), default=None)
