"""A task that merges after another one ("after #40", backend/merge_order.py). Drill B, Nov 12: B6 (#40) and B7 (#42)
changed the same line; the order "B6, then B7" lived on a side task, both were queued at once, both were ejected for a
conflict and B7 merged first. With the order on B7 itself, Merge holds B7 until B6 has merged, then queues it (or says
to rebase it onto B6). GitHub is backend/tests/helpers/fake_github.py."""

import sqlite3

import pytest

from backend import merge_order as MO
from backend import never_stuck as NS
from backend import task_engine as E
from backend import task_relations as TR
from backend import task_requirements as R
from backend import waiting_on as W
from backend.item_sources import github as MQ
from backend.migrations import renames as _renames  # noqa: F401 - the migrations package loads its helpers first
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.fake_github import FakeGitHub
from backend.tests.helpers.github_facts_merge_queue import EM, REPO, RM, B, effects, engineering, later, new, seen, stage


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def refused(fn, rule):
    with pytest.raises(H.Refused) as raised:
        fn()
    assert raised.value.rule == rule, raised.value
    return raised.value.detail


def green(c, gh, number):
    """The PR opens with green checks: its task leaves Build for Merge."""
    gh.set_checks(REPO, gh.open_pr(REPO, number), "success")
    seen(c, gh, number)


def pair(c):
    """B6 with PR 1 and B7 with PR 2, B7 after B6, both in Build."""
    engineering(c)
    b6, b7 = new(c, 1), new(c, 2)
    TR.relate(c, X.ANA, b7, b6, "after")
    return b6, b7


def ref(c, task_id):
    return MO.ref(E.task(c, task_id))


def merged_line(c, task_id):
    row = E.task(c, task_id)
    s = next(s for s in R.standings(c, row) if s.requirement.get("condition") == "pr_merged")
    return (s.state, s.on, s.line)


def turn(c, task_id):
    return W.of(c, E.task(c, task_id))[0]


def test_a_later_task_builds_side_by_side_and_waits_in_merge_until_the_earlier_one_merges(company):
    """B7 reaches Merge while B6 is still in Build: nothing held it in Build, but Merge does not queue it. Its turn is
    B6's ("After #40: waits for it to merge"), nobody is woken for it, and `tico task show` says so. B6 merges: B7 is
    queued on the same pass and GitHub queues it."""
    gh = FakeGitHub()
    with company.transaction() as c:
        b6, b7 = pair(c)
        green(c, gh, 2)
        assert stage(c, b7) == "Merge" and stage(c, b6) == "Build"  # the order changes nothing before Merge
        assert effects(c, b7) == []
        wait = turn(c, b7)
        assert (wait.state, wait.who, wait.line) == (W.TASKS, b6, f"After {ref(c, b6)}: waits for it to merge")
        assert merged_line(c, b7) == (R.WAITING, "github", f"Not queued until {ref(c, b6)} merges")
        assert W.turn(c, E.task(c, b7)) is None
        assert [who for _, who in X.wakes(c, b7)] == [B]  # Build's only
        green(c, gh, 1)
        assert stage(c, b6) == "Merge" and effects(c, b6) == [("act", "owed")] and effects(c, b7) == []
    assert MQ.run(company, gh) == 1  # B6 alone
    assert [call[2] for call in gh.calls if call[0] == "enqueue"] == [1]
    gh.merge(REPO, 1)
    MQ.refresh(company, gh, later())
    with company.transaction() as c:
        assert stage(c, b6) == "Release"
        assert effects(c, b7) == [("act", "owed")]
        assert merged_line(c, b7) == (R.WAITING, "github", "Queuing the PR")
        assert [who for _, who in X.wakes(c, b7)] == [B]
    assert MQ.run(company, gh) == 1
    assert gh.prs[(REPO, 2)]["queue_state"] == "queued"


def test_a_task_waiting_on_its_order_is_not_late_and_wakes_nobody(c):
    """Merge expects an hour; B7 waits three on B6. Late does not hand it to the manager bot: nobody can move it."""
    gh = FakeGitHub()
    b6, b7 = pair(c)
    c.execute("UPDATE stages SET expected_minutes=60 WHERE name='Merge'")
    green(c, gh, 2)
    before = X.wakes(c, b7)  # the Builder's, in Build
    NS.check(c, later(180))
    NS.check(c, later(240))
    assert X.wakes(c, b7) == before and "late" not in X.events(c, b7)
    assert W.turn(c, E.task(c, b7)) is None


