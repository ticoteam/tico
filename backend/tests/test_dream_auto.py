"""Auto dream (backend/dream_auto.py, docs/learnings.md): the Maintenance run's dream step starts a dream for each bot whose
memory changed a lot since its last dream, under the gates, up to "Most bots per run"; a run claims its schedule bucket
once; Run now ignores the schedule only; the requester is the bot's manager, else the owner; a question from the dream
reaches the requester without ask ingest.
Synthetic fixtures: the api fixture's bots (backend/tests/helpers/api_app.py; Ana owns the company, Ben is an admin). Memory is
written as memory_history.record stores it, except the one test that reports through the route."""

import json
import secrets
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from backend import dream
from backend import dream_auto as DA
from backend import hubdb as H
from backend import maintenance as M
from backend import maintenance_limits as L
from backend.maintenance_steps import dream as dream_step
from backend.scheduler import Scheduler
from backend.tests.helpers.api import assign, claim, get, headers, post, ready, runner  # noqa: F401  (fixtures)
from backend.tests.helpers.ask_ingest import gate, plate  # noqa: F401  (the ask ingest fixture)

QUESTIONS = "Should weekly reports go to Ana or the team?"
LEARNINGS = "memory/learnings.md"


def now():
    return datetime.now(timezone.utc)


@contextmanager
def settings(store, **changes):
    """The store with some settings changed for a while. `test_identities={}` makes it a company server's (the dream step
    starts nothing on a test server)."""
    saved = store.settings
    store.settings = replace(saved, **changes)
    try:
        yield store
    finally:
        store.settings = saved


def diff(lines, path=LEARNINGS):
    body = "".join(f"+note {i}\n" for i in range(lines))
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1,{lines} @@\n{body}"


def remember(api, bot, commits, path=LEARNINGS, when=None, attempt=""):
    """`commits` shared commits to `path`, as memory_history.record stores them; `attempt` is the run that made them ('' for
    a person's edit outside any run, which is a correction)."""
    when = when or now() - timedelta(hours=2)
    with api.app.state.store.transaction() as c:
        for _ in range(commits):
            c.execute(
                "INSERT INTO bot_memory_updates(bot,sha,subject,author,committed_at,files,diff,truncated,attempt_id,"
                "shared,runner_id,reported_at) VALUES(?,?,?,?,?,?,?,0,?,1,'test-runner',?)",
                (
                    bot,
                    secrets.token_hex(20),
                    "Edit",
                    bot,
                    DA.stamp(when),
                    json.dumps([path]),
                    diff(3, path),
                    attempt,
                    H.now(),
                ),
            )


def worked(api, bot, turns, when=None, task=None):
    """`turns` finished runs of the bot, at `when` (two hours ago by default), on `task` when given."""
    when = DA.stamp(when or now() - timedelta(hours=2))
    with api.app.state.store.transaction() as c:
        for _ in range(turns):
            c.execute(
                "INSERT INTO attempts(id,bot_id,created_at,settled_at,task_id) VALUES(?,?,?,?,?)",
                (secrets.token_hex(8), bot, when, when, task),
            )


def thumbs_down(api, bot, count=1):
    """`count` thumbs-down by a person on the bot's work, an hour ago."""
    at = DA.stamp(now() - timedelta(hours=1))
    with api.app.state.store.transaction() as c:
        for _ in range(count):
            c.execute(
                "INSERT INTO feedbacks(id,person_id,subject_type,subject_id,bot_id,rating,created_at,updated_at) "
                "VALUES(?,'ana','message',?,?,'down',?,?)",
                (secrets.token_hex(8), secrets.token_hex(8), bot, at, at),
            )


def aged(api, days=30):
    """Every bot made `days` ago: past the 48 hours a bot that never dreamed must be."""
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET created_at=?", (H.shift(H.now(), days=-days),))


def verdict(api, bot, at):
    with api.app.state.store.read() as c:
        return DA.verdict(c, bot, at)


def owner_of(c, bot):
    """A bot's owner id for its tasks: the bot's id, which a branch's slug does not equal (backend/tests/test_dream.py)."""
    return "bot:" + ((H.resolve_bot(c, bot) or {}).get("id") or bot)


