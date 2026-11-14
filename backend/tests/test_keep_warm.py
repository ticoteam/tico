"""Keep active tasks warm (backend/keep_warm.py): a task session in an active stage that has been idle 45 to 55 minutes
gets one short turn that reads its cache, for up to the stage's or pipeline's hours after its last real work. The turn
is marked, starts without a turn on the task, and is never a run on the task, work, or a run that did nothing."""

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from backend import bot_settings as BS
from backend import keep_warm as K
from backend import pipeline_settings as PS
from backend import task_engine as E
from backend import task_wakes
from backend.store import H
from backend.tests.helpers import engine as X

BUILDER, REVIEWER = "bot:builder", "bot:reviewer"
START = datetime(2030, 1, 7, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def c(company, time_machine):
    time_machine.move_to(START, tick=False)
    with company.transaction() as conn:
        for slug in ("builder", "reviewer", "manager"):
            X.bot(conn, slug)
        # As Engineering: the Builder is the task's assignee, Review 1 is the Reviewer's, Fixes 1 the assignee's again.
        stages = (
            {"name": "Build", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
            {"name": "Review 1", "owner": REVIEWER, "requirements": [{"kind": "owner_done"}]},
            {"name": "Fixes 1", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
        )
        X.pipeline(conn, pid="eng", name="Engineering", stages=stages, manager="bot:manager")
        yield conn


def _ran(c, task_id, bot="builder", at=None, purpose=None, result=None, tokens=50_000):
    """A run of `bot` on the task that reached the model and ended at `at`."""
    at = at or H.now()
    run = H.new_id()
    c.execute(
        "INSERT INTO attempts (id, bot_id, state, created_at, started_at, finished_at, task_id, purpose, result, "
        "cached_tokens) VALUES (?,?,'completed',?,?,?,?,?,?,?)",
        (run, bot, at, at, at, task_id, purpose, json.dumps(result) if result else None, tokens),
    )
    return run


def _in_review(c):
    """The Builder built it (its run ended now) and it moved on to Review 1, where it waits on the Reviewer."""
    task_id = X.new_task(c, X.ANA, "Add the export button", pipeline_id="eng", assignee=BUILDER)["id"]
    c.execute("DELETE FROM jobs")
    _ran(c, task_id)
    X.say_done(c, E.task(c, task_id), by=BUILDER)
    E.advance(c, task_id)
    assert E.task(c, task_id)["stage"]["name"] == "Review 1"
    c.execute("DELETE FROM jobs")
    return task_id


def _at(minutes):
    return H.shift(START.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), minutes=minutes)


def _sweep(c, minutes):
    return K.sweep(c, _at(minutes), every_minute=False)


def _pings(c, task_id):
    return [
        (m["recipient_id"], json.loads(m["refs"]))
        for m in c.execute(
            "SELECT recipient_id, refs FROM messages WHERE json_extract(refs, '$.wake') = 'keep_warm' "
            "AND json_extract(refs, '$.task') = ? ORDER BY rowid",
            (task_id,),
        )
    ]


def _queued(c, task_id):
    return c.execute(
        "SELECT j.id, j.message_id FROM jobs j JOIN messages m ON m.id = j.message_id WHERE j.state='queued' "
        "AND json_extract(m.refs, '$.wake') = 'keep_warm' AND json_extract(m.refs, '$.task') = ?",
        (task_id,),
    ).fetchall()


def test_a_session_idle_in_the_warm_window_gets_one_marked_turn_in_its_tasks_conversation(c, caplog):
    task_id = _in_review(c)
    assert _sweep(c, 44) == 0  # not idle long enough
    with caplog.at_level(logging.INFO, logger="tico.keep_warm"):
        assert _sweep(c, 46) == 1
    [(to, refs)] = _pings(c, task_id)
    assert to == BUILDER and refs["trigger"] == "keep_warm" and refs["purpose"] == "keep warm"
    row = c.execute(
        "SELECT body, conversation_id FROM messages WHERE json_extract(refs, '$.wake') = 'keep_warm'"
    ).fetchone()
    assert row[0] == K.PING and row[1] == E.task(c, task_id)["conversation_id"]
    assert len(_queued(c, task_id)) == 1
    assert _sweep(c, 47) == 0  # its turn is queued: not a second
    assert any(r.getMessage().startswith("keep warm: sent (task ") for r in caplog.records)


def test_a_session_past_the_window_is_cold_already_and_gets_nothing(c):
    task_id = _in_review(c)
    assert _sweep(c, 56) == 0 and _pings(c, task_id) == []


def test_the_turn_starts_without_a_turn_on_the_task_and_is_no_run_on_it(c):
    task_id = _in_review(c)
    built = task_wakes.last_run(c, E.task(c, task_id), BUILDER)
    assert _sweep(c, 46) == 1
    [(_, message_id)] = _queued(c, task_id)
    assert E.task(c, task_id)["owner_id"] == REVIEWER  # Review 1 is the Reviewer's turn, not the Builder's
    assert task_wakes.may_start(c, message_id) is True
    before = task_wakes.runs_since(c, E.task(c, task_id))
    run = _ran(c, task_id, at=_at(47), purpose="keep warm")
    c.execute("UPDATE attempts SET message_id=?, writes=0 WHERE id=?", (message_id, run))
    assert task_wakes.runs_since(c, E.task(c, task_id)) == before  # not one of the 7, not a try
    assert task_wakes.last_run(c, E.task(c, task_id), BUILDER) == built  # nor when the Builder last stopped
    H.turn_finish(c, "keeper", run)
    outcome, did_nothing = c.execute("SELECT outcome, did_nothing FROM attempts WHERE id=?", (run,)).fetchone()
    assert (outcome, did_nothing) == (None, None)  # neither work nor a run that did nothing


def test_a_session_kept_warm_comes_round_again_from_its_last_turn_until_the_hours_run_out(c):
    task_id = _in_review(c)
    PS.save(c, X.ANA, {**PS.read(c, "eng"), "keep_warm_hours": 2})
    minute = 46
    while minute < 4 * 60:
        if _sweep(c, minute):
            for job_id, _ in _queued(c, task_id):
                c.execute("UPDATE jobs SET state='completed' WHERE id=?", (job_id,))
            _ran(c, task_id, at=_at(minute + 1), purpose="keep warm")
        minute += 1
    sent = len(_pings(c, task_id))
    assert sent == 2  # at about 46 and 93 minutes; by 140 its last work is over 2 hours ago


WHY = {
    "closed": "task ended",
    "by_hand": "not in an active stage",
    "stage_off": "not in an active stage",
    "pipeline_off": "not in an active stage",
    "fallback": "on a fallback model",
    "fallback_newest": "on a fallback model",
    "queued": "a run is queued or going",
    "on_ice": "snoozed with no end",
    "goal": "worked as a goal",
    "nothing_warm": "no warm session on its computer",
}


@pytest.mark.parametrize("case", sorted(WHY))
def test_it_stops_when_the_task_or_session_is_no_longer_one_to_keep(c, case, caplog):
    task_id = _in_review(c)
    row = E.task(c, task_id)
    if case == "closed":
        H.task_close(c, X.ANA, task_id, "Not needed", E.task(c, task_id)["version"])
    elif case == "by_hand":
        c.execute("UPDATE stages SET by_hand=1 WHERE id=?", (row["stage"]["id"],))
    elif case == "stage_off":
        c.execute("INSERT INTO stage_keep_warm (stage_id, hours) VALUES (?, 0)", (row["stage"]["id"],))
    elif case == "pipeline_off":
        c.execute("UPDATE pipelines SET keep_warm_hours=0 WHERE id='eng'")
    elif case == "fallback":
        c.execute("UPDATE attempts SET result=? WHERE task_id=?", (json.dumps({"fallback": "codex"}), task_id))
    elif case == "fallback_newest":
        _ran(c, task_id, at=_at(1), purpose="keep warm", result={"fallback": "codex"})
    elif case == "queued":  # the Builder has a wake on the task queued already
        H._write_message(
            c,
            H.KEEPER,
            BUILDER,
            "A person's note",
            H.conversation(c, row["conversation_id"]),
            "notice",
            {"task": task_id, "wake": "task_turn"},
            None,
            None,
        )
    elif case == "on_ice":
        E.snooze(c, task_id, X.ANA, "On ice")
    elif case == "nothing_warm":  # the last keep-warm turn ended on its computer without a model (0 tokens)
        _ran(c, task_id, at=_at(1), purpose="keep warm", tokens=0)
    elif case == "goal":  # the Reviewer's own session, on a task it works as a goal
        c.execute("DELETE FROM attempts WHERE task_id=?", (task_id,))
        _ran(c, task_id, bot="reviewer")
        BS.update(c, "reviewer", {"task_goal": "on"})
    with caplog.at_level(logging.DEBUG, logger="tico.keep_warm"):
        assert _sweep(c, 46) == 0
    assert _pings(c, task_id) == []
    bot = "reviewer" if case == "goal" else "builder"
    said = [r.getMessage() for r in caplog.records if r.getMessage().startswith("skipped:")]
    assert said == [f"skipped: {WHY[case]} (keep warm, task {task_id}, bot {bot})"]


def test_only_a_session_whose_bot_has_the_stage_or_one_ahead_is_kept(c, caplog):
    """In Review 1 the Builder is kept warm: Fixes 1 is ahead of it, on the assignee. Once the task is in Fixes 1 the
    Reviewer's session is not: Review 1 is behind the task."""
    task_id = _in_review(c)
    _ran(c, task_id, bot="reviewer")  # the review
    X.say_done(c, E.task(c, task_id), by=REVIEWER)
    E.advance(c, task_id)
    assert E.task(c, task_id)["stage"]["name"] == "Fixes 1"
    c.execute("DELETE FROM jobs")
    _ran(c, task_id)  # the Builder's fixes began; its session is the current stage's
    with caplog.at_level(logging.DEBUG, logger="tico.keep_warm"):
        assert _sweep(c, 46) == 1
    assert [to for to, _ in _pings(c, task_id)] == [BUILDER]
    said = [r.getMessage() for r in caplog.records if r.getMessage().startswith("skipped:")]
    assert said == [f"skipped: its stage is behind the task (keep warm, task {task_id}, bot reviewer)"]


def test_the_notice_is_in_no_timeline_and_no_later_turns_history(c):
    """Nobody sees a keep-warm turn: its notice has no seq, so the task's thread, its unread counts and the next turn's
    room history leave it out."""
    task_id = _in_review(c)
    conversation = E.task(c, task_id)["conversation_id"]
    counters = "SELECT last_seq, last_unread_seq FROM conversations WHERE id=?"
    before = tuple(c.execute(counters, (conversation,)).fetchone())
    assert _sweep(c, 46) == 1
    assert tuple(c.execute(counters, (conversation,)).fetchone()) == before  # nothing new to read, nothing unread
    row = c.execute("SELECT seq, restricted FROM messages WHERE json_extract(refs, '$.wake') = 'keep_warm'").fetchone()
    assert row[0] is None and row[1] == 1
    shown = c.execute(
        "SELECT COUNT(*) FROM messages WHERE conversation_id=? AND seq IS NOT NULL AND body=?",
        (E.task(c, task_id)["conversation_id"], K.PING),
    ).fetchone()[0]
    assert shown == 0


def test_a_stage_of_its_own_overrides_the_pipeline(c):
    task_id = _in_review(c)
    c.execute("UPDATE pipelines SET keep_warm_hours=0 WHERE id='eng'")
    c.execute("INSERT INTO stage_keep_warm (stage_id, hours) VALUES (?, 3)", (E.task(c, task_id)["stage"]["id"],))
    assert K.hours(c, E.task(c, task_id)) == 3
    assert _sweep(c, 46) == 1


def test_a_queued_turn_is_dropped_at_its_claim_when_the_task_left_or_real_work_is_queued(c):
    task_id = _in_review(c)
    assert _sweep(c, 46) == 1
    [(job_id, message_id)] = _queued(c, task_id)
    H.task_close(c, X.ANA, task_id, "Not needed", E.task(c, task_id)["version"])
    assert task_wakes.may_start(c, message_id) is False
    assert c.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "canceled"


def test_the_release_policy_tick_sends_it_once_a_minute(c):
    from backend import wake_presets as WP

    task_id = _in_review(c)
    WP.tick(c, _at(46))
    assert len(_pings(c, task_id)) == 1
    c.execute("UPDATE jobs SET state='canceled'")  # as if it ran: the same minute does not look again
    WP.tick(c, _at(46))
    assert len(_pings(c, task_id)) == 1


def test_the_window_is_the_release_policys():
    from backend import wake_presets as WP

    assert (WP.WARM_AFTER, WP.WARM_BEFORE) == (45 * 60, 55 * 60)
    assert K.LOOK_BACK_S > WP.WARM_BEFORE
    assert timedelta(seconds=WP.WARM_BEFORE) < timedelta(hours=1)


def test_a_computer_claims_the_turn_in_the_tasks_own_session_marked_keep_warm(api):
    """End to end through the claim: the turn names its task (so the runner resumes `task:<id>`), runs though Review 1
    is not its bot's turn, and its run carries the trigger and purpose usage reports group by."""
    from backend.tests.helpers.wake_rules import claim, setup

    r = setup(api)
    with api.app.state.store.transaction() as c:
        stages = (
            {"name": "Build", "owner": "bot:cpo", "requirements": [{"kind": "owner_done"}]},
            {"name": "Review 1", "owner": "bot:ops", "requirements": [{"kind": "owner_done"}]},
            {"name": "Fixes 1", "owner": "bot:cpo", "requirements": [{"kind": "owner_done"}]},
        )
        X.pipeline(c, pid="warm", name="Warm", stages=stages)
        task_id = X.new_task(c, "bot:cpo", "Add the export button", pipeline_id="warm")["id"]
        X.say_done(c, E.task(c, task_id), by="bot:cpo")
        E.advance(c, task_id)
        c.execute("DELETE FROM jobs")
        _ran(c, task_id, bot="cpo", at=H.shift(H.now(), minutes=-46))
        assert K.sweep(c, every_minute=False) == 1
    attempt = claim(api, r, "cpo")
    assert attempt["task"]["id"] == task_id and attempt["message"]["body"] == K.PING
    with api.app.state.store.read() as c:
        row = c.execute("SELECT trigger_kind, purpose, task_id FROM attempts WHERE id=?", (attempt["id"],)).fetchone()
    assert tuple(row) == ("keep_warm", "keep warm", task_id)