def test_a_conflict_after_the_earlier_task_merged_sends_it_back_to_rebase_onto_that_task(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        b6, b7 = pair(c)
        green(c, gh, 1)
        green(c, gh, 2)
        b6_ref = ref(c, b6)
    MQ.run(company, gh)
    gh.merge(REPO, 1)
    gh.mergeable(REPO, 2, "CONFLICTING")  # B6's merge changed the same line
    MQ.refresh(company, gh, later())
    assert MQ.run(company, gh, later(1)) == 1
    with company.transaction() as c:
        assert effects(c, b7) == [("act", "failed")]
        (key,) = [key for key, _ in X.wakes(c, b7, RM) if ":queue_refused:" in key]
        body = c.execute("SELECT body FROM messages WHERE wake_key=?", (key,)).fetchone()[0]
        assert f"rebase it onto {b6_ref}" in body and "Fixes 2" in body


def test_an_earlier_task_closed_without_merging_ends_the_order_and_it_is_the_owner_s_turn_once(c):
    gh = FakeGitHub()
    b6, b7 = pair(c)
    green(c, gh, 2)
    b6_ref = ref(c, b6)
    E.move(c, b6, E.CLOSED, X.ANA, "Not needed after all")
    said = f"{b6_ref} closed without merging. Queue it, or send it back."
    assert merged_line(c, b7) == (R.NOT_MET, RM, said)
    assert effects(c, b7) == []  # Tico does not queue it by itself
    assert [k for k, _ in X.wakes(c, b7, RM) if ":after_closed:" in k] == [f"{b7}:{E.task(c, b7)['visit']['id']}:after_closed:{b6}"]
    MO.release(c, b6, E.SYSTEM)  # read again: told once
    assert len([k for k, _ in X.wakes(c, b7, RM) if ":after_closed:" in k]) == 1
    # The Release Owner queues it.
    MQ.requeue(c, b7, RM, seen_version=E.task(c, b7)["version"])
    assert effects(c, b7) == [("act", "owed")]


def test_a_bot_may_not_queue_a_held_task_and_a_person_may_with_history_saying_so(c):
    gh = FakeGitHub()
    b6, b7 = pair(c)
    green(c, gh, 2)
    for bot in (RM, EM):
        detail = refused(lambda: MQ.requeue(c, b7, bot, seen_version=E.task(c, b7)["version"]), "task_requirement")
        assert detail == MQ.HELD_FOR.format(earlier=ref(c, b6))
    assert effects(c, b7) == []
    MQ.requeue(c, b7, X.ANA)
    assert effects(c, b7) == [("act", "owed")]
    note = c.execute(
        "SELECT note FROM history_events WHERE subject_id=? AND kind='pr_queued' ORDER BY rowid DESC", (b7,)
    ).fetchone()[0]
    assert note == f"Queued before {ref(c, b6)} merged"
    assert not MO.held(c, E.task(c, b7))  # queued: nothing holds it any more


def test_taking_the_order_off_queues_a_held_task(c):
    gh = FakeGitHub()
    b6, b7 = pair(c)
    green(c, gh, 2)
    assert effects(c, b7) == []
    TR.relate(c, X.ANA, b7, b6, "after", remove=True)
    assert effects(c, b7) == [("act", "owed")]


def test_an_earlier_task_with_no_pr_releases_it_when_it_is_done(c):
    """A Product task has no PR: the wait ends when it is Done."""
    gh = FakeGitHub()
    engineering(c)
    spec = X.new_task(c, X.ANA, "The spec", "general")["id"]
    b7 = new(c, 2)
    TR.relate(c, X.ANA, b7, spec, "after")
    green(c, gh, 2)
    assert effects(c, b7) == [] and turn(c, b7).who == spec
    E.move(c, spec, E.DONE, X.ANA, "Written")
    assert effects(c, b7) == [("act", "owed")]


def test_an_order_that_would_loop_or_names_an_ended_task_is_refused(c):
    engineering(c)
    a, b, d = new(c, 1), new(c, 2), new(c, 3)
    TR.relate(c, X.ANA, b, a, "after")
    TR.relate(c, X.ANA, d, b, "after")
    detail = refused(lambda: TR.relate(c, X.ANA, a, d, "after"), "kind")
    assert detail == (
        f"That would make a loop ({ref(c, a)} → {ref(c, d)} → {ref(c, b)} → {ref(c, a)}): "
        f"{ref(c, d)} already goes after {ref(c, a)}."
    )
    assert "loop" in refused(lambda: TR.relate(c, X.ANA, a, b, "after"), "kind")
    refused(lambda: TR.relate(c, X.ANA, a, a, "after"), "kind")
    E.move(c, a, E.CLOSED, X.ANA, "Not needed")
    e = new(c, 4)
    assert "has ended" in refused(lambda: TR.relate(c, X.ANA, e, a, "after"), "task_ended")
    assert "has ended" in refused(lambda: TR.relate(c, X.ANA, a, e, "after"), "task_ended")


def test_both_tasks_show_the_order_and_a_reader_who_cannot_open_the_earlier_one_is_not_told_it(c):
    gh = FakeGitHub()
    b6, b7 = pair(c)
    green(c, gh, 2)
    later_rows = TR.grouped(c, [b7])[b7]["after"]
    earlier_rows = TR.grouped(c, [b6])[b6]["after"]
    assert [(r["id"], r["direction"], r["stage"]) for r in later_rows] == [(b6, "out", "Build")]
    assert [(r["id"], r["direction"], r["stage"]) for r in earlier_rows] == [(b7, "in", "Merge")]
    wait = turn(c, b7)
    shown = W.view(wait, lambda task_id: task_id != b6)
    assert (shown["who"], shown["line"]) == (None, MO.UNNAMED)


def test_the_migration_lets_an_old_table_take_after_and_keeps_its_rows():
    from importlib import import_module

    migration = import_module("backend.migrations.20261112165100_task_relations_after")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY)")
    conn.executemany("INSERT INTO tasks VALUES(?)", [("a",), ("b",)])
    conn.executescript(TR.SCHEMA.replace(",'after'", ""))
    conn.execute("INSERT INTO task_relations VALUES('a','b','follow_up','human:ana','2026-11-12T00:00:00Z')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO task_relations VALUES('b','a','after','human:ana','2026-11-12T00:00:00Z')")
    migration.up(conn)
    migration.up(conn)  # once only
    conn.execute("INSERT INTO task_relations VALUES('b','a','after','human:ana','2026-11-12T00:00:01Z')")
    assert conn.execute("SELECT from_task, to_task, kind FROM task_relations ORDER BY rowid").fetchall() == [
        ("a", "b", "follow_up"),
        ("b", "a", "after"),
    ]
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
    assert indexes == {"task_relations_to", "task_relations_from", "task_relations_one_parent"}


