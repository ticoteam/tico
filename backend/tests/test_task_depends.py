"""A task that depends on another one ("Blocked by #304", backend/task_depends.py). Nov 13: 10 of 13 snoozed Engineering
tasks were really waiting on another task (#304, #311, #264, the fix for a red main) and slept to a guessed date, several
past the moment their blocker cleared. A dependency is a relation with no date: the task shows Blocked by the task, it is
nobody's turn, holds no slot, is never Late, and its owner is woken once, the moment its last open dependency ends."""

import sqlite3
from importlib import import_module

import pytest

from backend import never_stuck as NS
from backend import task_depends as D
from backend import task_engine as E
from backend import task_relations as TR
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X

B = "bot:builder"
# The column the until-released migration (20261113070100) adds: a table from before it has no such line.
UNTIL_COLUMN = "  until TEXT CHECK (until IS NULL OR (kind = 'depends_on' AND until = 'released')),\n"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        yield conn


def build(c, limit=None, minutes=None, release=None):
    """Build, owned by the Builder bot, left when the owner says done; with `release`, a Release stage whose In a
    release has those settings."""
    X.bot(c, "builder")
    stages = [
        {
            "name": "Build",
            "owner": B,
            "task_limit": limit,
            "expected_minutes": minutes,
            "requirements": [{"kind": "owner_done"}],
        },
        {"name": "Review", "owner": B, "requirements": [{"kind": "owner_done"}]},
    ]
    if release is not None:
        stages.append({"name": "Release", "owner": B, "requirements": [X.github("in_release", settings=release)]})
    return X.pipeline(c, "eng", "Engineering", stages=tuple(stages))


def task(c, title):
    return X.new_task(c, X.ANA, title, "eng")["id"]


def ref(c, task_id):
    return D.ref(E.task(c, task_id))


def bodies(c, task_id, what=D.WAKE):
    """The bodies of the wakes the engine sent about the task for an ended dependency."""
    return [
        row[0]
        for row in c.execute(
            "SELECT body FROM messages WHERE wake_key LIKE ? ORDER BY created_at, id", (f"{task_id}:%:{what}:%",)
        )
    ]


def ended(c, task_id, to=E.DONE):
    E.move(c, task_id, to, X.ANA, "Finished" if to == E.DONE else "Not needed")


def test_a_task_that_depends_on_another_is_blocked_by_it_and_nobodys_turn(c):
    build(c)
    blocker, blocked = task(c, "Growth synced sources part 1"), task(c, "Growth synced sources part 2")
    entered = X.wakes(c, blocked, B)  # Build's, on entering
    assert TR.relate(c, X.ANA, blocked, blocker, "depends_on")
    waits = W.waiting_on(c, blocked)
    assert [(w.state, w.who, w.line) for w in waits] == [
        (W.TASKS, blocker, f"Blocked by {ref(c, blocker)} Growth synced sources part 1, in Build")
    ]
    assert D.is_wait(waits[0].requirement_id)
    assert W.turn(c, E.task(c, blocked)) is None and W.turns(c, E.task(c, blocked)) == set()
    # Read from both ends: it depends on #304, and #304 blocks it.
    both = TR.grouped(c, [blocked, blocker])
    assert [(e["id"], e["direction"]) for e in both[blocked]["depends_on"]] == [(blocker, "out")]
    assert [(e["id"], e["direction"]) for e in both[blocker]["depends_on"]] == [(blocked, "in")]
    assert TR.readback(c, blocked, blocker, "depends_on")[1].startswith(f"{ref(c, blocker)} ")
    assert " blocks " in TR.readback(c, blocked, blocker, "depends_on")[1]
    # A reader who may not open the blocker is not told its title.
    hidden = W.unnamed(waits[0], lambda _task_id: False)
    assert (hidden.who, hidden.line) == (None, D.UNNAMED)
    # The never-stuck pass does not wake the Builder for it.
    NS.check(c, later(60))
    assert X.wakes(c, blocked, B) == entered


