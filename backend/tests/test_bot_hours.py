"""Bot hours against human hours on the Overview (backend/bot_hours.py): runs count start to finish with silences over
30 minutes left out, people on the org chart count 40 hours a week, weeks are Monday to Sunday in the company's time
zone, and only people may read the totals. Synthetic runs only."""

from datetime import datetime, timedelta, timezone

from backend import bot_hours as B
from backend.store import encode
from backend.tests.test_api import api, get, headers, setup_attempt  # noqa: F401

ZONE = B.ZONE
MONDAY = datetime(2026, 10, 5, 0, 0, tzinfo=ZONE)       # a past week, Monday 00:00 Pacific
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=ZONE)        # the Wednesday of the week after


def runs(api):
    """The fixture's one real run, finished at once, and a function adding finished runs beside it."""
    _, _, attempt = setup_attempt(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE turns SET finished_at=started_at WHERE id=?", (attempt["id"],))
        base = dict(c.execute("SELECT * FROM attempts WHERE id=?", (attempt["id"],)).fetchone())

    def add(c, rid, start, end, events=()):
        row = {**base, "id": rid, "token_hash": rid, "state": "completed"}
        c.execute(f"INSERT INTO attempts({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))
        run(c, rid, start, end, events)
    add.bot_token = attempt["token"]
    return add


def run(c, rid, start, end, events=()):
    c.execute("INSERT INTO turns(id,bot,started_at,finished_at,trigger,exit) VALUES(?,?,?,?,?,?)",
              (rid, "ops", B.stamp(start), B.stamp(end) if end else None, "task", "completed"))
    for seq, (at, kind) in enumerate(events, 1):
        c.execute("INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES(?,?,?,?,?)",
                  (rid, seq, kind, "{}", B.stamp(at)))


def people(c, ids):
    c.execute("UPDATE registry_metadata SET value=? WHERE key='people'",
              (encode({"people": [{"id": p, "email": p + "@acme.example"} for p in ids]}),))


def week(answer, monday):
    return next(w for w in answer["weeks"] if w["week"] == monday.date().isoformat())


def test_eighty_bot_hours_and_one_person_read_two_to_one(api):
    store, run = api.app.state.store, runs(api)
    with store.transaction() as c:
        people(c, ["ana"])
        # Ten runs that never fall silent (an event every 10 minutes), Monday to Friday, two a day: 79 hours.
        for day in range(5):
            for lane in range(2):
                start, hours = MONDAY + timedelta(days=day, hours=8 + lane), 7 if (day, lane) == (0, 0) else 8
                run(c, f"r{day}{lane}", start, start + timedelta(hours=hours),
                    [(start + timedelta(minutes=m), "tool") for m in range(10, hours * 60, 10)])
        # A run across Sunday midnight counts on both sides of it: its second hour makes 80.
        late = MONDAY - timedelta(hours=1)
        run(c, "across", late, late + timedelta(hours=2))
    with store.read_transaction() as c:
        answer = B.Counter().count(c, now=NOW)
    first = week(answer, MONDAY)
    assert (first["bot_hours"], first["people"], first["human_hours"], first["ratio"]) == (80.0, 1, 40, 2.0)
    assert first["so_far"] is False
    assert week(answer, MONDAY - timedelta(days=7))["bot_hours"] == 1.0
    assert week(answer, MONDAY + timedelta(days=7))["so_far"] is True
    assert [w["week"] for w in answer["weeks"]] == ["2026-09-28", "2026-10-05", "2026-10-12"], "none before the first run"
    assert answer["last_7_days"]["full"] is True

    # Three people: 120 human hours a week. Nobody on the chart: hours with no ratio.
    with store.transaction() as c:
        people(c, ["ana", "ben", "cara"])
    with store.read_transaction() as c:
        assert week(B.Counter().count(c, now=NOW), MONDAY)["human_hours"] == 120
    with store.transaction() as c:
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'",
                  (encode({"people": [{"id": "ana", "email": "ana@acme.example", "hidden": True}],
                           "default_user": "ana"}),))
    with store.read_transaction() as c:
        empty = week(B.Counter().count(c, now=NOW), MONDAY)
    assert (empty["people"], empty["human_hours"], empty["ratio"], empty["bot_hours"]) == (0, 0, None, 80.0)


def test_a_silence_over_thirty_minutes_is_left_out():
    start = MONDAY + timedelta(hours=9)
    m = lambda n: start + timedelta(minutes=n)  # noqa: E731
    # Work 9:00-10:00 (an event every 10 minutes), two silent hours, then work 12:00-12:30 and finish.
    activity = [m(n) for n in range(10, 61, 10)] + [m(n) for n in range(180, 211, 10)]
    spans = B.worked(start, m(210), activity)
    assert spans == [(start, m(60)), (m(180), m(210))]
    assert B.overlap(spans, start, m(210)) == 90 * 60
    # Nothing recorded inside a finished run: start to finish. A run still going stops at its last activity.
    assert B.worked(start, m(240)) == [(start, m(240))]
    assert B.worked(start, m(240), [m(20)], finished=False) == [(start, m(20))]


def test_a_long_runs_few_reads_find_every_silence_and_only_people_read_it(api):
    store, run = api.app.state.store, runs(api)
    start = datetime.now(timezone.utc) - timedelta(hours=7)
    s = lambda n: start + timedelta(seconds=n)  # noqa: E731
    # A six-hour run with an event every 20 seconds, silent 40 min after 1 h, 31 min after 3 h and 29 min after 4 h,
    # and quiet for its last 45 minutes: 6 h - 40 - 31 - 45 min = 4 h 4 min.
    quiet = [(3600, 6000), (10800, 12660), (14400, 16140), (18900, 21600)]
    times = [s(n) for n in range(20, 21600, 20) if not any(a < n < b for a, b in quiet)]
    with store.transaction() as c:
        run(c, "long", start, s(21600), [(t, "delta") for t in times])
    with store.read_transaction() as c:
        reads = B.sampled(c, "long")
    assert B.worked(start, s(21600), reads) == B.worked(start, s(21600), times)
    assert len(reads) < len(times) / 4, "a long run's events are not all read"
    answer = get(api, "overview/bot-hours")
    assert answer["last_7_days"]["bot_hours"] == round(244 / 60, 2)
    assert answer["people"] == 3 and answer["last_7_days"]["human_hours"] == 120
    assert set(answer) == {"zone", "now", "first_run", "people", "hours_per_person", "gap_minutes", "last_7_days", "weeks"}
    assert all(set(w) == {"week", "bot_hours", "people", "human_hours", "ratio", "so_far"} for w in answer["weeks"])
    assert api.get("/api/v2/overview/bot-hours", headers=headers(run.bot_token)).status_code == 403


def test_a_finish_that_changes_and_a_run_older_than_the_weeks_both_count(api, monkeypatch):
    store, run, counter = api.app.state.store, runs(api), B.Counter()
    reads = []
    real = B.sampled
    monkeypatch.setattr(B, "sampled", lambda c, rid: reads.append(rid) or real(c, rid))
    oldest = datetime(2026, 7, 27, 0, 0, tzinfo=ZONE)   # NOW's twelfth week back
    stopped = MONDAY + timedelta(hours=9)
    with store.transaction() as c:
        people(c, ["ana"])
        # Started three days before the oldest week, finished a day into it, nothing recorded inside: 24 h there.
        run(c, "old", oldest - timedelta(days=3), oldest + timedelta(days=1))
        # Finished before the oldest week: nothing.
        run(c, "older", oldest - timedelta(days=7), oldest - timedelta(days=1))
        # Stopped 40 minutes in when its lease ran out.
        run(c, "late", stopped, stopped + timedelta(minutes=40))
    with store.read_transaction() as c:
        answer = counter.count(c, now=NOW)
    assert answer["weeks"][0]["week"] == "2026-07-27" and answer["weeks"][0]["bot_hours"] == 24.0
    assert week(answer, MONDAY)["bot_hours"] == round(40 / 60, 2)
    with store.read_transaction() as c:
        reads.clear()
        counter.count(c, now=NOW)
    assert reads == [], "a finished run that hasn't changed is not read again"
    # Its result arrives late, three hours in: the same counter counts the new finish.
    with store.transaction() as c:
        c.execute("UPDATE turns SET finished_at=? WHERE id='late'", (B.stamp(stopped + timedelta(hours=3)),))
    with store.read_transaction() as c:
        assert week(counter.count(c, now=NOW), MONDAY)["bot_hours"] == 3.0
    assert reads == ["late"]
    # A week with a clock change is as long as it really is: 169 h when the clocks go back, 167 when they go forward.
    for day, hours in ((datetime(2026, 11, 1, 12, tzinfo=ZONE), 169), (datetime(2026, 3, 8, 12, tzinfo=ZONE), 167)):
        lo = B.week_start(day)
        assert B.overlap([(lo - timedelta(days=1), lo + timedelta(days=9))], lo, B.next_week(lo)) == hours * 3600