def test_a_pr_whose_metadata_comes_after_the_task_entered_merge_is_held_too(c):
    b6, b7 = pair(c)
    c.execute("UPDATE requirements SET can_push_past=1")
    E.move(c, b7, "next", X.ANA, "Reviewed by hand")
    assert stage(c, b7) == "Merge" and effects(c, b7) == []
    gh = FakeGitHub()
    gh.open_pr(REPO, 2)
    seen(c, gh, 2)
    assert effects(c, b7) == []
    assert turn(c, b7).who == b6


def test_an_earlier_task_that_merged_but_stays_in_merge_still_releases_it(company):
    """B6's PR merges while a person's snooze keeps B6 itself in Merge: B7 goes on its PR's merge, not B6's move."""
    gh = FakeGitHub()
    with company.transaction() as c:
        b6, b7 = pair(c)
        green(c, gh, 1)
        green(c, gh, 2)
        E.snooze(c, b6, X.ANA, "Holding it here", later(600))
    MQ.run(company, gh)
    gh.merge(REPO, 1)
    MQ.refresh(company, gh, later())
    with company.transaction() as c:
        assert stage(c, b6) == "Merge"
        assert effects(c, b7) == [("act", "owed")]


def test_an_earlier_task_closed_before_the_later_one_reaches_merge_is_the_owner_s_to_judge(c):
    gh = FakeGitHub()
    b6, b7 = pair(c)
    E.move(c, b6, E.CLOSED, X.ANA, "Not needed after all")
    green(c, gh, 2)
    assert stage(c, b7) == "Merge" and effects(c, b7) == []
    assert merged_line(c, b7)[:2] == (R.NOT_MET, RM)


def test_who_may_change_the_later_task_sets_its_order_and_need_only_read_the_earlier_one(c):
    """The Builder has B7 but not the Spec task: it may say B7 goes after the Spec, and may not relate the two."""
    engineering(c)
    spec = X.new_task(c, X.ANA, "The spec", "general")["id"]
    b7 = new(c, 2)
    assert TR.relate(c, B, b7, spec, "after") is True
    refused(lambda: TR.relate(c, B, b7, spec, "related"), "identity")
    refused(lambda: TR.relate(c, B, spec, b7, "after"), "identity")
