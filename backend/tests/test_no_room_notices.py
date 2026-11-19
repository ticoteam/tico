"""No "has room" notices (Spec: "Spec has room: move it, don't send a notice", task #224). When the stage after a
by-hand stage has room, the never-stuck pass moves the oldest idea a person picked and tells nobody about room; an idea
nobody picked stays where it is.

Synthetic rows only (backend/tests/helpers/engine.py): a fictional company, its bots and pipelines. Never a model.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import never_stuck as N
from backend import task_engine as E
from backend.store import H
from backend.tests.helpers import engine as X

ANA = X.ANA
WRITER, BUILDER, WATCHER, AREA = "bot:writer", "bot:builder", "bot:watcher", "bot:area-owner"
START = datetime(2030, 1, 7, 9, 0, tzinfo=timezone.utc)


def _work(owner, limit=1):
    return {"name": "Next", "owner": owner, "task_limit": limit, "requirements": [{"kind": "owner_done"}]}


# Every kind of by-hand stage with a limited stage after it: Product's Ideas (a fixed owner, a person's request moves
# on), Product's Ideas pointed at the assignee, Growth's Ideas (the assignee, nothing moves on) and a stage owned by
# one fixed bot (nothing moves on).
PIPELINES = {
    "product": ({"name": "Ideas", "owner": WRITER, "by_hand": True, "requests_move_on": True}, _work(WRITER)),
    "areas": ({"name": "Ideas", "owner": "assignee", "by_hand": True, "requests_move_on": True}, _work(WRITER)),
    "growth": ({"name": "Ideas", "owner": "assignee", "by_hand": True}, _work(BUILDER)),
    "watch": ({"name": "Watching", "owner": WATCHER, "by_hand": True}, _work(WATCHER)),
}


@pytest.fixture
def c(company, time_machine):
    time_machine.move_to(START, tick=False)
    with company.transaction() as conn:
        for slug in ("writer", "builder", "watcher", "area-owner", "manager"):
            X.bot(conn, slug)
        for pid, stages in PIPELINES.items():
            X.pipeline(conn, pid, pid.title(), stages, manager="bot:manager")
        yield conn


def _room_messages(c):
    """Every message about room: a "room:" wake key, or the notice's words."""
    return c.execute(
        "SELECT wake_key, recipient_id, body FROM messages "
        "WHERE (wake_key >= 'room:' AND wake_key < 'room;') OR body LIKE '%has room.%'"
    ).fetchall()


def _messages(c):
    return {row[0] for row in c.execute("SELECT id FROM messages")}


def _pass(c):
    done = N.check(c)
    assert N.rules_hold(c) == []
    return done


def _stage(c, row):
    return E.task(c, row["id"])["stage_id"]


def test_no_by_hand_stage_in_any_pipeline_gets_a_room_message_over_an_hour(c, time_machine):
    """Given a bot's own idea in each kind of by-hand stage and room after it, when the pass runs every 2 minutes for
    an hour while slots fill and free, then no message about room exists and every bot idea stays where it is."""
    ideas = {
        "product": X.new_task(c, WRITER, "Brief", "product"),
        "areas": X.new_task(c, WRITER, "Area brief", "areas", assignee=AREA),
        "growth": X.new_task(c, BUILDER, "Try a referral bonus", "growth"),
        "watch": X.new_task(c, WATCHER, "Weekly signups", "watch"),
    }
    X.settled(c)
    for minute in range(0, 61, 2):
        if minute in (10, 30):  # fill each next stage
            filled = {pid: X.new_task(c, ANA, f"Moved by hand {pid} {minute}", pid) for pid in PIPELINES}
            for pid, row in filled.items():
                E.move(c, row["id"], f"{pid}-next", ANA, "Picked")
        if minute in (20, 40):  # free each slot
            for (task_id,) in c.execute("SELECT id FROM tasks WHERE stage_id LIKE '%-next' AND status='open'"):
                E.move(c, task_id, E.CLOSED, ANA, "Not needed")
        time_machine.move_to(START + timedelta(minutes=minute), tick=False)
        _pass(c)
        assert _room_messages(c) == [], minute
    for pid, row in ideas.items():
        assert _stage(c, row) == E.task(c, row["id"])["pipeline"]["id"] + ("-watching" if pid == "watch" else "-ideas")


def test_a_bots_idea_stays_in_ideas_and_nobody_is_told(c):
    """Given a bot's own idea in Ideas and Spec has room, when the pass runs, then it stays in Ideas and no message
    is written."""
    idea = X.new_task(c, WRITER, "Brief", "product")
    X.settled(c)
    before = _messages(c)
    _pass(c)
    _pass(c)
    assert _stage(c, idea) == "product-ideas"
    assert _messages(c) == before


def test_a_persons_idea_moves_in_and_nobody_is_told_about_room(c):
    """Given room in Spec and a person's idea waiting in Ideas, when the pass runs, then it is in Spec and the only
    message written is its owner's turn there."""
    request = X.new_task(c, ANA, "Export to CSV", "product", assignee=WRITER)
    X.settled(c)
    before = _messages(c)
    assert _pass(c)["moved"] == 1
    assert _stage(c, request) == "product-next"
    new = c.execute(
        "SELECT wake_key, recipient_id FROM messages WHERE id IN (SELECT value FROM json_each(?))",
        (json.dumps(sorted(_messages(c) - before)),),
    ).fetchall()
    assert new and all(key.startswith(request["id"] + ":") for key, _ in new), new
    assert _room_messages(c) == []


def test_when_one_slot_frees_the_older_of_two_requests_moves_and_nothing_else_is_written(c, time_machine):
    """Given Spec is full and two people's ideas wait, when one slot frees, then the older one moves and nothing is
    written but its owner's turn in Spec."""
    holder = X.new_task(c, ANA, "Holder", "product", assignee=WRITER)
    _pass(c)
    assert _stage(c, holder) == "product-next"
    older = X.new_task(c, ANA, "Older", "product", assignee=WRITER)
    time_machine.shift(timedelta(minutes=1))
    newer = X.new_task(c, ANA, "Newer", "product", assignee=WRITER)
    idea = X.new_task(c, WRITER, "Brief", "product")
    _pass(c)
    assert (_stage(c, older), _stage(c, newer)) == ("product-ideas", "product-ideas")
    E.move(c, holder["id"], E.CLOSED, ANA, "Done elsewhere")
    X.settled(c)
    before = _messages(c)
    assert _pass(c)["moved"] == 1
    assert (_stage(c, older), _stage(c, newer), _stage(c, idea)) == ("product-next", "product-ideas", "product-ideas")
    new = json.dumps(sorted(_messages(c) - before))
    keys = [
        row[0]
        for row in c.execute("SELECT wake_key FROM messages WHERE id IN (SELECT value FROM json_each(?))", (new,))
    ]
    assert keys and all(key.startswith(older["id"] + ":") for key in keys), keys