def test_the_owner_is_woken_once_when_the_last_open_dependency_is_done(c):
    build(c)
    first, second, blocked = task(c, "Part 1"), task(c, "Fix main"), task(c, "Part 2")
    TR.relate(c, X.ANA, blocked, first, "depends_on")
    TR.relate(c, X.ANA, blocked, second, "depends_on")
    entered = X.wakes(c, blocked, B)
    ended(c, first)
    # Still blocked by the fix: nobody is woken.
    assert [w.line for w in W.waiting_on(c, blocked)] == [f"Blocked by {ref(c, second)} Fix main, in Build"]
    assert bodies(c, blocked) == [] and X.wakes(c, blocked, B) == entered
    ended(c, second)
    (body,) = bodies(c, blocked)
    assert f"{ref(c, second)} is done" in body
    waits = W.waiting_on(c, blocked)
    assert not any(D.is_wait(w.requirement_id) for w in waits) and W.turn(c, E.task(c, blocked)) == B
    # Once: the never-stuck pass and a second end change nothing more.
    NS.check(c)
    assert len(bodies(c, blocked)) == 1


def test_a_dependency_closed_without_doing_it_wakes_the_owner_to_re_plan(c):
    build(c)
    blocker, blocked = task(c, "Part 1"), task(c, "Part 2")
    TR.relate(c, X.ANA, blocked, blocker, "depends_on")
    X.settled(c)
    ended(c, blocker, E.CLOSED)
    (body,) = bodies(c, blocked)
    assert f"{ref(c, blocker)} was closed without doing it" in body


def test_a_blocked_task_whose_work_is_finished_waits_and_moves_on_when_unblocked(c):
    """The Builder said done while still blocked: the task does not move on until the blocker ends; then it moves on by
    itself, as a task does when what it filed ends."""
    build(c)
    blocker, blocked = task(c, "Part 1"), task(c, "Part 2")
    TR.relate(c, X.ANA, blocked, blocker, "depends_on")
    X.say_done(c, E.task(c, blocked))
    assert E.advance(c, blocked) is None
    NS.check(c)
    assert E.task(c, blocked)["stage"]["name"] == "Build"
    ended(c, blocker)
    assert E.task(c, blocked)["stage"]["name"] == "Review"


def test_a_loop_a_dependency_on_an_ended_task_and_on_itself_are_refused(c):
    build(c)
    a, b, d = task(c, "A"), task(c, "B"), task(c, "D")
    TR.relate(c, X.ANA, a, b, "depends_on")
    TR.relate(c, X.ANA, b, d, "depends_on")
    with pytest.raises(H.Refused) as loop:
        TR.relate(c, X.ANA, d, a, "depends_on")
    assert loop.value.rule == "kind" and "loop" in loop.value.detail
    with pytest.raises(H.Refused):
        TR.relate(c, X.ANA, a, a, "depends_on")
    done = task(c, "Done already")
    ended(c, done)
    with pytest.raises(H.Refused) as gone:
        TR.relate(c, X.ANA, a, done, "depends_on")
    assert gone.value.rule == "task_ended"
    # Taking it off: the task is no longer blocked by it.
    assert TR.relate(c, X.ANA, a, b, "depends_on", remove=True)
    assert not any(D.is_wait(w.requirement_id) for w in W.waiting_on(c, a))


def test_a_bot_with_only_its_own_task_may_make_it_wait_on_another_owners_main_red_task(c):
    """Nov 13: the Builder, Reviewer, QA Owner and Release Owner templates tell a bot whose checks fail only because main
    is red to make its own task depend on the one open "Main red:" task, which another bot owns. A dependency is the
    waiting task's alone, so that bot may write it; relating the two any other way changes the main-red task too and is
    refused, which is why the templates say to comment on it instead."""
    build(c)
    fixer = X.bot(c, "release-owner")
    main_red = X.new_task(c, X.ANA, "Main red: inboxes.cts, since #3335", "general", assignee=fixer)["id"]
    mine = task(c, "My change")
    assert E.task(c, mine)["owner_id"] == B and E.task(c, main_red)["owner_id"] == fixer
    assert TR.relate(c, B, mine, main_red, "depends_on") is True
    assert D.blocked(c, mine)
    for other_way in (
        lambda: TR.relate(c, B, mine, main_red, "related"),
        lambda: TR.relate(c, B, main_red, mine, "depends_on"),
    ):
        with pytest.raises(H.Refused) as refused:
            other_way()
        assert refused.value.rule == "identity"


