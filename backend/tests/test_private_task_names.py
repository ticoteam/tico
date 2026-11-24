"""A task a reader may not open is never named to them: not by title, number (#40) or id (task_calls.may_name). Each
line that names another task (what a task merges after, what it depends on) reads "a private task" instead, in a
wake, a note kept in the task's thread, a refusal, a standing and the task's own read; a task they may open is named
as before."""

import json
from types import SimpleNamespace

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


def ana():
    return SimpleNamespace(actor=X.ANA)


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
    assert MO.UNNAMED in body and MO.NOT_QUEUED_UNNAMED in body and not told(body, b6, "#40")
    # GitHub's standing carries the earlier task and is said per reader: Ana opens B6, the Release Owner does not.
    merged = next(s for s in R.standings(c, row) if s.requirement.get("condition") == "pr_merged").requirement["id"]
    assert TC.ask_entry(c, b7, merged, X.ANA)["standing"] == "Not queued until #40 merges"
    assert TC.ask_entry(c, b7, merged, RM)["standing"] == MO.NOT_QUEUED_UNNAMED
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


def blocked_on(c, hide, settle=True):
    """Part 2 depends on Part 1 (private when `hide`), both in Build, owned by the Builder; `settle`: no wake left
    queued."""
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
    if settle:
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


def test_a_wake_folded_into_another_keeps_no_key_naming_a_private_blocker(c):
    """A wake for Part 2 is still queued when Part 1 ends: the new wake folds it, and the refs the thread serves keep
    each folded wake's key only as a digest (the key `unblocked:<id>` names the blocker)."""
    from backend import threads

    blocker, blocked = blocked_on(c, hide=True)
    row = E.task(c, blocked)
    key = f"{blocked}:{row['visit']['id']}:note"
    assert task_wakes.add(c, row, "bot:builder", row["visit"], "Look again", wake_key=key)
    E.move(c, blocker, E.DONE, X.ANA, "Finished")
    key, refs = c.execute("SELECT wake_key, refs FROM messages WHERE wake_key LIKE ?", (f"%:{D.WAKE}:%",)).fetchone()
    message = dict(c.execute("SELECT * FROM messages WHERE wake_key=?", (key,)).fetchone())
    parts = threads._message_out(message)["refs"]["parts"]
    assert len(parts) == 2 and blocker in key and blocker not in refs
    # It is taken back by its key all the same.
    assert task_wakes.cancel(c, key) == 0
    assert len(json.loads(c.execute("SELECT refs FROM messages WHERE wake_key=?", (key,)).fetchone()[0])["parts"]) == 1


def test_a_deleted_private_blocker_stays_hidden_in_the_history_of_the_task_it_blocked(c):
    from backend import task_delete, task_reads

    blocker, blocked = blocked_on(c, hide=True)
    assert task_delete.delete_tasks(c, [blocker], apply=True, actor=X.ANA, keep_work=True)["applied"]
    (body,) = unblocked(c, blocked)
    assert "A private task was deleted" in body and not told(body, blocker, "#304")
    history = H.task_history(c, blocked)
    assert any(blocker in (e.get("old"), e.get("new")) for e in history)  # kept: the read decides
    # Ben opens company tasks only (the rule as SQL over `tasks`, which the trash is read as too).
    shown = task_reads.readable_history(c, None, SimpleNamespace(actor="human:ben"), history, "tasks.private = 0")
    assert not any(blocker in (e.get("old"), e.get("new")) for e in shown)
    assert any(e.get("field") == "depends_on" for e in task_reads.readable_history(c, None, ana(), history, "1"))
    # Purged, it is hidden from everyone: no audience is left to read it by.
    c.execute("UPDATE task_trash SET purged_at=? WHERE task_id=?", (H.now(), blocker))
    shown = task_reads.readable_history(c, None, ana(), history, "1")
    assert not any(blocker in (e.get("old"), e.get("new")) for e in shown)


def test_a_blocker_whose_release_production_runs_is_not_named_when_private(c):
    from backend.tests.test_task_depends import APP, merged_pr

    blocker, blocked = blocked_on(c, hide=True)
    c.execute("DELETE FROM task_relations WHERE from_task=? AND kind='depends_on'", (blocked,))
    merged_pr(c, blocker, 7, "m7")
    D.on_release(c, "0.6.0-rc.8", APP)
    assert TR.relate(c, X.ANA, blocked, blocker, "depends_on", until="released")
    private(c, blocker)
    E.move(c, blocker, E.DONE, X.ANA, "Finished")
    from backend.tests.test_task_depends import published

    published(c, "v0.6.0-rc.9", "m7")
    assert D.on_release(c, "0.6.0-rc.9", APP) == [blocked]
    (body,) = unblocked(c, blocked)
    assert "A private task is in production: it runs 0.6.0-rc.9" in body and not told(body, blocker, "#304")


