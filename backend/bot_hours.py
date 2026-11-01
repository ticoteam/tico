"""Bot hours against human hours, for the Overview card (`GET /api/v2/overview/bot-hours`).

Nothing new is recorded. Bot hours are read from the runs Tico already keeps (`turns`, one row per run of every bot,
BotOps and the Assistant included, failed or not) and their output events (`attempt_events`, keyed by the same id):

- A run counts from its start to its finish (to now while it is still going).
- A silence longer than GAP inside it is left out: between the start, each event and the finish. A run with no event
  inside it counts start to finish.
- A run no longer than GAP cannot hold such a silence, so only longer runs have their events read; most runs are a
  minute or two. A long run can have thousands of events, so `sampled` reads only the few that find its silences.

Human hours are the people on the org chart (`people.visible_people`) times HOURS_PER_PERSON a week. The roster keeps
no history, so every week uses today's count.

Weeks run Monday to Sunday where the company keeps its calendar (`updates.ZONE`), for up to WEEKS weeks, starting no
earlier than the week of the first run. Every run that worked inside them counts, however long ago it started. Only totals leave this module: never a bot's or a person's own hours.
"""

import threading
import time as clock
from datetime import datetime, time, timedelta, timezone

from fastapi import Request

from . import views
from .store import H, P
from .updates import ZONE

GAP = timedelta(minutes=30)
WEEKS = 12
HOURS_PER_PERSON = 40
CACHE_S = 60                        # the Overview may open often; the answer is an estimate and keeps a minute


def stamp(at):
    """`at` in the format every stored timestamp has, so it compares as text."""
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def worked(start, end, activity=(), finished=True):
    """The stretches of `start`..`end` a run spent working, as (from, to) pairs.

    A finished run with no activity inside it worked all of it. Otherwise each silence longer than GAP between the
    start, the activity and the end is left out; a run still going counts up to `end` (now) the same way."""
    inside = sorted(t for t in activity if start < t < end)
    if finished and not inside:
        return [(start, end)] if end > start else []
    out, points = [], [start, *inside, end]
    for a, b in zip(points, points[1:]):
        if b - a > GAP:
            continue
        if out and out[-1][1] == a:
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return [(a, b) for a, b in out if b > a]


def week_start(at):
    """Monday 00:00 of `at`'s week where the company keeps its calendar."""
    day = at.astimezone(ZONE).date()
    return datetime.combine(day - timedelta(days=day.weekday()), time(0), ZONE)


def next_week(start):
    return datetime.combine(start.date() + timedelta(days=7), time(0), ZONE)


def overlap(spans, lo, hi):
    """Seconds of `spans` inside lo..hi. Elapsed time is taken in UTC: two times in the company's zone subtract as wall
    clock, which a week with a clock change makes an hour long or short."""
    utc = timezone.utc
    return sum(max(0.0, (min(b, hi).astimezone(utc) - max(a, lo).astimezone(utc)).total_seconds()) for a, b in spans)


def ratio(bot_hours, human_hours):
    return round(bot_hours / human_hours, 2) if human_hours else None


def sampled(c, rid):
    """Event times of run `rid` that give `worked` the same answer as all of them: halve the run's events by sequence
    number until each stretch spans GAP or less, or is two neighbours further apart than that (a silence). Every
    stretch left between neighbouring times read is then either short or a true silence. Each read is one lookup on
    the (attempt_id, seq) key: a run with thousands of events costs a few dozen."""
    lo, hi = c.execute("SELECT MIN(seq), MAX(seq) FROM attempt_events WHERE attempt_id=?", (rid,)).fetchone()
    if lo is None:
        return []
    times = {}

    def at(seq):
        # The first event at or after `seq`: an old run's text pieces are swept (store.sweep_deltas), leaving gaps.
        if seq not in times:
            row = c.execute("SELECT created_at FROM attempt_events WHERE attempt_id=? AND seq>=? ORDER BY seq LIMIT 1",
                            (rid, seq)).fetchone()
            times[seq] = H.parse_ts(row[0]) if row else None
        return times[seq]

    todo = [(lo, hi)]
    while todo:
        a, b = todo.pop()
        first, last = at(a), at(b)
        if b - a < 2 or first is None or last is None or last - first <= GAP:
            continue
        mid = (a + b) // 2
        at(mid)
        todo += [(a, mid), (mid, b)]
    return [t for t in times.values() if t is not None]