def later(minutes):
    return H.shift(H.now(), minutes=minutes)


def test_a_blocked_task_holds_no_slot_is_never_late_and_is_not_handed_on(c):
    build(c, limit=2, minutes=60)
    blocker, blocked = task(c, "Part 1"), task(c, "Part 2")
    stage = E.task(c, blocker)["stage_id"]
    assert not E.has_room(c, E.stage(c, stage))  # both hold Build's two slots
    TR.relate(c, X.ANA, blocked, blocker, "depends_on")
    assert E.has_room(c, E.stage(c, stage)) and W.slots(c, stage).held == 1
    third = task(c, "Part 3")
    assert E.task(c, third)["stage_id"] == stage  # took the slot the blocked task gave up
    entered = X.wakes(c, blocked)
    NS.check(c, later(180))
    NS.check(c, later(300))
    assert "late" not in X.events(c, blocked) and X.wakes(c, blocked) == entered
    assert (E.task(c, blocked)["visit"].get("level") or "owner") == "owner"


def test_a_blocked_task_leaves_its_person_owners_needs_you(c):
    build(c)
    X.pipeline(c)  # General: To do, the assignee's
    blocker = task(c, "Part 1")
    mine = X.new_task(c, X.ANA, "Write the copy", assignee=X.ANA)["id"]
    assert any(item.get("task_id") == mine for item in W.needs(c, X.ANA))
    TR.relate(c, X.ANA, mine, blocker, "depends_on")
    assert not any(item.get("task_id") == mine for item in W.needs(c, X.ANA))


def test_blocked_time_does_not_count_toward_late():
    """A task blocked for five and a half hours of its six in Build (two dependencies that overlap) spent half an hour
    there: each blocked moment is counted once."""
    spans = [("2026-11-13T00:00:00Z", "2026-11-13T05:00:00Z"), ("2026-11-13T04:00:00Z", "2026-11-13T05:30:00Z")]
    assert NS._covered(spans, "2026-11-13T00:00:00Z", "2026-11-13T06:00:00Z") == pytest.approx(5.5 * 3600)


def test_the_migration_lets_an_old_table_take_depends_on_and_keeps_its_rows():
    migration = import_module("backend.migrations.20261113060000_task_relations_depends_on")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY)")
    conn.executemany("INSERT INTO tasks VALUES(?)", [("a",), ("b",)])
    conn.executescript(TR.SCHEMA.replace("'after','depends_on'", "'after','blocks'").replace(UNTIL_COLUMN, ""))
    conn.execute("INSERT INTO task_relations VALUES('a','b','after','human:ana','2026-11-12T00:00:00Z')")
    conn.execute("INSERT INTO task_relations VALUES('b','a','blocks','human:ana','2026-11-12T00:00:01Z')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO task_relations VALUES('b','a','depends_on','human:ana','2026-11-12T00:00:02Z')")
    migration.up(conn)
    migration.up(conn)  # once only
    conn.execute("INSERT INTO task_relations VALUES('b','a','depends_on','human:ana','2026-11-13T00:00:00Z')")
    assert conn.execute("SELECT from_task, to_task, kind FROM task_relations ORDER BY rowid").fetchall() == [
        ("a", "b", "after"),
        ("b", "a", "depends_on"),
    ]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO task_relations VALUES('a','b','blocks','human:ana','2026-11-13T00:00:01Z')")
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
    assert indexes == {"task_relations_to", "task_relations_from", "task_relations_one_parent"}


def blocked_since(c, task_id, minutes):
    """The task entered its stage and its dependency was written `minutes` ago."""
    at = H.shift(H.now(), minutes=-minutes)
    c.execute("UPDATE visits SET entered_at=? WHERE task_id=? AND left_at IS NULL", (at, task_id))
    c.execute("UPDATE task_relations SET created_at=? WHERE from_task=? AND kind='depends_on'", (at, task_id))
    c.execute(
        "UPDATE history_events SET created_at=? WHERE subject_type='task' AND subject_id=? AND kind='depends_on'",
        (at, task_id),
    )


