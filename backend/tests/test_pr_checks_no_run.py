"""A head's finished checks start no run when the run would have nothing to do (task_engine._checks_need_no_run), in
two cases only, each said in a "skipped:" log line: the checks passed and the stage then asks for nothing more, so it
moves on by itself; or the checks passed and the owner's last run on the task began after Tico had that result, on the
same head. A failure always wakes, and so does a pass the bot has not seen."""

import json
import logging
from datetime import datetime, timezone

import pytest

from backend import github as G
from backend import task_engine as E
from backend.repositories import metadata, save_metadata
from backend.store import H
from backend.tests.helpers import engine as X

BUILDER = "bot:builder"
HEAD = "a" * 40
START = datetime(2030, 1, 7, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def c(company, time_machine):
    time_machine.move_to(START, tick=False)
    with company.transaction() as conn:
        X.bot(conn, "builder")
        X.bot(conn, "manager")
        yield conn


def _pipeline(c, limit=None):
    stages = (
        {"name": "Build", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
        {"name": "Review 1", "owner": "assignee", "task_limit": limit, "requirements": [{"kind": "owner_done"}]},
    )
    X.pipeline(c, pid="eng", name="eng", stages=stages, manager="bot:manager")


def _task(c, title="Fix the flaky test"):
    """A Build task the Builder owns, its entry wake had its run, and its PR is at HEAD."""
    task_id = X.new_task(c, BUILDER, title, pipeline_id="eng")["id"]
    X.ran(c, task_id, BUILDER)
    c.execute(
        "INSERT INTO task_links (id, task_id, kind, url, repo, number, source, item_type, item_key, detail, created_at) "
        "VALUES (?,?,'pr','https://github.com/acme/app/pull/7','acme/app',7,'github','pr','acme/app#7',?,?)",
        (H.new_id(), task_id, json.dumps({"head_sha": HEAD}), H.now()),
    )
    return task_id


def _queue(c, task_id, result="pass", head=HEAD):
    G.queue_checks_done(c, E.task(c, task_id), head, result, f"Checks finished on acme/app#7: {result}", False)


def _flush(c, task_id):
    key = "github-task-wake:" + task_id
    save_metadata(c, key, {**metadata(c, key), "due": H.shift(H.now(), seconds=-1)})
    G.flush_wakes(c)
    row = c.execute(
        "SELECT m.refs, (SELECT COUNT(*) FROM jobs j WHERE j.message_id = m.id) FROM messages m "
        "WHERE json_extract(m.refs, '$.pr_head') IS NOT NULL AND json_extract(m.refs, '$.task') = ? "
        "ORDER BY m.rowid DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    refs = json.loads(row[0])
    return bool(row[1]), bool(refs.get("quiet"))


def _ran_since(c, task_id, seconds=1, purpose=None):
    """A run of the Builder's on the task began `seconds` after now."""
    at = H.shift(H.now(), seconds=seconds)
    run = H.new_id()
    c.execute(
        "INSERT INTO attempts (id, bot_id, state, created_at, started_at, finished_at, task_id, purpose) "
        "VALUES (?, 'builder', 'completed', ?, ?, ?, ?, ?)",
        (run, at, at, at, task_id, purpose),
    )


def _skips(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("skipped:")]


def test_checks_that_pass_when_the_stage_moves_on_by_itself_wake_no_one(c, caplog):
    _pipeline(c, limit=1)
    X.new_task(c, BUILDER, "Already in review", pipeline_id="eng")
    full = X.new_task(c, BUILDER, "Fills Review 1", pipeline_id="eng")["id"]
    X.say_done(c, E.task(c, full), by=BUILDER)  # Review 1 is full now
    task_id = _task(c)
    X.say_done(c, E.task(c, task_id), by=BUILDER)  # everything Build asks is met; it waits for room
    _queue(c, task_id)
    with caplog.at_level(logging.INFO, logger="tico.task_engine"):
        assert _flush(c, task_id) == (False, True)  # a note for its next run, no run
    assert _skips(caplog) == [
        f"skipped: checks passed and the stage moves on by itself (task {task_id}, bot builder, head {HEAD[:12]})"
    ]


def test_checks_that_pass_on_a_head_the_bots_last_run_already_had_start_no_run(c, caplog):
    _pipeline(c)
    task_id = _task(c)
    _queue(c, task_id)  # Tico has the result now
    _ran_since(c, task_id)  # a run (a person's comment woke it) began after that, on the same head
    with caplog.at_level(logging.INFO, logger="tico.task_engine"):
        assert _flush(c, task_id) == (False, True)
    assert _skips(caplog) == [
        f"skipped: head and checks unchanged since the bot's last run (task {task_id}, bot builder, head {HEAD[:12]})"
    ]


@pytest.mark.parametrize("case", ["failed", "run_before_result", "new_head", "keep_warm_since", "no_run_since"])
def test_a_failure_or_a_pass_the_bot_has_not_seen_wakes_it(c, caplog, case):
    _pipeline(c)
    task_id = _task(c)
    if case == "run_before_result":
        _ran_since(c, task_id, seconds=-30)
    _queue(c, task_id, result="fail" if case == "failed" else "pass", head="b" * 40 if case == "new_head" else HEAD)
    if case in ("failed", "new_head"):
        _ran_since(c, task_id)
    if case == "keep_warm_since":
        _ran_since(c, task_id, purpose="keep warm")  # a keep-warm turn looked at nothing
    with caplog.at_level(logging.INFO, logger="tico.task_engine"):
        assert _flush(c, task_id) == (True, False)
    assert _skips(caplog) == []