class Counter:
    """Each finished run's working stretches are kept once read, with the start and finish they were read for. A
    finish can change: a run stopped when its lease ran out is restored and finishes again, or files its result late,
    and then it is read again. Its events need no such check: the server stamps each event as it arrives, so one that
    comes after a finish lies past it, outside the stretch that finish closes."""

    def __init__(self):
        self.lock = threading.Lock()
        self.done = {}              # run id -> ((started, finished), its stretches), for finished runs in the window
        self.answer = None          # (monotonic time, answer)

    def spans(self, c, rows, now):
        """Working stretches for each run row (id, started, finished)."""
        with self.lock:
            known = dict(self.done)
        out, keep, wanted = {}, {}, {}
        for row in rows:
            start, end = H.parse_ts(row["started_at"]), H.parse_ts(row["finished_at"]) if row["finished_at"] else None
            if start is None:
                continue
            key = (row["started_at"], row["finished_at"])
            held = known.get(row["id"])
            if end is not None and held and held[0] == key:
                out[row["id"]], keep[row["id"]] = held[1], held
                continue
            stop = end or now
            if stop - start <= GAP:
                out[row["id"]] = worked(start, stop)
                if end is not None:
                    keep[row["id"]] = (key, out[row["id"]])
            else:
                wanted[row["id"]] = (start, stop, key, end is not None)
        activity = {rid: sampled(c, rid) for rid in wanted}
        for rid, (start, stop, key, finished) in wanted.items():
            out[rid] = worked(start, stop, activity[rid], finished)
            if finished:
                keep[rid] = (key, out[rid])
        with self.lock:
            self.done = keep        # runs that left the window drop out here
        return out

    def count(self, c, now=None):
        now = now or H.parse_ts(H.now())
        this_week = week_start(now)
        earliest = this_week
        for _ in range(WEEKS - 1):
            earliest = week_start(earliest - timedelta(days=1))
        lo = min(earliest, now - timedelta(days=7))
        first = c.execute("SELECT MIN(started_at) FROM turns WHERE started_at IS NOT NULL").fetchone()[0]
        people = len(P.visible_people(views.roster(c)))
        human = people * HOURS_PER_PERSON
        # Runs finished inside the window or still going, by the `turns_finished` index: a run that started weeks
        # before the window still counts for the time it worked inside it.
        rows = c.execute("SELECT id, started_at, finished_at FROM turns WHERE finished_at > ? AND started_at < ? "
                         "UNION ALL SELECT id, started_at, finished_at FROM turns WHERE finished_at IS NULL AND started_at < ?",
                         (stamp(lo), stamp(now), stamp(now))).fetchall()
        spans = [s for run in self.spans(c, rows, now).values() for s in run]
        week_from = max(earliest, week_start(H.parse_ts(first))) if first else None
        weeks, at = [], week_from
        while at is not None and at <= this_week:
            end = next_week(at)
            hours = round(overlap(spans, at, min(end, now)) / 3600, 2)
            weeks.append({"week": at.date().isoformat(), "bot_hours": hours, "people": people,
                          "human_hours": human, "ratio": ratio(hours, human), "so_far": end > now})
            at = end
        last7 = round(overlap(spans, now - timedelta(days=7), now) / 3600, 2)
        return {"zone": str(ZONE), "now": stamp(now), "first_run": first, "people": people,
                "hours_per_person": HOURS_PER_PERSON, "gap_minutes": int(GAP.total_seconds() // 60),
                "last_7_days": {"bot_hours": last7, "human_hours": human, "ratio": ratio(last7, human),
                                "full": bool(first) and H.parse_ts(first) <= now - timedelta(days=7)},
                "weeks": weeks}

    def cached(self, c):
        with self.lock:
            held = self.answer
        if held and clock.monotonic() - held[0] < CACHE_S:
            return held[1]
        answer = self.count(c)
        with self.lock:
            self.answer = (clock.monotonic(), answer)
        return answer


def install(app, store, auth):
    counter = app.state.bot_hours = Counter()

    @app.get("/api/v2/overview/bot-hours")
    def bot_hours(request: Request):
        """The Overview's bot hours against human hours: the last 7 days and one row a week (Monday to Sunday in the
        company's time zone) for up to 12 weeks. Totals only, the same for every person."""
        views.human_only(request.state.identity)    # the Overview is a page for people
        with store.read_transaction() as c:
            return counter.cached(c)
