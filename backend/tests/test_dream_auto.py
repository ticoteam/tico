"""Auto dream (backend/dream_auto.py, docs/learnings.md): the nightly run starts a dream for each bot whose memory changed a
lot since its last dream, under the gates; one scheduled run per company night; Run now ignores the clock only; the
requester is the bot's manager, else the owner; a question from the dream reaches the requester without ask ingest.
Synthetic fixtures: the api fixture's bots (backend/tests/test_api.py; Ana owns the company, Ben is an admin). Memory is
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
from backend import system_routines as SR
from backend.scheduler import Scheduler
from backend.store import Problem
from backend.tests.test_api import api, assign, claim, get, headers, post, put, ready, runner  # noqa: F401  (fixtures)
from backend.tests.test_ask_ingest import gate, plate   # noqa: F401  (the ask ingest fixture)
from backend.updates import ZONE

QUESTIONS = "1. Should weekly reports go to Ana or the team? Suggested: the team"
LEARNINGS = "memory/learnings.md"


def now():
    return datetime.now(timezone.utc)


def night(hour=3, days=3):
    """A company night a few days on, at `hour` company time: the bots made today are old enough by then."""
    return (now() + timedelta(days=days)).astimezone(ZONE).replace(hour=hour, minute=0, second=0, microsecond=0)


@contextmanager
def settings(store, **changes):
    """The store with some settings changed for a while. `test_identities={}` makes it a company server's (the scheduled
    night runs on test servers only when the identities are off)."""
    saved = store.settings
    store.settings = replace(saved, **changes)
    try:
        yield store
    finally:
        store.settings = saved


def diff(lines, path=LEARNINGS):
    body = "".join(f"+note {i}\n" for i in range(lines))
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1,{lines} @@\n{body}"


def remember(api, bot, commits, lines=30, path=LEARNINGS, subject="Remember the release check", when=None, attempt=""):
    """`commits` shared memory commits of `lines` changed lines each, as memory_history.record stores them; `attempt` is
    the run that made them (a dream's run, for the exclusion)."""
    when = when or now() - timedelta(hours=2)
    with api.app.state.store.transaction() as c:
        for _ in range(commits):
            c.execute("INSERT INTO bot_memory_updates(bot,sha,subject,author,committed_at,files,diff,truncated,attempt_id,"
                      "shared,runner_id,reported_at) VALUES(?,?,?,?,?,?,?,0,?,1,'test-runner',?)",
                      (bot, secrets.token_hex(20), subject, bot, DA.stamp(when), json.dumps([path]), diff(lines, path),
                       attempt, H.now()))


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
        H.task_update(c, owner, row["id"], status="done", note="Merged the release rules.")


def routine(api, token="ben-test"):
    return {job["key"]: job for job in get(api, "system-routines", token=token)["jobs"]}["auto_dream"]


def run(api, at=None):
    return DA.run(api.app.state.store, api.app.state.auth, at or now())


def test_what_counts_as_memory(api):
    assert DA.counted(LEARNINGS) and DA.counted("AGENT.md") and DA.counted("CLAUDE.md")
    assert not DA.counted("memory/state.md"), "state.md is rewritten every turn: churn, not memory"
    assert not DA.counted("knowledge/notes.md") and not DA.counted("secrets/x")


def test_changed_lines_are_the_hunk_lines_not_the_file_headers(api):
    text = ("diff --git a/memory/learnings.md b/memory/learnings.md\n--- a/memory/learnings.md\n+++ b/memory/learnings.md\n"
            "@@ -1,2 +1,2 @@\n-old\n+new\n keep\n+++ not a header\n"
            "diff --git a/memory/state.md b/memory/state.md\n--- a/memory/state.md\n+++ b/memory/state.md\n@@ -1 +1 @@\n-a\n+b\n")
    assert DA.changed_lines(text) == {"memory/learnings.md": 3, "memory/state.md": 2}


def test_a_bot_that_never_dreamed_must_be_48_hours_old_and_an_inactive_one_is_not_due(api):
    remember(api, "ops", 10)
    assert verdict(api, "ops", now()).status == "new"
    assert verdict(api, "ops", now() + timedelta(days=3)).status == "due"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE id='ops'")
    assert verdict(api, "ops", now() + timedelta(days=3)).status == "inactive"


def test_the_thresholds_are_200_changed_lines_and_10_memory_turns(api):
    aged(api)
    remember(api, "ops", 10, lines=19)      # 190 lines in 10 turns
    remember(api, "coo", 9, lines=30)       # 270 lines in 9 turns
    remember(api, "cpo", 10, lines=20)      # exactly 200 lines in 10 turns
    at = now()
    assert verdict(api, "ops", at).status == "quiet"
    assert verdict(api, "coo", at).status == "quiet"
    cpo = verdict(api, "cpo", at)
    assert (cpo.status, cpo.lines, cpo.turns) == ("due", 200, 10)


def test_state_churn_and_the_dreams_own_commits_are_not_memory(api):
    aged(api)
    remember(api, "ops", 20, lines=50, path="memory/state.md")                   # 1000 lines of churn
    remember(api, "ops", 1, lines=500, subject="Dream: merged report rules")     # the dream's own commit
    v = verdict(api, "ops", now())
    assert (v.status, v.lines, v.turns) == ("quiet", 0, 0)


def test_memory_reported_by_the_bots_computer_counts_and_its_dream_is_for_the_manager(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    committed = (now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    commits = [{"sha": f"{n:040x}", "subject": "Remember the release check", "author": "ops", "committed": committed,
                "files": [LEARNINGS], "diff": diff(30), "shared": True} for n in range(1, 11)]
    assert post(api, "bots/ops/memory/report", {"commits": commits}, machine["token"])["recorded"] == 10
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='ops'")
    aged(api)

    started = run(api)
    assert started == [verdict(api, "ops", now()).name]
    [row] = dream_rows(api, "ops")
    assert row["requester_id"] == "human:ben" and H.task_labels(row) == ["dream"] and row["title"] == "Dream"
    first_line, _, _ = row["body"].partition("\n")
    assert first_line.startswith("Auto dream: 300 memory lines changed in 10 turns since ")
    with api.app.state.store.read() as c:
        assert dream.auto_event(c, row)["reason"] == first_line
    view = get(api, "bots/ops/dream", token="ben-test")["dream"]
    assert view["auto"] is True and view["reason"] == first_line
    assert run(api) == [], "one open dream: nothing more for the bot"


def test_a_bot_gets_one_dream_per_48_hours_and_none_while_one_is_open(api):
    aged(api)
    remember(api, "ops", 10)
    assert len(run(api)) == 1
    [first] = dream_rows(api, "ops")
    remember(api, "ops", 10, when=now() + timedelta(seconds=1))      # new memory after the start
    assert verdict(api, "ops", now() + timedelta(days=2)).status == "busy", "an open dream blocks a new one"
    close(api, "ops")
    assert verdict(api, "ops", now() + timedelta(hours=1)).status == "paced", "48 hours since the last dream's start"
    later = now() + timedelta(hours=49)
    assert verdict(api, "ops", later).status == "due"
    assert len(run(api, later)) == 1
    assert [d["id"] for d in dream_rows(api, "ops")][-1] == first["id"]
    assert len(dream_rows(api, "ops")) == 2


def test_the_nightly_cap_starts_three_most_changed_first_and_says_what_it_held(api):
    aged(api)
    for bot, lines in (("finance", 50), ("product-design", 60), ("cpo", 70), ("ops", 80), ("coo", 90)):
        remember(api, bot, 10, lines=lines)        # 500, 600, 700, 800, 900 lines in 10 turns
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='ops'")
    started = run(api)
    assert started == [verdict(api, b, now()).name for b in ("coo", "ops", "cpo")], "most changed lines first"
    assert [b for b in ("coo", "ops", "cpo") if dream_rows(api, b)] == ["coo", "ops", "cpo"]
    assert not dream_rows(api, "product-design") and not dream_rows(api, "finance")
    assert dream_rows(api, "coo")[0]["requester_id"] == "human:ana", "no manager: the company owner"
    assert dream_rows(api, "ops")[0]["requester_id"] == "human:ben", "the nearest human manager"
    result = routine(api)["last"]["result"]
    assert "Started 3 dreams" in result and "2 due past the 3 a night" in result and "2 not due" in result, result


def test_an_auto_dreams_question_reaches_its_manager_without_ask_ingest(gate):
    api, model, token = gate
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='ops'")
    aged(api)
    remember(api, "ops", 10)
    plate(api, 15)                                 # a plate full enough that ingest would send a question back
    assert len(run(api)) == 1
    [row] = dream_rows(api, "ops")
    r = api.post(f"/api/v2/tasks/{row['id']}/ask", json={"text": QUESTIONS}, headers=headers(token))
    assert r.status_code == 200, r.text
    assert "ingest" not in r.json() and model.calls == []
    with api.app.state.store.read() as c:
        assert [a["recipient_id"] for a in H.open_task_asks(c, H.task(c, row["id"]))] == ["human:ben"]


def test_the_scheduled_night_runs_once_and_a_restart_does_not_run_it_again(api):
    aged(api)
    store, auth = api.app.state.store, api.app.state.auth
    remember(api, "ops", 10)
    at = night()
    with settings(store, test_identities={}):
        assert len(DA.nightly(store, auth, at)) == 1
        assert DA.nightly(store, auth, at + timedelta(minutes=1)) is None, "this process has seen the night claimed"
        with store.read() as c:
            assert DA.claimed(c, DA.night_of(at))
        # A restart: this process forgets the night, the claim does not. The bot is due again by every gate.
        close(api, "ops")
        remember(api, "ops", 10, when=now() + timedelta(seconds=1))
        store.__dict__.pop("auto_dream_night", None)
        assert DA.nightly(store, auth, at + timedelta(minutes=2)) is None
    assert len(dream_rows(api, "ops")) == 1
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM registry_metadata WHERE key=?",
                         (DA.CLAIM + DA.night_of(at),)).fetchone()[0] == 1


def test_before_the_routine_time_nothing_runs_and_nothing_is_claimed(api):
    aged(api)
    store, auth = api.app.state.store, api.app.state.auth
    remember(api, "ops", 10)
    with settings(store, test_identities={}):
        assert DA.nightly(store, auth, night(hour=1)) is None
    with store.read() as c:
        assert not DA.claimed(c, DA.night_of(night(hour=1)))
    assert dream_rows(api, "ops") == []


def test_switched_off_nothing_starts_at_night_or_by_run_now(api):
    aged(api)
    store, auth = api.app.state.store, api.app.state.auth
    remember(api, "ops", 10)
    put(api, "system-routines/auto_dream", {"on": False}, token="ben-test")
    assert routine(api)["on"] is False and routine(api)["next"] is None
    with settings(store, test_identities={}):
        assert DA.nightly(store, auth, night()) is None
    with store.read() as c:
        assert not DA.claimed(c, DA.night_of(night()))
    post(api, "system-routines/auto_dream/run", {}, token="ben-test", expected=409)
    assert dream_rows(api, "ops") == []
    put(api, "system-routines/auto_dream", {"on": True}, token="ben-test")
    assert routine(api)["on"] is True


def test_run_now_ignores_the_clock_but_keeps_the_gates_and_claims_no_night(api):
    aged(api)
    remember(api, "ops", 10)                      # due
    remember(api, "coo", 10, lines=19)            # 190 lines: not due
    post(api, "system-routines/auto_dream/run", {}, token="ben-test")
    assert [d["id"] for d in dream_rows(api, "ops")] and dream_rows(api, "coo") == []
    job = routine(api)
    assert job["last"]["ok"] is True and job["last"]["result"].startswith("Started 1 dream (")
    with api.app.state.store.read() as c:
        assert not DA.claimed(c, DA.night_of(now())), "Run now claims no company night"
    with settings(api.app.state.store, rehearsal=True):
        with pytest.raises(Problem) as refused:
            DA.run_now(api.app.state.store, api.app.state.auth)
        assert refused.value.status == 409


def test_the_scheduler_tick_hands_its_time_to_the_auto_dream_night(api, monkeypatch):
    seen = []
    monkeypatch.setattr(DA, "nightly", lambda store, auth, at: seen.append(at))
    at = now()
    Scheduler(api.app.state.store, api.app.state.execution).tick(at)
    assert seen == [at]


def test_a_store_with_no_app_has_no_night_and_its_tick_does_not_fail(tmp_path):
    """A bare Scheduler (no app installed the dream route) at 02:30 skips the night quietly and claims nothing."""
    from backend.auth import Auth
    from backend.execution import Execution
    from backend.store import Store
    from backend.config import Settings
    s = Store(Settings(db_path=tmp_path / "bare.sqlite"))
    s.initialize(seed_market=False)
    assert DA.nightly(s, Auth(s), night()) is None
    with s.read() as c:
        assert not DA.claimed(c, DA.night_of(night()))
    assert not [f for f in Scheduler(s, Execution(s, Auth(s))).tick(night())["failures"] if "auto_dream" in f]


# ---- regressions from the review of 676ac34a0 -------------------------------------------------------------------------

def test_an_overlapping_run_cannot_start_past_the_cap(api, monkeypatch):
    """The cap is counted inside each start's write transaction. The first count here is stale, as a run that read it
    before another run's writes would have it: with two dreams already started tonight, one more starts, not three."""
    aged(api)
    store = api.app.state.store
    for bot, lines in (("coo", 900), ("ops", 800), ("cpo", 700)):
        remember(api, bot, 10, lines=lines // 10)
    with store.transaction() as c:
        for n in (1, 2):
            H.event(c, H.KEEPER, dream.AUTO_MARK, f"seed-{n}", {})
    real = DA._started_tonight
    reads = []

    def stale_first(c, at):
        n = real(c, at)
        reads.append(n)
        return 0 if len(reads) == 1 else n

    monkeypatch.setattr(DA, "_started_tonight", stale_first)
    assert run(api) == [verdict(api, "coo", now()).name]
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM events WHERE actor=? AND action=?",
                         (H.KEEPER, dream.AUTO_MARK)).fetchone()[0] == DA.NIGHTLY_MAX


def test_a_dream_that_lands_on_a_branch_is_judged_on_the_branch(api):
    """Cara's own dream lands on her branch of cpo and is open there. cpo's night must not file a second dream on that
    branch: the busy check reads the bot the dream lands on, not the original it was judged as."""
    from backend.tests.test_shared_bots import branch, share
    share(api)
    branch(api, runner(api, "cara"))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:cara' WHERE bot='cpo'")
    aged(api)
    remember(api, "cpo", 10)
    r = api.post("/api/v2/bots/cpo/dream", json={}, headers=headers("cara-test"))
    assert r.status_code == 200, r.text
    assert len(dream_rows(api, "cpo-cara")) == 1 and dream_rows(api, "cpo") == [], r.json()
    assert run(api) == []
    assert len(dream_rows(api, "cpo-cara")) == 1 and dream_rows(api, "cpo") == []


@pytest.mark.parametrize("failure", [H.Refused("duplicate", "already filed"), RuntimeError("boom")],
                         ids=["refused", "failed"])
def test_one_bot_refused_or_failing_holds_only_itself_and_the_night_goes_on(api, monkeypatch, failure):
    aged(api)
    for bot, lines in (("coo", 900), ("ops", 800), ("cpo", 700)):
        remember(api, bot, 10, lines=lines // 10)
    real = dream.start

    def flaky(c, who, owner, create, reason=None):
        if owner == "bot:coo":
            raise failure
        return real(c, who, owner, create, reason=reason)

    monkeypatch.setattr(dream, "start", flaky)
    assert run(api) == [verdict(api, "ops", now()).name, verdict(api, "cpo", now()).name]
    assert dream_rows(api, "coo") == [] and len(dream_rows(api, "ops")) == 1 and len(dream_rows(api, "cpo")) == 1
    assert "1 not started" in routine(api)["last"]["result"]


def test_switched_off_between_the_check_and_the_claim_claims_nothing(api, monkeypatch):
    aged(api)
    store, auth = api.app.state.store, api.app.state.auth
    remember(api, "ops", 10)
    put(api, "system-routines/auto_dream", {"on": False}, token="ben-test")
    real = SR.config_of
    reads = []

    def first_read_still_on(c, key):
        cfg = real(c, key)
        reads.append(1)
        return {**cfg, "on": True} if len(reads) == 1 else cfg      # the tick's read, before the switch

    monkeypatch.setattr(SR, "config_of", first_read_still_on)
    with settings(store, test_identities={}):
        assert DA.nightly(store, auth, night()) is None
    with store.read() as c:
        assert not DA.claimed(c, DA.night_of(night()))
    assert dream_rows(api, "ops") == []


def test_a_bot_with_no_memory_since_is_judged_without_any_run_lookup(api, monkeypatch):
    def looked_up(*args):
        raise AssertionError("no commit since: nothing to look up")

    monkeypatch.setattr(DA, "_own_runs", looked_up)
    monkeypatch.setattr(DA, "_tidy_runs", looked_up)
    aged(api)
    assert verdict(api, "ops", now()).status == "quiet"


def test_a_dreams_own_run_is_not_memory_through_the_one_query(api):
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    aged(api)
    api.post("/api/v2/bots/ops/dream", json={}, headers=headers("ben-test"))
    first = claim(api, machine, "ops")
    remember(api, "ops", 1, lines=500, attempt=first["id"])        # the dream's own run: not memory
    remember(api, "ops", 1, lines=7)                              # an ordinary commit: counted
    with api.app.state.store.read() as c:
        assert first["id"] in DA._own_runs(c, "bot:ops")
        assert DA.change(c, "ops", DA.stamp(now() - timedelta(days=1))) == (7, 1)


def test_paths_with_spaces_and_deletions_are_read_whole(api):
    text = ("diff --git a/memory/meeting notes.md b/memory/meeting notes.md\nindex 1..2 100644\n"
            "--- a/memory/meeting notes.md\t\n+++ b/memory/meeting notes.md\t\n@@ -1,2 +1,2 @@\n-old\n+new\n keep\n"
            "diff --git a/memory/gone file.md b/memory/gone file.md\ndeleted file mode 100644\n"
            "--- a/memory/gone file.md\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n")
    counts = DA.changed_lines(text)
    assert counts == {"memory/meeting notes.md": 2, "memory/gone file.md": 2}
    assert all(DA.counted(path) for path in counts)