@pytest.mark.parametrize("how", ["done", "unrelate", "delete"])
def test_three_hours_blocked_in_a_one_hour_stage_is_not_late_however_the_block_ends(c, how):
    """Review 1: taking the dependency off, or deleting the blocker, removes the relation row; the blocked time must not
    count toward Late after that, as it does not when the blocker is done."""
    from backend import task_delete

    build(c, minutes=60)
    blocker, blocked = task(c, "Part 1"), task(c, "Part 2")
    TR.relate(c, X.ANA, blocked, blocker, "depends_on")
    blocked_since(c, blocked, 180)
    if how == "done":
        ended(c, blocker)
    elif how == "unrelate":
        TR.relate(c, X.ANA, blocked, blocker, "depends_on", remove=True)
    else:
        assert task_delete.delete_tasks(c, [blocker], apply=True, actor=X.ANA, keep_work=True)["applied"]
    assert not D.blocked(c, blocked)
    NS.check(c, later(1))
    assert "late" not in X.events(c, blocked) and "ladder" not in X.events(c, blocked)


def test_a_deleted_blocker_wakes_the_owner_once(c):
    from backend import task_delete

    build(c)
    blocker, blocked = task(c, "Part 1"), task(c, "Part 2")
    TR.relate(c, X.ANA, blocked, blocker, "depends_on")
    number = ref(c, blocker)
    assert task_delete.delete_tasks(c, [blocker], apply=True, actor=X.ANA, keep_work=True)["applied"]
    (body,) = bodies(c, blocked)
    assert f"{number} was deleted" in body


def test_a_dependency_holds_only_until_its_task_first_ends(c):
    """Review 1: a blocker reopened after its dependent went on does not silently block the dependent again."""
    build(c)
    blocker, blocked = task(c, "Part 1"), task(c, "Part 2")
    TR.relate(c, X.ANA, blocked, blocker, "depends_on")
    ended(c, blocker)
    E.move(c, blocker, E.REOPEN, X.ANA, "Found another case")
    assert not D.blocked(c, blocked)
    assert not any(D.is_wait(w.requirement_id) for w in W.waiting_on(c, blocked))
    # Both tasks' history says it came off when the blocker ended.
    assert "depends_on" in X.events(c, blocked) and "blocks" in X.events(c, blocker)


def test_a_refused_move_of_the_dependent_never_stops_its_blocker_ending(c, monkeypatch):
    """Review 1: release moves the dependent inside the blocker's own move; a refusal there must not refuse the end."""
    build(c)
    blocker, blocked = task(c, "Part 1"), task(c, "Part 2")
    TR.relate(c, X.ANA, blocked, blocker, "depends_on")

    def refuse(conn, task_id):
        H.refuse(conn, "system", "stage_rule", "A rule holds it")

    monkeypatch.setattr(E, "advance", refuse)
    ended(c, blocker)
    assert E.task(c, blocker)["status"] == "done"
    (body,) = bodies(c, blocked)
    assert f"{ref(c, blocker)} is done" in body


@pytest.mark.parametrize("how", ["done", "unrelate", "delete"])
def test_the_late_clock_runs_again_once_the_block_has_ended(c, how):
    """Review 2: the blocked span must end when the block does, or the task could never be Late again."""
    from backend import task_delete

    build(c, minutes=60)
    blocker, blocked = task(c, "Part 1"), task(c, "Part 2")
    TR.relate(c, X.ANA, blocked, blocker, "depends_on")
    blocked_since(c, blocked, 180)
    if how == "done":
        ended(c, blocker)
    elif how == "unrelate":
        TR.relate(c, X.ANA, blocked, blocker, "depends_on", remove=True)
    else:
        assert task_delete.delete_tasks(c, [blocker], apply=True, actor=X.ANA, keep_work=True)["applied"]
    NS.check(c, later(90))
    assert "late" in X.events(c, blocked)