def side(c, title):
    """A private task in another pipeline, held by another bot: the Builder may not open it."""
    X.bot(c, "other")
    stages = ({"name": "Do", "owner": "bot:other", "requirements": [{"kind": "owner_done"}]},)
    X.pipeline(c, "side", "Side", stages=stages)
    found = X.new_task(c, X.ANA, title, "side")["id"]
    private(c, found)
    return found


def test_a_loop_refusal_names_only_the_tasks_the_caller_may_open(c):
    X.bot(c, "builder")
    stages = ({"name": "Build", "owner": "bot:builder", "requirements": [{"kind": "owner_done"}]},)
    X.pipeline(c, "eng", "Engineering", stages=stages)
    a, d = (X.new_task(c, X.ANA, title, "eng")["id"] for title in ("A", "D"))
    b = side(c, SECRET)
    numbers = "UPDATE tasks SET number=CASE id WHEN ? THEN 1 WHEN ? THEN 2 ELSE 3 END WHERE id IN (?,?,?)"
    c.execute(numbers, (a, b, a, b, d))
    TR.relate(c, X.ANA, d, b, "depends_on")
    TR.relate(c, X.ANA, b, a, "depends_on")
    with pytest.raises(H.Refused) as loop:
        TR.relate(c, "bot:builder", a, d, "depends_on")
    assert "a private task" in loop.value.detail and not told(loop.value.detail, b, "#2")
    assert "#1" in loop.value.detail and "#3" in loop.value.detail
    # Ana opens the private task: she is told it, and the refusal on record still does not name it.
    with pytest.raises(H.Refused) as hers:
        TR.relate(c, X.ANA, a, d, "depends_on")
    assert "#2" in hers.value.detail
    recorded = c.execute("SELECT detail FROM refusals WHERE actor=? ORDER BY rowid DESC", (X.ANA,)).fetchone()[0]
    kept = json.loads(recorded)
    assert "a private task" in kept["detail"] and not told(kept["detail"], b, "#2") and "#1" in kept["detail"]


def test_a_merge_order_loop_refusal_names_only_the_tasks_the_caller_may_open(c):
    from backend.tests.helpers.github_facts_merge_queue import B

    engineering(c)
    b6, b8 = new(c, 1), new(c, 3)
    b7 = side(c, SECRET)
    numbers = "UPDATE tasks SET number=CASE id WHEN ? THEN 6 WHEN ? THEN 7 ELSE 8 END WHERE id IN (?,?,?)"
    c.execute(numbers, (b6, b7, b6, b7, b8))
    TR.relate(c, X.ANA, b7, b6, "after")
    TR.relate(c, X.ANA, b8, b7, "after")
    with pytest.raises(H.Refused) as loop:
        TR.relate(c, B, b6, b8, "after")
    assert "a private task" in loop.value.detail and not told(loop.value.detail, b7, "#7")
    assert "#6" in loop.value.detail and "#8" in loop.value.detail


def test_a_wake_folded_before_keys_were_digests_keeps_only_digests_once_folded_again():
    old = {"parts": [{"key": "t1:v1:unblocked:secret-task", "text": "One"}, {"key": "0" * 32, "text": "Two"}]}
    parts = task_wakes._parts(old, "t1:v1:owner:0", "One\n\nTwo")
    assert [p["key"] for p in parts] == [task_wakes._part_key("t1:v1:unblocked:secret-task"), "0" * 32]


def test_the_inbox_serves_no_wake_key(c):
    blocker, blocked = blocked_on(c, hide=True)
    E.move(c, blocker, E.DONE, X.ANA, "Finished")
    (woken,) = c.execute("SELECT id FROM messages WHERE wake_key LIKE ?", (f"%:{D.WAKE}:{blocker}",)).fetchone()
    messages = H.inbox(c, "bot:builder")["messages"]
    assert woken in [m["id"] for m in messages] and all("wake_key" not in m for m in messages)