def dream_rows(api, bot):
    with api.app.state.store.read() as c:
        return [H.task(c, row["id"]) for row in dream.dreams(c, owner_of(c, bot))]


def close(api, bot):
    """The bot's open dream is done, as a bot's own finish does it (backend/tests/test_dream.py)."""
    with api.app.state.store.transaction() as c:
        owner = owner_of(c, bot)
        row = dream._open(c, [owner])
        assert row is not None, "there is an open dream to close"
        H.task_done(c, owner, row["id"], "Merged the release rules.", seen_version=row["version"])


def five_due(api):
    """Five bots in need at once: finance, product-design, cpo, ops and assistant, with 10 to 14 turns. Ops reports to
    Ben."""
    aged(api)
    for bot, turns in (("finance", 10), ("product-design", 11), ("cpo", 12), ("ops", 13), ("assistant", 14)):
        worked(api, bot, turns)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='ops'")


def run(api, at=None, report=None, limit=DA.MAX_PER_RUN):
    return DA.run(api.app.state.store, api.app.state.auth, at or now(), limit=limit, report=report)


def steps_by_key(run_dict):
    return {s["step"]: s for s in run_dict["steps"]}


def step_result(store, auth, max_bots=3):
    """The dream step as the Maintenance run calls it: run(c, ctx) on a read connection (backend/maintenance.py)."""
    ctx = {"store": store, "auth": auth, "now": H.now(), "settings": {"max_bots": max_bots}}
    with store.read() as c:
        return dream_step.run(c, ctx)


def test_a_bot_that_never_dreamed_must_be_48_hours_old_and_an_inactive_one_is_not_due(api):
    worked(api, "ops", 10)
    assert verdict(api, "ops", now()).status == "new"
    assert verdict(api, "ops", now() + timedelta(days=3)).status == "due"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE id='ops'")
    assert verdict(api, "ops", now() + timedelta(days=3)).status == "inactive"