def test_a_blocker_restored_from_the_trash_does_not_block_again(c):
    """Review 2: the dependents were released (and told) when the blocker was deleted; restoring it changes nothing
    for them."""
    from backend import task_delete

    build(c)
    blocker, blocked = task(c, "Part 1"), task(c, "Part 2")
    TR.relate(c, X.ANA, blocked, blocker, "depends_on")
    assert task_delete.delete_tasks(c, [blocker], apply=True, actor=X.ANA, keep_work=True)["applied"]
    told = bodies(c, blocked)
    task_delete.restore_tasks(c, [blocker], actor=X.ANA)
    assert E.task(c, blocker) and not D.blocked(c, blocked)
    assert bodies(c, blocked) == told


# ----------------------------------------------------------------------------- until released
APP = "acme/app"


def merged_pr(c, task_id, number, sha, repo=APP):
    """A PR of the task, merged as `sha`."""
    from backend import item_metadata as IM

    c.execute(
        "INSERT INTO task_links (id, task_id, kind, url, repo, number, source, item_type, item_key, created_at) "
        "VALUES (?,?,?,?,?,?,'github','pr',?,?)",
        (
            H.new_id(),
            task_id,
            "pr",
            f"https://github.com/{repo}/pull/{number}",
            repo,
            number,
            f"{repo}#{number}",
            H.now(),
        ),
    )
    IM.save(c, "github", "pr", f"{repo}#{number}", {"state": "merged", "head_sha": sha, "merged_sha": sha}, 1)


def published(c, tag, *holding, repo=APP):
    """A release Tico has saved, whose history holds the commits `holding`."""
    rid, at = H.new_id(), H.now()
    c.execute(
        "INSERT INTO github_releases (id, repo, tag, commit_sha, published_at, metadata_version, read_at, created_at) "
        "VALUES (?,?,?,?,?,1,?,?)",
        (rid, repo, tag, "x" + tag, at, at, at),
    )
    for sha in holding:
        c.execute(
            "INSERT INTO github_release_commits (release_id, commit_sha, included, checked_at) VALUES (?,?,1,?)",
            (rid, sha, at),
        )


def test_until_released_stays_blocked_after_done_and_wakes_once_when_production_runs_its_release(c):
    """The spec's acceptance: a Validate task that depends on its Engineering task until released stays blocked when
    that task is Done; when production runs a release holding its merged PRs, its owner is woken once. Nov 13: 45 tasks
    were snoozed to a guessed release date instead."""
    build(c)
    work, check = task(c, "Growth synced sources"), task(c, "Validate growth synced sources")
    merged_pr(c, work, 7, "m7")
    D.on_release(c, "0.6.0-rc.8", APP)  # production runs rc.8
    assert TR.relate(c, X.ANA, check, work, "depends_on", until="released")
    assert TR.grouped(c, [check])[check]["depends_on"][0]["until"] == "released"
    assert TR.readback(c, check, work, "depends_on")[0].endswith("until it is released")
    ended(c, work)
    assert D.blocked(c, check) and bodies(c, check) == []
    (wait,) = W.waiting_on(c, check)
    assert (wait.state, wait.line) == (
        W.TASKS,
        f"Blocked by {ref(c, work)} Growth synced sources, done, until production runs its release",
    )
    assert W.turn(c, E.task(c, check)) is None
    # rc.9 is cut with the merge, but production still runs rc.8: nothing.
    published(c, "v0.6.0-rc.9", "m7")
    assert D.on_release(c, "0.6.0-rc.8", APP) == [] and D.blocked(c, check)
    # Production runs rc.10 (after rc.9, not before it as text would sort): it holds the merge. Woken once.
    assert D.on_release(c, "0.6.0-rc.10", "Acme/App") == [check]
    assert not D.blocked(c, check)
    (body,) = bodies(c, check)
    assert f"{ref(c, work)} is in production: it runs 0.6.0-rc.10" in body
    assert D.on_release(c, "0.6.0-rc.11", APP) == [] and len(bodies(c, check)) == 1


