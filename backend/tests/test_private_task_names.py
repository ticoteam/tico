"""A task a reader may not open is never named to them: not by title, number (#40) or id (task_calls.may_name). Each
line that names another task (what a task merges after, what it depends on) reads "a private task" instead, in a
wake, a note kept in the task's thread, a refusal, a standing and the task's own read; a task they may open is named
as before."""

import json

import pytest

from backend import merge_order as MO
from backend import task_calls as TC
from backend import task_depends as D
from backend import task_engine as E
from backend import task_relations as TR
from backend import task_requirements as R
from backend import task_wakes
from backend.item_sources import github as MQ
from backend.migrations import renames as _renames  # noqa: F401 - the migrations package loads its helpers first
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.fake_github import FakeGitHub
from backend.tests.helpers.github_facts_merge_queue import REPO, RM, engineering, new, seen

SECRET = "Salary numbers"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def private(c, task_id):
    """Only Ana reads it, and it says what it is about."""
    c.execute("UPDATE tasks SET private=1, readers=?, title=? WHERE id=?", (json.dumps([X.ANA]), SECRET, task_id))


def told(text, task_id, ref):
    return any(word in text for word in (task_id, ref, SECRET))


def ordered(c, hide):
    """B6 (#40) and B7 (#41, after B6) in Engineering, B7 in Merge held on B6; B6 private when `hide`."""
    gh = FakeGitHub()
    engineering(c)
    b6, b7 = new(c, 1), new(c, 2)
    TR.relate(c, X.ANA, b7, b6, "after")
    c.execute("UPDATE tasks SET number=CASE id WHEN ? THEN 40 ELSE 41 END WHERE id IN (?,?)", (b6, b6, b7))
    if hide:
        private(c, b6)
    else:
        c.execute("UPDATE tasks SET title=? WHERE id=?", (SECRET, b6))
    gh.set_checks(REPO, gh.open_pr(REPO, 2), "success")
    seen(c, gh, 2)
    assert E.task(c, b7)["stage"]["name"] == "Merge" and MO.held(c, E.task(c, b7))["id"] == b6
    return b6, b7


def merged_line(c, task_id):
    row = E.task(c, task_id)
    return next(s for s in R.standings(c, row) if s.requirement.get("condition") == "pr_merged").line


def test_a_private_task_another_merges_after_is_not_named_in_its_wake_standing_read_or_refusal(c):
    b6, b7 = ordered(c, hide=True)
    row = E.task(c, b7)
    body = task_wakes.body(c, row, to=RM)
    assert MO.UNNAMED in body and not told(body, b6, "#40")
    assert merged_line(c, b7) == "Not queued until a private task merges"
    view = TC.task_view(c, b7, RM)
    assert view["turn"]["line"] == MO.UNNAMED and not told(json.dumps(view), b6, "#40")
    said = E._said(c, RM, row, [s for s in R.standings(c, row) if not s.met])
    assert not told(" ".join(said), b6, "#40")
    entries = [TC.ask_entry(c, b7, s.requirement["id"], RM) for s in R.standings(c, row)]
    assert not told(json.dumps(entries), b6, "#40")
    with pytest.raises(H.Refused) as refused:
        MQ.requeue(c, b7, RM, seen_version=row["version"])
    assert refused.value.detail == MQ.HELD_FOR.format(earlier=TC.PRIVATE_TASK)
    # A person may queue it anyway; its history, read by everyone who reads B7, does not name B6 either.
    MQ.requeue(c, b7, X.ANA)
    note, data = c.execute(
        "SELECT note, data FROM history_events WHERE subject_id=? AND kind='pr_queued' ORDER BY rowid DESC", (b7,)
    ).fetchone()
    assert note == "Queued before a private task merged" and b6 not in str(data)


def test_a_private_earlier_task_closed_without_merging_is_not_named_to_the_later_task_s_owner(c):
    b6, b7 = ordered(c, hide=True)
    E.move(c, b6, E.CLOSED, X.ANA, "Not needed after all")
    assert merged_line(c, b7) == "A private task closed without merging. Queue it, or send it back."
    (key,) = [key for key, _ in X.wakes(c, b7, RM) if ":after_closed:" in key]
    body = c.execute("SELECT body FROM messages WHERE wake_key=?", (key,)).fetchone()[0]
    assert "A private task closed without merging" in body and not told(body, b6, "#40")


def test_an_earlier_task_every_reader_may_open_is_still_named(c):
    b6, b7 = ordered(c, hide=False)
    body = task_wakes.body(c, E.task(c, b7), to=RM)
    assert "After #40: waits for it to merge" in body
    assert merged_line(c, b7) == "Not queued until #40 merges"
    assert TC.task_view(c, b7, RM)["turn"]["line"] == "After #40: waits for it to merge"


def blocked_on(c, hide):
    """Part 2 depends on Part 1 (private when `hide`), both in Build, owned by the Builder."""
    X.bot(c, "builder")
    stages = (
        {"name": "Build", "owner": "bot:builder", "requirements": [{"kind": "owner_done"}]},
        {"name": "Review", "owner": "bot:builder", "requirements": [{"kind": "owner_done"}]},
    )
    X.pipeline(c, "eng", "Engineering", stages=stages)
    blocker, blocked = (X.new_task(c, X.ANA, title, "eng")["id"] for title in (SECRET, "Part 2"))
    c.execute("UPDATE tasks SET number=304 WHERE id=?", (blocker,))
    assert TR.relate(c, X.ANA, blocked, blocker, "depends_on")
    if hide:
        private(c, blocker)
    X.settled(c)
    return blocker, blocked


def unblocked(c, task_id):
    rows = c.execute("SELECT body FROM messages WHERE wake_key LIKE ?", (f"{task_id}:%:{D.WAKE}:%",)).fetchall()
    return [row[0] for row in rows]


def test_a_private_blocker_is_not_named_in_the_turn_line_or_the_wake_when_it_ends(c):
    blocker, blocked = blocked_on(c, hide=True)
    view = TC.task_view(c, blocked, "human:ben")
    assert view["turn"]["line"] == D.UNNAMED and not told(json.dumps(view), blocker, D.ref(E.task(c, blocker)))
    # Ana reads the blocker: she is told it.
    assert SECRET in TC.task_view(c, blocked, X.ANA)["turn"]["line"]
    E.move(c, blocker, E.DONE, X.ANA, "Finished")
    (body,) = unblocked(c, blocked)
    assert "A private task is done" in body and not told(body, blocker, D.ref(E.task(c, blocker)))


def test_a_blocker_everyone_may_open_is_named_when_it_ends(c):
    blocker, blocked = blocked_on(c, hide=False)
    assert SECRET in TC.task_view(c, blocked, "human:ben")["turn"]["line"]
    E.move(c, blocker, E.DONE, X.ANA, "Finished")
    (body,) = unblocked(c, blocked)
    assert f"{D.ref(E.task(c, blocker))} is done" in body