def test_memory_commits_reported_with_no_run_are_not_corrections_and_the_dream_is_for_the_manager(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    committed = (now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    commits = [
        {
            "sha": f"{n:040x}",
            "subject": "Fix the release rule",
            "author": "ben",
            "committed": committed,
            "files": [LEARNINGS],
            "diff": diff(30),
            "shared": True,
        }
        for n in range(1, 3)
    ]
    # Reported with no run: a person's edit or Tico's own (a migration) alike, so neither counts.
    assert post(api, "bots/ops/memory/report", {"commits": commits}, machine["token"])["recorded"] == 2
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='ops'")
    aged(api)
    assert verdict(api, "ops", now()).corrections == 0
    thumbs_down(api, "ops", 2)  # two corrections, which weigh 10

    started = run(api)
    assert started == [verdict(api, "ops", now()).name]
    [row] = dream_rows(api, "ops")
    assert row["requester_id"] == "human:ben" and H.task_labels(row) == ["dream"] and row["title"] == "Dream"
    first_line, _, _ = row["body"].partition("\n")
    assert first_line.startswith("Auto dream: 0 turns and 2 corrections from people since ")
    with api.app.state.store.read() as c:
        assert dream.auto_event(c, row)["reason"] == first_line
    view = get(api, "bots/ops/dream", token="ben-test")["dream"]
    assert view["auto"] is True and view["reason"] == first_line
    assert run(api) == [], "one open dream: nothing more for the bot"


def test_a_bot_gets_one_dream_per_48_hours_and_none_while_one_is_open(api):
    aged(api)
    worked(api, "ops", 10)
    assert len(run(api)) == 1
    [first] = dream_rows(api, "ops")
    worked(api, "ops", 10, when=now() + timedelta(seconds=1))  # new work after the start
    worked(api, "ops", 1, task=first["id"])  # the dream ran
    # Busy on its own, waiting once the release hold holds the dream's job: either way not picked again.
    assert verdict(api, "ops", now() + timedelta(days=2)).status in ("busy", "waiting"), (
        "an open dream blocks a new one"
    )
    close(api, "ops")
    assert verdict(api, "ops", now() + timedelta(hours=1)).status == "paced", "48 hours since the last dream's start"
    # The wake limiter's ledger is real time, so the first start's wake is 49 hours old by the second start.
    with api.app.state.store.transaction() as c:
        c.execute(
            "UPDATE events SET created_at=? WHERE actor=? AND action=?",
            (H.shift(H.now(), hours=-49), H.KEEPER, L.BOT_ACTION),
        )
    later = now() + timedelta(hours=49)
    assert verdict(api, "ops", later).status == "due"
    assert len(run(api, later)) == 1
    assert [d["id"] for d in dream_rows(api, "ops")][-1] == first["id"]
    assert len(dream_rows(api, "ops")) == 2


def test_the_cap_starts_three_most_changed_first_and_says_what_it_held(api):
    five_due(api)
    r = {}
    started = run(api, report=r)
    assert started == [verdict(api, b, now()).name for b in ("assistant", "ops", "cpo")], "most need first"
    assert [b for b in ("assistant", "ops", "cpo") if dream_rows(api, b)] == ["assistant", "ops", "cpo"]
    assert not dream_rows(api, "product-design") and not dream_rows(api, "finance")
    assert dream_rows(api, "assistant")[0]["requester_id"] == "human:ana", "no manager: the company owner"
    assert dream_rows(api, "ops")[0]["requester_id"] == "human:ben", "the nearest human manager"
    assert (r["started"], r["due"], r["over"], r["held"]) == (started, 5, 2, []), "two due bots over the cap, none held"
    assert "Queued 3 dreams" in r["reason"] and "2 due past the 3 this run" in r["reason"], r
    assert "2 not due" in r["reason"], r


def test_a_per_step_limit_of_one_starts_only_the_most_changed(api):
    five_due(api)
    r = {}
    assert run(api, limit=1, report=r) == [verdict(api, "assistant", now()).name]
    assert (r["over"], r["held"]) == (4, []), r
    assert not any(dream_rows(api, b) for b in ("ops", "cpo", "product-design", "finance"))


def test_an_auto_dreams_question_reaches_its_manager_without_ask_ingest(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='ops'")
    aged(api)
    worked(api, "ops", 10)
    plate(api, 15)  # a plate full enough that ingest would send a question back
    assert len(run(api)) == 1
    [row] = dream_rows(api, "ops")
    r = api.post(
        f"/api/v2/tasks/{row['id']}/asks",
        json={"to": "human:ben", "line": QUESTIONS, "version": row["version"]},
        headers=headers(token),
    )
    assert r.status_code == 200, r.text
    assert "ingest" not in r.json() and model.calls == []
    with api.app.state.store.read() as c:
        waits = H.task_waits(c, [row["id"]])[row["id"]]
        assert [(w.state, w.who, w.ask) for w in waits][:1] == [("person", "human:ben", True)]


def test_the_schedule_runs_the_dream_step_once_per_bucket(api, monkeypatch):
    """The scheduled run claims its bucket in the store: the first tick in a bucket runs the dream step, and a second
    tick in the same bucket starts no second run. A restart keeps no memory, so it changes nothing. Collect and Clean up
    are skipped here: they are not under test."""
    aged(api)
    store, auth = api.app.state.store, api.app.state.auth
    worked(api, "ops", 10)
    quiet = {"status": "skipped", "reason": "not under test"}
    monkeypatch.setitem(M.LIVE, "collect", lambda c, ctx: quiet)
    monkeypatch.setitem(M.LIVE, "clean_up", lambda c, ctx: quiet)
    monkeypatch.setattr(M, "_start", lambda s, a, run_id, only, wait: M._execute(s, a, run_id, only))
    at = now()
    with settings(store, test_identities={}):
        assert M.tick(store, auth, at) is not None
        assert M.tick(store, auth, at) is None, "the bucket has its run: no second"
    assert len(dream_rows(api, "ops")) == 1
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM maintenance_runs WHERE trigger='schedule'").fetchone()[0] == 1


def test_the_dream_step_runs_once_a_day_on_its_own_clock(api):
    """There is no night any more. The dream step is "once a day" (its default), so a run that finds it started since the
    day's 05:00 company start leaves it alone. The 05:00 boundary itself is test_maintenance's."""
    aged(api)
    store, auth = api.app.state.store, api.app.state.auth
    worked(api, "ops", 10)
    with settings(store, test_identities={}):
        first, _ = M.run_now(store, auth, "human:ana", step="dream", wait=True)
        # A plain Run now keeps the due rule. A step-only one runs the step whether or not it is due.
        second, _ = M.run_now(store, auth, "human:ana", wait=True)
    assert steps_by_key(first)["dream"]["status"] == "ran" and len(dream_rows(api, "ops")) == 1
    assert steps_by_key(second)["dream"]["status"] == "skipped"
    assert steps_by_key(second)["dream"]["reason"] == "not due"
    assert len(dream_rows(api, "ops")) == 1


def test_switched_off_the_dream_step_logs_off_and_starts_nothing(api):
    aged(api)
    store, auth = api.app.state.store, api.app.state.auth
    worked(api, "ops", 10)
    with store.transaction() as c:
        M.save_step(c, "human:ana", {"key": "dream", "enabled": False})
    assert [s for s in get(api, "maintenance")["steps"] if s["key"] == "dream"][0]["enabled"] is False
    with settings(store, test_identities={}):
        run_dict, started = M.run_now(store, auth, "human:ana", step="dream", wait=True)
        assert started and steps_by_key(run_dict)["dream"]["status"] == "off"
        assert steps_by_key(run_dict)["dream"]["reason"] == "switched off"
        assert dream_rows(api, "ops") == []
        with store.transaction() as c:
            M.save_step(c, "human:ana", {"key": "dream", "enabled": True})
        run_dict, _ = M.run_now(store, auth, "human:ana", step="dream", wait=True)
    assert steps_by_key(run_dict)["dream"]["status"] == "ran" and len(dream_rows(api, "ops")) == 1


def test_run_now_runs_the_dream_step_without_the_schedule_but_keeps_the_gates(api):
    aged(api)
    store, auth = api.app.state.store, api.app.state.auth
    worked(api, "ops", 10)  # in need
    worked(api, "assistant", 9)  # 9 turns: not in need
    with settings(store, test_identities={}):
        run_dict, started = M.run_now(store, auth, "human:ana", step="dream", wait=True)
    assert started and run_dict["trigger"] == "run_now" and run_dict["occurrence_key"].startswith("run_now:")
    row = steps_by_key(run_dict)["dream"]
    assert row["status"] == "ran" and row["details"]["started"] == [verdict(api, "ops", now()).name]
    assert dream_rows(api, "assistant") == [] and len(dream_rows(api, "ops")) == 1, "9 turns is not in need"


def test_the_scheduler_tick_hands_its_time_to_the_maintenance_run(api, monkeypatch):
    seen = []
    monkeypatch.setattr(M, "tick", lambda store, auth, at, failures=None: seen.append(at))
    at = now()
    Scheduler(api.app.state.store, api.app.state.execution).tick(at)
    assert seen == [at]


def test_a_store_with_no_app_skips_the_dream_step_quietly(tmp_path):
    """A bare store (no app installed the dream route): the dream step skips, says why, and starts nothing."""
    from backend.auth import Auth
    from backend.store import Store
    from backend.config import Settings

    s = Store(Settings(db_path=tmp_path / "bare.sqlite"))
    s.initialize(seed_market=False)
    with settings(s, test_identities={}):
        out = step_result(s, Auth(s))
    assert out["status"] == "skipped" and out["reason"] == "the dream's task path is not wired on this server"
    assert out["woke"] == 0


def test_most_bots_per_run_of_zero_skips_the_dream_step(api):
    aged(api)
    worked(api, "ops", 10)
    store = api.app.state.store
    with settings(store, test_identities={}):
        out = step_result(store, api.app.state.auth, max_bots=0)
    assert out["status"] == "skipped" and out["reason"] == '"Most bots per run" is 0'
    assert dream_rows(api, "ops") == []


def test_the_wake_limiter_holds_a_bot_already_woken_this_run(api):
    """A dream start is a Maintenance wake: a bot the limiter already woke this run is held, and the others still start."""
    five_due(api)
    store = api.app.state.store
    with store.transaction() as c:
        L.record(c, "ops")
    report = {}
    started = run(api, report=report)
    assert "ops" in report["held"] and "ops" not in started, report
    assert started, "the other due bots still start"
    assert dream_rows(api, "ops") == []


@pytest.mark.parametrize("limiter", ["bot", "fleet"])
def test_a_dream_refused_only_by_wake_limits_retries_on_a_later_run_that_day(api, monkeypatch, limiter):
    store, auth = api.app.state.store, api.app.state.auth
    clock = [DA.stamp(M.day_start(now()) + timedelta(hours=2))]
    first_at = clock[0]
    monkeypatch.setattr(H, "now", lambda: clock[0])
    aged(api)
    worked(api, "ops", 10, when=H.parse_ts(first_at) - timedelta(hours=1))
    previous = H.shift(first_at, days=-1)
    with store.transaction() as c:
        c.execute("UPDATE maintenance_steps SET enabled=0 WHERE key NOT IN ('collect','dream')")
        M.save_step(c, "human:ana", {"key": "dream", "max_bots": 1})
        c.execute("UPDATE maintenance_steps SET last_started_at=?,cursor_at=? WHERE key='dream'", (previous, previous))

    def earlier_wakes(c, ctx):
        if ctx["now"] == first_at:
            with store.transaction() as write:
                for bot in ["ops"] if limiter == "bot" else [f"other_{n}" for n in range(L.WAKES_PER_HOUR)]:
                    L.record(write, bot)
        return {"status": "ran"}

    monkeypatch.setitem(M.LIVE, "collect", earlier_wakes)  # a step that runs before Dream
    with settings(store, test_identities={}):
        first, _ = M.run_now(store, auth, "human:ana", wait=True)
        row = steps_by_key(first)["dream"]
        assert row["status"] == "skipped" and row["woke"] == 0
        assert row["details"]["limited"] == [verdict(api, "ops", H.parse_ts(clock[0])).name]
        assert dream_rows(api, "ops") == []
        with store.read() as c:
            saved = c.execute("SELECT last_started_at,cursor_at FROM maintenance_steps WHERE key='dream'").fetchone()
            assert tuple(saved) == (previous, previous), (
                "a limiter refusal uses neither the daily attempt nor its cursor"
            )
        clock[0] = H.shift(first_at, hours=2)  # a later run, past the fleet's rolling hour
        second, _ = M.run_now(store, auth, "human:ana", wait=True)
        assert steps_by_key(second)["dream"]["woke"] == 1 and len(dream_rows(api, "ops")) == 1
        worked(api, "assistant", 10, when=H.parse_ts(first_at) - timedelta(hours=1))
        clock[0] = H.shift(first_at, hours=4)
        third, _ = M.run_now(store, auth, "human:ana", wait=True)
    assert steps_by_key(third)["dream"]["reason"] == "not due", "a real start still uses the daily attempt"
    assert dream_rows(api, "assistant") == []


@pytest.mark.parametrize("outcome", ["started", "failed"])
def test_a_limiter_refusal_beside_another_dream_attempt_still_counts_for_the_day(api, monkeypatch, outcome):
    store, auth = api.app.state.store, api.app.state.auth
    clock = [DA.stamp(M.day_start(now()) + timedelta(hours=2))]
    first_at = clock[0]
    monkeypatch.setattr(H, "now", lambda: clock[0])
    aged(api)
    for bot, turns in (("ops", 18), ("assistant", 13)):
        worked(api, bot, turns, when=H.parse_ts(first_at) - timedelta(hours=1))
    with store.transaction() as c:
        c.execute("UPDATE maintenance_steps SET enabled=0 WHERE key<>'dream'")
        M.save_step(c, "human:ana", {"key": "dream", "max_bots": 1})
        L.record(c, "ops")
    if outcome == "failed":

        def refused(*args, **kwargs):
            raise H.Refused("duplicate", "already filed")

        monkeypatch.setattr(dream, "start", refused)
    with settings(store, test_identities={}):
        first, _ = M.run_now(store, auth, "human:ana", wait=True)
        row = steps_by_key(first)["dream"]
        assert row["status"] == "ran" and row["woke"] == int(outcome == "started")
        assert row["details"]["limited"] == [verdict(api, "ops", H.parse_ts(clock[0])).name]
        clock[0] = H.shift(first_at, hours=2)
        second, _ = M.run_now(store, auth, "human:ana", wait=True)
    assert steps_by_key(second)["dream"]["reason"] == "not due"
    assert dream_rows(api, "ops") == []


def test_rehearsal_and_test_servers_skip_the_dream_step(api):
    aged(api)
    worked(api, "ops", 10)
    store, auth = api.app.state.store, api.app.state.auth
    with settings(store, rehearsal=True, test_identities={}):
        assert step_result(store, auth)["reason"] == "rehearsal and test servers start no auto dreams"
    out = step_result(store, auth)  # the api fixture's identities make this one a test server
    assert out["status"] == "skipped" and out["reason"] == "rehearsal and test servers start no auto dreams"
    assert dream_rows(api, "ops") == []


# ---- regressions from the review of 676ac34a0 -------------------------------------------------------------------------


def test_a_bot_no_longer_due_at_its_start_does_not_use_up_a_slot(api, monkeypatch):
    """A run judges every bot, then judges each due one again inside its own start's write transaction. A bot that is no
    longer due by then (a dream landed on it meanwhile) is passed over, and the next due bot takes its slot."""
    aged(api)
    for bot, turns in (("assistant", 30), ("ops", 20), ("cpo", 15)):
        worked(api, bot, turns)
    real = DA.verdict

    def busy_at_its_start(c, bot, at, owner=None):
        if owner is not None and bot == "assistant":
            return DA.Verdict(bot, "assistant", "busy")
        return real(c, bot, at, owner=owner)

    monkeypatch.setattr(DA, "verdict", busy_at_its_start)
    r = {}
    assert run(api, limit=2, report=r) == [verdict(api, "ops", now()).name, verdict(api, "cpo", now()).name]
    assert dream_rows(api, "assistant") == [] and r["held"] == [] and r["over"] == 0, r


def test_a_dream_that_lands_on_a_branch_is_judged_on_the_branch(api):
    """Cara's own dream lands on her branch of cpo and is open there. cpo's night must not file a second dream on that
    branch: the busy check reads the bot the dream lands on, not the original it was judged as."""
    from backend.tests.helpers.shared_bots import branch, share

    share(api)
    branch(api, runner(api, "cara"))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:cara' WHERE bot='cpo'")
    aged(api)
    worked(api, "cpo", 10)
    r = api.post("/api/v2/bots/cpo/dream", json={}, headers=headers("cara-test"))
    assert r.status_code == 200, r.text
    assert len(dream_rows(api, "cpo-cara")) == 1 and dream_rows(api, "cpo") == [], r.json()
    assert run(api) == []
    assert len(dream_rows(api, "cpo-cara")) == 1 and dream_rows(api, "cpo") == []


@pytest.mark.parametrize(
    "failure", [H.Refused("duplicate", "already filed"), RuntimeError("boom")], ids=["refused", "failed"]
)
def test_one_bot_refused_or_failing_holds_only_itself_and_the_night_goes_on(api, monkeypatch, failure):
    aged(api)
    for bot, turns in (("assistant", 30), ("ops", 20), ("cpo", 15)):
        worked(api, bot, turns)
    real = dream.start

    def flaky(c, who, owner, create, reason=None, wake_refs=None):
        if owner == "bot:assistant":
            raise failure
        return real(c, who, owner, create, reason=reason, wake_refs=wake_refs)

    monkeypatch.setattr(dream, "start", flaky)
    r = {}
    assert run(api, report=r) == [verdict(api, "ops", now()).name, verdict(api, "cpo", now()).name]
    assert dream_rows(api, "assistant") == [] and len(dream_rows(api, "ops")) == 1 and len(dream_rows(api, "cpo")) == 1
    assert r["held"] == [verdict(api, "assistant", now()).name] and "1 not queued" in r["reason"], r


def test_a_switch_turned_off_while_a_run_waits_stops_its_dream_step(api, monkeypatch):
    """A run's steps are planned when it executes, not when it is claimed: a switch turned off after the claim counts."""
    aged(api)
    store, auth = api.app.state.store, api.app.state.auth
    worked(api, "ops", 10)
    monkeypatch.setattr(M, "_start", lambda *args, **kwargs: None)  # the run waits, unstarted
    waiting, started = M.run_now(store, auth, "human:ana", step="dream")
    assert started
    with store.transaction() as c:
        M.save_step(c, "human:ana", {"key": "dream", "enabled": False})
    M._execute(store, auth, waiting["id"], "dream")
    assert steps_by_key(M.get_run(store, waiting["id"]))["dream"]["status"] == "off"
    assert dream_rows(api, "ops") == []


def test_need_is_turns_plus_corrections_weighed_five_and_memory_diffs_do_not_count(api):
    aged(api)
    worked(api, "ops", 9)  # 9: quiet
    worked(api, "assistant", 5)
    thumbs_down(api, "assistant")  # 5 + 5
    worked(api, "cpo", 2)
    remember(api, "cpo", 30, attempt="a-run")  # memory written in a run: not counted
    remember(api, "cpo", 3, path="AGENTS.md")  # commits with no known run: not counted either
    at = now()
    assert verdict(api, "ops", at).status == "quiet"
    helper = verdict(api, "assistant", at)
    assert (helper.status, helper.turns, helper.corrections, helper.need) == ("due", 5, 1, 10)
    cpo = verdict(api, "cpo", at)
    assert (cpo.status, cpo.turns, cpo.corrections) == ("quiet", 2, 0)


def test_a_dreams_own_turns_and_work_before_the_last_dream_do_not_count(api):
    aged(api)
    worked(api, "ops", 10)
    assert len(run(api)) == 1
    [first] = dream_rows(api, "ops")
    worked(api, "ops", 20, task=first["id"])  # the dream's own runs
    close(api, "ops")
    v = verdict(api, "ops", now() + timedelta(days=3))
    assert (v.status, v.turns) == ("quiet", 0), "the 10 turns came before the dream, the 20 were the dream"


def test_a_person_changing_a_rule_for_the_bot_is_a_correction(api):
    aged(api)
    worked(api, "ops", 5)
    at = DA.stamp(now() - timedelta(hours=1))
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO org_rules(id,kind,text,who,status,suggested_by,decided_by,created_at,updated_at) "
            "VALUES('r1','internal','Cite the invoice number',?,'active','human:ana','human:ana',?,?)",
            (json.dumps({"bots": ["ops"]}), at, at),
        )
        c.execute(
            "INSERT INTO org_rules(id,kind,text,who,status,suggested_by,created_at,updated_at) "
            "VALUES('r2','internal','A bot suggested this',?,'suggested','bot:ops',?,?)",
            (json.dumps({"bots": ["ops"]}), at, at),
        )
    v = verdict(api, "ops", now())
    assert (v.corrections, v.status) == (1, "due"), "a person's active rule counts; a bot's suggestion does not"


def test_five_in_need_two_waiting_queues_three_others_by_need_warm_on_a_fork(api):
    """Spec acceptance 20: N = 3, five bots in need, two with a dream job waiting."""
    five_due(api)  # assistant 14, ops 13, cpo 12, product-design 11, finance 10
    worked(api, "finance", 5)  # finance 15: first among the three
    _waiting(api, "assistant")
    _waiting(api, "ops")
    assert verdict(api, "assistant", now()).status == "waiting"
    r = {}
    queued = run(api, report=r)
    assert queued == [verdict(api, b, now()).name for b in ("finance", "cpo", "product-design")], "most need first"
    assert r["due"] == 3 and r["over"] == 0, r
    assert not dream_rows(api, "assistant") and not dream_rows(api, "ops"), "a waiting bot gets no second dream"
    with api.app.state.store.read() as c:
        for bot in ("finance", "cpo", "product-design"):
            job = c.execute(
                "SELECT j.held,j.state,j.wake_class,m.refs FROM jobs j JOIN messages m ON m.id=j.message_id "
                "WHERE j.bot_id=? ORDER BY j.created_at DESC LIMIT 1",
                (bot,),
            ).fetchone()
            refs = json.loads(job["refs"])
            assert refs["release"] == {
                "due": None,
                "wait": 48 * 3600,
                "warm": True,
                "deadline": "skip",
                "session": "fork",
            }, refs
            assert refs["release_kind"] == "dream" and job["state"] == "queued", dict(job)


def _waiting(api, bot):
    """A dream job of the bot, held and queued: what an earlier pass left waiting for a warm window."""
    with api.app.state.store.transaction() as c:
        mid, jid = secrets.token_hex(8), secrets.token_hex(8)
        conv = H._pair_conversation(c, "human:ana", "bot:" + bot, "chat")["id"]
        c.execute(
            "INSERT INTO messages(id,conversation_id,sender_id,recipient_id,kind,body,refs,created_at) "
            "VALUES(?,?,?,?,'say','Dream',?,?)",
            (mid, conv, H.KEEPER, "bot:" + bot, json.dumps({"release_kind": "dream"}), H.now()),
        )
        c.execute(
            "INSERT INTO jobs(id,message_id,bot_id,state,created_at,held,wake_class) VALUES(?,?,?,'queued',?,1,'dream')",
            (jid, mid, bot, H.now()),
        )


def test_a_dream_skipped_before_it_ran_is_not_the_last_dream(api):
    """A dream the release hold skips at its deadline is closed without a run: the work it was queued for still counts,
    and the next dream reads from the last dream that ran."""
    aged(api)
    worked(api, "ops", 12, when=now() - timedelta(days=3))
    assert len(run(api)) == 1
    [first] = dream_rows(api, "ops")
    worked(api, "ops", 1, task=first["id"])  # it ran
    close(api, "ops")
    with api.app.state.store.transaction() as c:  # 49 hours on: the limiter's ledger and the first dream are that old
        c.execute("UPDATE tasks SET created_at=? WHERE id=?", (H.shift(H.now(), hours=-49), first["id"]))
        # Finished 48 hours ago, so its end is a different minute from the skip's close below.
        c.execute("UPDATE tasks SET done_at=? WHERE id=?", (H.shift(H.now(), hours=-48), first["id"]))
        c.execute(
            "UPDATE events SET created_at=? WHERE actor=? AND action=?",
            (H.shift(H.now(), hours=-49), H.KEEPER, L.BOT_ACTION),
        )
    with api.app.state.store.read() as c:
        read_from = dream.since(c, owner_of(c, "ops"))
    worked(api, "ops", 12)
    assert len(run(api)) == 1
    with api.app.state.store.transaction() as c:  # skipped at the deadline, as the hold closes it: no run on it
        owner = owner_of(c, "ops")
        row = dream._open(c, [owner])
        H.task_close(c, H.KEEPER, row["id"], "Skipped: no warm window in 48 hours.")
    v = verdict(api, "ops", now())
    assert (v.status, v.turns) == ("due", 12), "the skipped dream's work still counts"
    with api.app.state.store.read() as c:
        assert dream.since(c, owner_of(c, "ops")) == read_from, "reading starts at the last dream that ran"


def test_a_thumbs_down_given_before_the_last_dream_does_not_count_again_when_edited(api):
    aged(api)
    old = DA.stamp(now() - timedelta(days=5))
    edited = DA.stamp(now() - timedelta(hours=1))
    with api.app.state.store.transaction() as c:  # given 5 days ago, its reason edited an hour ago
        c.execute(
            "INSERT INTO feedbacks(id,person_id,subject_type,subject_id,bot_id,rating,created_at,updated_at) "
            "VALUES('f-old','ana','message','m-old','ops','down',?,?)",
            (old, edited),
        )
    with api.app.state.store.read() as c:
        assert DA.work(c, "ops", DA.stamp(now() - timedelta(days=2))) == (0, 0), "given before: not a new correction"
        assert DA.work(c, "ops", DA.stamp(now() - timedelta(days=6))) == (0, 1), "given after: one correction"