def test_a_task_held_after_done_until_released_counts_as_blocked_everywhere_a_snooze_does(c):
    """BLOCKED is the filter beside every open-snooze filter (never-stuck, Health, stage picks, turns): a Validate task
    held past its Engineering task's Done must match it, or it is reported as stuck while it waits for a release."""
    build(c)
    work, check, free = task(c, "Part 1"), task(c, "Validate part 1"), task(c, "Something else")
    merged_pr(c, work, 7, "m7")
    D.on_release(c, "1.1.0", APP)
    TR.relate(c, X.ANA, check, work, "depends_on", until="released")
    ended(c, work)
    matched = {str(r[0]) for r in c.execute(f"SELECT t.id FROM tasks t WHERE {D.BLOCKED % 't'}")}
    assert check in matched and free not in matched


def test_a_done_task_whose_release_was_never_read_is_read_again_and_its_dependent_woken(company):
    """Review 1 of #3660, Probe A: the Engineering task reached Done (moved by hand, or Release pushed past) before any
    release holding its merge was read. Nothing read its releases again once it was Done, so the Validate task stayed
    blocked for good and nobody was told. The background re-read now reads them while a dependency waits; the moment it
    finds the merge in the release production runs, the owner is woken once."""
    from backend.item_sources import github as G
    from backend.tests.helpers.fake_github import FakeGitHub

    gh = FakeGitHub()
    with company.transaction() as c:
        build(c)
        work, check = task(c, "Part 1"), task(c, "Validate part 1")
        merged_pr(c, work, 7, "m7")
        D.on_release(c, "1.1.0", APP)
        TR.relate(c, X.ANA, check, work, "depends_on", until="released")
        ended(c, work)
        assert D.blocked(c, check)
    gh.history[APP] = ["m7"]
    gh.publish(APP, "v1.2.0")
    G._refresh_releases(company, gh, H.now())
    with company.transaction() as c:
        assert G.release_of(c, APP, "m7", prereleases=True)["tag"] == "v1.2.0"
        assert D.blocked(c, check) and bodies(c, check) == []  # read, but production still runs 1.1.0
        assert D.on_release(c, "1.2.0", APP) == [check]
        assert len(bodies(c, check)) == 1


def test_a_release_read_after_start_up_that_production_already_runs_wakes_the_dependent_then(company):
    """Production started on 1.2.0 before Tico read that 1.2.0 holds the merge: the read itself releases the task,
    not the next start-up."""
    from backend.item_sources import github as G
    from backend.tests.helpers.fake_github import FakeGitHub

    gh = FakeGitHub()
    with company.transaction() as c:
        build(c)
        work, check = task(c, "Part 1"), task(c, "Validate part 1")
        merged_pr(c, work, 7, "m7")
        D.on_release(c, "1.2.0", APP)
        TR.relate(c, X.ANA, check, work, "depends_on", until="released")
        ended(c, work)
        assert D.blocked(c, check)
    gh.history[APP] = ["m7"]
    gh.publish(APP, "v1.2.0")
    G._refresh_releases(company, gh, H.now())
    with company.transaction() as c:
        assert not D.blocked(c, check)
        (body,) = bodies(c, check)
        assert f"{ref(c, work)} is in production: it runs 1.2.0" in body


def test_until_released_reads_the_release_repo_the_engineering_release_stage_names(c):
    """The Release stage's In a release may read another repository's releases (`release_repo`), and production's
    release comes from there: the merge is looked for where Release found it, not in the PR's own repository."""
    from backend.item_sources import github as G

    shipped = "acme/app-releases"
    build(c, release={"release_repo": shipped})
    work, check = task(c, "Part 1"), task(c, "Validate part 1")
    merged_pr(c, work, 7, "m7")
    D.on_release(c, "1.1.0", shipped)
    E.move(c, work, "Release", X.ANA, "Merged")
    published(c, "v1.2.0", "m7", repo=shipped)
    G.saw_releases(c, shipped, G.version_now())
    TR.relate(c, X.ANA, check, work, "depends_on", until="released")
    ended(c, work)
    assert D.blocked(c, check)
    assert D.on_release(c, "1.2.0", shipped) == [check]


def test_until_released_waits_for_every_merged_pr_of_the_task(c):
    build(c)
    work, check = task(c, "Part 1"), task(c, "Validate part 1")
    merged_pr(c, work, 7, "m7")
    merged_pr(c, work, 8, "m8")
    merged_pr(c, work, 9, "o9", repo="acme/other")  # another product's code: this server never runs it
    TR.relate(c, X.ANA, check, work, "depends_on", until="released")
    ended(c, work)
    published(c, "v1.2.0", "m7")
    assert D.on_release(c, "1.2.0", APP) == []
    published(c, "v1.3.0", "m8")
    assert D.on_release(c, "1.3.0", APP) == [check]


def test_until_released_on_a_task_production_already_runs_comes_off_when_it_is_done(c):
    """Production may run the release before the Engineering task reaches Done: Done then releases at once."""
    build(c)
    work, check = task(c, "Part 1"), task(c, "Validate part 1")
    merged_pr(c, work, 7, "m7")
    published(c, "v1.2.0", "m7")
    D.on_release(c, "1.2.0", APP)
    TR.relate(c, X.ANA, check, work, "depends_on", until="released")
    ended(c, work)
    assert not D.blocked(c, check)
    (body,) = bodies(c, check)
    assert f"{ref(c, work)} is in production: it runs 1.2.0" in body


@pytest.mark.parametrize("how", ["closed", "nothing merged"])
def test_until_released_ends_with_the_task_when_there_is_no_release_to_wait_for(c, how):
    build(c)
    work, check = task(c, "Part 1"), task(c, "Validate part 1")
    D.on_release(c, "1.2.0", APP)
    TR.relate(c, X.ANA, check, work, "depends_on", until="released")
    ended(c, work, E.CLOSED if how == "closed" else E.DONE)
    assert not D.blocked(c, check)
    (body,) = bodies(c, check)
    assert (f"{ref(c, work)} was closed without doing it" if how == "closed" else f"{ref(c, work)} is done") in body


def test_a_validate_task_may_depend_on_a_done_task_until_released_but_not_on_one_in_production(c):
    """A Validate task reaches Validate when its Engineering task is Done, so that is when it is related."""
    build(c)
    work, check, late = task(c, "Part 1"), task(c, "Validate part 1"), task(c, "Validate again")
    merged_pr(c, work, 7, "m7")
    D.on_release(c, "1.1.0", APP)
    ended(c, work)
    with pytest.raises(H.Refused, match="has ended"):
        TR.relate(c, X.ANA, check, work, "depends_on")
    assert TR.relate(c, X.ANA, check, work, "depends_on", until="released")
    assert D.blocked(c, check)
    published(c, "v1.2.0", "m7")
    assert D.on_release(c, "1.2.0", APP) == [check]
    with pytest.raises(H.Refused, match="in production already"):
        TR.relate(c, X.ANA, late, work, "depends_on", until="released")


def test_until_released_on_another_products_task_is_refused_with_why(c):
    """Review 1 of #3660, Probe B: a task whose PRs merged into another product's repository never reaches the release
    this server runs. Until released cannot wait for it, and the refusal says so instead of "in production already"."""
    build(c)
    work, check = task(c, "Lights part 1"), task(c, "Validate lights part 1")
    merged_pr(c, work, 7, "l7", repo="acme/lights")
    D.on_release(c, "1.1.0", APP)
    ended(c, work)
    with pytest.raises(H.Refused, match=f"merged nothing into {APP}, whose release this server runs") as refused:
        TR.relate(c, X.ANA, check, work, "depends_on", until="released")
    assert "in production" not in str(refused.value)


def test_a_held_dependency_on_a_done_task_cannot_be_written_plain_again(c):
    """Writing it again without --until would leave a plain dependency on a Done task: a row that blocks nothing and is
    never taken off. It is refused, as a new plain dependency on a Done task is."""
    build(c)
    work, check = task(c, "Part 1"), task(c, "Validate part 1")
    merged_pr(c, work, 7, "m7")
    D.on_release(c, "1.1.0", APP)
    TR.relate(c, X.ANA, check, work, "depends_on", until="released")
    ended(c, work)
    with pytest.raises(H.Refused, match="has ended"):
        TR.relate(c, X.ANA, check, work, "depends_on")
    assert TR.until_of(c, check, work, "depends_on") == "released" and D.blocked(c, check)


def test_until_is_only_released_and_only_on_a_dependency(c):
    build(c)
    one, two = task(c, "Part 1"), task(c, "Part 2")
    with pytest.raises(H.Refused, match="Only a depends_on"):
        TR.relate(c, X.ANA, two, one, "follow_up", until="released")
    with pytest.raises(H.Refused, match="Only a depends_on"):
        TR.relate(c, X.ANA, two, one, "depends_on", until="merged")
    # A plain dependency written again until released holds until released; written plain again, it no longer does.
    TR.relate(c, X.ANA, two, one, "depends_on")
    assert TR.relate(c, X.ANA, two, one, "depends_on", until="released")
    assert TR.until_of(c, two, one, "depends_on") == "released"
    assert not TR.relate(c, X.ANA, two, one, "depends_on", until="released")
    assert TR.relate(c, X.ANA, two, one, "depends_on")
    assert TR.until_of(c, two, one, "depends_on") is None


def test_the_until_migration_adds_the_column_and_keeps_every_row():
    migration = import_module("backend.migrations.20261113070100_task_relations_until")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY)")
    conn.executemany("INSERT INTO tasks VALUES(?)", [("a",), ("b",)])
    conn.executescript(TR.SCHEMA.replace(UNTIL_COLUMN, ""))
    conn.execute(
        "INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES('a','b','depends_on','2026-11-13T00:00:00Z')"
    )
    migration.up(conn)
    migration.up(conn)  # once only
    assert conn.execute("SELECT from_task, to_task, kind, until FROM task_relations").fetchall() == [
        ("a", "b", "depends_on", None)
    ]
    conn.execute("UPDATE task_relations SET until='released'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE task_relations SET until='merged'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO task_relations(from_task,to_task,kind,created_at,until) "
            "VALUES('b','a','follow_up','2026-11-13T00:00:00Z','released')"
        )
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
    assert indexes == {"task_relations_to", "task_relations_from", "task_relations_one_parent"}


def test_the_until_and_depends_on_migrations_give_one_table_in_either_order():
    """Both are pending on an install from before them, and pending migrations run in any order: the table, its rows
    and their order come out the same either way."""
    depends = import_module("backend.migrations.20261113060000_task_relations_depends_on")
    until = import_module("backend.migrations.20261113070100_task_relations_until")
    out = []
    for order in ((depends, until), (until, depends)):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO tasks VALUES(?)", [("a",), ("b",), ("c",)])
        conn.executescript(TR.SCHEMA.replace("'after','depends_on'", "'after','blocks'").replace(UNTIL_COLUMN, ""))
        for row in (("a", "b", "after"), ("b", "a", "blocks"), ("c", "a", "follow_up")):
            conn.execute("INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,?,'t')", row)
        for migration in order:
            migration.up(conn)
        conn.execute(
            "INSERT INTO task_relations(from_task,to_task,kind,created_at,until) VALUES('c','b','depends_on','t','released')"
        )
        out.append(
            (
                conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall(),
                conn.execute(
                    "SELECT rowid, from_task, to_task, kind, until FROM task_relations ORDER BY rowid"
                ).fetchall(),
            )
        )
    assert out[0] == out[1]
    assert [r[1:4] for r in out[0][1]] == [("a", "b", "after"), ("c", "a", "follow_up"), ("c", "b", "depends_on")]


def test_a_dependency_that_comes_off_because_nothing_merged_here_never_says_in_production(c):
    """Review 2: a server with no release manifest counts every repository, so the dependency holds; once it runs one
    (or a Release stage's release repo changes), the task merged nothing there. It comes off, and its owner is told
    that, not "is in production"."""
    build(c)
    work, check = task(c, "Lights firmware"), task(c, "Validate lights firmware")
    merged_pr(c, work, 7, "m7", repo="acme/lights")
    D.on_release(c, "1.1.0", "")
    TR.relate(c, X.ANA, check, work, "depends_on", until="released")
    ended(c, work)
    assert D.blocked(c, check)
    assert D.on_release(c, "1.2.0", APP) == [check]
    (body,) = bodies(c, check)
    assert "in production" not in body and f"{ref(c, work)} is done" in body and f"merged nothing into {APP}" in body
